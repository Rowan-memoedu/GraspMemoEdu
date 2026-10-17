"""Read back a real isolated batch and verify a portable backup/restore round trip."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import GraphStore
from graspmemoedu.mathacademy_import import install
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.source_content import reading_blocks, SourceReader

p=argparse.ArgumentParser(); p.add_argument('data',type=Path); p.add_argument('package',type=Path); p.add_argument('output',type=Path)
a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
s=Settings.load(a.data); c=Catalog.load(s); g=GraphStore(s,c)
report=json.loads((a.package/'report.json').read_text(encoding='utf-8'))
assert len(c.topics)==len(report['scope']['topic_ids'])==888
assert c.topic_courses['ma-topic-6169'] >= {'ma-course-72','ma-course-140'}
assert c.topic_courses['ma-topic-967'] >= {'ma-course-82','ma-course-72'}
assert 'ma-topic-894' in g.dependencies()['ma-topic-925']
assert all(not t['availability']['trainable'] for t in c.topics.values())
def q(tid,qid):
    t=c.topic('ma-topic-'+str(tid))
    return next(q for m in t['modules'] for q in m['practices'] if q['id']=='ma-question-'+str(qid))
proof=q(2940,355212)['interaction']; assert len(proof['sections'])==10 and len(proof['controls'])==23
assert any(not section['complete'] for section in proof['sections'])
assert 'face(s)' in json.dumps(q(2754,257643),ensure_ascii=False)
assert any('<pre' in block['html'] and '<code' in block['html'] for block in reading_blocks(c.topic('ma-topic-2940')))
SourceReader(c.topic(c.default_topic_id),a.data)
with closing(sqlite3.connect(a.data/'learning.sqlite3')) as con:
    assert con.execute('SELECT COUNT(*) FROM topics').fetchone()[0]==0
print('Real membership, explicit edges, source gaps and control samples passed',flush=True)
same=install(a.package,a.data); assert same['status']=='unchanged'
print('Repeat install unchanged',flush=True)
backup=backup_site(Settings.load(a.data),a.output/'site.zip')
print('Backup created; restoring into a separate directory',flush=True)
restore_site(a.output/'site.zip',a.output/'restored')
restored=Catalog.load(Settings.load(a.output/'restored'))
assert restored.topics==c.topics and restored.topic_courses==c.topic_courses
assert GraphStore(Settings.load(a.output/'restored'),restored).dependencies()==g.dependencies()
result={'topics':len(c.topics),'courses':len(c.courses),'readable':report['readable'],'trainable':report['trainable'],
    'shared_identity':True,'real_control_samples':True,'source_gaps_preserved':True,'learning_rows':0,
    'repeat':same['status'],'backup_sha256':backup['sha256'],'backup_restore':'passed'}
(a.output/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result),flush=True)
