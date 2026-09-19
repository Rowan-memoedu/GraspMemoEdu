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

from .catalog import Catalog
from .config import Settings
from .guide import GuideReader
from .service import DomainError, LearningService


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')


def _validate_answers(topic, data, *, code='invalid_backup', status=422):
    answers = json.loads(data)
    if not isinstance(answers, dict) or answers.get('source_sha256') != topic['source_sha256']:
        raise DomainError(code, '答案表与课程来源版本不同。', status)
    rows = answers.get('answers')
    if not isinstance(rows, dict):
        raise DomainError(code, '答案表格式不正确。', status)
    questions = [question for module in topic['modules'] for question in [module['example'], *module['practices']]]
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


def _read_archive(backup):
    with zipfile.ZipFile(backup) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        required = {'learning.sqlite3', 'catalog.json', 'manifest.json'}
        if (len(names) > 10000 or len(names) != len({name.casefold() for name in names})
                or not required <= set(names) or not all(_safe_name(name) for name in names)
                or any(info.file_size > 100_000_000 for info in infos)
                or sum(info.file_size for info in infos) > 250_000_000
                or archive.getinfo('manifest.json').file_size > 1_000_000):
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
    for course in portable['courses']:
        for declaration in course['topics']:
            topic_id = Catalog._identifier(declaration['id'])
            expected = f'courses/{topic_id}.json'
            if topic_id in topics or declaration['path'] != expected or expected not in blobs:
                raise ValueError('Invalid portable topic path')
            topic = json.loads(blobs[expected])
            if not isinstance(topic, dict) or topic.get('id') != topic_id or topic.get('version') != versions.get(topic_id):
                raise ValueError('Invalid topic version')
            Catalog._validate_topic(topic)
            topics[topic_id] = topic
            expected_files.add(expected)
    if not topics or set(versions) != set(topics) or set(sources) != set(topics) or default_id not in topics:
        raise ValueError('Topic manifest membership differs from catalog')
    for topic_id, entry in sources.items():
        if not isinstance(entry, dict):
            raise ValueError('Invalid sources entry')
        expected = {'source': f'sources/{topic_id}.md', 'answers': f'courses/{topic_id}.answers.json'}
        if any(key not in expected or value != expected[key] or value not in blobs for key, value in entry.items()):
            raise ValueError('Invalid sources path')
        if 'source' in entry and hashlib.sha256(blobs[entry['source']]).hexdigest() != topics[topic_id]['source_sha256']:
            raise ValueError('Source differs from topic content')
        if 'answers' in entry:
            _validate_answers(topics[topic_id], blobs[entry['answers']])
        expected_files.update(entry.values())
    guide_files = set(blobs) - expected_files
    if guide_files:
        if 'guide/guide.md' not in guide_files or len(blobs['guide/guide.md']) > GuideReader.MAX_SOURCE_BYTES:
            raise ValueError('Unexpected backup member')
        for name in guide_files:
            if name == 'guide/guide.md':
                blobs[name].decode('utf-8-sig')
            elif (not name.startswith('guide/') or PurePosixPath(name).suffix.lower() not in GuideReader.ASSET_SUFFIXES
                  or len(blobs[name]) > GuideReader.MAX_ASSET_BYTES):
                raise ValueError('Invalid guide asset')
    return default_id, topics


