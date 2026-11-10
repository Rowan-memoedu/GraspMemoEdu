from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from training_fixtures import training_bank
from graspmemoedu.training_content import TrainingCatalog,fingerprint
from graspmemoedu.training import TrainingService
from graspmemoedu.oj_progress import location
from graspmemoedu.hydro_sync import extend,migrate_version
from scripts.sync_hydro import templates
from graspmemoedu.review_cards import point_id

class HydroSyncTests(unittest.TestCase):
    def test_accepted_facts_are_private_and_create_no_attempt_or_fsrs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'training-banks').mkdir()
            bank=training_bank();(root/'training-banks/isolated-bank.json').write_text(json.dumps(bank))
            catalog=TrainingCatalog(root);service=TrainingService(catalog,root,'owner')
            before=(root/'learning.sqlite3').read_bytes()
            path=location(root,'owner');path.parent.mkdir()
            path.write_text(json.dumps({'learner_id':'owner','bank_id':bank['id'],'questions':{'essay':{'submitted':True,'accepted':True,'accepted_at':'2026-10-26T00:00:00+00:00'}}}))
            self.assertTrue(service.group(bank['id'],'group-a')['questions'][0]['completed'])
            self.assertFalse(TrainingService(catalog,root,'other').group(bank['id'],'group-a')['questions'][0]['completed'])
            self.assertEqual((root/'learning.sqlite3').read_bytes(),before)

    def test_submission_without_ac_is_not_completion(self):
        from graspmemoedu.oj_progress import project_group
        row={'questions':[{'id':'q','completed':False,'completed_count':0}]}
        result=project_group(row,{'q':{'submitted':True,'accepted':False}})
        self.assertFalse(result['questions'][0]['completed'])
        self.assertTrue(result['questions'][0]['oj_status']['submitted'])

    def test_append_keeps_all_existing_questions_and_is_idempotent(self):
        bank=training_bank();bank['groups'].insert(0,{'id':'oj','title':'OJ','questions':[]})
        bank['version']=fingerprint(bank)
        q=deepcopy(next(q for g in bank['groups'] for q in g.get('questions',[])));q['id']='new-q'
        entry={'pid':'T9999','question':q,'topic':{'math_css':''}}
        result,added=extend(bank,[entry]);self.assertEqual(len(added),1)
        self.assertEqual(result['groups'][:len(bank['groups'])],bank['groups'])
        self.assertEqual(extend(result,[entry])[0],result)
        bad=deepcopy(entry);bad['question']['title']='changed'
        with self.assertRaises(ValueError):extend(result,[bad])

    def test_version_migration_preserves_cards_dates_and_foreign_keys(self):
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row
        con.executescript('''CREATE TABLE review_points(id TEXT PRIMARY KEY,subject TEXT,kind TEXT,container TEXT,version TEXT,source_point TEXT,created_at TEXT);
        CREATE TABLE review_reservations(paper TEXT,point TEXT REFERENCES review_points(id));
        CREATE TABLE training_memory(learner TEXT,bank TEXT,question TEXT,version TEXT,card_json TEXT,due_at TEXT);
        CREATE TABLE unrelated(id TEXT,body TEXT);''')
        old='a'*64;new='b'*64;pid=point_id('training','bank',old,'q')
        con.execute('INSERT INTO review_points VALUES (?,?,?,?,?,?,?)',(pid,'cs','training','bank',old,'q','yesterday'))
        con.execute('INSERT INTO review_reservations VALUES (?,?)',('paper',pid))
        con.execute('INSERT INTO training_memory VALUES (?,?,?,?,?,?)',('learner','bank','q',old,'{"stability": 45}','2026-11-01'))
        con.execute('INSERT INTO unrelated VALUES (?,?)',('keep','untouched'))
        migrate_version(con,'bank',old,new)
        self.assertEqual(con.execute('SELECT point FROM review_reservations').fetchone()[0],point_id('training','bank',new,'q'))
        row=con.execute('SELECT * FROM training_memory').fetchone()
        self.assertEqual((row['card_json'],row['due_at']),('{"stability": 45}','2026-11-01'))
        self.assertEqual(con.execute('SELECT body FROM unrelated').fetchone()[0],'untouched')

    def test_non_main_class_uses_source_method_contract(self):
        for name in ['User','Wallet']:
            body='只提交 class `'+name+'`，不要编写 main 方法。String `account`、double `money`。\n```java\nString getAccount();\nvoid deposit(double value);\n```\n```java\npublic class Example { public static void main(String[] args) {} }\n```'
            files,source=templates(body)
            self.assertEqual(source,'src/'+name+'.java')
            self.assertIn('public void deposit(double value)',files[source])
            self.assertNotIn('static void main',files[source])
            self.assertIn('src/Example.java',files)
