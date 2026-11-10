"""Portable backups for the catalog, all topic sources, guide and shared DB."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import zipfile
from types import SimpleNamespace

from .catalog import Catalog
from .config import Settings
from .guide import GuideReader
from .service import DomainError, LearningService, create_learning_service
from .subjects import Subjects
from scripts.verify_content import collect_questions, parse_outline, sha256

MAX_ARCHIVE_BYTES = 3_000_000_000
MAX_MANIFEST_BYTES = 4_000_000


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def _source_suffix(raw):
    return '.json' if raw.decode('utf-8-sig').lstrip().startswith('{') else '.md'


def _retired_versions(manifest, active_topics):
    retired = manifest.get('retired_topic_versions', {})
    if not isinstance(retired, dict):
        raise ValueError('Invalid retired topic versions')
    for topic_id, versions in retired.items():
        Catalog._identifier(topic_id)
        if (topic_id in active_topics or not isinstance(versions, list) or not versions
                or any(not isinstance(value, str) or not value for value in versions)
                or len(versions) != len(set(versions))):
            raise ValueError('Invalid retired topic versions')
    return retired


def _introduction_versions(manifest, active_topics):
    """An installed introduction may still have its previous, unopened DB row."""
    from .topic_runtime import is_introduction
    historical = manifest.get('introduction_previous_versions', {})
    if not isinstance(historical, dict):
        raise ValueError('Invalid previous introduction versions')
    for topic_id, versions in historical.items():
        if (topic_id not in active_topics or not is_introduction(active_topics[topic_id])
                or not isinstance(versions, list) or not versions
                or any(not isinstance(v, str) or not v or v == active_topics[topic_id]['version'] for v in versions)
                or len(versions) != len(set(versions))):
            raise ValueError('Invalid previous introduction versions')
    return historical


def _validate_answers(topic, data, *, code='invalid_backup', status=422):
    answers = json.loads(data)
    if not isinstance(answers, dict) or answers.get('source_sha256') != topic['source_sha256']:
        raise DomainError(code, '答案表与课程来源版本不同。', status)
    rows = answers.get('answers')
    if not isinstance(rows, dict):
        raise DomainError(code, '答案表格式不正确。', status)
    questions = topic.get('flashcards', []) + [question for module in topic.get('modules', []) for question in [module['example'], *module['practices']]]
    if any(not isinstance(rows.get(question['id']), dict) or any(
            rows[question['id']].get(key) != question[key] for key in ('reference_answer', 'answer_type', 'constraints'))
           for question in questions):
        raise DomainError(code, '答案表与课程内容不一致。', status)


def _safe_name(name):
    """Canonical relative ZIP names that can also safely be materialized on Windows."""
    if not isinstance(name, str) or not name or name.startswith('/'):
        return False
    path = PurePosixPath(name)
    if str(path) != name or any(character in name for character in '\\:<>"|?*'):
        return False
    reserved = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]|CONIN\$|CONOUT\$)(\.|$)', re.IGNORECASE)
    return all(part not in {'.', '..'} and part == part.rstrip(' .') and not reserved.match(part)
               and not any(ord(character) < 32 for character in part) for part in path.parts)


def _validate_review(topic, source, answers, data, assets=None):
    """Validate preserved review evidence without sending another LLM request."""
    review = json.loads(data)
    mechanical = review.get('mode') == 'mechanical'
    if mechanical and (review.get('status') != 'mechanically_validated'
                       or review.get('method') != 'mechanical-import'
                       or review.get('semantic_review_performed') is not False
                       or review.get('mathematical_correctness_checked') is not False):
        raise ValueError('Mechanical evidence must not claim semantic approval')
    if review.get('assets', []) != (assets or []):
        raise ValueError('Review asset evidence differs from its source attachments')
    if (review.get('schema_version') != 1 or review.get('status') != ('mechanically_validated' if mechanical else 'approved')
            or review.get('source_sha256') != sha256(source)
            or review.get('sidecar_sha256') != sha256(answers)):
        raise ValueError('Review evidence is unapproved or differs from its inputs')
    if review.get('source_format') == 'structured-atomic-v1':
        from .structured_atomic import validate_projection, fingerprint
        if not mechanical or review.get('topic_fingerprint') != fingerprint(topic):
            raise ValueError('Structured atomic compiled content changed')
        validate_projection(topic, json.loads(source))
        rows = json.loads(answers)['answers']
        if set(rows) != {q['id'] for q in topic['flashcards']}:
            raise ValueError('Structured atomic answer coverage changed')
        _validate_answers(topic, answers)
        return
    questions = collect_questions(parse_outline(source.decode('utf-8-sig')))
    if topic.get('topic_format_version') == 2:
        from .topic_format import parse_source, content_metadata, content_version
        document, _ = parse_source(source.decode('utf-8-sig'))
        if (any(topic.get(key) != document.get(key) for key in ('id', 'title', 'topic_kind', 'source_rem_id'))
                or topic.get('content_version') != content_version(document, assets or [])
                or any(topic.get(key) != value for key, value in content_metadata(document).items())):
            raise ValueError('Topic v2 content identity or references differ from its source')
    by_id = {question['id']: question for question in questions}
    topic_questions = {q['id']: q for m in topic['modules'] for q in [m['example'], *m['practices']]}
    answer_rows = json.loads(answers)['answers']
    records = review.get('questions')
    if not isinstance(records, list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in records):
        raise ValueError('Invalid review questions')
    ids = [row['id'] for row in records]
    if (len(ids) != len(set(ids)) or set(ids) != set(by_id)
            or set(ids) != set(topic_questions) or set(ids) != set(answer_rows)):
        raise ValueError('Review question coverage differs from the source or topic')
    for row in records:
        qid = row['id']
        valid = (row.get('structurally_valid') is True and row.get('field_mapping') == 'reference_answer=explanation'
                 and 'approved' not in row) if mechanical else row.get('approved') is True and row.get('issues') == []
        if mechanical and (answer_rows[qid].get('reference_answer') != by_id[qid]['explanation']):
            raise ValueError('Mechanical answer is not mapped from its source explanation')
        if (not valid
                or row.get('question_sha256') != sha256(by_id[qid]['question'])
                or answer_rows[qid].get('question_sha256') != row['question_sha256']
                or topic_questions[qid]['question'] != by_id[qid]['question']):
            raise ValueError('Review question evidence is invalid')


def _read_archive(backup):
    with zipfile.ZipFile(backup) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        required = {'learning.sqlite3', 'catalog.json', 'manifest.json'}
        if (len(names) > 10000 or len(names) != len({name.casefold() for name in names})
                or not required <= set(names) or not all(_safe_name(name) for name in names)
                or any(info.file_size > 100_000_000 for info in infos)
                or sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES
                or archive.getinfo('manifest.json').file_size > MAX_MANIFEST_BYTES):
            raise ValueError('Invalid archive structure')
        files = {name.casefold() for name in names}
        if any(any(str(parent).casefold() in files for parent in PurePosixPath(name).parents if str(parent) != '.') for name in names):
            raise ValueError('A file cannot also be a parent directory')
        manifest = json.loads(archive.read('manifest.json'))
        blobs = {name: archive.read(name) for name in names if name != 'manifest.json'}
    if (not isinstance(manifest, dict) or manifest.get('format') != 2
            or not isinstance(manifest.get('sha256'), dict) or set(manifest['sha256']) != set(blobs)
            or any(hashlib.sha256(data).hexdigest() != manifest['sha256'][name] for name, data in blobs.items())):
        raise ValueError('Invalid archive checksums')
    return manifest, blobs


def _validate_payload(manifest, blobs):
    """Validate every path-bearing manifest field before any extraction takes place."""
    default_id = Catalog._identifier(manifest.get('default_topic_id'))
    versions, sources = manifest.get('topic_versions'), manifest.get('sources')
    portable = json.loads(blobs['catalog.json'])
    if not isinstance(versions, dict) or not isinstance(sources, dict) or not isinstance(portable, dict):
        raise ValueError('Invalid manifest data')
    topics, expected_files = {}, {'catalog.json', 'learning.sqlite3'}
    if 'judge-daily-quota.sqlite3' in blobs:
        expected_files.add('judge-daily-quota.sqlite3')
    from .help_images import MAX_BYTES as HELP_IMAGE_MAX
    for name, raw in blobs.items():
        if name.startswith('help-images/'):
            if not re.fullmatch(r'help-images/[0-9a-f]{32}\.png', name) or len(raw) > HELP_IMAGE_MAX:
                raise ValueError('Invalid help image attachment')
            expected_files.add(name)
    from .test_materials import FILENAME as TEST_MATERIALS, MAX_BYTES as TEST_POLICY_MAX, validate as validate_test_materials
    if TEST_MATERIALS in blobs:
        if len(blobs[TEST_MATERIALS]) > TEST_POLICY_MAX:
            raise ValueError('Test material policy too large')
        validate_test_materials(json.loads(blobs[TEST_MATERIALS]))
        expected_files.add(TEST_MATERIALS)
    for course in portable['courses']:
        for declaration in course['topics']:
            topic_id = Catalog._identifier(declaration['id'])
            expected = f'courses/{topic_id}.json'
            if declaration['path'] != expected or expected not in blobs:
                raise ValueError('Invalid portable topic path')
            topic = json.loads(blobs[expected])
            if not isinstance(topic, dict) or topic.get('id') != topic_id or topic.get('version') != versions.get(topic_id):
                raise ValueError('Invalid topic version')
            Catalog._validate_topic(topic)
            topics[topic_id] = topic
            expected_files.add(expected)
    if not topics or set(versions) != set(topics) or set(sources) != set(topics) or default_id not in topics:
        raise ValueError('Topic manifest membership differs from catalog')
    from .training_audio import validate_audio_archive
    expected_files.update(validate_audio_archive(blobs, topics))
    _retired_versions(manifest, topics)
    _introduction_versions(manifest, topics)
    from .question_bank import validate_archive
    expected_files.update(validate_archive(blobs, topics))
    from .training_content import validate_archive as validate_training_archive
    expected_files.update(validate_training_archive(blobs))
    for topic_id, entry in sources.items():
        if not isinstance(entry, dict):
            raise ValueError('Invalid sources entry')
        from .topic_assets import validate_assets
        assets = entry.get('assets', [])
        asset_root = f'sources/{topic_id}/'
        asset_bytes = validate_assets(assets, lambda name: blobs[asset_root + name],
                                      source=blobs[entry['source']].decode('utf-8-sig') if 'source' in entry else None)
        if assets and not {'source', 'answers', 'review'} <= set(entry):
            raise ValueError('Source attachments require complete reviewed source evidence')
        suffix = _source_suffix(blobs[entry['source']]) if 'source' in entry else '.md'
        expected = {'source': f'sources/{topic_id}/source{suffix}' if assets else f'sources/{topic_id}{suffix}', 'answers': f'courses/{topic_id}.answers.json',
                    'review': f'courses/{topic_id}.review.json'}
        if any(key not in expected or value != expected[key] or value not in blobs for key, value in entry.items() if key != 'assets'):
            raise ValueError('Invalid sources path')
        if 'source' in entry and hashlib.sha256(blobs[entry['source']]).hexdigest() != topics[topic_id]['source_sha256']:
            raise ValueError('Source differs from topic content')
        if 'answers' in entry:
            _validate_answers(topics[topic_id], blobs[entry['answers']])
        if 'review' in entry:
            if not {'source', 'answers'} <= set(entry):
                raise ValueError('Review requires its source and answers')
            _validate_review(topics[topic_id], blobs[entry['source']], blobs[entry['answers']], blobs[entry['review']], assets)
        expected_files.update(value for key, value in entry.items() if key != 'assets')
        expected_files.update(asset_root + name for name in asset_bytes)
    from .course_graph import validate_graph_archive
    expected_files.update(validate_graph_archive(blobs))
    for name, raw in blobs.items():
        if name.startswith('mathacademy-sources/'):
            # The native importer preserves source JSON together with its scope
            # and nested review receipts. They are private data, never runtime
            # paths; the archive-wide path/checksum checks also apply here.
            if not re.fullmatch(r'mathacademy-sources/(?:[A-Za-z0-9][A-Za-z0-9_.-]*/)*[A-Za-z0-9][A-Za-z0-9_.-]*\.json', name):
                raise ValueError('Invalid private source snapshot path')
            json.loads(raw)
            expected_files.add(name)
    if 'code-judges.json' in blobs:
        from .code_judges import valid_registry
        if len(blobs['code-judges.json']) > 4 * 1024 * 1024 or not valid_registry(json.loads(blobs['code-judges.json'])):
            raise ValueError('Invalid local code judge registry')
        expected_files.add('code-judges.json')
    guide_roots = {'guide': 'guide/guide.md'}
    if 'subjects.json' in blobs:
        if len(blobs['subjects.json']) > 1024 * 1024:
            raise ValueError('Subject configuration too large')
        subject_data = json.loads(blobs['subjects.json'])
        probe_settings = SimpleNamespace(data_dir=Path.cwd() / '.portable-subject-validation', guide_path=Path('guide/guide.md'))
        Subjects(probe_settings, SimpleNamespace(courses={course['id']: course for course in portable['courses']}), subject_data)
        for subject in subject_data['subjects']:
            if 'guide_path' in subject:
                path = subject['guide_path']
                expected = f"subject-guides/{subject['id']}/guide.md"
                if path != expected and not (subject['id'] == 'math' and path == 'guide/guide.md'):
                    raise ValueError('Invalid portable subject guide path')
                guide_roots[str(PurePosixPath(path).parent)] = path
        expected_files.add('subjects.json')
    for name in set(blobs) - expected_files:
        root = next((root for root in guide_roots if name.startswith(root + '/')), None)
        if root is None or guide_roots[root] not in blobs:
            raise ValueError('Unexpected backup member')
        if name == guide_roots[root]:
            if len(blobs[name]) > GuideReader.MAX_SOURCE_BYTES:
                raise ValueError('Guide too large')
            blobs[name].decode('utf-8-sig')
        elif PurePosixPath(name).suffix.lower() not in GuideReader.ASSET_SUFFIXES or len(blobs[name]) > GuideReader.MAX_ASSET_BYTES:
            raise ValueError('Invalid guide asset')
    return default_id, topics


def backup_site(settings, destination=None):
    catalog = Catalog.load(settings)
    from .training_content import TrainingCatalog, validate_saved as validate_training_saved
    training_catalog = TrainingCatalog(settings.data_dir)
    subjects = Subjects(settings, catalog)
    subject_file = settings.content_dir / 'subjects.json'
    subject_bytes = subject_file.read_bytes() if subject_file.is_file() else None
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = Path(destination or settings.data_dir / 'backups' / f'learning-{stamp}.zip')
    if target.exists():
        raise DomainError('backup_exists', '备份目标已存在。', 422)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not (settings.data_dir / 'learning.sqlite3').exists():
        create_learning_service(catalog.topic(catalog.default_topic_id), settings.data_dir)
    blobs = {}
    code_judges = settings.data_dir / 'code-judges.json'
    if code_judges.exists():
        from .code_judges import registry
        registry(settings.data_dir)
        blobs['code-judges.json'] = code_judges.read_bytes()
    with tempfile.TemporaryDirectory(prefix='backup-', dir=settings.data_dir) as folder:
        snapshot_path = Path(folder) / 'learning.sqlite3'
        with closing(sqlite3.connect(settings.data_dir / 'learning.sqlite3')) as source:
            with closing(sqlite3.connect(snapshot_path)) as snapshot:
                source.backup(snapshot)
                if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise DomainError('backup_failed', '备份数据库完整性校验失败。', 500)
                saved_versions = snapshot.execute('SELECT DISTINCT topic,version FROM topics').fetchall()
                from .question_bank import validate_saved_revisions
                validate_saved_revisions(snapshot, catalog.topics, settings.data_dir)
                validate_training_saved(snapshot, training_catalog)
                from .help_images import archive_images
                blobs.update(archive_images(snapshot, settings.data_dir))
        from .topic_runtime import is_introduction
        introduction_versions = {}
        for topic_id, version in saved_versions:
            if (topic_id in catalog.topics and is_introduction(catalog.topics[topic_id])
                    and catalog.topics[topic_id]['version'] != version):
                introduction_versions.setdefault(topic_id, []).append(version)
        if any(topic_id in catalog.topics and not is_introduction(catalog.topics[topic_id])
               and catalog.topics[topic_id]['version'] != version for topic_id, version in saved_versions):
            raise DomainError('backup_version_changed', '部分进度的课程来源缺失或版本不同，请先处理课程目录。', 409)
        retired_versions = {}
        for topic_id, version in sorted(saved_versions):
            if topic_id not in catalog.topics:
                retired_versions.setdefault(topic_id, []).append(version)
        blobs['learning.sqlite3'] = snapshot_path.read_bytes()
        quota_path = settings.data_dir / 'judge-daily-quota.sqlite3'
        if quota_path.is_file():
            quota_snapshot = Path(folder) / 'judge-daily-quota.sqlite3'
            with closing(sqlite3.connect(quota_path)) as source:
                with closing(sqlite3.connect(quota_snapshot)) as snapshot:
                    source.backup(snapshot)
                    if (snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                            or not snapshot.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage'").fetchone()
                            or snapshot.execute('SELECT 1 FROM usage WHERE count < 0 LIMIT 1').fetchone()):
                        raise DomainError('backup_failed', '判题用量数据库校验失败，未创建备份。', 500)
            blobs['judge-daily-quota.sqlite3'] = quota_snapshot.read_bytes()
    blobs.update(training_catalog.archive_blobs())
    from .test_materials import FILENAME as TEST_MATERIALS
    test_policy_path = settings.content_dir / TEST_MATERIALS
    if test_policy_path.is_file():
        blobs[TEST_MATERIALS] = test_policy_path.read_bytes()
    sources_index = {}
    for topic_id in catalog.topics:
        bank_root = settings.data_dir / 'question-banks' / topic_id
        if bank_root.exists():
            for bank_path in bank_root.glob('*.json'):
                blobs[f'question-banks/{topic_id}/{bank_path.name}'] = bank_path.read_bytes()
    for topic_id, topic in catalog.topics.items():
        path = catalog.topic_paths[topic_id]
        course_bytes = path.read_bytes()
        if json.loads(course_bytes) != topic:
            raise DomainError('backup_version_changed', '备份期间课程发生变化，请重新备份。', 409)
        blobs[f'courses/{topic_id}.json'] = course_bytes
        index_path = path.with_name(path.stem + '.sources.json')
        index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
        if not isinstance(index, dict):
            raise DomainError('invalid_sources', '课程来源索引格式不正确。', 422)
        def source_location(value):
            if not isinstance(value, str) or not value.strip():
                raise DomainError('invalid_sources', '课程来源路径格式不正确。', 422)
            location = Path(value)
            return location if location.is_absolute() else index_path.parent / location
        source_path = source_location(index['source_path']) if 'source_path' in index else None
        answers_path = source_location(index['answers_path']) if 'answers_path' in index else path.with_name(path.stem + '.answers.json')
        entry = {}
        if source_path is not None and not source_path.is_file():
            raise DomainError('source_missing', '课程原稿缺失，无法生成完整备份。', 422)
        if source_path is not None:
            original = source_path.read_bytes()
            if hashlib.sha256(original).hexdigest() != topic['source_sha256']:
                raise DomainError('source_changed', '原稿已变化，请先完成新版本导入后再备份。', 409)
            assets = index.get('assets', [])
            from .topic_assets import validate_assets, read_local_asset
            asset_bytes = validate_assets(assets, lambda name: read_local_asset(source_path.parent, name), source=original.decode('utf-8-sig'))
            suffix = _source_suffix(original)
            entry['source'] = f'sources/{topic_id}/source{suffix}' if assets else f'sources/{topic_id}{suffix}'
            blobs[entry['source']] = original
            if assets:
                entry['assets'] = assets
                blobs.update({f'sources/{topic_id}/{name}': raw for name, raw in asset_bytes.items()})
        elif index.get('assets'):
            raise DomainError('source_missing', '课程附件缺少对应原稿，无法完整备份。', 422)
        if answers_path.is_file():
            answers_bytes = answers_path.read_bytes()
            _validate_answers(topic, answers_bytes, code='answers_changed', status=409)
            entry['answers'] = f'courses/{topic_id}.answers.json'
            blobs[entry['answers']] = answers_bytes
        elif source_path is not None or 'answers_path' in index:
            raise DomainError('answers_missing', '独立答案表缺失，无法生成完整备份。', 422)
        if 'review_path' in index:
            review_path = source_location(index['review_path'])
            if not review_path.is_file():
                raise DomainError('review_missing', '课程审核凭据缺失，无法生成完整备份。', 422)
            review_bytes = review_path.read_bytes()
            if sha256(review_bytes) != index.get('review_sha256'):
                raise DomainError('review_changed', '课程审核凭据已变化，未创建备份。', 409)
            entry['review'] = f'courses/{topic_id}.review.json'
            blobs[entry['review']] = review_bytes
        sources_index[topic_id] = entry
    portable = {'courses': [{**course, 'topics': [{**topic, 'path': f"courses/{topic['id']}.json"}
                                                for topic in course['topics']]} for course in catalog.courses.values()]}
    blobs['catalog.json'] = _json(portable)
    from .training_audio import audio_backup_blobs
    blobs.update(audio_backup_blobs(catalog))
    from .course_graph import graph_backup_blobs
    blobs.update(graph_backup_blobs(settings))
    for source in (settings.content_dir / 'mathacademy-sources').rglob('*.json'):
        blobs['mathacademy-sources/' + source.relative_to(settings.content_dir / 'mathacademy-sources').as_posix()] = source.read_bytes()
    def include_guide(source, prefix):
        if not source.is_file():
            return
        name = prefix + '/guide.md'
        blobs[name] = source.read_bytes()
        reader = GuideReader(source)
        # A portable backup includes every permission section and its assets.
        if reader.read('https://guide-assets.invalid', {'role': 'account', 'is_admin': True})['status'] == 'unavailable':
            raise DomainError('guide_unavailable', '指南暂时无法读取，未创建不完整备份。', 422)
        for relative in reader._resources:
            try:
                resource = reader._resource(relative)
                # Keep the path used by Markdown even for an in-directory symlink.
                blobs[prefix + '/' + relative] = resource.read_bytes()
            except (OSError, ValueError):
                pass  # A missing guide image is retained as a missing image, not a fabricated file.
        if source.read_bytes() != blobs[name]:
            raise DomainError('backup_version_changed', '备份期间指南发生变化，请重新备份。', 409)
    include_guide(settings.guide_path, 'guide')
    if subject_bytes is not None:
        declarations = []
        for subject_id, subject in subjects.subjects.items():
            declaration = {**subject, 'course_ids': sorted(subjects.course_ids(subject_id))}
            if subject_id in subjects.guide_paths:
                source = subjects.guide_paths[subject_id]
                if subject_id == 'math' and subject_id not in subjects.explicit_guides:
                    prefix = 'guide'
                else:
                    prefix = 'subject-guides/' + subject_id
                    include_guide(source, prefix)
                declaration['guide_path'] = prefix + '/guide.md'
            declarations.append(declaration)
        blobs['subjects.json'] = _json({'subjects': declarations})
        if subject_file.read_bytes() != subject_bytes:
            raise DomainError('backup_version_changed', '备份期间学科配置发生变化，请重新备份。', 409)
    if len({name.casefold() for name in blobs}) != len(blobs):
        raise DomainError('invalid_backup', '课程文件名在本机存在大小写冲突。', 422)
    manifest = {'format': 2, 'created_at': stamp, 'default_topic_id': catalog.default_topic_id,
                'topic_versions': {key: topic['version'] for key, topic in catalog.topics.items()},
                'retired_topic_versions': retired_versions,
                'introduction_previous_versions': introduction_versions,
                'sources': sources_index,
                'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()}}
    manifest_bytes = _json(manifest)
    if (not all(_safe_name(name) for name in blobs)
            or any(len(data) > 100_000_000 for data in blobs.values())
            or len(manifest_bytes) > MAX_MANIFEST_BYTES
            or sum(map(len, blobs.values())) + len(manifest_bytes) > MAX_ARCHIVE_BYTES):
        raise DomainError('invalid_backup', '备份路径或体积超出可恢复范围。', 422)
    _validate_payload(manifest, blobs)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in blobs.items():
            archive.writestr(name, data)
        archive.writestr('manifest.json', manifest_bytes)
    return {'backup': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'format': 2, 'contains': [*blobs, 'manifest.json']}


def restore_site(backup, destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise DomainError('restore_exists', '恢复目标必须是尚不存在的新目录，避免覆盖现有数据。', 422)
    try:
        manifest, blobs = _read_archive(backup)
        default_id, topics = _validate_payload(manifest, blobs)
        # Validate database and catalog in an isolated sibling. The requested
        # destination remains absent until every check and recovery succeeds.
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=destination.parent) as temporary:
            staged = Path(temporary) / 'site'
            staged.mkdir()
            for name, data in blobs.items():
                target = staged / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            settings = Settings(data_dir=staged, course_path=staged / f'courses/{default_id}.json')
            catalog = Catalog.load(settings)
            Subjects(settings, catalog)
            from .course_graph import GraphStore
            GraphStore(settings, catalog).all()
            with closing(sqlite3.connect(staged / 'learning.sqlite3')) as con:
                if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Invalid database integrity')
                rows = con.execute('SELECT learner,topic,version FROM topics').fetchall()
                from .question_bank import validate_saved_revisions
                validate_saved_revisions(con, topics, staged)
                from .training_content import TrainingCatalog, validate_saved as validate_training_saved
                training_catalog = TrainingCatalog(staged)
                validate_training_saved(con, training_catalog)
                from .help_images import archive_images
                saved_images = archive_images(con, staged)
                if set(saved_images) != {name for name in blobs if name.startswith('help-images/')}:
                    raise ValueError('Help image archive differs from database')
                retired = _retired_versions(manifest, topics)
                historical = _introduction_versions(manifest, topics)
                archived_rows = {(topic_id, version) for _, topic_id, version in rows if topic_id not in topics}
                declared_rows = {(topic_id, version) for topic_id, versions in retired.items() for version in versions}
                mismatched_rows = {(topic_id, version) for _, topic_id, version in rows
                                   if topic_id in topics and topics[topic_id]['version'] != version}
                previous_rows = {(topic_id, version) for topic_id, versions in historical.items() for version in versions}
                if archived_rows != declared_rows or mismatched_rows != previous_rows:
                    raise ValueError('Database topics differ from the catalog')
                learners = [(learner_id, topic_id) for learner_id, topic_id, _ in rows if topic_id in topics]
            if 'judge-daily-quota.sqlite3' in blobs:
                with closing(sqlite3.connect(staged / 'judge-daily-quota.sqlite3')) as con:
                    if (con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                            or not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage'").fetchone()
                            or con.execute('SELECT 1 FROM usage WHERE count < 0 LIMIT 1').fetchone()):
                        raise ValueError('Invalid judge usage database')
            for learner_id, topic_id in learners:
                create_learning_service(catalog.topic(topic_id), staged, learner_id=learner_id).recover_interrupted()
            from .training import TrainingService
            TrainingService(training_catalog, staged, 'recovery').recover_interrupted()
            from .review_papers import PaperService
            with closing(sqlite3.connect(staged / 'learning.sqlite3')) as recovered_papers:
                PaperService.recover(recovered_papers)
                recovered_papers.commit()
            for topic_id, sources in manifest['sources'].items():
                if not sources:
                    continue
                index = {'source_sha256': topics[topic_id]['source_sha256']}
                if 'source' in sources:
                    index['source_path'] = str((destination / sources['source']).resolve())
                if 'answers' in sources:
                    index['answers_path'] = str((destination / sources['answers']).resolve())
                if 'review' in sources:
                    index['review_path'] = str((destination / sources['review']).resolve())
                    index['review_sha256'] = sha256(blobs[sources['review']])
                if sources.get('assets'):
                    index['assets'] = sources['assets']
                (staged / f'courses/{topic_id}.sources.json').write_bytes(_json(index))
            (staged / 'settings.json').write_bytes(_json({
                'course_path': str((destination / f'courses/{default_id}.json').resolve()),
                'catalog_path': str((destination / 'catalog.json').resolve()),
                'guide_path': str((destination / 'guide/guide.md').resolve())}))
            if destination.exists() or destination.is_symlink():
                raise DomainError('restore_exists', '恢复目标已出现，未覆盖现有数据。', 422)
            staged.rename(destination)
    except DomainError as exc:
        if exc.code == 'restore_exists':
            raise
        raise DomainError('invalid_backup', '备份内容或数据库校验失败，未创建恢复目录。', 422) from exc
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise DomainError('invalid_backup', '备份内容或数据库校验失败，未创建恢复目录。', 422) from exc
    return {'restored_to': str(destination), 'format': 2, 'courses': len(catalog.courses),
            'topics': len(catalog.topics), 'learner_topics': len(learners)}
