import argparse
from contextlib import contextmanager, closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys
import tempfile
import zipfile

from .config import Settings
from .service import DomainError, LearningService
from .access import AccessStore, FEATURES
from .catalog import Catalog


def output(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def make_service(settings, learner='local', topic_id=None):
    catalog = Catalog.load(settings)
    course = catalog.topic(topic_id or catalog.default_topic_id)
    return LearningService(course, settings.data_dir, learner_id=learner)


def make_access_store(settings):
    catalog = Catalog.load(settings)
    return AccessStore(settings.data_dir, [{'id': topic['id'], 'title': topic['title']} for topic in catalog.topics.values()])


def split_permissions(value):
    return None if value is None else [item.strip() for item in value.split(',') if item.strip()]


def learner_statuses(settings):
    store = make_access_store(settings)
    with closing(sqlite3.connect(store.db_path)) as con:
        con.row_factory = sqlite3.Row
        has_topics = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topics'").fetchone()
        records = con.execute('SELECT learner,topic,version,body FROM topics ORDER BY learner,topic').fetchall() if has_topics else []
    principals = {item['learner_id']: item for item in store.list_principals()}
    result = []
    seen = set()
    for row in records:
        state = json.loads(row['body'])
        identity = principals.get(row['learner'], {'learner_id': row['learner'], 'role': 'legacy', 'display_name': row['learner']})
        result.append({**identity, 'topic_id': row['topic'], 'course_version': row['version'],
                       'status': state['status'], 'revision': state['revision'], 'pause': state['pause']})
        seen.add(row['learner'])
    result.extend({**identity, 'status': 'not_started', 'pause': None}
                  for learner_id, identity in principals.items() if learner_id not in seen)
    return result


@contextmanager
def server_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'server.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DomainError('server_running', '此数据目录已有学习服务运行。', 409)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def backup_bundle(settings, destination=None):
    if settings.catalog_path.exists() or settings.guide_path.exists():
        from .site_backup import backup_site
        return backup_site(settings, destination)
    service = make_service(settings)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    destination = Path(destination or settings.data_dir / 'backups' / f'learning-{timestamp}.zip')
    if destination.exists():
        raise DomainError('backup_exists', '备份目标已存在。', 422)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='backup-', dir=settings.data_dir) as folder:
        temporary = Path(folder)
        service.backup(temporary / 'learning.sqlite3')
        with closing(sqlite3.connect(temporary / 'learning.sqlite3')) as snapshot:
            saved_version = snapshot.execute('SELECT version FROM topics WHERE learner=? AND topic=?', ('local', service.topic_id)).fetchone()[0]
        course_bytes = settings.course_path.read_bytes()
        live_course = json.loads(course_bytes)
        if live_course['version'] != saved_version or live_course != service.course:
            raise DomainError('backup_version_changed', '备份期间课程发生变化，请重新备份。', 409)
        (temporary / 'course.json').write_bytes(course_bytes)
        sources_file = settings.course_path.with_name(settings.course_path.stem + '.sources.json')
        sources = json.loads(sources_file.read_text(encoding='utf-8')) if sources_file.exists() else {}
        source_path = Path(sources.get('source_path', ''))
        if sources and not source_path.is_file():
            raise DomainError('source_missing', '课程原稿缺失，无法生成完整备份。', 422)
        if source_path.is_file():
            source_bytes = source_path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != service.course['source_sha256']:
                raise DomainError('source_changed', '原稿已变化，请先完成新版本导入后再备份。', 409)
            (temporary / 'source.md').write_bytes(source_bytes)
        answers = Path(sources['answers_path']) if sources.get('answers_path') else settings.course_path.with_name(settings.course_path.stem + '.answers.json')
        if answers.is_file():
            answers_bytes = answers.read_bytes()
            answers_data = json.loads(answers_bytes)
            if answers_data.get('source_sha256') != service.course['source_sha256']:
                raise DomainError('answers_changed', '答案表与课程来源版本不同，无法生成完整备份。', 409)
            for question in service.questions.values():
                row = answers_data.get('answers', {}).get(question['id'], {})
                if any(row.get(key) != question[key] for key in ('reference_answer', 'answer_type', 'constraints')):
                    raise DomainError('answers_changed', '答案表与课程内容不一致，无法生成完整备份。', 409)
            (temporary / 'answers.json').write_bytes(answers_bytes)
        elif sources:
            raise DomainError('answers_missing', '独立答案表缺失，无法生成完整备份。', 422)
        manifest = {'format': 1, 'created_at': timestamp, 'topic_id': service.topic_id,
                    'course_version': service.course['version'], 'sha256': {}}
        for file in temporary.iterdir():
            manifest['sha256'][file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
        with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            for file in temporary.iterdir():
                archive.write(file, file.name)
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return {'backup': str(destination), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'contains': [*manifest['sha256'], 'manifest.json']}


def restore_bundle(backup, destination):
    destination = Path(destination)
    if destination.exists():
        raise DomainError('restore_exists', '恢复目标必须是尚不存在的新目录，避免覆盖现有数据。', 422)
    with zipfile.ZipFile(backup) as archive:
        if json.loads(archive.read('manifest.json')).get('format') == 2:
            from .site_backup import restore_site
            return restore_site(backup, destination)
    with zipfile.ZipFile(backup) as archive:
        names = archive.namelist()
        if (len(names) != len(set(names)) or not {'course.json', 'learning.sqlite3', 'manifest.json'} <= set(names)
                or set(names) - {'course.json', 'learning.sqlite3', 'manifest.json', 'source.md', 'answers.json'}
                or any(info.file_size > 100_000_000 for info in archive.infolist())):
            raise DomainError('invalid_backup', '备份结构不符合预期。', 422)
        manifest = json.loads(archive.read('manifest.json'))
        blobs = {name: archive.read(name) for name in names if name != 'manifest.json'}
        if manifest.get('format') != 1 or any(hashlib.sha256(blob).hexdigest() != manifest['sha256'].get(name) for name, blob in blobs.items()):
            raise DomainError('invalid_backup', '备份校验失败。', 422)
    course = json.loads(blobs['course.json'])
    if course['version'] != manifest['course_version'] or course['id'] != manifest['topic_id']:
        raise DomainError('invalid_backup', '课程版本校验失败。', 422)
    destination.mkdir(parents=True)
    (destination / 'courses').mkdir()
    course_path = destination / 'courses/digits-to-letters.json'
    course_path.write_bytes(blobs['course.json'])
    if 'answers.json' in blobs:
        course_path.with_name('digits-to-letters.answers.json').write_bytes(blobs['answers.json'])
    if 'source.md' in blobs:
        (destination / 'sources').mkdir()
        (destination / 'sources/source.md').write_bytes(blobs['source.md'])
        course_path.with_name('digits-to-letters.sources.json').write_text(json.dumps({
            'source_path': str((destination / 'sources/source.md').resolve()),
            'answers_path': str(course_path.with_name('digits-to-letters.answers.json').resolve()),
            'source_sha256': course['source_sha256']}, ensure_ascii=False, indent=2), encoding='utf-8')
    (destination / 'learning.sqlite3').write_bytes(blobs['learning.sqlite3'])
    with closing(sqlite3.connect(destination / 'learning.sqlite3')) as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise DomainError('invalid_backup', '恢复数据库完整性检查失败。', 422)
    service = LearningService(course, destination)
    service.recover_interrupted()
    state = service.state()
    return {'restored_to': str(destination), 'topic_id': state['topic_id'], 'status': state['status'],
            'course_version': state['course_version'], 'revision': state['revision'],
            'modules': state['modules'], 'pause': state['pause'], 'visited_step_count': len(state['steps'])}


def main():
    parser = argparse.ArgumentParser(description='本机数学学习网站与管理命令')
    parser.add_argument('--data-dir', type=Path, default=None)
    sub = parser.add_subparsers(dest='command', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int)
    serve.add_argument('--instance-id')
    status = sub.add_parser('status')
    status.add_argument('--learner', default='local')
    status.add_argument('--topic', help='指定 Topic；默认目录中的首个 Topic')
    status.add_argument('--list-learners', action='store_true', help='列出游客/账号标识和进度，供精确解锁使用')
    unlock = sub.add_parser('unlock')
    unlock.add_argument('--learner', required=True)
    unlock.add_argument('--topic', required=True)
    unlock.add_argument('--module', required=True)
    unlock.add_argument('--pause-event', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('--output', type=Path)
    restore = sub.add_parser('restore')
    restore.add_argument('--backup', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    pair = sub.add_parser('pair')
    pair.add_argument('--origin', required=True, help='Exact HTTPS origin, without a path')
    export = sub.add_parser('export-web')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--api-origin', default='', help='固定 HTTPS 后端地址；留空用于本机同源页面')
    account_create = sub.add_parser('account-create', help='由本机管理者创建账号，并仅显示一次邀请码')
    account_create.add_argument('--name', required=True, help='账号显示名称')
    account_create.add_argument('--topics', help='逗号分隔的 Topic 标识；默认当前已配置的全部 Topic')
    account_create.add_argument('--features', help='逗号分隔的功能权限；默认 ' + ','.join(FEATURES))
    sub.add_parser('account-list', help='查看账号及当前 Topic/功能权限，不显示邀请码')
    feedback = sub.add_parser('feedback-list', help='仅在本机查看收到的支持与内容纠错反馈')
    feedback.add_argument('--limit', type=int, default=30)
    permissions = sub.add_parser('account-permissions', help='设置账号权限；下一次 API 请求生效')
    permissions.add_argument('--learner', required=True, help='account-list 返回的账号 learner_id')
    permissions.add_argument('--topics', help='逗号分隔 Topic 标识；空字符串清空；省略则保留')
    permissions.add_argument('--features', help='逗号分隔功能权限；空字符串清空；省略则保留')
    args = parser.parse_args()
    try:
        if args.command == 'restore':
            output(restore_bundle(args.backup, args.destination))
            return
        settings = Settings.load(args.data_dir)
        if args.command == 'serve':
            if args.port is not None:
                settings.port = args.port
                settings.__post_init__()
            if args.instance_id:
                settings.instance_id = args.instance_id
            import uvicorn
            from .api import create_app
            with server_lock(settings.data_dir):
                uvicorn.run(create_app(settings), host='127.0.0.1', port=settings.port, access_log=False, proxy_headers=False)
        elif args.command == 'status':
            if args.list_learners:
                output({'learners': learner_statuses(settings)})
            else:
                state = make_service(settings, args.learner, args.topic).state()
                output({k: state[k] for k in ['topic_id', 'learner_id', 'status', 'revision', 'pause', 'modules']})
        elif args.command == 'unlock':
            state = make_service(settings, args.learner, args.topic).unlock(args.topic, args.module, args.pause_event)
            output({'status': state['status'], 'attempt_id': state['attempt_id'], 'pause': state['pause']})
        elif args.command == 'backup':
            output(backup_bundle(settings, args.output))
        elif args.command == 'account-create':
            output(make_access_store(settings).create_account(
                args.name, split_permissions(args.topics), split_permissions(args.features)))
        elif args.command == 'account-list':
            output({'accounts': make_access_store(settings).list_principals(accounts_only=True)})
        elif args.command == 'feedback-list':
            database = settings.data_dir / 'learning.sqlite3'
            rows = []
            if database.exists():
                with closing(sqlite3.connect(database)) as con:
                    con.row_factory = sqlite3.Row
                    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback'").fetchone():
                        rows = [dict(row) for row in con.execute('SELECT * FROM feedback ORDER BY created_at DESC,id DESC LIMIT ?', (max(1, min(500, args.limit)),))]
            output({'feedback': rows})
        elif args.command == 'account-permissions':
            if args.topics is None and args.features is None:
                raise DomainError('permissions_required', '请指定 --topics 或 --features；空字符串表示清空。', 422)
            output(make_access_store(settings).set_permissions(
                args.learner, split_permissions(args.topics), split_permissions(args.features)))
        elif args.command == 'pair':
            # An exact origin and a local, manually supplied credential are both required.
            origins = tuple(dict.fromkeys([*settings.allowed_origins, args.origin]))
            Settings(data_dir=settings.data_dir, port=settings.port, allowed_origins=origins)
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            credential, salt = secrets.token_urlsafe(32), secrets.token_hex(16)
            (settings.data_dir / 'pairing.json').write_text(json.dumps({'salt': salt, 'digest': hashlib.sha256((salt + credential).encode()).hexdigest()}), encoding='utf-8')
            config = {'course_path': str(settings.course_path), 'judge_project': str(settings.judge_project),
                      'port': settings.port, 'allowed_origins': origins,
                      'public_api_origin': settings.public_api_origin,
                      'catalog_path': str(settings.catalog_path), 'guide_path': str(settings.guide_path)}
            (settings.data_dir / 'settings.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            print('请将下面的本机配对码输入你信任的学习网页；不要发给他人。新增来源需重启学习服务后生效。')
            print(credential)
        elif args.command == 'export-web':
            if args.api_origin:
                Settings(public_api_origin=args.api_origin)
            if args.output.exists():
                raise DomainError('export_exists', '导出目标必须是新目录。', 422)
            shutil.copytree(Path(__file__).resolve().parent.parent / 'web', args.output)
            (args.output / 'deployment.json').write_text(json.dumps({'api_origin': args.api_origin}, indent=2) + '\n', encoding='utf-8')
            (args.output / '.nojekyll').write_text('', encoding='utf-8')
            output({'static_export': str(args.output), 'published': False, 'contains_course_or_credentials': False})
    except (DomainError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
