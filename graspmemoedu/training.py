"""Free-order single-question training over the shared judge and FSRS adapter."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import sqlite3

from .fsrs_engine import FSRSEngine, utc_time
from .judging import judge_answer
from .question_types import parse_answer, public_interaction, display_answer
from .service import DomainError
from .learning_storage import administrator


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class TrainingService:
    submission_table = 'training_submissions'

    def __init__(self, catalog, data_dir, learner_id, judge=None, *, runtime=None, clock=None):
        self.catalog, self.learner_id, self.judge = catalog, learner_id, judge
        self.db_path, self.runtime = Path(data_dir) / 'learning.sqlite3', runtime
        if runtime is None and administrator(data_dir, learner_id):
            raise DomainError('administrator_memory_required', '管理员学习必须使用临时会话。', 409)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.fsrs = FSRSEngine()
        with closing(self._connect()) as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS training_memory (
                    learner TEXT NOT NULL, bank TEXT NOT NULL, question TEXT NOT NULL, version TEXT NOT NULL,
                    mastered INTEGER NOT NULL, revision INTEGER NOT NULL, card_json TEXT, due_at TEXT NOT NULL,
                    PRIMARY KEY(learner,bank,question,version));
                CREATE TABLE IF NOT EXISTS training_attempts (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, bank TEXT NOT NULL, group_id TEXT NOT NULL,
                    question TEXT NOT NULL, version TEXT NOT NULL, snapshot TEXT NOT NULL,
                    mode TEXT NOT NULL, memory_revision INTEGER NOT NULL, phase TEXT NOT NULL,
                    created_at TEXT NOT NULL, completed_at TEXT, result TEXT, submission_id TEXT);
                CREATE INDEX IF NOT EXISTS training_attempt_owner ON training_attempts(learner,bank,question,created_at);
                CREATE TABLE IF NOT EXISTS training_submissions (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, attempt TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, answer TEXT NOT NULL, elapsed_ms INTEGER,
                    status TEXT NOT NULL, reason TEXT);
                CREATE TABLE IF NOT EXISTS training_ratings (
                    attempt TEXT PRIMARY KEY, learner TEXT NOT NULL, bank TEXT NOT NULL, question TEXT NOT NULL,
                    version TEXT NOT NULL, rating INTEGER, completed_at TEXT NOT NULL, fsrs TEXT);
                CREATE TABLE IF NOT EXISTS training_preferences (
                    learner TEXT PRIMARY KEY, self_rating_enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS training_suspensions (
                    learner TEXT NOT NULL, bank TEXT NOT NULL, question TEXT NOT NULL, version TEXT NOT NULL,
                    stopped_at TEXT NOT NULL, PRIMARY KEY(learner,bank,question,version));
            ''')

    def _connect(self):
        if self.runtime is not None:
            return self.runtime.connect()
        con = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        return con

    @contextmanager
    def transaction(self):
        with closing(self._connect()) as con:
            con.execute('BEGIN IMMEDIATE')
            try:
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise

    def preferences(self, enabled=None):
        # Account settings persist even when administrator learning is transient.
        with closing(sqlite3.connect(self.db_path, timeout=15, isolation_level=None)) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS training_preferences (
                learner TEXT PRIMARY KEY, self_rating_enabled INTEGER NOT NULL)''')
            if enabled is not None:
                con.execute('''INSERT INTO training_preferences VALUES (?,?)
                    ON CONFLICT(learner) DO UPDATE SET self_rating_enabled=excluded.self_rating_enabled''',
                            (self.learner_id, int(enabled)))
            row = con.execute('SELECT self_rating_enabled FROM training_preferences WHERE learner=?',
                              (self.learner_id,)).fetchone()
        return {'self_rating_enabled': bool(row[0]) if row else True}

    def _attempt(self, con, attempt_id):
        row = con.execute('SELECT * FROM training_attempts WHERE id=? AND learner=?',
                          (attempt_id, self.learner_id)).fetchone()
        if not row:
            raise DomainError('unknown_training_attempt', '未找到此作答记录。', 404)
        bank = self.catalog.bank(row['bank'])
        if bank['version'] != row['version']:
            raise DomainError('training_version_changed', '题库版本已变化，请联系管理者恢复原版本。', 409)
        return row

    def _memory(self, con, bank, question, version):
        return con.execute('SELECT * FROM training_memory WHERE learner=? AND bank=? AND question=? AND version=?',
                           (self.learner_id, bank, question, version)).fetchone()

    def _stopped(self, con, bank, question, version):
        return con.execute('''SELECT 1 FROM training_suspensions
            WHERE learner=? AND bank=? AND question=? AND version=?''',
            (self.learner_id, bank, question, version)).fetchone() is not None

    def open(self, bank_id, group_id, question_id, request_id):
        bank, _, question = self.catalog.question(bank_id, group_id, question_id)
        with self.transaction() as con:
            previous = con.execute('SELECT * FROM training_attempts WHERE id=?', (request_id,)).fetchone()
            if previous:
                if (previous['learner'], previous['bank'], previous['group_id'], previous['question']) != (
                        self.learner_id, bank_id, group_id, question_id):
                    raise DomainError('request_conflict', '请求标识已用于另一项操作。', 409)
                return request_id
            memory = self._memory(con, bank_id, question_id, bank['version'])
            now = utc_time(self.clock())
            mode = ('practice' if self._stopped(con, bank_id, question_id, bank['version']) else
                    'initial' if not memory or not memory['mastered'] else
                    'review' if utc_time(memory['due_at']) <= now else 'practice')
            spec = question.get('interaction', {})
            if spec.get('type') == 'choice' and spec.get('shuffle', True):
                # Stable for this attempt, and independent of the answer key.
                random.Random(hashlib.sha256(request_id.encode()).hexdigest()).shuffle(spec['options'])
            con.execute('''INSERT INTO training_attempts
                (id,learner,bank,group_id,question,version,snapshot,mode,memory_revision,phase,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,'answer',?)''',
                (request_id, self.learner_id, bank_id, group_id, question_id, bank['version'], packed(question),
                 mode, memory['revision'] if memory else 0, now.isoformat()))
        return request_id

    def state(self, attempt_id, *, history=True, self_rating=False):
        with closing(self._connect()) as con:
            row = self._attempt(con, attempt_id)
            if not history:
                latest = con.execute('''SELECT id FROM training_attempts WHERE learner=? AND bank=? AND question=?
                    ORDER BY created_at DESC,rowid DESC LIMIT 1''',
                    (self.learner_id, row['bank'], row['question'])).fetchone()
                if latest['id'] != attempt_id:
                    raise DomainError('feature_forbidden', '当前账号没有查看历史记录的权限。', 403)
            question = json.loads(row['snapshot'])
            submission = con.execute('SELECT status,reason,answer FROM training_submissions WHERE id=?',
                                     (row['submission_id'],)).fetchone()
            result = json.loads(row['result']) if row['result'] else None
            memory = self._memory(con, row['bank'], row['question'], row['version'])
            stopped = self._stopped(con, row['bank'], row['question'], row['version'])
        revealed = row['phase'] in ('shown', 'done')
        return {'attempt_id': row['id'], 'bank_id': row['bank'], 'group_id': row['group_id'],
                'question_id': row['question'], 'version': row['version'], 'phase': row['phase'],
                'mode': row['mode'], 'created_at': row['created_at'], 'completed_at': row['completed_at'],
                'question': {'id': question['id'], 'title': question.get('title', question['id']),
                             'html': question['html'], 'interaction': public_interaction(question)},
                'explanation_html': question['explanation_html'] if revealed else None,
                'can_self_rate': self_rating and question.get('interaction', {}).get('type', 'text') == 'text',
                'submission_id': row['submission_id'], 'submission_status': submission['status'] if submission else None,
                'answer': submission['answer'] if submission else '',
                'answer_display': display_answer(question, submission['answer']) if submission else '',
                'error': submission['reason'] if submission and submission['status'] == 'error' else None,
                'result': result, 'stopped': stopped, 'due_at': memory['due_at'] if memory and not stopped else None}

    @staticmethod
    def submission_fingerprint(data):
        return hashlib.sha256(packed({k: data.get(k) for k in ('attempt_id', 'answer', 'elapsed_ms')}).encode()).hexdigest()

    def submit(self, data):
        with self.transaction() as con:
            row = self._attempt(con, data['attempt_id'])
            question = json.loads(row['snapshot'])
            answer = data['answer']
            try:
                if not answer.strip():
                    raise ValueError('Empty answer')
                parse_answer(question, answer)
            except (ValueError, TypeError, KeyError):
                raise DomainError('invalid_answer', '请完整填写此题的答案。', 422)
            fp = self.submission_fingerprint(data)
            old = con.execute('SELECT * FROM training_submissions WHERE id=?', (data['request_id'],)).fetchone()
            if old:
                if old['learner'] != self.learner_id or old['fingerprint'] != fp:
                    raise DomainError('request_conflict', '请求标识已用于另一项作答。', 409)
                if old['status'] != 'error':
                    return old['id']
            if row['phase'] != 'answer' or row['submission_id'] and row['submission_id'] != data['request_id']:
                raise DomainError('training_already_answered', '本次作答已提交；需要重做时请开始新一次训练。', 409)
            con.execute('''INSERT INTO training_submissions VALUES (?,?,?,?,?,?,'pending',NULL)
                ON CONFLICT(id) DO UPDATE SET status='pending',reason=NULL''',
                (data['request_id'], self.learner_id, row['id'], fp, answer, data.get('elapsed_ms')))
            con.execute('UPDATE training_attempts SET submission_id=? WHERE id=?', (data['request_id'], row['id']))
        return data['request_id']

    def submission_attempt(self, submission_id):
        with closing(self._connect()) as con:
            row = con.execute('SELECT attempt FROM training_submissions WHERE id=? AND learner=?',
                              (submission_id, self.learner_id)).fetchone()
        if not row:
            raise DomainError('unknown_submission', '未找到此提交记录。', 404)
        return row[0]

    def _complete(self, con, row, *, correct=None, reason='', rating=None, elapsed_ms=None, stop=False):
        memory = self._memory(con, row['bank'], row['question'], row['version'])
        revision = memory['revision'] if memory else 0
        now = utc_time(self.clock())
        if stop:
            con.execute('''INSERT INTO training_suspensions VALUES (?,?,?,?,?)
                ON CONFLICT(learner,bank,question,version) DO NOTHING''',
                (self.learner_id, row['bank'], row['question'], row['version'], now.isoformat()))
        stopped = self._stopped(con, row['bank'], row['question'], row['version'])
        eligible = not stopped and row['mode'] != 'practice' and row['memory_revision'] == revision
        scheduled, fsrs, mastery = False, None, None
        due = memory['due_at'] if memory else None
        if eligible:
            mastery = rating if rating is not None else 1 if not correct else 3 if row['mode'] == 'review' else None
            mastered = bool(memory and memory['mastered'] or (rating > 1 if rating is not None else correct))
            if mastery is not None:
                fsrs = self.fsrs.schedule(memory['card_json'] if memory else None, mastery, now, elapsed_ms)
                due = fsrs['due_at']
            else:
                # Same initial correct behavior as atomic retrieval: no rating,
                # first review six hours later, preserving previous wrong-answer state.
                due = (now + timedelta(hours=6)).isoformat()
            card_json = fsrs['card_json'] if fsrs else memory['card_json'] if memory else None
            con.execute('''INSERT INTO training_memory VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(learner,bank,question,version) DO UPDATE SET mastered=excluded.mastered,
                revision=excluded.revision,card_json=excluded.card_json,due_at=excluded.due_at''',
                (self.learner_id, row['bank'], row['question'], row['version'], int(mastered), revision + 1, card_json, due))
            scheduled = True
        result = {'correct': correct, 'reason': reason, 'rating': mastery,
                  'self_rating': rating, 'scheduled': scheduled, 'due_at': None if stopped else due,
                  'stopped': stopped, 'stop_requested': stop,
                  'practice_only': not eligible}
        con.execute('''INSERT INTO training_ratings VALUES (?,?,?,?,?,?,?,?)''',
                    (row['id'], self.learner_id, row['bank'], row['question'], row['version'],
                     mastery, now.isoformat(), packed(fsrs) if fsrs else None))
        con.execute("UPDATE training_attempts SET phase='done',completed_at=?,result=? WHERE id=?",
                    (now.isoformat(), packed(result), row['id']))

    def process_submission(self, submission_id):
        with self.transaction() as con:
            submission = con.execute("SELECT * FROM training_submissions WHERE id=? AND learner=? AND status='pending'",
                                     (submission_id, self.learner_id)).fetchone()
            if not submission:
                return
            row = self._attempt(con, submission['attempt'])
            con.execute("UPDATE training_submissions SET status='running' WHERE id=?", (submission_id,))
            question = json.loads(row['snapshot'])
        correct, reason = judge_answer(self.judge, question, submission['answer'])
        try:
            with self.transaction() as con:
                current = con.execute('SELECT status FROM training_submissions WHERE id=?', (submission_id,)).fetchone()
                if not current or current[0] != 'running':
                    return
                row = self._attempt(con, submission['attempt'])
                if correct is None:
                    con.execute("UPDATE training_submissions SET status='error',reason=? WHERE id=?", (reason, submission_id))
                else:
                    self._complete(con, row, correct=correct, reason=reason, elapsed_ms=submission['elapsed_ms'])
                    con.execute("UPDATE training_submissions SET status='judged',reason=? WHERE id=?", (reason, submission_id))
        except Exception:
            with self.transaction() as con:
                con.execute("UPDATE training_submissions SET status='error',reason=? WHERE id=? AND status='running'",
                            ('作答结果暂未保存，请重试；本次未计入作答。', submission_id))

    def reveal(self, attempt_id):
        with self.transaction() as con:
            row = self._attempt(con, attempt_id)
            if json.loads(row['snapshot']).get('interaction', {}).get('type', 'text') != 'text':
                raise DomainError('self_rating_forbidden', '只有问答题支持高阶学习者作答。', 403)
            if row['submission_id']:
                raise DomainError('training_already_answered', '本次作答已提交。', 409)
            if row['phase'] == 'answer':
                con.execute("UPDATE training_attempts SET phase='shown' WHERE id=?", (attempt_id,))

    def rate(self, attempt_id, rating, elapsed_ms=None):
        with self.transaction() as con:
            row = self._attempt(con, attempt_id)
            if row['phase'] == 'done':
                if json.loads(row['result']).get('self_rating') != rating:
                    raise DomainError('rating_conflict', '本次作答已记录另一项评分。', 409)
                return
            if row['phase'] != 'shown' or type(rating) is not int or rating not in (1, 2, 3, 4):
                raise DomainError('invalid_rating', '请先显示答案，再选择熟练度。', 422)
            self._complete(con, row, rating=rating, elapsed_ms=elapsed_ms)

    def stop_reviews(self, attempt_id):
        with self.transaction() as con:
            row = self._attempt(con, attempt_id)
            if row['phase'] == 'done':
                if not json.loads(row['result']).get('stop_requested'):
                    raise DomainError('rating_conflict', '本次作答已记录另一项评分。', 409)
                return
            if row['phase'] != 'shown':
                raise DomainError('invalid_rating', '请先显示答案，再选择熟练度。', 422)
            self._complete(con, row, stop=True)

    def bank(self, bank_id, *, history=True):
        bank = self.catalog.bank(bank_id)
        with closing(self._connect()) as con:
            counts = {row['group_id']: row['count'] for row in con.execute('''
                SELECT group_id,COUNT(DISTINCT question) AS count FROM training_attempts
                WHERE learner=? AND bank=? AND version=? AND phase='done' GROUP BY group_id''',
                (self.learner_id, bank_id, bank['version']))}
        return {**{k: bank.get(k, '') for k in ('id', 'title', 'subject_id', 'description', 'version')},
                'groups': [{'id': g['id'], 'title': g['title'], 'parent_id': g.get('parent_id'),
                            'question_count': len(g.get('questions', [])),
                            'completed_count': counts.get(g['id'], 0) if history else None}
                           for g in bank['groups']]}

    def group(self, bank_id, group_id, *, history=True):
        bank = self.catalog.bank(bank_id)
        group = next((g for g in bank['groups'] if g['id'] == group_id), None)
        if group is None:
            raise DomainError('unknown_group', '未找到此题组。', 404)
        with closing(self._connect()) as con:
            rows = con.execute('''SELECT question,COUNT(*) AS count,MAX(completed_at) AS completed_at
                FROM training_attempts WHERE learner=? AND bank=? AND group_id=? AND version=? AND phase='done'
                GROUP BY question''', (self.learner_id, bank_id, group_id, bank['version'])).fetchall()
            recent = {q['id']: con.execute('''SELECT id,mode,phase FROM training_attempts WHERE learner=? AND bank=?
                AND question=? AND version=? ORDER BY created_at DESC,rowid DESC LIMIT 1''',
                (self.learner_id, bank_id, q['id'], bank['version'])).fetchone() for q in group.get('questions', [])}
        counts = {r['question']: r['count'] for r in rows}
        return {'id': group_id, 'title': group['title'], 'bank_id': bank_id, 'bank_title': bank['title'],
                'version': bank['version'], 'math_css': bank.get('math_css', ''),
                'questions': [{'id': q['id'], 'title': q.get('title', q['id']),
                               'completed_count': counts.get(q['id'], 0) if history else None,
                               'latest_attempt_id': recent[q['id']]['id'] if recent[q['id']] else None,
                               'latest_attempt_mode': recent[q['id']]['mode'] if recent[q['id']] else None,
                               'latest_attempt_phase': recent[q['id']]['phase'] if recent[q['id']] else None}
                              for q in group.get('questions', [])]}

    def history(self, bank_id, question_id, offset=0):
        with closing(self._connect()) as con:
            rows = con.execute('''SELECT id,created_at,completed_at,result,phase FROM training_attempts
                WHERE learner=? AND bank=? AND question=? ORDER BY created_at DESC,rowid DESC LIMIT 51 OFFSET ?''',
                (self.learner_id, bank_id, question_id, offset)).fetchall()
        return {'items': [{**dict(r), 'result': json.loads(r['result']) if r['result'] else None} for r in rows[:50]],
                'next_offset': offset + 50 if len(rows) > 50 else None}

    def reviews(self, banks, *, all_materials=False):
        now, items, next_due = utc_time(self.clock()), [], None
        if all_materials:
            return {'items': [{'bank_id': b['id'], 'bank_title': b['title'], 'group_id': g['id'],
                               'group_title': g['title'], 'question_id': q['id'], 'title': q.get('title', q['id']),
                               'due_at': None} for b in banks for g in b['groups'] for q in g.get('questions', [])],
                    'next_due_at': None, 'all_materials': True}
        with closing(self._connect()) as con:
            rows = con.execute('''SELECT m.* FROM training_memory m WHERE learner=? AND NOT EXISTS (
                SELECT 1 FROM training_suspensions s WHERE s.learner=m.learner AND s.bank=m.bank
                AND s.question=m.question AND s.version=m.version) ORDER BY due_at,bank,question''',
                               (self.learner_id,)).fetchall()
        allowed = {b['id']: b for b in banks}
        for row in rows:
            bank = allowed.get(row['bank'])
            if not bank or bank['version'] != row['version']:
                continue
            due = utc_time(row['due_at'])
            if due > now:
                next_due = min(next_due, due) if next_due else due
                continue
            for group in bank['groups']:
                q = next((q for q in group.get('questions', []) if q['id'] == row['question']), None)
                if q:
                    items.append({'bank_id': bank['id'], 'bank_title': bank['title'], 'group_id': group['id'],
                                  'group_title': group['title'], 'question_id': q['id'], 'title': q.get('title', q['id']),
                                  'due_at': row['due_at']})
        return {'items': items, 'next_due_at': next_due.isoformat() if next_due else None}

    def recover_interrupted(self):
        with closing(self._connect()) as con:
            con.execute("UPDATE training_submissions SET status='error',reason=? WHERE status IN ('pending','running')",
                        ('服务重新启动，请重试；本次未计入作答。',))
