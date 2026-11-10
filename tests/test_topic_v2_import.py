"""Real, isolated author/compiler/install checks for both Topic v2 inputs."""
import base64
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing
import unittest
import uuid
import zipfile
from unittest.mock import patch

from graspmemoedu.author_import import prepare_topic, resume_topic
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.topic_format import dump_json, dump_markdown, normalize_document
from graspmemoedu.topic_install import install_topic, read_package
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.service import create_learning_service
from tests.fixtures import temporary_test_directory, synthetic_course
from tests.topic_format_fixtures import introduction_document, lesson_document, node


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class TopicV2ImportTests(unittest.TestCase):
    def test_original_heading_and_run_in_labels_render_without_content_revision(self):
        value=introduction_document();value['nodes'][0]['body_layout']='prose'
        value['nodes'][0]['children']=[node('h','Independent heading'),node('p','Theorem 3.1. Original $x=1$ text.')]
        _,before=self.prepare(value)
        value['nodes'][0]['children'][0]['presentation']={'role':'heading','level':3}
        value['nodes'][0]['children'][1]['presentation']={'role':'run-in','lead':'Theorem 3.1.','indent':0}
        _,after=self.prepare(value)
        self.assertEqual(before['content_version'],after['content_version'])
        self.assertIn('<h3>Independent heading</h3>',after['introductions'][0]['html'])
        self.assertIn('<strong class="sourceLead">Theorem 3.1.</strong>',after['introductions'][0]['html'])
        self.assertIn('data-body-layout="prose"',after['introductions'][0]['html'])

    def test_reading_prose_uses_paragraphs_and_keeps_real_nested_lists(self):
        value = introduction_document()
        value['nodes'][0]['body_layout'] = 'prose'
        value['nodes'][0]['children'] = [node('paragraph','Original paragraph',[
            node('nested','Original nested item')])]
        _, topic = self.prepare(value)
        html = topic['introductions'][0]['html']
        self.assertRegex(html, r'<div class="outlineNode"[^>]*><div id="node-paragraph"')
        self.assertIn('<ul><li class="outlineNode"', html)
        self.assertIn('Original paragraph', html)
        self.assertIn('Original nested item', html)

    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def prepare(self, document, extension='json', with_image=False):
        self.counter += 1
        folder = self.root / str(self.counter)
        folder.mkdir()
        document = copy.deepcopy(document)
        if with_image:
            (folder / 'assets').mkdir()
            (folder / 'assets/diagram.png').write_bytes(base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC'))
            document['nodes'][0]['children'].append(node('asset-image', '![附件](assets/diagram.png)'))
        normalized, _ = normalize_document(document)
        source = folder / ('original.' + extension)
        source.write_text((dump_json if extension == 'json' else dump_markdown)(normalized), encoding='utf-8')
        before = source.read_bytes()
        with patch('graspmemoedu.author_import.make_client', side_effect=AssertionError('No model call permitted')):
            manifest = prepare_topic(source, document['id'], folder / 'package')
        self.assertEqual(manifest['status'], 'approved', manifest)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual((folder / 'package' / manifest['artifacts']['source']).read_bytes(), before)
        _, _, topic = read_package(folder / 'package')
        return folder / 'package', topic

    def settings(self):
        data = self.root / 'site'
        data.mkdir()
        existing = synthetic_course(1)
        write_json(data / 'existing.json', existing)
        write_json(data / 'catalog.json', {'courses': [{'id': 'sample-course', 'title': 'Synthetic',
            'topics': [{'id': existing['id'], 'path': 'existing.json'}]}]})
        write_json(data / 'subjects.json', {'subjects': [{'id': 'math', 'course_ids': ['sample-course']}]})
        with closing(sqlite3.connect(data / 'learning.sqlite3')) as con:
            con.execute('CREATE TABLE retained_progress (learner TEXT, completed TEXT)')
            con.execute("INSERT INTO retained_progress VALUES ('synthetic', 'original-progress')")
            con.commit()
        return Settings(data_dir=data)

    def install(self, settings, package):
        return install_topic(settings, package, subject_id='math', course_id='sample-course')

    def test_json_and_markdown_real_compile_preserve_semantics_assets_and_interleaving(self):
        packages = [self.prepare(lesson_document(), extension, with_image=True) for extension in ('json', 'md')]
        one, two = [topic for _, topic in packages]
        self.assertEqual(one['content_version'], two['content_version'])
        self.assertNotEqual(one['source_sha256'], two['source_sha256'])
        self.assertEqual(one['content_order'], two['content_order'])
        self.assertEqual(one['content_nodes'], two['content_nodes'])
        self.assertEqual(one['references'], two['references'])
        self.assertEqual([i['kind'] for i in one['content_order']], ['introduction', 'example', 'introduction'])
        self.assertEqual(len(one['modules'][0]['practices']), 7)
        html = one['introductions'][0]['html']
        self.assertEqual(one['introductions'][0]['title'], '引论: 第一节')
        for marker in ('data-callout="hint"', 'data-callout="source"', 'data-callout="aside"', 'data-callout="requirement"', 'local-mermaid', 'data-target-rem-id="LaterDocument"', 'data:image/png;base64,', 'id="node-definition"'):
            self.assertIn(marker, html)
        for absent in ('★', '信息缺失', '待补充'):
            self.assertNotIn(absent, html)
        self.assertIn('<strong>idea</strong>', one['modules'][0]['example']['explanation_html'])
        self.assertNotIn('href=', one['modules'][0]['example']['explanation_html'])
        settings = self.settings()
        self.assertEqual(self.install(settings, packages[0][0])['status'], 'installed')
        self.assertEqual(Catalog.load(settings).topic(one['id'])['content_version'], one['content_version'])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.install(settings, packages[1][0])

    def test_introduction_updates_keep_old_package_catalog_position_and_database(self):
        original = introduction_document()
        package, before_topic = self.prepare(original)
        settings = self.settings()
        database = settings.data_dir / 'learning.sqlite3'
        before_db = database.read_bytes()
        first = self.install(settings, package)
        before_artifact = Path(first['topic_path']).read_bytes()
        self.assertEqual(self.install(settings, package)['status'], 'already_installed')
        revised = copy.deepcopy(original)
        revised['nodes'][0]['children'][0]['text'] += ' 内容更新。'
        updated_package, after_topic = self.prepare(revised)
        result = self.install(settings, updated_package)
        self.assertEqual(result['status'], 'updated')
        self.assertTrue(result['content_changed'])
        self.assertNotEqual(before_topic['content_version'], after_topic['content_version'])
        self.assertEqual(Path(first['topic_path']).read_bytes(), before_artifact)
        command = subprocess.run([sys.executable, '-m', 'graspmemoedu', '--data-dir', str(settings.data_dir),
            'author-import', '--source', str(updated_package / 'source.json'), '--topic-id', original['id'],
            '--subject', 'math', '--course', 'sample-course', '--run-dir', str(self.root / 'cli-update')],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', timeout=180)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertEqual(json.loads(command.stdout)['status'], 'already_installed')
        self.assertEqual(database.read_bytes(), before_db)
        self.assertNotEqual(first['topic_path'], result['topic_path'])
        self.assertEqual(database.read_bytes(), before_db)
        self.assertEqual(len(Catalog.load(settings).courses['sample-course']['topics']), 2)
        provenance = copy.deepcopy(revised)
        provenance['nodes'][0]['children'][2]['source']['difficulty'] = '★★★'
        metadata_package, _ = self.prepare(provenance)
        self.assertFalse(self.install(settings, metadata_package)['content_changed'])
        self.assertEqual(database.read_bytes(), before_db)
        manifest = resume_topic(metadata_package, self.root / 'resumed')
        self.assertEqual(manifest['status'], 'approved', manifest)
        self.assertEqual(manifest['artifacts']['source'], 'source.json')
        catalog_before_rollback = settings.catalog_path.read_bytes()
        rollback = self.install(settings, package)
        self.assertEqual(rollback['status'], 'updated')
        self.assertEqual(Path(rollback['catalog_backup']).read_bytes(), catalog_before_rollback)
        self.assertEqual(database.read_bytes(), before_db)
        self.assertEqual(Path(first['topic_path']).read_bytes(), before_artifact)
        reupdate = subprocess.run([*command.args[:-1], str(self.root / 'cli-reupdate')],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', timeout=180)
        self.assertEqual(reupdate.returncode, 0, reupdate.stderr)
        self.assertEqual(json.loads(reupdate.stdout)['status'], 'updated')
        self.assertEqual(database.read_bytes(), before_db)

    def test_introduction_cannot_occupy_a_predeclared_graph_node(self):
        package, topic = self.prepare(introduction_document())
        settings = self.settings()
        graph_dir = settings.data_dir / 'graphs'
        graph_dir.mkdir()
        write_json(graph_dir / 'sample-course.json', {'schema_version': 1, 'subject_id': 'math', 'course_id': 'sample-course',
            'title': 'Synthetic graph', 'nodes': [{'topic_id': topic['id'], 'title': 'Reserved'}], 'edges': []})
        before = (settings.data_dir / 'catalog.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'knowledge graph'):
            self.install(settings, package)
        self.assertEqual((settings.data_dir / 'catalog.json').read_bytes(), before)

    def test_json_backup_restore_keeps_completed_old_version_before_first_reopen(self):
        document = introduction_document()
        package, original = self.prepare(document, with_image=True)
        settings = self.settings()
        # The other Topic is deliberately source-less, as supported by existing backups.
        self.install(settings, package)
        service = create_learning_service(original, settings.data_dir, learner_id='fixture-reader')
        while service.state()['status'] != 'completed':
            state = service.state()
            service.advance({'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                             'attempt_id': state['attempt_id'], 'step_id': state['active_step_id']})
        completed = service.state()['completed_at']
        document['nodes'][1]['children'][0]['text'] += ' 新内容。'
        updated_package, updated = self.prepare(document, with_image=True)
        self.install(settings, updated_package)
        before_db = (settings.data_dir / 'learning.sqlite3').read_bytes()
        archive = self.root / 'portable.zip'
        backup_site(settings, archive)
        self.assertEqual((settings.data_dir / 'learning.sqlite3').read_bytes(), before_db)
        with zipfile.ZipFile(archive) as saved:
            manifest = json.loads(saved.read('manifest.json'))
            self.assertEqual(manifest['introduction_previous_versions'], {original['id']: [original['version']]})
            self.assertIn(f"sources/{original['id']}/source.json", saved.namelist())
            self.assertIn(f"sources/{original['id']}/assets/diagram.png", saved.namelist())
        destination = self.root / 'restored'
        restore_site(archive, destination)
        restored = create_learning_service(updated, destination, learner_id='fixture-reader')
        state = restored.state()
        self.assertEqual(state['completed_at'], completed)
        self.assertTrue(state['reread_pending'])
        self.assertEqual(state['status'], 'completed')
        with closing(restored._connect()) as con:
            body = json.loads(con.execute('SELECT body FROM topics WHERE learner=? AND topic=?', restored.key).fetchone()[0])
            self.assertEqual(body['content_history'][0]['content_version'], original['content_version'])
        backup_site(Settings(data_dir=destination), self.root / 'restored-again.zip')

    def test_whole_course_install_uses_immutable_introduction_package_paths(self):
        from scripts.install_author_course import install_course
        package, topic = self.prepare(introduction_document())
        settings = self.settings()
        result = install_course(settings, {'course_id': 'new-course', 'subject_id': 'math', 'title': 'Synthetic new course',
            'topics': [{'topic_id': topic['id'], 'package': str(package)}]})
        self.assertEqual(result['status'], 'installed')
        loaded = Catalog.load(settings)
        self.assertEqual(loaded.topic_courses[topic['id']], {'new-course'})
        source_index = json.loads(loaded.topic_paths[topic['id']].with_name('topic.sources.json').read_bytes())
        self.assertEqual(Path(source_index['source_path']).read_bytes(), (package / 'source.json').read_bytes())

    def test_grouped_practice_and_all_existing_interactions_compile_in_v2(self):
        from tests.question_fixtures import interactions
        document = lesson_document()
        module = document['nodes'][1]
        practices = []
        for number in range(1, 16):
            stem, spec = interactions()[(number - 1) % len(interactions())]
            practices.append(node(f'practice-{number}', f'Practice {(number-1)//3+1}.{(number-1)%3+1}', [
                node(f'question-{number}', stem),
                node(f'interaction-{number}', 'Interaction', [node(f'spec-{number}', json.dumps(spec, ensure_ascii=False))]),
                node(f'explanation-{number}', 'Explanation', [node(f'answer-{number}', '原始合成解析。')])]))
        module['children'] = module['children'][:2] + practices
        _, topic = self.prepare(document)
        actual = topic['modules'][0]['practices']
        self.assertEqual([q['id'] for q in actual], [f'example-1-practice-{i}' for i in range(1, 16)])
        self.assertEqual(actual[3]['source_practice'], {'label': '2.1', 'group': 2, 'member': 1})
        for index, question in enumerate(actual):
            expected = interactions()[index % len(interactions())][1]
            self.assertEqual(question['interaction']['type'], expected['type'])
            for key in ('pairs', 'blanks', 'answers', 'grading'):
                if key in expected:
                    self.assertEqual(question['interaction'][key], expected[key])

    def test_literal_reference_does_not_create_a_link_and_active_markup_fails(self):
        document = introduction_document()
        document['id'] = 'format.Literal'
        document['nodes'][1]['children'].append(node('literal',
            '`[[id:InlineLiteral|语法样例]]`\n\n```text\n[[id:FencedLiteral|语法样例]]\n```'))
        _, topic = self.prepare(document)
        html = topic['introductions'][1]['html']
        self.assertIn('[[id:FencedLiteral|语法样例]]', html)
        self.assertNotIn('data-target-rem-id', html)
        for index, text in enumerate(('<img src=x onerror=alert(1)>', '`$x$`', '```text\n$x$\n```')):
            document['nodes'][1]['children'][-1]['text'] = text
            source = self.root / f'rejected-{index}.json'
            source.write_text(dump_json(normalize_document(document)[0]), encoding='utf-8')
            result = prepare_topic(source, document['id'], self.root / f'rejected-package-{index}')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['stage'], 'convert')
