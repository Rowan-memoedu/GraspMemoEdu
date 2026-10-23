from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import wave

from graspmemoedu.academy_import import install
from graspmemoedu.atomic_content_revision import AtomicContentRevision, validate_compatible, verify_transition
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.question_types import demo_answer
from graspmemoedu.service import create_learning_service, DomainError
from tests.test_ordered_learning import payload
from tests.test_unified_learning import composed_topic


def source_topic():
    topic = composed_topic()
    topic.update(modules=[], training_mode='atomic_retrieval')
    topic['content_order'] = [entry for entry in topic['content_order'] if entry['kind'] == 'introduction']
    return topic


class AtomicContentRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.old = source_topic()
        self.new = deepcopy(self.old)
        self.new.update(version='revised-artifact', content_version='revised-teaching')
        for card in self.new['flashcards']:
            card['title'] = '练习'
            card['question'] += ' 请独立回答。'
            card['html'] = '<p>' + card['question'] + '</p>'
        self.other = deepcopy(self.old)
        self.other['id'] = 'unrelated'
        topics = [self.old, self.other]
        for topic in topics:
            (self.data / (topic['id'] + '.json')).write_text(json.dumps(topic), encoding='utf-8')
        self.course = {'id': 'course', 'title': 'Course', 'description': '',
                       'topics': [{'id': t['id'], 'path': t['id'] + '.json', 'prerequisites': []} for t in topics]}
        (self.data / 'catalog.json').write_text(json.dumps({'courses': [self.course]}))
        (self.data / 'subjects.json').write_text(json.dumps({'subjects': [{'id': 'math', 'course_ids': ['course']}]}))
        self.when = datetime(2026, 10, 23, tzinfo=timezone.utc)
        self.service = create_learning_service(self.old, self.data, learner_id='student', clock=lambda: self.when)
        create_learning_service(self.other, self.data, learner_id='other').state()
        self.package = self.root / 'package'
        self.package.mkdir()
        self.write_package()

    def write_package(self, *, flags=None):
        course = {**self.course, 'topics': [self.course['topics'][0]]}
        (self.package / (self.new['id'] + '.json')).write_text(json.dumps(self.new), encoding='utf-8')
        (self.package / 'catalog.json').write_text(json.dumps({'courses': [course]}))
        files = {p.relative_to(self.package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.package.rglob('*') if p.is_file() and p.name != 'manifest.json'}
        manifest = {'schema': 1, 'adapter': 'structured-atomic', 'subject_id': 'math', 'files': files,
                    'preserve_atomic_progress': True, 'atomic_previous_versions': {self.old['id']: self.old['version']},
                    **(flags or {})}
        (self.package / 'manifest.json').write_text(json.dumps(manifest))

    def db_snapshot(self, path):
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con, closing(sqlite3.connect(path)) as target:
            con.backup(target)

    def raw(self):
        with closing(self.service._connect()) as con:
            return self.service._load(con)

    def to_first_answer(self):
        for _ in range(5):
            state = self.service.state()
            if state.get('phase') == 'answer':
                return
            self.service.advance(payload(self.service))
        self.fail('The learner did not reach a card')

    def grade(self, answer=None):
        state = self.service.state()
        card = self.service.atomic.questions[state['current']['card_id']]
        request = payload(self.service, answer=demo_answer(card) if answer is None else answer)
        self.service.submit(request)
        self.service.process_submission(request['request_id'])
        return request

    def test_install_preserves_fsrs_answers_history_and_other_student(self):
        self.to_first_answer()
        self.grade('wrong')  # Creates a real FSRS state, not a hand-made row.
        self.when += timedelta(seconds=181)
        self.service.advance(payload(self.service))
        before_state = self.raw()
        before_db = self.root / 'before.sqlite3'
        self.db_snapshot(before_db)
        result = install(self.package, self.data, extend_course=True)
        self.assertTrue(result['migration']['fsrs_unchanged'])
        current = Catalog.load(Settings.load(self.data)).topic(self.old['id'])
        self.service = create_learning_service(current, self.data, learner_id='student', clock=lambda: self.when)
        after_state = self.raw()
        self.assertEqual(before_state['atomic']['points'], after_state['atomic']['points'])
        self.assertEqual(before_state['atomic']['card_uses'], after_state['atomic']['card_uses'])
        self.assertEqual(before_state['atomic']['chunk_last_seen'], after_state['atomic']['chunk_last_seen'])
        self.assertEqual(before_state['attempts'], after_state['attempts'])
        self.assertEqual(before_state['steps'], after_state['steps'])
        saved = after_state['atomic']['history'][0]
        self.assertEqual(saved['snapshot']['html'], self.old['flashcards'][0]['html'])
        self.assertEqual(saved['answer'], 'wrong')
        self.assertEqual(saved['correct'], False)
        self.assertEqual(self.service.state()['current']['title'], '练习')
        self.assertTrue(verify_transition(before_db, self.data / 'learning.sqlite3',
                                        {self.old['id']: self.old}, {self.new['id']: self.new})['learning_records_preserved'])
        self.assertEqual(install(self.package, self.data, extend_course=True)['status'], 'unchanged')
        self.grade()
        self.assertTrue(self.service.state()['current']['correct'])

    def test_unanswered_card_refresh_rejects_old_browser_action(self):
        self.to_first_answer()
        stale = payload(self.service, answer='red')
        current_id = self.service.state()['current']['card_id']
        install(self.package, self.data, extend_course=True)
        self.service = create_learning_service(self.new, self.data, learner_id='student', clock=lambda: self.when)
        self.assertEqual(self.service.state()['current']['card_id'], current_id)
        self.assertIn('请独立回答', self.service.state()['current']['html'])
        with self.assertRaises(DomainError) as caught:
            self.service.submit(stale)
        self.assertEqual(caught.exception.code, 'stale_revision')

    def test_feedback_snapshot_remains_the_answered_question(self):
        self.to_first_answer()
        self.grade()
        html = self.service.state()['current']['html']
        install(self.package, self.data, extend_course=True)
        self.service = create_learning_service(self.new, self.data, learner_id='student', clock=lambda: self.when)
        self.assertEqual(self.service.state()['current']['html'], html)
        self.assertTrue(self.service.state()['current']['correct'])

    def test_completed_learner_keeps_completion_and_original_dashboard_answers(self):
        from graspmemoedu.access import AccessStore
        from graspmemoedu.dashboard import DashboardStore
        for _ in range(40):
            state = self.service.state()
            if state['status'] == 'completed':
                break
            if state.get('phase') == 'answer':
                self.grade()
            else:
                self.when += timedelta(minutes=5)
                self.service.advance(payload(self.service))
        self.assertEqual(self.service.state()['status'], 'completed')
        completed = self.raw()['completed_at']
        memories = deepcopy(self.raw()['atomic']['points'])
        install(self.package, self.data, extend_course=True)
        catalog = Catalog.load(Settings.load(self.data))
        self.service = create_learning_service(self.new, self.data, learner_id='student', clock=lambda: self.when)
        self.assertEqual(self.service.state()['status'], 'completed')
        self.assertEqual(self.raw()['completed_at'], completed)
        self.assertEqual(self.raw()['atomic']['points'], memories)
        store = AccessStore(self.data, [{'id': t['id'], 'title': t['title']} for t in catalog.topics.values()])
        dashboard = DashboardStore(catalog, store)
        access = {'learner_id': 'student', 'role': 'account', 'topics': list(catalog.topics.values()),
                  'features': ['review_history']}
        result = dashboard.answers(access, 'lesson:' + self.old['id'])
        answers = [answer for group in result['groups'] for answer in group['answers']]
        self.assertEqual(len(answers), 3)
        self.assertTrue(all('请独立回答' not in answer['html'] for answer in answers))
        self.assertTrue(all(answer['content_version'] == self.old['version'] for answer in answers))

    def test_inflight_judgment_blocks_without_changing_progress_or_pointer(self):
        self.to_first_answer()
        self.service.submit(payload(self.service, answer='red'))
        before = self.raw()
        with self.assertRaisesRegex(ValueError, 'in-flight'):
            install(self.package, self.data, extend_course=True)
        self.assertEqual(self.raw(), before)
        self.assertFalse((self.data / 'active-content.json').exists())

    def test_active_review_paper_blocks_and_completed_paper_is_preserved(self):
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            con.execute('CREATE TABLE review_papers(id TEXT, phase TEXT, items TEXT)')
            con.execute('INSERT INTO review_papers VALUES (?,?,?)',
                        ('paper', 'answer', json.dumps([{'source': {'kind': 'atomic', 'container': self.old['id']}}])))
            con.commit()
        with self.assertRaisesRegex(ValueError, 'active review'):
            install(self.package, self.data, extend_course=True)
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            con.execute("UPDATE review_papers SET phase='feedback'")
            con.commit()
        # Retry the unchanged reviewed package after the blocking work finishes.
        # The failed prepared batch stays intact for inspection.
        failed_batches = list((self.data / 'content-batches').iterdir())
        self.assertEqual(len(failed_batches), 1)
        failed_bytes = {p.relative_to(failed_batches[0]): p.read_bytes()
                        for p in failed_batches[0].rglob('*') if p.is_file()}
        before = self.root / 'paper-before.sqlite3'
        self.db_snapshot(before)
        install(self.package, self.data, extend_course=True)
        verify_transition(before, self.data / 'learning.sqlite3', {self.old['id']: self.old}, {self.new['id']: self.new})
        self.assertEqual(len(list((self.data / 'content-batches').iterdir())), 2)
        self.assertTrue(all((failed_batches[0] / name).read_bytes() == value for name, value in failed_bytes.items()))

    def test_structural_changes_and_combined_reset_are_rejected(self):
        mutations = [lambda t: t['flashcards'].reverse(),
                     lambda t: t['flashcards'][0].update(learning_phases=['review']),
                     lambda t: t['flashcards'][0].update(point_ids=['b']),
                     lambda t: t.update(atomic_policy={'first_review_seconds': 86400}),
                     lambda t: t['flashcards'][0]['interaction'].update(grading='semantic'),
                     lambda t: t['content_order'].reverse()]
        for mutate in mutations:
            candidate = deepcopy(self.new)
            mutate(candidate)
            with self.assertRaises(ValueError):
                validate_compatible(self.old, candidate)
        self.write_package(flags={'replace_atomic_and_reset_progress': True})
        with self.assertRaisesRegex(ValueError, 'one explicit'):
            install(self.package, self.data, extend_course=True)

    def test_mismatched_previous_version_is_rejected(self):
        self.write_package(flags={'atomic_previous_versions': {self.old['id']: 'different'}})
        with self.assertRaisesRegex(ValueError, 'Pinned content changed'):
            install(self.package, self.data, extend_course=True)

    def test_nested_choice_identity_and_matching_direction_are_structural(self):
        composite = {'version':2,'type':'composite','grading':'exact',
            'controls':[{'id':'pick','type':'select','options':[{'id':'a','text':'Red'},{'id':'b','text':'Blue'}],
                         'answers':['a']}],
            'sections':[{'id':'one','html':'<p>Choose a color.</p>','control_ids':['pick'],'complete':True}]}
        old, new = deepcopy(self.old),deepcopy(self.new)
        old['flashcards'][0]['interaction']=deepcopy(composite)
        new['flashcards'][0]['interaction']=deepcopy(composite)
        new['flashcards'][0]['interaction']['controls'][0]['options'][1]['text']='Green'
        validate_compatible(old,new)  # Text is editable, identities are not.
        new['flashcards'][0]['interaction']['controls'][0]['options'][1]['id']='c'
        with self.assertRaisesRegex(ValueError,'answer controls'): validate_compatible(old,new)
        matching={'version':1,'type':'matching','fixed_side':'left',
            'left':[{'id':'a','text':'Red'},{'id':'b','text':'Blue'}],
            'right':[{'id':'x','text':'Color one'},{'id':'y','text':'Color two'}],
            'pairs':[{'left':'a','right':'x'},{'left':'b','right':'y'}]}
        old['flashcards'][0]['interaction']=deepcopy(matching)
        for changes in ({'fixed_side':'right'},{'direction':'both'}):
            with self.subTest(changes=changes):
                new['flashcards'][0]['interaction']={**deepcopy(matching),**changes}
                with self.assertRaisesRegex(ValueError,'answer controls'): validate_compatible(old,new)
        new['flashcards'][0]['interaction']={**deepcopy(matching),'direction':'forward'}
        validate_compatible(old,new)

    def test_complete_audio_object_is_locked_during_content_revision(self):
        media={'version':1,'asset':'audio/'+'0'*64+'.wav','mime_type':'audio/wav',
               'mode':'listen','transcript':'Original recording.'}
        old,new=deepcopy(self.old),deepcopy(self.new)
        old['flashcards'][0]['audio']=deepcopy(media)
        new['flashcards'][0]['audio']=deepcopy(media)
        validate_compatible(old,new)
        for changed in (None,{**media,'asset':'audio/'+'1'*64+'.wav'},
                        {**media,'mode':'dictation'},{**media,'transcript':'Changed transcript.'}):
            with self.subTest(changed=changed):
                candidate=deepcopy(new)
                if changed is None: candidate['flashcards'][0].pop('audio')
                else: candidate['flashcards'][0]['audio']=changed
                with self.assertRaisesRegex(ValueError,'complete audio source'):
                    validate_compatible(old,candidate)

    def test_completed_listening_paper_keeps_identical_playable_audio_after_revision(self):
        from graspmemoedu.dependencies import DependencyPolicy
        from graspmemoedu.review_papers import PaperService
        from graspmemoedu.training import TrainingService
        from graspmemoedu.training_content import TrainingCatalog
        from graspmemoedu.training_audio import read_audio
        from graspmemoedu.site_backup import backup_site,restore_site
        stream=io.BytesIO()
        with wave.open(stream,'wb') as writer:
            writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(16000)
            writer.writeframes(b'\x00\x00'*1600)
        audio=stream.getvalue(); asset='audio/'+hashlib.sha256(audio).hexdigest()+'.wav'
        media={'version':1,'asset':asset,'mime_type':'audio/wav','mode':'listen','transcript':'Audio fixture.'}
        for topic in (self.old,self.new):
            for card in topic['flashcards']: card['audio']=deepcopy(media)
        for folder in (self.data,self.package):
            (folder/asset).parent.mkdir(exist_ok=True); (folder/asset).write_bytes(audio)
        (self.data/(self.old['id']+'.json')).write_text(json.dumps(self.old),encoding='utf-8')
        self.write_package()
        self.service=create_learning_service(self.old,self.data,learner_id='student',clock=lambda:self.when)
        for _ in range(40):
            state=self.service.state()
            if state['status']=='completed': break
            if state.get('phase')=='answer': self.grade()
            else:
                self.when+=timedelta(minutes=5); self.service.advance(payload(self.service))
        self.assertEqual(self.service.state()['status'],'completed')
        self.when+=timedelta(hours=7)

        def make_papers(folder):
            catalog=Catalog.load(Settings.load(folder))
            access={'learner_id':'student','role':'account','topics':list(catalog.topics.values()),
                    'features':['review_history','submit_answer']}
            dependencies=DependencyPolicy(catalog,None)
            training=TrainingService(TrainingCatalog(folder),folder,'student',clock=lambda:self.when,
                                     dependencies=dependencies,access=access)
            papers=PaperService(training,catalog,dependencies,access,[],lambda tid:
                create_learning_service(catalog.topic(tid),folder,learner_id='student',clock=lambda:self.when))
            return papers,catalog

        papers,catalog=make_papers(self.data)
        choice=next(row for row in papers.inbox('math')['items'] if row['kind']=='single')
        paper_id=papers.open(choice['id'],'listening-history','math')
        state=papers.state(paper_id,'math')
        qid=state['items'][0]['content_context']['question_id']
        question=next(q for q in self.old['flashcards'] if q['id']==qid)
        papers.save(paper_id,state['revision'],[{'index':0,'answer':demo_answer(question),'elapsed_ms':1000}],'math')
        state=papers.state(paper_id,'math')
        submission=papers.submit({'paper_id':paper_id,'request_id':'listening-answer',
                                  'expected_revision':state['revision']},'math')
        papers.process_submission(submission)
        before=papers.state(paper_id,'math')
        self.assertEqual(before['phase'],'done'); self.assertTrue(before['items'][0]['result']['correct'])
        before_audio=read_audio(catalog.topic_paths[self.old['id']],question)
        self.assertIn('audio',before['items'][0])
        before_db=self.root/'listening-before.sqlite3'; self.db_snapshot(before_db)
        install(self.package,self.data,extend_course=True)
        verify_transition(before_db,self.data/'learning.sqlite3',{self.old['id']:self.old},{self.new['id']:self.new})
        papers,catalog=make_papers(self.data)
        after=papers.state(paper_id,'math')
        self.assertEqual(after['items'][0]['audio'],before['items'][0]['audio'])
        self.assertEqual(after['items'][0]['content_version'],self.old['version'])
        current=next(q for q in catalog.topic(self.old['id'])['flashcards'] if q['id']==qid)
        self.assertEqual(read_audio(catalog.topic_paths[self.old['id']],current),before_audio)
        backup_site(Settings.load(self.data),self.root/'listening.zip')
        restore_site(self.root/'listening.zip',self.root/'restored')
        restored,restored_catalog=make_papers(self.root/'restored')
        self.assertEqual(restored.state(paper_id,'math')['items'][0]['audio'],before['items'][0]['audio'])
        self.assertEqual(read_audio(restored_catalog.topic_paths[self.old['id']],current),before_audio)
        # A mismatching current endpoint must never be exposed for an old
        # snapshot, even if another importer bypassed the revision gate.
        current['audio']={**current['audio'],'transcript':'A different recording.'}
        self.assertNotIn('audio',papers.state(paper_id,'math')['items'][0])

    def test_compiler_binds_previous_versions_and_rejects_reset_before_writing(self):
        from argparse import Namespace
        from scripts.prepare_structured_atomic import prepare
        from tests.test_structured_atomic import StructuredAtomicTests
        source = self.root / 'source.json'
        source.write_text(json.dumps(StructuredAtomicTests().source()))
        pins = self.root / 'previous-versions.json'
        pins.write_text(json.dumps({'atomic-new': 'previous-version'}))
        args = Namespace(input=source, output=self.root / 'compiled', subject='math', topic_prefix='atomic-',
                         title_suffix='', course_id='course', course_title='Course', unit_id='unit',
                         unit_title='Unit', unit_parent=None, node='node', preserve_atomic_progress=pins)
        prepare(args)
        manifest = json.loads((args.output / 'manifest.json').read_bytes())
        self.assertTrue(manifest['preserve_atomic_progress'])
        self.assertEqual(manifest['atomic_previous_versions'], {'atomic-new': 'previous-version'})
        args.output = self.root / 'forbidden'
        args.reset_reading_progress = True
        with self.assertRaisesRegex(ValueError, 'no reset'):
            prepare(args)
        self.assertFalse(args.output.exists())

    def test_activation_failure_restores_pointer_and_rows_with_recovery_evidence(self):
        migration = AtomicContentRevision(self.package, self.data)
        migration.prepare(Catalog.load(Settings.load(self.data)))
        before = self.raw()
        with self.assertRaisesRegex(RuntimeError, 'activation'):
            with migration.activate(None, None):
                (self.data / 'active-content.json').write_text('candidate')
                raise RuntimeError('activation failed')
        self.assertEqual(before, self.raw())
        self.assertFalse((self.data / 'active-content.json').exists())
        archives = list((self.data / 'migration-backups' / 'atomic-content-revision').glob('*.json'))
        self.assertEqual(len(archives), 1)
        archived = json.loads(archives[0].read_bytes())
        self.assertEqual(archived['previous_topics'][self.old['id']], self.old)

    def test_transition_detects_fsrs_tampering(self):
        self.to_first_answer()
        self.grade('wrong')
        before = self.root / 'before.sqlite3'
        self.db_snapshot(before)
        install(self.package, self.data, extend_course=True)
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            raw = json.loads(con.execute('SELECT body FROM topics WHERE learner=?', ('student',)).fetchone()[0])
            raw['atomic']['points']['a']['due_at'] = '2099-01-01T00:00:00+00:00'
            con.execute('UPDATE topics SET body=? WHERE learner=?', (json.dumps(raw), 'student'))
            con.commit()
        with self.assertRaisesRegex(ValueError, 'outside its contract'):
            verify_transition(before, self.data / 'learning.sqlite3', {self.old['id']: self.old}, {self.new['id']: self.new})


if __name__ == '__main__':
    unittest.main()
