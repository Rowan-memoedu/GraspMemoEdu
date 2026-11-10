"""Private CLI for append-only OJ content and account-scoped source progress."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from .training_content import TrainingCatalog, fingerprint, validate, validate_saved
from .oj_progress import location
from .review_cards import point_id
from .topic_install import import_lock

def extend(bank, entries):
    result=deepcopy(bank)
    root=next(g for g in result['groups'] if g['title']=='OJ')
    known={q['id']:q for g in result['groups'] for q in g.get('questions',[])}
    existing={g['title'] for g in result['groups'] if g.get('parent_id')==root['id']}
    added=[]
    for entry in entries:
        q=entry['question'];pid=entry['pid']
        if not re.fullmatch('[A-Za-z][0-9]+',pid):raise ValueError('Invalid source problem number')
        if q['id'] in known:
            if known[q['id']]!=q:raise ValueError('An existing question cannot be overwritten by synchronization')
            continue
        if pid in existing:raise ValueError('Problem number already has another question')
        existing.add(pid);known[q['id']]=q;added.append(entry)
        result['groups'].append({'id':'oj-'+pid.lower(),'title':pid,'parent_id':root['id'],
            'questions':[q],'introductions':[],'content_order':[{'type':'question','id':q['id']}]})
    if added:
        result['math_css']=result.get('math_css','')+'\n'+'\n'.join(e['topic'].get('math_css','') for e in added)
        result['version']=fingerprint(result)
    return validate(result),added

def replace_values(value,mapping):
    if isinstance(value,str):return mapping.get(value,value)
    if isinstance(value,list):return [replace_values(v,mapping) for v in value]
    if isinstance(value,dict):return {mapping.get(k,k):replace_values(v,mapping) for k,v in value.items()}
    return value

def snapshot(con):
    tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t:[dict(r) for r in con.execute('SELECT rowid AS __sync_rowid__,* FROM "'+t.replace('"','""')+'" ORDER BY rowid')] for t in tables}

def migrate_version(con,bank,old,new):
    if old==new:return 0
    before=snapshot(con);mapping={old:new}
    for r in before.get('review_points',[]):
        if r['kind']=='training' and r['container']==bank and r['version']==old:
            mapping[r['id']]=point_id('training',bank,new,r['source_point'])
    for r in before.get('review_variants',[]):
        if r['point'] in mapping:
            body=json.dumps(replace_values(json.loads(r['snapshot']),mapping),ensure_ascii=False,sort_keys=True)
            mapping[r['id']]='variant-'+hashlib.sha256((mapping[r['point']]+body).encode()).hexdigest()
    expected=deepcopy(before);count=0
    for table,rows in expected.items():
        for row,prior in zip(rows,before[table]):
            for k,v in list(row.items()):
                if not isinstance(v,str):continue
                if v in mapping:row[k]=mapping[v]
                elif v[:1] in ('{','['):
                    try:body=json.loads(v)
                    except ValueError:continue
                    changed=replace_values(body,mapping)
                    if changed!=body:row[k]=json.dumps(changed,ensure_ascii=False,sort_keys=True)
            changes={k:v for k,v in row.items() if v!=prior[k]}
            if changes:
                con.execute('UPDATE "'+table.replace('"','""')+'" SET '+','.join('"'+k+'"=?' for k in changes)+' WHERE rowid=?',[*changes.values(),row['__sync_rowid__']]);count+=1
    if snapshot(con)!=expected:raise ValueError('Unrelated database values changed')
    if list(con.execute('PRAGMA foreign_key_check')):raise ValueError('Review reference migration failed')
    return count

def validate_progress(payload,bank,learner):
    known={q['id']:g['title'] for g in bank['groups'] for q in g.get('questions',[])}
    facts={}
    for qid,entry in payload['progress'].items():
        if qid not in known or known[qid]!=entry['pid']:raise ValueError('OJ progress/question mapping mismatch')
        status=entry['status']
        if type(status.get('accepted')) is not bool or type(status.get('submitted')) is not bool:raise ValueError('Invalid OJ verdict flags')
        if status['accepted'] and (not status['submitted'] or not status.get('accepted_at')):raise ValueError('Accepted requires a dated submission')
        facts[qid]={k:status.get(k) for k in ['submitted','accepted','submission_count','latest_status','latest_rid','accepted_rid','accepted_at']}
    return {'schema':1,'bank_id':bank['id'],'learner_id':learner,'origin':payload['origin'],'domain':payload['domain'],
            'user_id':payload['user_id'],'fetched_at':payload['fetched_at'],'questions':facts}

def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    pending=path.with_suffix('.pending');pending.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');os.replace(pending,path)

def install(data_dir,payload):
    data=Path(data_dir)
    binding=json.loads((data/'hydro-sync-binding.json').read_bytes())
    if any(payload[k]!=binding[k] for k in ['origin','domain','user_id','bank_id']):raise ValueError('OJ account/domain binding mismatch')
    if payload.get('complete') is not True:raise ValueError('Incomplete OJ snapshot')
    learner=binding['learner_id'];bank_path=data/'training-banks'/(binding['bank_id']+'.json')
    old=json.loads(bank_path.read_bytes());bank,added=extend(old,payload['entries'])
    progress=validate_progress(payload,bank,learner)
    if len(progress['questions'])!=payload['problem_count']:raise ValueError('Incomplete problem progress coverage')
    with sqlite3.connect(data/'learning.sqlite3') as con:
        row=con.execute('SELECT enabled,purpose,is_admin FROM access_principals WHERE learner_id=?',(learner,)).fetchone()
        if row!=(1,'test',1):raise ValueError('The authorized test administrator is unavailable')
    from .config import Settings
    from .catalog import Catalog
    from .subjects import Subjects
    changed_rows=0
    if added:
        # Back up and validate restore before a content/version transition. Status-only runs do not stop the service.
        subprocess.run(['/usr/local/sbin/graspmemoedu-backup'],check=True)
        timer=subprocess.run(['systemctl','is-active','--quiet','graspmemoedu-healthcheck.timer']).returncode==0
        if timer:subprocess.run(['systemctl','stop','graspmemoedu-healthcheck.timer'],check=True)
        subprocess.run(['systemctl','stop','graspmemoedu'],check=True)
        pointer=data/'active-content.json';original_pointer=pointer.read_bytes();original_bank=bank_path.read_bytes()
        backup=data/'migration-backups'/('hydro-sync-'+bank['version']+'.sqlite3');backup.parent.mkdir(exist_ok=True)
        with sqlite3.connect(data/'learning.sqlite3') as src,sqlite3.connect(backup) as dst:src.backup(dst)
        try:
            with import_lock(data):
                if bank_path.read_bytes()!=original_bank:raise ValueError('Concurrent bank update')
                settings=Settings.load(data);catalog=Catalog.load(settings)
                course=deepcopy(catalog.courses[bank['id']]);declarations={t['id'] for t in course['topics']}
                generation=hashlib.sha256(json.dumps(payload['entries'],sort_keys=True).encode()+original_pointer).hexdigest()
                batch=data/'content-batches'/generation;batch.mkdir(exist_ok=False)
                for e in added:
                    qid=e['question']['id']
                    if qid in declarations:raise ValueError('Existing Topic identity collision')
                    target=batch/'topics'/(qid+'.json');atomic_json(target,e['topic'])
                    course['topics'].append({'id':qid,'path':str(target),'prerequisites':[]})
                    parent=next(g['parent_id'] for g in bank['groups'] if g['id']=='oj-'+e['pid'].lower())
                    course['units'].append({'id':'oj-'+e['pid'].lower(),'title':e['pid'],'parent_id':parent,'topic_ids':[qid]})
                courses=deepcopy(list(catalog.courses.values()))
                for c in courses:
                    for t in c['topics']:t['path']=str(catalog.topic_paths[t['id']])
                courses=[course if c['id']==bank['id'] else c for c in courses]
                # Preserve paths of all existing members in the extended course.
                for t in course['topics']:
                    if t['id'] in catalog.topic_paths:t['path']=str(catalog.topic_paths[t['id']])
                atomic_json(batch/'catalog.json',{'courses':courses})
                import shutil
                for name in ['subjects.json','test-materials.json']:
                    if (settings.content_dir/name).exists():shutil.copyfile(settings.content_dir/name,batch/name)
                for name in ['graphs','mathacademy-sources']:
                    if (settings.content_dir/name).exists():shutil.copytree(settings.content_dir/name,batch/name)
                with sqlite3.connect(data/'learning.sqlite3') as con:
                    con.row_factory=sqlite3.Row;con.execute('PRAGMA foreign_keys=OFF');con.execute('BEGIN IMMEDIATE')
                    changed_rows=migrate_version(con,bank['id'],old['version'],bank['version'])
                atomic_json(bank_path,bank);atomic_json(pointer,{'schema':1,'generation':generation})
                subprocess.run(['chown','-R','graspmemoedu:graspmemoedu',str(batch)],check=True)
                subprocess.run(['chown','graspmemoedu:graspmemoedu',str(pointer),str(bank_path)],check=True)
                settings=Settings.load(data);current=Catalog.load(settings);all_banks=TrainingCatalog(data)
                all_banks.validate_ownership(current,Subjects(settings,current))
                with sqlite3.connect(data/'learning.sqlite3') as con:validate_saved(con,all_banks)
        except BaseException:
            pointer.write_bytes(original_pointer);bank_path.write_bytes(original_bank)
            with sqlite3.connect(backup) as src,sqlite3.connect(data/'learning.sqlite3') as dst:src.backup(dst)
            raise
        finally:
            subprocess.run(['systemctl','start','graspmemoedu'],check=True)
            if timer:subprocess.run(['systemctl','start','graspmemoedu-healthcheck.timer'],check=True)
    path=location(data,learner);atomic_json(path,progress)
    subprocess.run(['chown','graspmemoedu:graspmemoedu',str(path),str(path.parent)],check=True)
    return {'ok':True,'questions':len(progress['questions']),'accepted':sum(s['accepted'] for s in progress['questions'].values()),
            'submitted':sum(s['submitted'] for s in progress['questions'].values()),'added':len(added),'rekeyed_rows':changed_rows,
            'fsrs_grades_created':0,'bank_id':bank['id']}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--data-dir',required=True);p.add_argument('--input',required=True);a=p.parse_args()
    print(json.dumps(install(a.data_dir,json.loads(Path(a.input).read_bytes()))),flush=True)
