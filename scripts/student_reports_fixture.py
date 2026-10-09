"""Isolated, synthetic read-only report browser fixture; never production data."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from starlette.responses import JSONResponse
from starlette.routing import Route
from graspmemoedu.config import Settings
from tests.test_student_reports import StudentReportsTests

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--port', type=int, default=18870)
parser.add_argument('--presence', action='store_true', help='Seed isolated visit history and a Q&A content snapshot')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
with socket.socket() as probe:
    probe.bind(('127.0.0.1', args.port))

def local_settings(**kwargs):
    kwargs.pop('public_api_origin', None); kwargs.pop('allowed_origins', None)
    return Settings(**kwargs, port=args.port)

case = StudentReportsTests()
with patch('tests.test_admin_students.Settings', local_settings):
    case.setUp()
case.time = datetime.now(timezone.utc) - timedelta(days=5)
training = case.training()
case.answer(training, answer='0')
case.time += timedelta(days=1)
case.answer(training, answer='0', elapsed=55000)
case.time += timedelta(days=1)
case.answer(training, answer='2', elapsed=31000)
case.answer(training, qid='choice', answer='{"selected":"b"}', elapsed=7000)
case.answer(training, qid='choice', answer='{"selected":"a"}', elapsed=5000)
essay = training.open('isolated-bank', 'group-a', 'essay', 'self-rating')
training.reveal(essay); training.rate(essay, 2)
unanswered = training.open('isolated-bank', 'group-a', 'blank', 'manual-notebook')
training.add_to_notebook(unanswered)
case.time += timedelta(days=3)
lesson = case.learner()
state = lesson.choose(case.action(lesson.state(), choice='mastered'))
submission = lesson.submit(case.action(state, answer='correct', elapsed_ms=42000))
lesson.process_submission(submission['submission_id'])
lesson.review_backend.clock = lambda: case.time
lesson.review_backend.record_module_result(module_id='module-2', mastery=4, result_id='future-review', completed_at=case.time + timedelta(days=8))
atoms = case.learner('atoms')
state = atoms.choose(case.action(atoms.state(), choice='learn'))
state = atoms.advance(case.action(state))
submission = atoms.submit(case.action(state, answer='错误', elapsed_ms=12000))
atoms.process_submission(submission['submission_id'])
case.store.set_enabled(case.second, False)
# Match an established profile before checking that report browsing is read-only.
case.app.state.dashboard_store.update_profile(case.store.resolve(case.headers['X-Learning-Identity']), {'timezone': 'Asia/Shanghai'})
with case.store._transaction() as con:
    con.execute('UPDATE access_principals SET admin_name=?,display_name=? WHERE learner_id=?',
                ('Private roster name', 'Learner nickname', case.student))
case.app.state.admin_students.catalog.courses['math']['units'] = [
    {'id': 'math', 'title': 'Math fixture', 'parent_id': None, 'topic_ids': []},
    {'id': 'chapter', 'title': 'Real chapter', 'parent_id': 'math', 'topic_ids': [case.course['id']]}]

def snapshot(request):
    # Learner-owned rows only; administrator authentication does not count as learning.
    with closing(sqlite3.connect(case.root / 'learning.sqlite3')) as con:
        data = {}
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if name.startswith('presence_'): continue  # Visit telemetry is independent of learning records.
            columns = [r[1] for r in con.execute('PRAGMA table_info("' + name + '")')]
            field = 'learner' if 'learner' in columns else 'learner_id' if 'learner_id' in columns else None
            if field:
                data[name] = con.execute('SELECT * FROM "' + name + '" WHERE "' + field + '" IN (?,?) ORDER BY rowid', (case.student, case.second)).fetchall()
        presence_active = con.execute('SELECT COUNT(*) FROM presence_sessions WHERE learner=? AND active=1', (case.student,)).fetchone()[0]
    return JSONResponse({'hash': hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(), 'presence_active': presence_active})

case.app.router.routes.insert(0, Route('/qa/snapshot', snapshot))
case.app.router.routes.insert(0, Route('/deployment.json', lambda request: JSONResponse({'api_origin': ''})))
identities = {role: {'identity_token': headers['X-Learning-Identity'], 'access': case.store.resolve(headers['X-Learning-Identity'])}
              for role, headers in [('admin', case.admin_headers), ('ordinary', case.normal_headers), ('student', case.headers)]}
identities.update(target=case.student, empty_target=case.second)
if args.presence:
    from graspmemoedu.presence import PresencePulse
    now = datetime.now(timezone.utc)
    case.app.state.presence.clock = lambda: now - timedelta(seconds=30)
    student = case.store.resolve(case.headers['X-Learning-Identity'])
    case.app.state.presence.pulse(student, PresencePulse(stream_id='a' * 32, sequence=1, visible=True, topic_ids=[case.course['id']]))
    case.app.state.presence.clock = lambda: now
    case.app.state.presence.pulse(student, PresencePulse(stream_id='a' * 32, sequence=2, visible=False))
    case.app.state.presence.clock = lambda: datetime.now(timezone.utc)
    case.app.state.admin_students.clock = lambda: datetime.now(timezone.utc)
    saved = case.app.state.help_requests.save(student, {'request_id': 'isolated-content-reader', 'message': '',
        'topic_id': case.course['id'], 'content_block_id': 'introduction', 'content_version': case.course['version']})
    identities['report_id'] = saved['id']
(args.output / 'auth.private.json').write_text(json.dumps(identities), encoding='utf-8')
(args.output / 'fixture.json').write_text(json.dumps({'data_dir': str(case.root), 'port': args.port}), encoding='utf-8')
case.client.__exit__(None, None, None)
print('Synthetic student dashboard fixture ready.', flush=True)
uvicorn.run(case.app, host='127.0.0.1', port=args.port, log_level='warning')
