"""Independent adapter regression and review-only Flashcard behavior."""
from copy import deepcopy
from argparse import Namespace
from contextlib import closing
from pathlib import Path
import hashlib
from datetime import datetime, timezone
import json
import tempfile
import sqlite3
import unittest

from graspmemoedu.atomic_content import validate_atomic
from graspmemoedu.review_cards import eligible_presentations
from graspmemoedu.structured_atomic import normalize, SourceHTML, validate_projection
from tests.atomic_fixtures import atomic_topic
from scripts.prepare_structured_atomic import prepare
from graspmemoedu.academy_import import install
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.service import create_learning_service
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.atomic_reading_upgrade import verify_transition


class StructuredAtomicTests(unittest.TestCase):
    def source(self):
        return {'schema': 2, 'topics': [{'id': 'new', 'title':'Independent fact', 'sections': [{
            'id':'section', 'section_title':'Statement', 'status':'ai_passed', 'import_eligible':True,
            'introduction':{'html':'<p>A label.</p>', 'plain_text':'A label.'},
            'memory_chunks':[{'id':'c', 'content':'A label.', 'anchors':[{'start':0,'end':8,'quote':'A label.'}]}],
            'memory_points':[{'id':'p','chunk_id':'c','content':'A label.'}],
            'flashcards':[{'id':'q','point_ids':['p'],'question':r'Write the Java escape `\u0050`.', 'answer':'A label.'},
                          {'id':'review','point_ids':['p'],'question':'Which label?', 'reference_answer':'A label.',
                           'explanation':'A label.', 'interaction':{'version':1,'type':'text','grading':'semantic'},
                           'learning_phases':['review']}]}]}]}

    def test_review_only_cards_share_the_model_but_never_enter_initial_selection(self):
        topic = atomic_topic()
        card = deepcopy(topic['flashcards'][0])
        card.update(id='new-review', learning_phases=['review'])
        topic['flashcards'].insert(0, card)
        validate_atomic(topic)
        self.assertEqual(eligible_presentations(topic['flashcards'], {'a'}, {}, singles=True, phase='learn')[0]['id'], 'a-1')
        self.assertEqual(eligible_presentations(topic['flashcards'], {'a'}, {}, singles=True, phase='review')[0]['id'], 'new-review')
        for original in topic['flashcards']:
            if original['point_ids'] == ['a']: original['learning_phases'] = ['review']
        with self.assertRaisesRegex(ValueError, 'individual flashcard'):
            validate_atomic(topic)

    def test_phase_contract_rejects_empty_unknown_and_duplicate_phases(self):
        for phases in ([], ['learn', 'learn'], ['unknown']):
            topic = atomic_topic(); topic['flashcards'][0]['learning_phases'] = phases
            with self.assertRaisesRegex(ValueError, 'learning phases'):
                validate_atomic(topic)

    def test_source_links_are_unwrapped_and_text_offsets_preserved(self):
        html = '<p>A <a href="https://example.org">label</a> &amp; B.</p>\n'
        self.assertEqual(''.join(SourceHTML(html, {}).parts), '<p>A label &amp; B.</p>\n')
        for html in ('<script>alert(1)</script>', '<p onclick="bad()">x</p>', '<img src="https://example.org/a.png">'):
            with self.assertRaises(ValueError): SourceHTML(html, {})

    def test_adapter_preserves_parallel_point_and_card_entities(self):
        source = {'source_format':'structured-atomic-v1', 'id':'neutral', 'title':'Fact', 'subject_id':'english',
                  'sections':[{'id':'section', 'section_title':'Statement', 'status':'ai_passed', 'import_eligible':True,
                    'introduction':{'html':'<p>A label.</p>', 'plain_text':'A label.'},
                    'memory_chunks':[{'id':'c', 'content':'A label.', 'anchors':[{'start':0,'end':8,'quote':'A label.'}]}],
                    'memory_points':[{'id':'p','chunk_id':'c','content':'A label.'}],
                    'flashcards':[{'id':'q','point_ids':['p'],'question':'Recall the label.','answer':'A label.'}]}]}
        before = deepcopy(source); topic = normalize(source)
        self.assertEqual(source, before)
        self.assertEqual(topic['memory_chunks'][0]['points'], [{'id':'p','text':'A label.'}])
        self.assertEqual(topic['flashcards'][0]['reference_answer'], 'A label.')
        self.assertEqual(topic['content_order'], [{'kind':'introduction','id':'section'}])

    def test_import_and_backup_restore_preserve_existing_learning_and_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = root / 'data'; data.mkdir()
            original = atomic_topic('cs', 'existing')
            (data / 'existing.json').write_text(json.dumps(original), encoding='utf-8')
            (data / 'catalog.json').write_text(json.dumps({'courses':[{'id':'course','title':'Course','description':'',
                'topics':[{'id':'existing','path':'existing.json','prerequisites':[]}],
                'units':[{'id':'unit','title':'Unit','topic_ids':['existing']}]}]}), encoding='utf-8')
            (data / 'subjects.json').write_text(json.dumps({'subjects':[{'id':'cs','course_ids':['course']}]}), encoding='utf-8')
            backend = create_learning_service(original, data, learner_id='isolated', clock=lambda: datetime(2026, 10, 20, tzinfo=timezone.utc))
            before = backend.state()
            document = self.source()
            document['topics'][0]['sections'][0]['source_attributions'] = [
                {'text': 'Example source.', 'url': 'https://example.org/source', 'license': 'CC BY 4.0'}]
            document['topics'][0]['sections'][0]['flashcards'][1]['interaction'] = {
                'version': 2, 'type': 'composite', 'grading': 'exact',
                'controls': [{'id': 'answer', 'type': 'text', 'label': 'Label', 'answers': ['A label.']}],
                'sections': [{'id': 'part', 'html': '<p>Read this source <em>carefully</em>.</p>',
                              'control_ids': ['answer'], 'complete': True}]}
            source = root / 'source.json'; source.write_text(json.dumps(document), encoding='utf-8')
            package = root / 'package'
            prepare(Namespace(input=source, output=package, subject='cs', topic_prefix='atomic-', title_suffix='',
                              course_id='course', course_title='Course', unit_id='unit', unit_title='Unit', unit_parent=None, node='node'))
            result = install(package, data, extend_course=True)
            self.assertEqual(result['status'], 'installed')
            self.assertEqual(backend.state(), before)
            self.assertEqual(install(package, data, extend_course=True)['status'], 'unchanged')
            topic = Catalog.load(Settings.load(data)).topic('atomic-new')
            self.assertEqual(len(topic['flashcards']), 2)
            self.assertEqual(topic['introductions'][0]['source_attributions'],
                             document['topics'][0]['sections'][0]['source_attributions'])
            self.assertEqual(topic['flashcards'][1]['learning_phases'], ['review'])
            structured = json.loads((package / 'atomic-sources/atomic-new.json').read_bytes())
            self.assertEqual(topic['flashcards'][1]['interaction']['sections'][0]['html'],
                             '<p>Read this source <em>carefully</em>.</p>')
            changed = deepcopy(topic)
            changed['flashcards'][1]['interaction']['sections'][0]['html'] = '<p>Changed source.</p>'
            with self.assertRaisesRegex(ValueError, 'Structured interaction changed'):
                validate_projection(changed, structured)
            archive = root / 'site.zip'; backup_site(Settings.load(data), archive)
            restored = root / 'restored'; restore_site(archive, restored)
            self.assertEqual(Catalog.load(Settings.load(restored)).topic('atomic-new'), topic)

    def test_explicit_reading_upgrade_archives_only_the_selected_reading_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); data = root / 'data'; data.mkdir()
            existing = atomic_topic('cs', 'existing')
            reading = {'id':'atomic-new', 'title':'Independent fact', 'version':'old-reading', 'content_version':'old',
                'training_mode':'paradigm', 'topic_format_version':2, 'topic_kind':'introduction', 'modules':[],
                'introductions':[{'id':'old-intro','title':'Statement','html':'<p>Read this.</p>'}],
                'content_order':[{'kind':'introduction','id':'old-intro'}]}
            for topic in (existing, reading):
                (data / (topic['id'] + '.json')).write_text(json.dumps(topic), encoding='utf-8')
            (data / 'catalog.json').write_text(json.dumps({'courses':[{'id':'course','title':'Course','description':'',
                'topics':[{'id':t['id'],'path':t['id']+'.json','prerequisites':[]} for t in (existing,reading)],
                'units':[{'id':'unit','title':'Unit','topic_ids':['existing','atomic-new']}]}]}), encoding='utf-8')
            (data / 'subjects.json').write_text(json.dumps({'subjects':[{'id':'cs','course_ids':['course']}]}), encoding='utf-8')
            for topic in (existing, reading): create_learning_service(topic, data, learner_id='isolated').state()
            before = root / 'before.sqlite3'
            with closing(sqlite3.connect(data / 'learning.sqlite3')) as con, closing(sqlite3.connect(before)) as target: con.backup(target)
            source = root / 'source.json'; source.write_text(json.dumps(self.source()), encoding='utf-8')
            package = root / 'package'
            prepare(Namespace(input=source, output=package, subject='cs', topic_prefix='atomic-', title_suffix='',
                course_id='course', course_title='Course', unit_id='unit', unit_title='Unit', unit_parent=None,
                node='node', reset_reading_progress=True))
            result = install(package, data, extend_course=True)
            self.assertEqual(result['migration'], {'cleared_reading_rows':1})
            self.assertEqual(Catalog.load(Settings.load(data)).courses['course']['units'][0]['topic_ids'],
                             ['existing', 'atomic-new'])
            self.assertEqual(verify_transition(before, data / 'learning.sqlite3', {'atomic-new'})['cleared_reading_rows'], 1)
            journals = list((data / 'migration-backups/atomic-reading').glob('*.json'))
            self.assertEqual(len(json.loads(journals[0].read_bytes())['rows']), 1)
            self.assertEqual(install(package, data, extend_course=True)['status'], 'unchanged')


if __name__ == '__main__': unittest.main()
