from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from graspmemoedu.atomic_topic_replacement import AtomicTopicReplacement, clear_progress, snapshot, verify_transition


class AtomicTopicReplacementTests(unittest.TestCase):
    def database(self):
        con=sqlite3.connect(':memory:')
        con.executescript('''CREATE TABLE topics(learner TEXT,topic TEXT,version TEXT,body TEXT);
            CREATE TABLE submissions(id TEXT,topic TEXT,status TEXT);
            CREATE TABLE review_papers(id TEXT,items TEXT);
            CREATE TABLE review_reservations(point TEXT,paper TEXT);
            CREATE TABLE paper_submissions(paper TEXT,status TEXT);
            CREATE TABLE review_card_uses(learner TEXT,card TEXT,count INTEGER);
            CREATE TABLE access_principals(id TEXT);''')
        con.executemany('INSERT INTO topics VALUES (?,?,?,?)',[('a','selected','v','{}'),('b','selected','v','{}'),('a','other','v','{}')])
        con.execute("INSERT INTO access_principals VALUES ('a')")
        con.executemany('INSERT INTO review_card_uses VALUES (?,?,?)',[('a','atomic:selected:v:q',2),('a','atomic:other:v:q',1)])
        con.commit()
        return con

    def test_all_learners_selected_progress_clears_other_records_survive(self):
        with closing(self.database()) as con:
            clear_progress(con,['selected'])
            rows=snapshot(con)
            self.assertEqual([r['topic'] for r in rows['topics']],['other'])
            self.assertEqual([r['card'] for r in rows['review_card_uses']],['atomic:other:v:q'])
            self.assertEqual(rows['access_principals'],[{'id':'a'}])

    def test_pending_judgment_and_mixed_paper_block_before_writes(self):
        for mixed in (False,True):
            with closing(self.database()) as con:
                if mixed:
                    items=[{'source':{'kind':'atomic','container':t}} for t in ['selected','other']]
                    con.execute('INSERT INTO review_papers VALUES (?,?)',('paper',json.dumps(items)))
                else:
                    con.execute("INSERT INTO submissions VALUES ('job','selected','running')")
                before=snapshot(con)
                with self.assertRaises(ValueError): clear_progress(con,['selected'])
                self.assertEqual(snapshot(con),before)

    def test_transition_detects_unrelated_record_loss(self):
        with tempfile.TemporaryDirectory() as temporary, closing(self.database()) as con:
            before=Path(temporary)/'before.sqlite3'; after=Path(temporary)/'after.sqlite3'
            with closing(sqlite3.connect(before)) as target: con.backup(target)
            clear_progress(con,['selected']); con.commit()
            with closing(sqlite3.connect(after)) as target: con.backup(target)
            self.assertTrue(verify_transition(before,after,{'selected'})['selected_topic_progress_reset'])
            con.execute('DELETE FROM access_principals'); con.commit()
            with closing(sqlite3.connect(after)) as target: con.backup(target)
            with self.assertRaises(ValueError): verify_transition(before,after,{'selected'})

    def test_pointer_failure_rolls_back_progress_and_retains_recovery_archive(self):
        with tempfile.TemporaryDirectory() as temporary, closing(self.database()) as source:
            root=Path(temporary)
            with closing(sqlite3.connect(root/'learning.sqlite3')) as target: source.backup(target)
            migration=AtomicTopicReplacement(root,root)
            migration.replacements={'selected'}; migration.previous={'selected':'v'}
            with self.assertRaisesRegex(RuntimeError,'activation'):
                with migration.activate(None,None):
                    (root/'active-content.json').write_text('candidate')
                    raise RuntimeError('activation failed')
            self.assertFalse((root/'active-content.json').exists())
            with closing(sqlite3.connect(root/'learning.sqlite3')) as restored:
                self.assertEqual(snapshot(source),snapshot(restored))
            self.assertEqual(len(list((root/'migration-backups/atomic-replacement').glob('*.json'))),1)

    def test_compiled_atomic_package_replaces_old_atomic_content_idempotently(self):
        self._verify_compiled_replacement()

    def test_reading_only_topic_upgrades_with_explicit_full_progress_reset(self):
        self._verify_compiled_replacement(reading=True)

    def _verify_compiled_replacement(self, reading=False):
        from argparse import Namespace
        from tests.test_structured_atomic import StructuredAtomicTests
        from tests.atomic_fixtures import atomic_topic
        from scripts.prepare_structured_atomic import prepare
        from graspmemoedu.academy_import import install
        from graspmemoedu.catalog import Catalog
        from graspmemoedu.config import Settings
        from graspmemoedu.service import create_learning_service
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); data=root/'data'; data.mkdir()
            old=atomic_topic('cs','atomic-new'); old['title']='Independent fact'
            if reading:
                old={**old,'topic_format_version':2,'topic_kind':'introduction','training_mode':'paradigm',
                     'content_version':'fixture-reading-content',
                     'modules':[],'flashcards':[], 'memory_chunks':[],
                     'introductions':[{'id':'intro','title':'Read','html':'<p>A label.</p>'}],
                     'content_order':[{'kind':'introduction','id':'intro'}]}
            (data/'old.json').write_text(json.dumps(old),encoding='utf-8')
            (data/'catalog.json').write_text(json.dumps({'courses':[{'id':'course','title':'Course','description':'',
                'topics':[{'id':'atomic-new','path':'old.json','prerequisites':[]}],
                'units':[{'id':'unit','title':'Unit','topic_ids':['atomic-new']}]}]}))
            (data/'subjects.json').write_text(json.dumps({'subjects':[{'id':'cs','course_ids':['course']}]}))
            create_learning_service(old,data,learner_id='isolated').state()
            source=root/'source.json'; source.write_text(json.dumps(StructuredAtomicTests().source()))
            prepare(Namespace(input=source,output=root/'package',subject='cs',topic_prefix='atomic-',title_suffix='',
                course_id='course',course_title='Course',unit_id='unit',unit_title='Unit',unit_parent=None,node='node',
                replace_atomic_and_reset_progress=True))
            result=install(root/'package',data,extend_course=True)
            self.assertEqual(result['migration']['removed_rows']['topics'],1)
            self.assertEqual(len(Catalog.load(Settings.load(data)).topic('atomic-new')['flashcards']),2)
            self.assertEqual(install(root/'package',data,extend_course=True)['status'],'unchanged')