def backup_site(settings, destination=None):
    catalog = Catalog.load(settings)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    target = Path(destination or settings.data_dir / 'backups' / f'learning-{stamp}.zip')
    if target.exists():
        raise DomainError('backup_exists', '备份目标已存在。', 422)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if not (settings.data_dir / 'learning.sqlite3').exists():
        LearningService(catalog.topic(catalog.default_topic_id), settings.data_dir)
    blobs = {}
    with tempfile.TemporaryDirectory(prefix='backup-', dir=settings.data_dir) as folder:
        snapshot_path = Path(folder) / 'learning.sqlite3'
        with closing(sqlite3.connect(settings.data_dir / 'learning.sqlite3')) as source:
            with closing(sqlite3.connect(snapshot_path)) as snapshot:
                source.backup(snapshot)
                if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise DomainError('backup_failed', '备份数据库完整性校验失败。', 500)
                saved_versions = snapshot.execute('SELECT DISTINCT topic,version FROM topics').fetchall()
        if any(topic_id not in catalog.topics or catalog.topics[topic_id]['version'] != version for topic_id, version in saved_versions):
            raise DomainError('backup_version_changed', '部分进度的课程来源缺失或版本不同，请先处理课程目录。', 409)
        blobs['learning.sqlite3'] = snapshot_path.read_bytes()
    sources_index = {}
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
            entry['source'] = f'sources/{topic_id}.md'
            blobs[entry['source']] = original
        if answers_path.is_file():
            answers_bytes = answers_path.read_bytes()
            _validate_answers(topic, answers_bytes, code='answers_changed', status=409)
            entry['answers'] = f'courses/{topic_id}.answers.json'
            blobs[entry['answers']] = answers_bytes
        elif source_path is not None or 'answers_path' in index:
            raise DomainError('answers_missing', '独立答案表缺失，无法生成完整备份。', 422)
        sources_index[topic_id] = entry
    portable = {'courses': [{**course, 'topics': [{**topic, 'path': f"courses/{topic['id']}.json"}
                                                for topic in course['topics']]} for course in catalog.courses.values()]}
    blobs['catalog.json'] = _json(portable)
    if settings.guide_path.is_file():
        blobs['guide/guide.md'] = settings.guide_path.read_bytes()
        reader = GuideReader(settings.guide_path)
        if reader.read('https://guide-assets.invalid')['status'] == 'unavailable':
            raise DomainError('guide_unavailable', '指南暂时无法读取，未创建不完整备份。', 422)
        for relative in reader._resources:
            try:
                resource = reader._resource(relative)
                # Keep the path used by Markdown even for an in-directory symlink.
                blobs['guide/' + relative] = resource.read_bytes()
            except (OSError, ValueError):
                pass  # A missing guide image is retained as a missing image, not a fabricated file.
        if settings.guide_path.read_bytes() != blobs['guide/guide.md']:
            raise DomainError('backup_version_changed', '备份期间指南发生变化，请重新备份。', 409)
    if len({name.casefold() for name in blobs}) != len(blobs):
        raise DomainError('invalid_backup', '课程文件名在本机存在大小写冲突。', 422)
    manifest = {'format': 2, 'created_at': stamp, 'default_topic_id': catalog.default_topic_id,
                'topic_versions': {key: topic['version'] for key, topic in catalog.topics.items()},
                'sources': sources_index,
                'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()}}
    if (not all(_safe_name(name) for name in blobs)
            or any(len(data) > 100_000_000 for data in blobs.values())
            or sum(map(len, blobs.values())) > 250_000_000):
        raise DomainError('invalid_backup', '备份路径或体积超出可恢复范围。', 422)
    _validate_payload(manifest, blobs)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in blobs.items():
            archive.writestr(name, data)
        archive.writestr('manifest.json', _json(manifest))
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
            with closing(sqlite3.connect(staged / 'learning.sqlite3')) as con:
                if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or con.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Invalid database integrity')
                rows = con.execute('SELECT learner,topic,version FROM topics').fetchall()
                if any(topic_id not in topics or topics[topic_id]['version'] != version for _, topic_id, version in rows):
                    raise ValueError('Database topics differ from the catalog')
                learners = [(learner_id, topic_id) for learner_id, topic_id, _ in rows]
            for learner_id, topic_id in learners:
                LearningService(catalog.topic(topic_id), staged, learner_id=learner_id).recover_interrupted()
            for topic_id, sources in manifest['sources'].items():
                if not sources:
                    continue
                index = {'source_sha256': topics[topic_id]['source_sha256']}
                if 'source' in sources:
                    index['source_path'] = str((destination / sources['source']).resolve())
                if 'answers' in sources:
                    index['answers_path'] = str((destination / sources['answers']).resolve())
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
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        raise DomainError('invalid_backup', '备份内容或数据库校验失败，未创建恢复目录。', 422) from exc
    return {'restored_to': str(destination), 'format': 2, 'courses': len(catalog.courses),
            'topics': len(catalog.topics), 'learner_topics': len(learners)}
