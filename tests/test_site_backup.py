"""Portable site backups using synthetic courses and guest-only private fixtures."""

from contextlib import closing
from datetime import datetime
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import uuid
import zipfile
from zoneinfo import ZoneInfo

from math_learning.access import AccessStore
from math_learning.catalog import Catalog
from math_learning.cli import backup_bundle, restore_bundle
from math_learning.config import Settings
from math_learning.dashboard import DashboardStore
from math_learning.guide import GuideReader
from math_learning.service import DomainError, LearningService
from math_learning.site_backup import backup_site, restore_site
from math_learning.public_limits import DailyJudgeQuota, judge_usage_report
from tests.fixtures import DeterministicJudge, synthetic_course, temporary_test_directory
from scripts.verify_content import collect_questions, parse_outline, sha256


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class SiteBackupAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temporary_test_directory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / 'active'
        self.external = self.root / 'author'
        self.external.mkdir()
        self.topics = {}
        self.paths = {}
        for topic_id in ('portable-a', 'portable-b'):
            source_bytes = ('# Synthetic source ' + topic_id).encode()
            topic = synthetic_course(1)
            topic.update(id=topic_id, title='Fixture ' + topic_id,
                         source_sha256=hashlib.sha256(source_bytes).hexdigest())
            source = self.external / (topic_id + '.md')
            source.write_bytes(source_bytes)
            course_path = self.external / (topic_id + '.json')
            write_json(course_path, topic)
            answers_path = self.external / (topic_id + '.answers.json')
            write_json(answers_path, {'source_sha256': topic['source_sha256'], 'answers': {
                question['id']: {key: question[key] for key in ('reference_answer', 'answer_type', 'constraints')}
                for module in topic['modules'] for question in [module['example'], *module['practices']]}})
            if topic_id == 'portable-a':
                write_json(self.external / (topic_id + '.sources.json'), {
                    'source_sha256': topic['source_sha256'], 'source_path': str(source), 'answers_path': str(answers_path)})
            # The second topic deliberately has an answers-only companion.
            self.topics[topic_id], self.paths[topic_id] = topic, course_path
        self.catalog_data = {'courses': [
            {'id': 'course-a', 'title': 'Course A', 'description': 'A real fixture',
             'topics': [{'id': 'portable-a', 'path': str(self.paths['portable-a'])}]},
            {'id': 'course-b', 'title': 'Course B', 'description': '',
             'topics': [{'id': 'portable-b', 'path': str(self.paths['portable-b'])}]}]}
        write_json(self.data / 'catalog.json', self.catalog_data)
        self.settings = Settings(data_dir=self.data)
        self.settings.guide_path.parent.mkdir(parents=True)
        self.settings.guide_path.write_text(
            '# Fixture Guide\n![plot](assets/plot.png)\n[Notes](assets/notes.txt)\n![missing](assets/missing.png)', encoding='utf-8')
        assets = self.settings.guide_path.parent / 'assets'
        assets.mkdir()
        (assets / 'plot.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        (assets / 'notes.txt').write_text('Portable fixture notes', encoding='utf-8')
        (assets / 'unreferenced.txt').write_text('Must not be included', encoding='utf-8')
        self.judge = DeterministicJudge()
        LearningService(self.topics['portable-a'], self.data, self.judge)
        self.catalog = Catalog.load(self.settings)
        self.access = AccessStore(self.data, [{'id': item['id'], 'title': item['title']} for item in self.topics.values()])
        self.guest = self.access.create_guest()
        self.other = self.access.create_guest()
        self.dashboard = DashboardStore(self.catalog, self.access)
        self.dashboard.select_course(self.guest['access'], 'course-b')
        self.dashboard.update_profile(self.guest['access'], {'display_name': 'Portable guest', 'timezone': 'Asia/Shanghai'})
        self.dashboard.save_feedback(self.guest['access'], {'message': 'Keep this local feedback', 'course_id': 'course-a'})
        self.learner = LearningService(self.topics['portable-a'], self.data, self.judge, self.guest['access']['learner_id'])
        self._complete(self.learner)
        self.pending = LearningService(self.topics['portable-b'], self.data, self.judge, self.other['access']['learner_id'])
        self.pending.choose(self._payload(self.pending, choice='mastered'))
        self.pending_id = str(uuid.uuid4())
        self.pending.submit(self._payload(self.pending, request_id=self.pending_id, answer='correct'))

    def _payload(self, service, **extra):
        state = service.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def _complete(self, service):
        service.choose(self._payload(service, choice='mastered'))
        submitted = service.submit(self._payload(service, answer='correct', elapsed_ms=700))
        service.process_submission(submitted['submission_id'])
        service.advance(self._payload(service))

    def _backup(self):
        path = self.root / ('backup-' + uuid.uuid4().hex + '.zip')
        result = backup_bundle(self.settings, path)
        self.assertEqual(result['format'], 2)
        return path

    def test_backup_restores_daily_judge_usage_without_changing_learning_data(self):
        learner_id = self.guest['access']['learner_id']
        quota = DailyJudgeQuota(self.data)
        with quota.reserve(learner_id, 'guest'):
            pass
        archive = self._backup()
        with zipfile.ZipFile(archive) as saved:
            self.assertIn('judge-daily-quota.sqlite3', saved.namelist())
        destination = self.root / 'quota-restored'
        restore_site(archive, destination)
        day = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        report = judge_usage_report(destination, day=day, learner_id=learner_id)
        self.assertEqual(report['global_used'], 1)
        self.assertEqual(report['identities'][0]['used'], 1)
        corrupted = self._edited_archive(archive, lambda manifest, blobs:
                                         blobs.__setitem__('judge-daily-quota.sqlite3', b'not a database'))
        self._assert_invalid_without_target(corrupted)

    def _edited_archive(self, original, mutation):
        with zipfile.ZipFile(original) as archive:
            blobs = {name: archive.read(name) for name in archive.namelist() if name != 'manifest.json'}
            manifest = json.loads(archive.read('manifest.json'))
        mutation(manifest, blobs)
        manifest['sha256'] = {name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()}
        result = self.root / ('edited-' + uuid.uuid4().hex + '.zip')
        with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in blobs.items():
                archive.writestr(name, data)
            archive.writestr('manifest.json', json.dumps(manifest))
        return result

    def _assert_invalid_without_target(self, backup):
        destination = self.root / ('rejected-' + uuid.uuid4().hex)
        with self.assertRaises(DomainError) as caught:
            restore_site(backup, destination)
        self.assertEqual(caught.exception.code, 'invalid_backup')
        self.assertFalse(destination.exists(), 'Invalid backup must not leave a partially restored destination')

    def test_retired_progress_round_trip_without_republishing(self):
        retired = synthetic_course(1)
        retired['id'] = 'retired-fixture'
        learner = LearningService(retired, self.data, self.judge, self.guest['access']['learner_id'])
        learner.choose(self._payload(learner, choice='mastered'))
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            before = con.execute('SELECT * FROM topics WHERE topic=?', (retired['id'],)).fetchall()
        archive = self._backup()
        destination = self.root / 'retired-restored'
        restore_site(archive, destination)
        self.assertNotIn(retired['id'], Catalog.load(Settings.load(destination)).topics)
        with closing(sqlite3.connect(destination / 'learning.sqlite3')) as con:
            self.assertEqual(before, con.execute('SELECT * FROM topics WHERE topic=?', (retired['id'],)).fetchall())
        second = backup_site(Settings.load(destination), self.root / 'second-retired.zip')
        restore_site(Path(second['backup']), self.root / 'second-retired-restored')
        for replacement in ({}, {retired['id']: ['wrong-version']}, {'portable-a': [retired['version']]}):
            with self.subTest(replacement=replacement):
                altered = self._edited_archive(archive, lambda manifest, blobs: manifest.update(retired_topic_versions=replacement))
                self._assert_invalid_without_target(altered)

    def test_active_version_mismatch_still_blocks_backup(self):
        with closing(sqlite3.connect(self.data / 'learning.sqlite3')) as con:
            con.execute("UPDATE topics SET version='different' WHERE topic='portable-a'")
            con.commit()
        with self.assertRaises(DomainError) as caught:
            self._backup()
        self.assertEqual(caught.exception.code, 'backup_version_changed')

    def test_format2_cli_round_trip_migrates_all_sources_and_shared_guest_data(self):
        archive = self._backup()
        with zipfile.ZipFile(archive) as saved:
            names = set(saved.namelist())
            self.assertIn('guide/assets/plot.png', names)
            self.assertIn('guide/assets/notes.txt', names)
            self.assertNotIn('guide/assets/unreferenced.txt', names)
            self.assertNotIn('guide/assets/missing.png', names)
        self.external.rename(self.root / 'old-author-unavailable')
        destination = self.root / 'restored'
        result = restore_bundle(archive, destination)
        self.assertEqual((result['format'], result['courses'], result['topics']), (2, 2, 2))
        settings = Settings.load(destination)
        catalog = Catalog.load(settings)
        for path in catalog.topic_paths.values():
            self.assertTrue(path.is_relative_to(destination))
        store = AccessStore(destination, [{'id': item['id'], 'title': item['title']} for item in catalog.topics.values()])
        access = store.resolve(self.guest['identity_token'])
        dashboard = DashboardStore(catalog, store)
        self.assertEqual(dashboard.profile(access), {'display_name': 'Portable guest', 'timezone': 'Asia/Shanghai', 'selected_course_id': 'course-b'})
        history = dashboard.dashboard(access, 'course-a')['history']
        self.assertEqual([item['id'] for item in history], ['lesson:portable-a'])
        answer = dashboard.answers(access, 'lesson:portable-a')['groups'][0]['answers'][0]
        self.assertEqual(answer['elapsed_seconds'], .7)
        self.assertTrue(answer['correct'])
        other = store.resolve(self.other['identity_token'])
        pending = LearningService(catalog.topic('portable-b'), destination, learner_id=other['learner_id'])
        self.assertEqual(pending.submission(self.pending_id)['status'], 'error')
        self.assertIsNone(pending.state()['pending_submission_id'])
        with closing(sqlite3.connect(destination / 'learning.sqlite3')) as con:
            self.assertEqual(con.execute('SELECT message FROM feedback').fetchone()[0], 'Keep this local feedback')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM access_principals WHERE role="account"').fetchone()[0], 0)
        index = json.loads((destination / 'courses/portable-a.sources.json').read_text(encoding='utf-8'))
        self.assertTrue(Path(index['source_path']).is_relative_to(destination))
        self.assertTrue(Path(index['answers_path']).is_relative_to(destination))
        self.assertEqual(GuideReader(settings.guide_path).read('https://fixture.invalid')['status'], 'ready')
        self.assertEqual((destination / 'guide/assets/notes.txt').read_text(), 'Portable fixture notes')

    def test_answers_only_index_remains_portable_across_second_backup(self):
        destination = self.root / 'first-restore'
        restore_site(self._backup(), destination)
        settings = Settings.load(destination)
        index = json.loads((destination / 'courses/portable-b.sources.json').read_text(encoding='utf-8'))
        self.assertNotIn('source_path', index)
        second_archive = self.root / 'second-backup.zip'
        backup_site(settings, second_archive)
        second_destination = self.root / 'second-restore'
        restore_site(second_archive, second_destination)
        second_index = json.loads((second_destination / 'courses/portable-b.sources.json').read_text(encoding='utf-8'))
        self.assertTrue(Path(second_index['answers_path']).is_relative_to(second_destination))
        self.assertEqual(Catalog.load(Settings.load(second_destination)).topics, self.catalog.topics)

    def _attach_review_evidence(self, with_image=False):
        # Add a separate source-backed topic; never reinterpret existing progress.
        topic = synthetic_course(1)
        topic.update(id='reviewed-topic', title='Reviewed fixture')
        module = topic['modules'][0]
        module['id'] = 'example-1'
        module['example']['id'] = 'example-1-question'
        for number, q in enumerate(module['practices'], 1):
            q['id'] = f'example-1-practice-{number}'
        lines = ['- Reviewed fixture', '  - Introduction', '    - Synthetic prose.', '  - Example 1: Question',
                 '    - State a fact.', '    - Explanation', '      - A synthetic explanation.']
        assets = []
        if with_image:
            image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC')
            image_path = self.external / 'assets' / 'diagram.png'
            image_path.parent.mkdir(exist_ok=True)
            image_path.write_bytes(image)
            assets = [{'path': 'assets/diagram.png', 'sha256': sha256(image), 'size': len(image), 'mime': 'image/png'}]
            lines.insert(3, '    - ![diagram](assets/diagram.png)')
        module['example']['question'] = 'State a fact.'
        for number, q in enumerate(module['practices'], 1):
            q['question'] = f'State fact {number}.'
            lines += [f'    - Practice {number}', f'      - {q["question"]}', '      - Explanation', '        - Synthetic explanation.']
        source = self.external / 'reviewed-topic.md'
        source.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        topic['source_sha256'] = sha256(source.read_bytes())
        questions = collect_questions(parse_outline(source.read_text()))
        topic_path = self.external / 'reviewed-topic.json'
        write_json(topic_path, topic)
        answers_path = self.external / 'reviewed-topic.answers.json'
        write_json(answers_path, {'source_sha256': topic['source_sha256'], 'answers': {
            q['id']: {**{key: q[key] for key in ('reference_answer', 'answer_type', 'constraints')},
                      'question_sha256': sha256(q['question'])} for q in [module['example'], *module['practices']]}})
        review_path = self.external / 'reviewed-topic.review.json'
        write_json(review_path, {'schema_version': 1, 'status': 'approved', 'source_sha256': topic['source_sha256'],
                               'sidecar_sha256': sha256(answers_path.read_bytes()), 'model': 'fixture', 'assets': assets,
                               'questions': [{'id': q['id'], 'question_sha256': sha256(q['question']),
                                              'approved': True, 'issues': []} for q in questions]})
        write_json(self.external / 'reviewed-topic.sources.json', {
            'source_path': str(source), 'answers_path': str(answers_path),
            'review_path': str(review_path), 'review_sha256': sha256(review_path.read_bytes()), 'assets': assets})
        self.catalog_data['courses'][0]['topics'].append({'id': 'reviewed-topic', 'path': str(topic_path)})
        write_json(self.data / 'catalog.json', self.catalog_data)
        return review_path

    def test_topic_attachments_survive_backup_restore_and_second_backup(self):
        self._attach_review_evidence(with_image=True)
        archive = self._backup()
        destination = self.root / 'with-assets'
        restore_site(archive, destination)
        index = json.loads((destination / 'courses/reviewed-topic.sources.json').read_text())
        self.assertEqual(Path(index['source_path']), destination / 'sources/reviewed-topic/source.md')
        self.assertEqual((Path(index['source_path']).parent / 'assets/diagram.png').read_bytes(),
                         (self.external / 'assets/diagram.png').read_bytes())
        self.assertEqual(index['assets'][0]['sha256'], sha256((self.external / 'assets/diagram.png').read_bytes()))
        second = self.root / 'assets-second.zip'
        backup_site(Settings.load(destination), second)
        with zipfile.ZipFile(second) as saved:
            self.assertEqual(saved.read('sources/reviewed-topic/assets/diagram.png'), (self.external / 'assets/diagram.png').read_bytes())

    def test_changed_missing_unbound_or_escaping_topic_assets_rejected(self):
        self._attach_review_evidence(with_image=True)
        archive = self._backup()
        def changed(manifest, blobs):
            blobs['sources/reviewed-topic/assets/diagram.png'] += b'changed'
        def missing(manifest, blobs):
            del blobs['sources/reviewed-topic/assets/diagram.png']
        def unbound(manifest, blobs):
            review = json.loads(blobs['courses/reviewed-topic.review.json'])
            review['assets'] = []
            blobs['courses/reviewed-topic.review.json'] = json.dumps(review).encode()
        def escaping(manifest, blobs):
            manifest['sources']['reviewed-topic']['assets'][0]['path'] = '../escape.png'
        for mutation in (changed, missing, unbound, escaping):
            with self.subTest(mutation=mutation):
                self._assert_invalid_without_target(self._edited_archive(archive, mutation))

    def test_review_evidence_survives_backup_restore_and_second_backup(self):
        review = self._attach_review_evidence().read_bytes()
        archive = self._backup()
        destination = self.root / 'with-review'
        restore_site(archive, destination)
        index = json.loads((destination / 'courses/reviewed-topic.sources.json').read_text())
        self.assertEqual(Path(index['review_path']).read_bytes(), review)
        self.assertEqual(index['review_sha256'], sha256(review))
        second = self.root / 'review-second.zip'
        backup_site(Settings.load(destination), second)
        with zipfile.ZipFile(second) as saved:
            self.assertEqual(saved.read('courses/reviewed-topic.review.json'), review)

    def test_review_tampering_and_noncanonical_paths_rejected_before_restore(self):
        self._attach_review_evidence()
        archive = self._backup()
        def change_record(manifest, blobs):
            review = json.loads(blobs['courses/reviewed-topic.review.json'])
            review['questions'][0]['approved'] = False
            blobs['courses/reviewed-topic.review.json'] = json.dumps(review).encode()
        def change_answers_bytes(manifest, blobs):
            blobs['courses/reviewed-topic.answers.json'] += b'\n'
        def change_path(manifest, blobs):
            manifest['sources']['reviewed-topic']['review'] = 'courses/portable-a.json'
        for mutation in (change_record, change_answers_bytes, change_path):
            with self.subTest(mutation=mutation):
                self._assert_invalid_without_target(self._edited_archive(archive, mutation))

    def test_referenced_in_directory_symlink_keeps_markdown_path(self):
        assets = self.settings.guide_path.parent / 'assets'
        try:
            (assets / 'alias.png').symlink_to(assets / 'plot.png')
        except OSError as exc:
            self.skipTest(f'File symlinks unavailable: {exc}')
        self.settings.guide_path.write_text('![alias](assets/alias.png)', encoding='utf-8')
        destination = self.root / 'symlink-restore'
        restore_site(self._backup(), destination)
        reader = GuideReader(destination / 'guide/guide.md')
        self.assertEqual(reader.asset('assets/alias.png').read_bytes(), (assets / 'plot.png').read_bytes())

    def test_invalid_source_manifest_is_rejected_before_destination_creation(self):
        original = self._backup()
        edited = self._edited_archive(original, lambda manifest, blobs: manifest['sources']['portable-a'].update(source='../escape.md'))
        self._assert_invalid_without_target(edited)

    def test_invalid_catalog_and_database_leave_no_partial_destination(self):
        original = self._backup()
        def broken_catalog(manifest, blobs):
            data = json.loads(blobs['catalog.json'])
            data['courses'][1]['id'] = data['courses'][0]['id']
            blobs['catalog.json'] = json.dumps(data).encode()
        for mutation in (broken_catalog, lambda manifest, blobs: blobs.update({'learning.sqlite3': b'not a database'})):
            with self.subTest(mutation=mutation):
                self._assert_invalid_without_target(self._edited_archive(original, mutation))

    def test_archive_paths_and_source_membership_are_validated_before_writes(self):
        original = self._backup()
        cases = ['guide/../escape.txt', 'guide//same.txt', 'guide/CON.txt', 'guide/bad?.txt',
                 'guide/evil.py', 'courses/orphan.json', 'guide/a.txt/inside.txt']
        for name in cases:
            def mutation(manifest, blobs):
                blobs[name] = b'fixture'
                if name == 'guide/a.txt/inside.txt':
                    blobs['guide/a.txt'] = b'parent is a file'
            with self.subTest(path=name):
                self._assert_invalid_without_target(self._edited_archive(original, mutation))

    def test_declared_source_hash_and_answer_content_are_validated_on_restore(self):
        original = self._backup()
        for filename in ('sources/portable-a.md', 'courses/portable-a.answers.json'):
            def mutation(manifest, blobs):
                if filename.endswith('.md'):
                    blobs[filename] = b'tampered author source'
                else:
                    answers = json.loads(blobs[filename])
                    answers['answers']['module-1-example']['reference_answer'] = 'tampered'
                    blobs[filename] = json.dumps(answers).encode()
            with self.subTest(filename=filename):
                self._assert_invalid_without_target(self._edited_archive(original, mutation))

    def test_manifest_topic_versions_must_cover_exactly_the_catalog(self):
        original = self._backup()
        def mutation(manifest, blobs):
            manifest['topic_versions']['unlisted-topic'] = 'unused'
        self._assert_invalid_without_target(self._edited_archive(original, mutation))

    def test_guide_render_failure_cannot_silently_create_incomplete_backup(self):
        destination = self.root / 'should-not-exist.zip'
        with patch.object(GuideReader, '_compile', side_effect=ValueError('Fixture renderer unavailable')):
            with self.assertRaises(DomainError) as caught:
                backup_site(self.settings, destination)
        self.assertEqual(caught.exception.code, 'guide_unavailable')
        self.assertFalse(destination.exists())

    def test_existing_restore_target_and_backup_path_are_never_overwritten(self):
        archive = self._backup()
        original_bytes = archive.read_bytes()
        with self.assertRaises(DomainError):
            backup_site(self.settings, archive)
        self.assertEqual(archive.read_bytes(), original_bytes)
        destination = self.root / 'existing'
        destination.mkdir()
        marker = destination / 'marker.txt'
        marker.write_text('keep')
        with self.assertRaises(DomainError) as caught:
            restore_site(archive, destination)
        self.assertEqual(caught.exception.code, 'restore_exists')
        self.assertEqual(marker.read_text(), 'keep')

    def test_old_format1_cli_backup_and_restore_remain_usable(self):
        legacy = self.root / 'legacy'
        topic_path = legacy / 'courses/digits-to-letters.json'
        write_json(topic_path, self.topics['portable-a'])
        settings = Settings(data_dir=legacy)
        service = LearningService(self.topics['portable-a'], legacy, self.judge)
        self._complete(service)
        legacy_access = AccessStore(legacy, [{'id': 'portable-a', 'title': 'Legacy fixture'}])
        guest = legacy_access.create_guest()
        archive = self.root / 'format1.zip'
        backup_bundle(settings, archive)
        with zipfile.ZipFile(archive) as saved:
            self.assertEqual(json.loads(saved.read('manifest.json'))['format'], 1)
        restored = self.root / 'legacy-restored'
        result = restore_bundle(archive, restored)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['topic_id'], 'portable-a')
        access = AccessStore(restored, [{'id': 'portable-a', 'title': 'Legacy fixture'}])
        self.assertEqual(access.resolve(guest['identity_token'])['learner_id'], guest['access']['learner_id'])
        self.assertEqual(Catalog.load(Settings.load(restored)).default_topic_id, 'portable-a')


if __name__ == '__main__':
    unittest.main()
