"""Free-order single-question training over the shared judge and FSRS adapter."""
from contextlib import closing, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from .fsrs_engine import FSRSEngine, utc_time
from .judging import judge_answer
from .question_types import parse_answer, public_interaction, display_answer, present_choice, is_reproduction, demo_answer
from .service import DomainError
from .learning_storage import administrator
from .review_cards import register_point, require_unreserved
from .reference_answers import reference_fields
from .dependency_nodes import group_key
from .linked_points import bypass_mastery


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class TrainingService:
    submission_table = 'training_submissions'

    def __init__(self, catalog, data_dir, learner_id, judge=None, *, runtime=None, clock=None, dependencies=None, access=None, atomic_for=None):
        self.catalog, self.learner_id, self.judge = catalog, learner_id, judge
        self.db_path, self.runtime = Path(data_dir) / 'learning.sqlite3', runtime
        self.dependencies, self.access = dependencies, access
        self.atomic_for, self.atoms = atomic_for, {}
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
                CREATE TABLE IF NOT EXISTS training_external_rounds (
                    attempt TEXT PRIMARY KEY, body TEXT NOT NULL);
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
        bank, group, question = self.catalog.question(bank_id, group_id, question_id)
        self.require_group(bank_id, group_id)
        self._bind_points(question)
        with self.transaction() as con:
            previous = con.execute('SELECT * FROM training_attempts WHERE id=?', (request_id,)).fetchone()
            if previous:
                if (previous['learner'], previous['bank'], previous['group_id'], previous['question']) != (
                        self.learner_id, bank_id, group_id, question_id):
                    raise DomainError('request_conflict', '请求标识已用于另一项操作。', 409)
                return request_id
            self.require_group(bank_id, group_id, con=con)
            memory = self._memory(con, bank_id, question_id, bank['version'])
            require_unreserved(con, self.learner_id, 'training', bank_id, bank['version'], question_id, now=utc_time(self.clock()).isoformat())
            now = utc_time(self.clock())
            enrolled = bool(memory and memory['revision'] == 0 and memory['card_json'] is None)
            mode = ('practice' if self._stopped(con, bank_id, question_id, bank['version']) else
                    'review' if enrolled else
                    'initial' if not memory or not memory['mastered'] else
                    'review' if utc_time(memory['due_at']) <= now else 'practice')
            if question.get('point_ids'):
                from .linked_points import require_available, pinned
                rows = self._point_rows(con, question)
                require_available(con, rows, self.learner_id, now.isoformat(),
                                  allow_unlearned=bypass_mastery(self.access))
                prior = con.execute("SELECT 1 FROM training_attempts WHERE learner=? AND bank=? AND question=? AND version=? AND phase='done'",
                                    (self.learner_id, bank_id, question_id, bank['version'])).fetchone()
                due = all(r['memory'].get('due_at') and utc_time(r['memory']['due_at']) <= now for r in rows)
                mode = ('initial' if any(not r['learned'] and not r['memory'].get('stopped_at') for r in rows)
                        else 'review' if due or group.get('unlock_policy') and not prior else 'practice')
                question['_linked_points'] = pinned(rows)
            question = present_choice(question, request_id)
            con.execute('''INSERT INTO training_attempts
                (id,learner,bank,group_id,question,version,snapshot,mode,memory_revision,phase,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,'answer',?)''',
                (request_id, self.learner_id, bank_id, group_id, question_id, bank['version'], packed(question),
                 mode, memory['revision'] if memory else 0, now.isoformat()))
        return request_id

    def _point_rows(self, con, question):
        from .linked_points import snapshot
        if self.dependencies is None:
            raise DomainError('linked_points_unavailable', '关联知识目录不可用。', 409)
        return snapshot(con, self.dependencies.catalog, self.learner_id, question['point_ids'], self.access)

    def _bind_points(self, question):
        """Initialize service adapters before entering the grading transaction."""
        from .linked_points import split_reference
        from .topic_structure import atomic_backend
        for ref in question.get('point_ids', []):
            tid, _ = split_reference(ref)
            if self.access is not None and tid not in {t['id'] for t in self.access['topics']}:
                raise DomainError('feature_forbidden', '当前账号无权访问关联知识。', 403)
            if tid not in self.atoms:
                if self.atomic_for is None:
                    raise DomainError('linked_points_unavailable', '关联知识服务不可用。', 409)
                self.atoms[tid] = atomic_backend(self.atomic_for(tid))

    def _complete_linked(self, con, row, question, correct, reason, elapsed_ms, stop=False, self_rating=None):
        from .linked_points import unchanged, require_available, split_reference
        from .atomic_review import apply_review
        from .point_assessment import assess_points
        current = utc_time(self.clock()).isoformat()
        rows = self._point_rows(con, question)
        allow_unlearned = bypass_mastery(self.access)
        valid = unchanged(rows, question['_linked_points'], allow_unlearned=allow_unlearned)
        eligible = valid and (row['mode'] != 'practice' or stop)
        submission = con.execute('SELECT answer FROM training_submissions WHERE id=?', (row['submission_id'],)).fetchone()
        answer = submission['answer'] if submission else ''
        ratings, dates = {}, []
        if eligible:
            require_available(con, rows, self.learner_id, current, question.get('_review_paper'),
                              allow_unlearned=allow_unlearned)
            for tid in dict.fromkeys(r['topic_id'] for r in rows):
                own = [r for r in rows if r['topic_id'] == tid]
                if row['mode'] == 'initial' and not stop:
                    own = [r for r in own if not r['learned'] or r['memory'].get('due_at')
                           and utc_time(r['memory']['due_at']) <= utc_time(current)]
                if not own:
                    continue
                projected = deepcopy(question)
                projected['id'] = f"bank:{row['bank']}:{question['id']}"
                projected['training_source'] = {'bank_id': row['bank'], 'group_id': row['group_id'],
                    'question_id': row['question'], 'version': row['version']}
                projected['point_ids'] = [r['local_id'] for r in own]
                if 'assessment' in projected:
                    mapping = {key: [split_reference(p)[1] for p in values if p in {r['reference'] for r in own}]
                               for key, values in question['assessment']['item_points'].items()}
                    mapping = {key: values for key, values in mapping.items() if values}
                    if mapping:
                        projected['assessment']['item_points'] = mapping
                    else:
                        projected.pop('assessment')
                result = apply_review(con, self.atoms[tid], projected, projected['point_ids'], row['id'],
                    answer, correct, reason, row['created_at'], elapsed_ms, 5 if stop else self_rating,
                    learning_points=[r['local_id'] for r in own if not r['learned']])
                ratings.update({tid + '/' + p: rating for p, rating in result['ratings'].items()})
                if result['due_at']:
                    dates.append(result['due_at'])
        _, details = assess_points(question, answer, correct, self_assessed=stop or self_rating is not None)
        if details:
            labels = {r['reference']: r['text'] for r in rows}
            for point in details['points']:
                point['text'] = labels[point['id']]
        result = {'correct': correct, 'reason': reason, 'rating': None, 'ratings': ratings, 'self_rating': self_rating,
            'scheduled': eligible and not stop, 'practice_only': not eligible,
            'due_at': min(dates, default=None), 'stopped': eligible and stop,
            'stop_requested': stop, 'assessment_result': details,
            'state_changed': not valid}
        con.execute('INSERT INTO training_ratings VALUES (?,?,?,?,?,?,?,?)',
            (row['id'], self.learner_id, row['bank'], row['question'], row['version'], None, current, None))
        con.execute("UPDATE training_attempts SET phase='done',completed_at=?,result=? WHERE id=?",
                    (current, packed(result), row['id']))
        return result

    def add_to_notebook(self, attempt_id):
        """Enroll a seen question as due, without inventing an answer or FSRS card."""
        with self.transaction() as con:
            row = self._attempt(con, attempt_id)
            if json.loads(row['snapshot']).get('point_ids'):
                raise DomainError('linked_points_scheduled', '此题按关联记忆点安排复习。', 409)
            if self._stopped(con, row['bank'], row['question'], row['version']):
                raise DomainError('training_reviews_stopped', '此题已停止后续推送。', 409)
            if self._memory(con, row['bank'], row['question'], row['version']) is None:
                now = utc_time(self.clock()).isoformat()
                require_unreserved(con, self.learner_id, 'training', row['bank'], row['version'],
                                   row['question'], now=now)
                # Revision zero matches an unanswered attempt. The first valid answer
                # goes through the existing review policy, scheduling from card=None.
                con.execute('INSERT INTO training_memory VALUES (?,?,?,?,0,0,NULL,?)',
                            (self.learner_id, row['bank'], row['question'], row['version'], now))
                bank = self.catalog.bank(row['bank'])
                register_point(con, 'training', row['bank'], row['version'], row['question'],
                               bank['subject_id'], now)

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
            if question.get('hyperskill'):
                from .hyperskill_answers import enrich
                question = enrich(self, row['bank'], question)
            media = getattr(self.catalog, 'formats', {}).get(row['bank'], {}).get('media')
            if media:
                from .training_presentation import present_book_question
                question = present_book_question(question, media)
            submission = con.execute('SELECT status,reason,answer FROM training_submissions WHERE id=?',
                                     (row['submission_id'],)).fetchone()
            result = json.loads(row['result']) if row['result'] else None
            memory = self._memory(con, row['bank'], row['question'], row['version'])
            stopped = self._stopped(con, row['bank'], row['question'], row['version'])
            linked = self._point_rows(con, question) if question.get('point_ids') else []
            if linked:
                stopped = all(r['memory'].get('stopped_at') for r in linked)
            external = con.execute('SELECT body FROM training_external_rounds WHERE attempt=?', (attempt_id,)).fetchone()
            run = json.loads(external['body']) if external else None
        revealed = row['phase'] in ('shown', 'done')
        return {'attempt_id': row['id'], 'bank_id': row['bank'], 'group_id': row['group_id'],
                'question_id': row['question'], 'version': row['version'], 'phase': row['phase'],
                'mode': row['mode'], 'created_at': row['created_at'], 'completed_at': row['completed_at'],
                'question': {'id': question['id'], 'title': question.get('title', question['id']),
                             'html': question['html'], 'interaction': public_interaction(question)},
                'explanation_html': question['explanation_html'] if revealed else None,
                **reference_fields(question, revealed),
                'can_self_rate': self_rating and is_reproduction(question) and not (question.get('academy') or question.get('hyperskill')),
                **({'academy': {**question['academy'], 'round_id': run['id'] if run else None,
                               'checks': len(run['events']) if run else 0,
                               'opened': bool(run and run.get('opened_at'))}} if question.get('academy') else {}),
                **self._hyperskill_state(question, run),
                'submission_id': row['submission_id'], 'submission_status': submission['status'] if submission else None,
                'answer': submission['answer'] if submission else '',
                'answer_display': display_answer(question, submission['answer']) if submission else '',
                'error': submission['reason'] if submission and submission['status'] == 'error' else None,
                'result': result, 'stopped': stopped, 'linked_points': bool(question.get('point_ids')),
                'can_review_early': memory is not None or stopped or bool(linked and all(r['learned'] for r in linked)),
                'in_error_notebook': memory is not None and not stopped,
                'awaiting_first_learning': bool(memory and memory['card_json'] is None and not stopped),
                'due_at': (min((r['memory']['due_at'] for r in linked if r['memory'].get('due_at')), default=None)
                           if linked and not stopped else memory['due_at'] if memory and not stopped else None)}

    def _hyperskill_state(self, question, run):
        if not question.get('hyperskill'):
            return {}
        from .hyperskill_training import public_binding
        return {'hyperskill': public_binding(question, run, self)}

    @staticmethod
    def submission_fingerprint(data):
        return hashlib.sha256(packed({k: data.get(k) for k in ('attempt_id', 'answer', 'elapsed_ms')}).encode()).hexdigest()

    def submit(self, data):
        with self.transaction() as con:
            row = self._attempt(con, data['attempt_id'])
            if row['phase'] != 'done':
                require_unreserved(con, self.learner_id, 'training', row['bank'], row['version'], row['question'], now=utc_time(self.clock()).isoformat())
            question = json.loads(row['snapshot'])
            if question.get('point_ids') and row['phase'] != 'done':
                self.require_group(row['bank'], row['group_id'], con=con)
                from .linked_points import require_available
                require_available(con, self._point_rows(con, question), self.learner_id, utc_time(self.clock()).isoformat(),
                                  allow_unlearned=bypass_mastery(self.access))
            if question.get('hyperskill'):
                raise DomainError('hyperskill_check_required', '请连接 Hyperskill 后提交此题。', 409)
            if question.get('academy'):
                raise DomainError('academy_check_required', '请在 IDEA 中检查此题。', 409)
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

    def _complete(self, con, row, *, correct=None, reason='', rating=None, elapsed_ms=None, stop=False,
                  self_rating=True, completed_at=None, enroll=False, first_try_correct=False):
        question = json.loads(row['snapshot'])
        if question.get('point_ids'):
            return self._complete_linked(con, row, question, correct if rating is None else rating > 1,
                                         reason, elapsed_ms, stop, rating)
        memory = self._memory(con, row['bank'], row['question'], row['version'])
        revision = memory['revision'] if memory else 0
        now = utc_time(completed_at or self.clock())
        if stop:
            con.execute('''INSERT INTO training_suspensions VALUES (?,?,?,?,?)
                ON CONFLICT(learner,bank,question,version) DO NOTHING''',
                (self.learner_id, row['bank'], row['question'], row['version'], now.isoformat()))
        stopped = self._stopped(con, row['bank'], row['question'], row['version'])
        register_point(con, 'training', row['bank'], row['version'], row['question'],
                       self.catalog.bank(row['bank'])['subject_id'], now.isoformat())
        eligible = not stopped and row['mode'] != 'practice' and row['memory_revision'] == revision
        scheduled, fsrs, mastery = False, None, None
        due = memory['due_at'] if memory else None
        if eligible:
            mastery = rating if rating is not None else 1 if not correct else 5 if memory is None else 3
            # External graders supply a feedback rating, but admission to review
            # follows the same first-answer policy as a locally judged question.
            # An explicit notebook enrollment already has a memory row.
            if first_try_correct and correct and memory is None:
                mastery = 5
                reason += ' · 首次答对，未加入错题本，不安排后续复习。'
            # A manually enrolled due card follows review semantics even on its
            # first wrong answer; later attempts must respect its new due date.
            mastered = bool(enroll or row['mode'] == 'review' or memory and memory['mastered']
                            or (memory and memory['revision'] == 0 and memory['card_json'] is None)
                            or (rating > 1 if rating is not None else correct))
            if mastery == 5:
                # FSRS accepts 1-4; use the persistent suspension for score 5.
                con.execute('INSERT INTO training_suspensions VALUES (?,?,?,?,?)',
                            (self.learner_id, row['bank'], row['question'], row['version'], now.isoformat()))
                stopped = True
                due = now.isoformat()
            else:
                fsrs = self.fsrs.schedule(memory['card_json'] if memory else None, mastery, now, elapsed_ms)
                due = fsrs['due_at']
            card_json = fsrs['card_json'] if fsrs else memory['card_json'] if memory else None
            con.execute('''INSERT INTO training_memory VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(learner,bank,question,version) DO UPDATE SET mastered=excluded.mastered,
                revision=excluded.revision,card_json=excluded.card_json,due_at=excluded.due_at''',
                (self.learner_id, row['bank'], row['question'], row['version'], int(mastered), revision + 1, card_json, due))
            scheduled = not stopped
        result = {'correct': correct, 'reason': reason, 'rating': mastery,
                  'self_rating': rating if self_rating else None, 'scheduled': scheduled, 'due_at': None if stopped else due,
                  'stopped': stopped, 'stop_requested': stop,
                  'practice_only': not eligible}
        con.execute('''INSERT INTO training_ratings VALUES (?,?,?,?,?,?,?,?)''',
                    (row['id'], self.learner_id, row['bank'], row['question'], row['version'],
                     mastery, now.isoformat(), packed(fsrs) if fsrs else None))
        con.execute("UPDATE training_attempts SET phase='done',completed_at=?,result=? WHERE id=?",
                    (now.isoformat(), packed(result), row['id']))
        return result

    def instant_submission(self, submission_id):
        from .judging import instant_question
        with closing(self._connect()) as con:
            row = con.execute('SELECT attempt FROM training_submissions WHERE id=? AND learner=?',
                              (submission_id, self.learner_id)).fetchone()
            return bool(row and instant_question(json.loads(self._attempt(con, row['attempt'])['snapshot'])))

    def process_submission(self, submission_id):
        with closing(self._connect()) as con:
            pending = con.execute("SELECT attempt FROM training_submissions WHERE id=? AND learner=? AND status='pending'",
                                  (submission_id, self.learner_id)).fetchone()
            if not pending:
                return
            question = json.loads(self._attempt(con, pending['attempt'])['snapshot'])
        self._bind_points(question)
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
            if json.loads(row['snapshot']).get('hyperskill'):
                raise DomainError('hyperskill_check_required', '本题由 Hyperskill 判题。', 409)
            if json.loads(row['snapshot']).get('academy'):
                raise DomainError('academy_check_required', '请在 IDEA 中完成本题后记录结果。', 409)
            if not is_reproduction(json.loads(row['snapshot'])):
                raise DomainError('self_rating_forbidden', '只有复现题支持高阶学习者自主评分。', 403)
            if row['submission_id']:
                raise DomainError('training_already_answered', '本次作答已提交。', 409)
            if row['phase'] == 'answer':
                con.execute("UPDATE training_attempts SET phase='shown' WHERE id=?", (attempt_id,))

    def rate(self, attempt_id, rating, elapsed_ms=None):
        with closing(self._connect()) as con:
            self._bind_points(json.loads(self._attempt(con, attempt_id)['snapshot']))
        with self.transaction() as con:
            row = self._attempt(con, attempt_id)
            if row['phase'] == 'done':
                if json.loads(row['result']).get('self_rating') != rating:
                    raise DomainError('rating_conflict', '本次作答已记录另一项评分。', 409)
                return
            if row['phase'] != 'shown' or type(rating) is not int or rating not in (1, 2, 3, 4):
                raise DomainError('invalid_rating', '请先显示答案，再选择熟练度。', 422)
            require_unreserved(con, self.learner_id, 'training', row['bank'], row['version'], row['question'], now=utc_time(self.clock()).isoformat())
            self._complete(con, row, rating=rating, elapsed_ms=elapsed_ms)

    def stop_reviews(self, attempt_id):
        with closing(self._connect()) as con:
            self._bind_points(json.loads(self._attempt(con, attempt_id)['snapshot']))
        with self.transaction() as con:
            row = self._attempt(con, attempt_id)
            if row['phase'] == 'done':
                if not json.loads(row['result']).get('stop_requested'):
                    raise DomainError('rating_conflict', '本次作答已记录另一项评分。', 409)
                return
            if row['phase'] != 'shown':
                raise DomainError('invalid_rating', '请先显示答案，再选择熟练度。', 422)
            require_unreserved(con, self.learner_id, 'training', row['bank'], row['version'], row['question'], now=utc_time(self.clock()).isoformat())
            self._complete(con, row, stop=True)

    def group_status(self, con, bank_id, group_id):
        return (self.dependencies.group_status(con, self.access, bank_id, group_id, utc_time(self.clock()).isoformat())
                if self.dependencies else {})

    def require_group(self, bank_id, group_id, *, con=None):
        if not self.dependencies:
            return
        if con is None:
            with closing(self._connect()) as connection:
                return self.require_group(bank_id, group_id, con=connection)
        if not self.dependencies.ready(con, self.access, group_key(bank_id, group_id), current_time=utc_time(self.clock()).isoformat()):
            raise DomainError('prerequisites_not_ready', '请先完成前置知识的学习和待复习内容，并解除前置知识的暂停状态。', 409)

    def bank(self, bank_id, *, history=True, root_id=None):
        bank = self.catalog.bank(bank_id)
        from .course_hierarchy import select_subtree
        groups = select_subtree(bank['groups'], root_id)
        with closing(self._connect()) as con:
            # Initial completion is permanent for this content version. Later
            # reviews keep their own queue and must not move this history date.
            first = {(row['group_id'], row['question']): row['completed_at'] for row in con.execute('''
                SELECT group_id,question,MIN(completed_at) AS completed_at FROM training_attempts
                WHERE learner=? AND bank=? AND version=? AND phase='done' GROUP BY group_id,question''',
                (self.learner_id, bank_id, bank['version']))}
            statuses = {g['id']: self.group_status(con, bank_id, g['id']) for g in bank['groups']}
        progress = {}
        for group in bank['groups']:
            questions = group.get('questions', [])
            dates = [first[(group['id'], q['id'])] for q in questions if (group['id'], q['id']) in first]
            completed = bool(questions) and len(dates) == len(questions)
            progress[group['id']] = {'completed': completed,
                'completed_count': len(dates) if history else None,
                'completed_at': max(filter(None, dates), default=None) if completed and history else None}
        return {**{k: bank.get(k, '') for k in ('id', 'title', 'subject_id', 'description', 'version')},
                'root_id': root_id,
                'title': next(g['title'] for g in groups if g['id'] == root_id) if root_id else bank['title'],
                'groups': [{'id': g['id'], 'title': g['title'], 'parent_id': g.get('parent_id'),
                            'question_count': len(g.get('questions', [])),
                            **({'introduction_count': len(g['introductions'])} if 'introductions' in g else {}),
                            **progress[g['id']],
                            **statuses[g['id']]}
                           for g in groups]}

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
            status = self.group_status(con, bank_id, group_id)
        counts = {r['question']: r['count'] for r in rows}
        return {'id': group_id, 'title': group['title'], 'bank_id': bank_id, 'bank_title': bank['title'],
                'version': bank['version'], 'math_css': bank.get('math_css', ''), **status,
                'external_providers': sorted({p for q in group.get('questions', [])
                                              for p in ('hyperskill', 'academy') if q.get(p)}),
                **({ 'introductions': group['introductions'], 'content_order': group['content_order']}
                   if 'introductions' in group else {}),
                'questions': [{'id': q['id'], 'title': q.get('title', q['id']),
                               'completed': bool(counts.get(q['id'], 0)),
                               'completed_count': counts.get(q['id'], 0) if history else None,
                               'latest_attempt_id': recent[q['id']]['id'] if recent[q['id']] else None,
                               'latest_attempt_mode': recent[q['id']]['mode'] if recent[q['id']] else None,
                               'latest_attempt_phase': recent[q['id']]['phase'] if recent[q['id']] else None}
                              for q in group.get('questions', [])]}

    def reader(self, bank_id, group_id, *, history=True, self_rating=False):
        """Read the whole group without opening attempts or changing learning data.

        Unseen questions carry a stable presentation id. The first explicit
        learning action opens that id, preserving shuffled option order.
        """
        result = self.group(bank_id, group_id, history=history)
        bank = self.catalog.bank(bank_id)
        source = next(g for g in bank['groups'] if g['id'] == group_id)
        questions = {q['id']: q for q in source.get('questions', [])}
        states, histories = {}, {}
        for item in result['questions']:
            qid = item['id']
            if item['latest_attempt_id']:
                value = self.state(item['latest_attempt_id'], history=history, self_rating=self_rating)
            else:
                ident = str(uuid5(NAMESPACE_URL, packed([self.learner_id, bank_id, bank['version'], qid])))
                question = present_choice(questions[qid], ident)
                media = getattr(self.catalog, 'formats', {}).get(bank_id, {}).get('media')
                if media:
                    from .training_presentation import present_book_question
                    question = present_book_question(question, media)
                value = {'attempt_id': ident, 'preview': True, 'bank_id': bank_id, 'group_id': group_id,
                         'linked_points': bool(question.get('point_ids')),
                         'question_id': qid, 'version': bank['version'], 'phase': 'answer',
                         'question': {'id': qid, 'title': question.get('title', qid), 'html': question['html'],
                                      'interaction': public_interaction(question)},
                         'answer': '', 'answer_display': '', 'explanation_html': None,
                         'can_self_rate': self_rating and is_reproduction(question)
                                          and not (question.get('academy') or question.get('hyperskill')),
                         **reference_fields(question, False),
                         **({'academy': {**question['academy'], 'round_id': None, 'checks': 0, 'opened': False}}
                            if question.get('academy') else {}),
                         **self._hyperskill_state(question, None)}
            states[qid] = value
            if history:
                histories[qid] = self.history(bank_id, qid)
        return {**result, 'reader_states': states, 'reader_histories': histories}

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
        with closing(self._connect()) as con:
            ready = {(b['id'], g['id']): self.group_status(con, b['id'], g['id']).get('dependency_ready', True)
                     for b in banks for g in b['groups']}
        for row in rows:
            bank = allowed.get(row['bank'])
            if not bank or bank['version'] != row['version']:
                continue
            if not any(q['id'] == row['question'] for g in bank['groups'] for q in g.get('questions', [])):
                continue
            due = utc_time(row['due_at'])
            if due > now:
                next_due = min(next_due, due) if next_due else due
                continue
            for group in bank['groups']:
                if not ready[(bank['id'], group['id'])]:
                    continue
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
