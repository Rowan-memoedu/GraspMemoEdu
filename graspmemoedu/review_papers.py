"""Resumable review papers over existing point-level schedules."""
from contextlib import closing
from datetime import timedelta
import json
import sqlite3

from .atomic_content import is_atomic
from .fsrs_engine import utc_time
from .judging import judge_answer
from .question_types import parse_answer, public_interaction, display_answer
from .review_cards import initialize_points, assemble, eligible_presentations
from .review_sources import ReviewSources, fingerprint
from .service import DomainError
from .training import packed
from .training_content import permission_id
from .content_timing import active_limit, limit_state, update_activity
from .review_hierarchy import source_trees, public_source
from .self_assessment import enabled as self_rating_enabled, presentation, reveal as reveal_question
from .reference_answers import reference_fields


class PaperService:
    submission_table = 'paper_submissions'

    def __init__(self, training, catalog, dependencies, access, templates, service_for, *, refresh_access=None):
        self.training, self.runtime, self.clock = training, training.runtime, training.clock
        self._connect, self.transaction = training._connect, training.transaction
        self.learner_id, self.templates, self.access = access['learner_id'], templates, access
        self.refresh_access = refresh_access
        self.catalog = catalog
        with closing(self._connect()) as con:
            initialize_points(con)
            con.executescript('''
                CREATE TABLE IF NOT EXISTS review_papers (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, subject TEXT NOT NULL,
                    candidate TEXT NOT NULL, title TEXT NOT NULL, mode TEXT NOT NULL,
                    phase TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, items TEXT NOT NULL, job TEXT);
                CREATE INDEX IF NOT EXISTS review_paper_owner ON review_papers(learner,subject,created_at);
                CREATE TABLE IF NOT EXISTS review_reservations (
                    learner TEXT NOT NULL, point TEXT NOT NULL, paper TEXT NOT NULL,
                    PRIMARY KEY(learner,point));
                CREATE TABLE IF NOT EXISTS review_card_uses (
                    learner TEXT NOT NULL, card TEXT NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(learner,card));
                CREATE TABLE IF NOT EXISTS review_paper_preferences (
                    learner TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('paper','question')));
                CREATE TABLE IF NOT EXISTS paper_submissions (
                    id TEXT PRIMARY KEY, learner TEXT NOT NULL, paper TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, status TEXT NOT NULL, items TEXT NOT NULL,
                    verdicts TEXT NOT NULL, reason TEXT NOT NULL, request_data TEXT NOT NULL DEFAULT '{}');
            ''')
            if 'request_data' not in {r[1] for r in con.execute('PRAGMA table_info(paper_submissions)')}:
                con.execute("ALTER TABLE paper_submissions ADD COLUMN request_data TEXT NOT NULL DEFAULT '{}'")
            if 'metadata' not in {r[1] for r in con.execute('PRAGMA table_info(review_papers)')}:
                con.execute("ALTER TABLE review_papers ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            existing = {r[0] for r in con.execute('SELECT topic FROM topics WHERE learner=?', (self.learner_id,))} if 'topics' in tables else set()
        atoms = {tid: service_for(tid) for tid in existing if tid in catalog.topics and is_atomic(catalog.topics[tid])}
        self.sources = ReviewSources(training, catalog, atoms, dependencies, access)

    def _now(self):
        return utc_time(self.clock())

    def _qualified(self):
        return bool(self.access.get('is_admin') or (self.access.get('is_advanced_learner')
                    and 'review_question_mode' in self.access['features']))

    def _mode(self, paper):
        return paper['mode'] if self._qualified() else 'paper'

    def _can_mark(self):
        return bool(self.access.get('is_admin') or self.access.get('is_advanced_learner'))

    def preferences(self, mode=None, timeout_action=None):
        # Preferences persist for administrators, unlike their learning history.
        if mode is not None and (mode not in ('paper', 'question') or (mode == 'question' and not self._qualified())):
            raise DomainError('review_mode_forbidden', '逐题提交仅对高阶学习者开放。', 403)
        if timeout_action is not None and (timeout_action not in ('submit', 'mark') or (timeout_action == 'mark' and not self._can_mark())):
            raise DomainError('review_timeout_forbidden', '超时不中断仅对高阶学习者开放。', 403)
        with closing(sqlite3.connect(self.training.db_path, isolation_level=None)) as con:
            con.execute('''CREATE TABLE IF NOT EXISTS review_paper_preferences (
                learner TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('paper','question')))''')
            if 'timeout_action' not in {r[1] for r in con.execute('PRAGMA table_info(review_paper_preferences)')}:
                con.execute("ALTER TABLE review_paper_preferences ADD COLUMN timeout_action TEXT NOT NULL DEFAULT 'submit'")
            if mode is not None:
                if mode not in ('paper', 'question') or (mode == 'question' and not self._qualified()):
                    raise DomainError('review_mode_forbidden', '逐题提交仅对高阶学习者开放。', 403)
                con.execute('''INSERT INTO review_paper_preferences (learner,mode) VALUES (?,?)
                    ON CONFLICT(learner) DO UPDATE SET mode=excluded.mode''', (self.learner_id, mode))
            if timeout_action is not None:
                if timeout_action not in ('submit', 'mark') or (timeout_action == 'mark' and not self._can_mark()):
                    raise DomainError('review_timeout_forbidden', '超时不中断仅对高阶学习者开放。', 403)
                con.execute('''INSERT INTO review_paper_preferences (learner,mode,timeout_action) VALUES (?,'paper',?)
                    ON CONFLICT(learner) DO UPDATE SET timeout_action=excluded.timeout_action''', (self.learner_id, timeout_action))
            row = con.execute('SELECT mode,timeout_action FROM review_paper_preferences WHERE learner=?', (self.learner_id,)).fetchone()
        return {'mode': row[0] if row and self._qualified() else 'paper', 'can_choose': self._qualified(),
                'timeout_action': row[1] if row and self._can_mark() else 'submit', 'can_choose_timeout': self._can_mark()}

    def _row(self, con, paper_id, subject=None):
        row = con.execute('SELECT * FROM review_papers WHERE id=? AND learner=?', (paper_id, self.learner_id)).fetchone()
        if not row:
            raise DomainError('unknown_review_paper', '未找到这份复习。', 404)
        if subject and row['subject'] != subject:
            raise DomainError('subject_paper_mismatch', '此复习不属于当前学科。', 403)
        paper = dict(row)
        paper['items'] = json.loads(paper['items'])
        paper['metadata'] = json.loads(paper['metadata'])
        if 'review_history' not in self.access['features'] and paper['phase'] not in ('answer', 'grading'):
            latest = con.execute('''SELECT id FROM review_papers WHERE learner=? AND subject=?
                ORDER BY created_at DESC,rowid DESC LIMIT 1''', (self.learner_id, paper['subject'])).fetchone()
            if latest and latest[0] != paper_id:
                raise DomainError('feature_forbidden', '当前账号没有查看历史记录的权限。', 403)
        if not self.sources.authorized(paper['items']):
            raise DomainError('feature_forbidden', '当前账号没有访问这份复习的权限。', 403)
        return paper

    def _expire(self, con):
        con.execute("UPDATE review_papers SET phase='expired',revision=revision+1 WHERE learner=? AND phase='answer' AND expires_at<? AND json_extract(metadata, '$.timing.limit_minutes') IS NULL",
                    (self.learner_id, self._now().isoformat()))
        con.execute("DELETE FROM review_reservations WHERE learner=? AND paper IN (SELECT id FROM review_papers WHERE phase IN ('expired','cancelled','conflict','done'))", (self.learner_id,))

    def _choices(self, con, subject):
        cards, next_due = self.sources.inventory(con, subject, self._now())
        by_id, candidates = {}, []
        occupied_numbers = {}
        for row in con.execute("SELECT metadata FROM review_papers WHERE learner=? AND subject=? AND phase IN ('answer','grading')", (self.learner_id, subject)):
            metadata = json.loads(row[0])
            occupied_numbers.setdefault(metadata.get('paper_type_id'), set()).add(metadata.get('paper_number'))
        for template in self.templates:
            if not template['enabled'] or template['subject'] != subject:
                continue
            remaining, number = cards, 1
            while (chosen := assemble(remaining, template['requirements'])):
                while number in occupied_numbers.get(template['id'], set()):
                    number += 1
                key = fingerprint([template, [(c['id'], c['source']['memory']) for c in chosen]])
                by_id[key] = chosen
                candidates.append({'id': key, 'title': template['title'], 'count': len(chosen),
                                   'point_count': len({p for c in chosen for p in c['point_ids']}),
                                   'kind': 'paper', 'requirements': template['requirements'],
                                   'paper_type_id': template['id'], 'paper_number': number,
                                   'time_limit_minutes': template.get('time_limit_minutes')})
                used = {p for c in chosen for p in c['point_ids']}
                remaining = [c for c in remaining if not used.intersection(c['point_ids'])]
                number += 1
        # Single cards are still available even when a full typed paper cannot be made.
        all_points = {p for c in cards for p in c['point_ids']}
        uses = {c['id']: c['uses'] for c in cards}
        covered = set()
        for card in eligible_presentations(cards, all_points, uses):
            if set(card['point_ids']) & covered:
                continue
            covered.update(card['point_ids'])
            key = fingerprint(['single', card['id'], card['source']['memory']])
            by_id[key] = [card]
            candidates.append({'id': key, 'title': card['question'].get('title') or card['source_title'],
                               'source_title': card['source_title'], 'count': 1,
                               'point_count': len(card['point_ids']), 'kind': 'single', 'source': public_source(card)})
        return candidates, by_id, len(all_points), next_due

    def inbox(self, subject):
        with self.transaction() as con:
            self._expire(con)
            candidates, _, count, next_due = self._choices(con, subject)
            rows = con.execute('''SELECT id,title,mode,phase,created_at,items,metadata FROM review_papers
                WHERE learner=? AND subject=? AND phase IN ('answer','grading') ORDER BY created_at DESC''',
                (self.learner_id, subject)).fetchall()
            active = [{'id': r['id'], 'title': r['title'], 'phase': r['phase'],
                       'done': sum(bool(x.get('result')) for x in json.loads(r['items'])),
                       'count': len(json.loads(r['items'])), **json.loads(r['metadata'])}
                      for r in rows if self.sources.authorized(json.loads(r['items']))]
            frozen = {r['id']: json.loads(r['items']) for r in rows}
            for item in active:
                item['active'] = True
                item.setdefault('kind', 'paper' if item['count'] > 1 else 'single')
                item.setdefault('paper_type_id', 'legacy-paper')
                if item['kind'] == 'single' and 'source' not in item:
                    item['source'] = public_source(frozen[item['id']][0])
            legacy = []
            allowed = {x['id'] for x in self.access['topics']}
            for tid, backend in self.sources.atoms.items():
                if tid not in allowed or backend.course['subject_id'] != subject:
                    continue
                old = backend._load(con)
                if backend.course.get('academy') or backend.course.get('source_adapter') == 'hyperskill':
                    from .atomic_api import review_summary
                    summary = review_summary(backend.course, old, self._now())
                    if not old.get('completed_at'):
                        continue
                    if summary['due_count'] or old.get('active'):
                        legacy.append({'id': 'legacy:' + tid, 'kind': 'single', 'source': {'kind': 'atomic', 'container': tid},
                                       'topic_id': tid, 'title': backend.course['title'], 'count': summary['due_count'] or 1,
                                       'paused': bool(old.get('pause')), 'academy': True,
                                       'can_resume': not old.get('pause') and self.sources.dependencies.ready(con, self.access, tid)})
                        count += summary['due_count']
                    if summary['next_due_at']:
                        next_due = min(filter(None, [next_due, summary['next_due_at']]))
                    continue
                if old.get('pause') or old.get('mode') == 'review' and old.get('active'):
                    legacy.append({'id': 'legacy:' + tid, 'kind': 'single', 'source': {'kind': 'atomic', 'container': tid},
                                   'topic_id': tid, 'title': backend.course['title'], 'paused': bool(old.get('pause')),
                                   'can_resume': not old.get('pause') and self.sources.dependencies.ready(con, self.access, tid)})
        trees = source_trees(self.catalog, self.training.catalog,
                             [e for e in candidates + active if e['kind'] == 'single'] + legacy)
        return {'items': candidates, 'active': active, 'trees': trees, 'due_count': count, 'next_due_at': next_due,
                'legacy': legacy, **self.preferences()}

    def timing(self, paper):
        return limit_state(paper['metadata'].get('timing'), self._now(), can_mark=self._can_mark(),
                           finished_at=paper['metadata'].get('submitted_at') or paper['metadata'].get('ended_at'))

    def _hard_expired(self, paper):
        timing = self.timing(paper)
        return bool(timing and timing['timed_out'] and timing['timeout_action'] == 'submit')

    @staticmethod
    def _answered(item):
        if item.get('self_rating') is not None:
            return True
        if item.get('revealed'):
            return False
        try:
            parse_answer(item['question'], item['answer'])
            return True
        except (ValueError, TypeError):
            return False

    def timeout_submission(self, paper_id, subject=None):
        with closing(self._connect()) as con:
            paper = self._row(con, paper_id, subject)
        if paper['phase'] == 'answer' and self._hard_expired(paper):
            return {'paper_id': paper_id, 'request_id': 'timeout-' + fingerprint(paper_id),
                    'expected_revision': paper['revision']}
        return None

    def timed_papers(self, subject):
        with closing(self._connect()) as con:
            ids = [r[0] for r in con.execute("SELECT id FROM review_papers WHERE learner=? AND subject=? AND phase='answer'",
                                           (self.learner_id, subject))]
            expired = []
            for pid in ids:
                try:
                    if self._hard_expired(self._row(con, pid, subject)):
                        expired.append(pid)
                except DomainError as error:
                    if error.code != 'feature_forbidden':
                        raise
            return expired

    def activity(self, paper_id, session_id, active, subject=None):
        with self.transaction() as con:
            paper = self._row(con, paper_id, subject)
            timing = paper['metadata'].get('timing')
            if paper['phase'] != 'answer' or not timing:
                return
            if not update_activity(timing, self._now(), session_id, active):
                if active:
                    raise DomainError('paper_active_elsewhere', '此复习正在另一个页面计时，请先退出该页面。', 409)
                return
            con.execute('UPDATE review_papers SET metadata=? WHERE id=?', (packed(paper['metadata']), paper_id))

    def open(self, candidate, request_id, subject):
        preferences = self.preferences()
        mode = preferences['mode']
        with self.transaction() as con:
            self._expire(con)
            old = con.execute('SELECT id,learner,candidate,subject FROM review_papers WHERE id=?', (request_id,)).fetchone()
            if old:
                if (old['learner'], old['candidate'], old['subject']) != (self.learner_id, candidate, subject):
                    raise DomainError('request_conflict', '请求标识已用于另一项操作。', 409)
                return request_id
            choices, cards, _, _ = self._choices(con, subject)
            if candidate not in cards:
                raise DomainError('review_candidate_changed', '到期内容已变化，请刷新后选择复习。', 409)
            description = next(c for c in choices if c['id'] == candidate)
            items = json.loads(packed(cards[candidate]))
            now = self._now()
            metadata = {key: description[key] for key in ('kind', 'paper_type_id', 'paper_number', 'source') if key in description}
            if description['kind'] == 'paper':
                metadata['timing'] = active_limit(description.get('time_limit_minutes'), now, preferences['timeout_action'])
            self.sources.begin(con, items, subject, request_id, now.isoformat())
            for item in items:
                item.update(answer='', elapsed_ms=0, result=None)
                for point in item['point_ids']:
                    con.execute('INSERT INTO review_reservations VALUES (?,?,?)', (self.learner_id, point, request_id))
            con.execute('INSERT INTO review_papers (id,learner,subject,candidate,title,mode,phase,revision,created_at,expires_at,items,job,metadata) VALUES (?,?,?,?,?,?,?,0,?,?,?,NULL,?)',
                        (request_id, self.learner_id, subject, candidate, description['title'], mode, 'answer',
                         now.isoformat(), (now + timedelta(hours=24)).isoformat(), packed(items), packed(metadata)))
        return request_id

    def state(self, paper_id, subject=None):
        with self.transaction() as con:
            self._expire(con)
            paper = self._row(con, paper_id, subject)
            job = con.execute('SELECT status,reason,request_data FROM paper_submissions WHERE id=?', (paper['job'],)).fetchone()
            self_rating = self_rating_enabled(self.access, con)
        items = []
        for index, item in enumerate(paper['items']):
            shown = item.get('result') is not None and (self._mode(paper) == 'question' or paper['phase'] in ('done', 'cancelled', 'expired', 'conflict'))
            question = item['question']
            source = item['source']
            context = ({'topic_id': permission_id(source['container']), 'bank_id': source['container'],
                        'group_id': source['group_id'], 'question_id': source['local_points'][0]}
                       if source['kind'] == 'training' else
                       {'topic_id': source['container'], 'question_id': source['local_card']} if source['kind'] == 'atomic' else None)
            items.append({'index': index, 'point_ids': item['point_ids'], 'title': question.get('title', ''),
                          'source_title': item['source_title'], 'html': question['html'],
                          'content_context': context,
                          'content_version': item['id'] if item['id'].startswith('variant-') else source['version'],
                          'source_kind': source['kind'],
                          'interaction': public_interaction(question), 'answer': item['answer'],
                          'elapsed_ms': item['elapsed_ms'], 'result': item['result'] if shown else None,
                          'explanation_html': question.get('explanation_html', '') if shown else None,
                          'answer_display': display_answer(question, item['answer']) if shown else '',
                          'completed': item.get('result') is not None,
                          'can_self_rate': self_rating and presentation(question, item)['reproduction'],
                          'revealed': self_rating and bool(item.get('revealed')),
                          **reference_fields(question, shown or self_rating and bool(item.get('revealed'))),
                          'self_rating': item.get('self_rating')})
            if self_rating and item.get('revealed'):
                items[-1]['explanation_html'] = question.get('explanation_html', '')
        return {'id': paper['id'], 'title': paper['title'], 'subject': paper['subject'], 'mode': self._mode(paper),
                'phase': paper['phase'], 'revision': paper['revision'], 'items': items,
                'kind': paper['metadata'].get('kind'), 'paper_number': paper['metadata'].get('paper_number'),
                'timing': self.timing(paper),
                'math_css': '\n'.join(dict.fromkeys(i.get('math_css', '') for i in paper['items'])),
                'job': paper['job'], 'job_status': job['status'] if job else None,
                'error': job['reason'] if job and job['status'] == 'error' else None,
                'retry_submission': json.loads(job['request_data']) if job and job['status'] == 'error' and paper['phase'] == 'grading' else None,
                'can_submit': 'submit_answer' in self.access['features'],
                'can_edit': paper['phase'] == 'answer' and not self._hard_expired(paper)}

    def save(self, paper_id, revision, answers, subject=None):
        with self.transaction() as con:
            self._expire(con)
            paper = self._row(con, paper_id, subject)
            self._editable(paper, revision)
            if self._hard_expired(paper):
                raise DomainError('review_time_elapsed', '限时已到，正在提交截止前保存的答案。', 409)
            seen = set()
            for draft in answers:
                index = draft['index']
                if index in seen or not 0 <= index < len(paper['items']):
                    raise DomainError('invalid_answer', '题目位置无效。', 422)
                seen.add(index)
                item = paper['items'][index]
                if item.get('result'):
                    raise DomainError('already_answered', '已完成的题目不能更改。', 409)
                item['answer'], item['elapsed_ms'] = draft['answer'], draft.get('elapsed_ms', 0)
                rating = draft.get('self_rating')
                if rating is not None:
                    if not self_rating_enabled(self.access, con) or not item.get('revealed') or type(rating) is not int or rating not in (1, 2, 3, 4):
                        raise DomainError('self_rating_forbidden', '请先显示复现题答案，再选择自主评分。', 403)
                    item['self_rating'] = rating
                elif item.get('revealed'):
                    item.pop('self_rating', None)
            con.execute('UPDATE review_papers SET items=?,revision=revision+1,expires_at=? WHERE id=?',
                        (packed(paper['items']), (self._now() + timedelta(hours=24)).isoformat(), paper_id))

    @staticmethod
    def _editable(paper, revision):
        if paper['phase'] != 'answer' or paper['revision'] != revision:
            raise DomainError('stale_revision', '复习状态已变化，请重新同步；草稿仍保留。', 409)

    def reveal(self, paper_id, index, revision, subject=None):
        with self.transaction() as con:
            paper = self._row(con, paper_id, subject)
            self._editable(paper, revision)
            if not self_rating_enabled(self.access, con):
                raise DomainError('feature_forbidden', 'Show Answer 仅对高阶学习者开放。', 403)
            if self._hard_expired(paper):
                raise DomainError('review_time_elapsed', '限时已到，无法显示答案。', 409)
            if not 0 <= index < len(paper['items']) or paper['items'][index].get('result'):
                raise DomainError('already_answered', '此题已完成或不存在。', 409)
            item = paper['items'][index]
            reveal_question(item['question'], item)
            con.execute('UPDATE review_papers SET items=?,revision=revision+1 WHERE id=?', (packed(paper['items']), paper_id))

    def submission_size(self, data):
        with closing(self._connect()) as con:
            old = con.execute('SELECT * FROM paper_submissions WHERE id=?', (data['request_id'],)).fetchone()
            if old:
                if old['learner'] != self.learner_id or old['fingerprint'] != fingerprint(data):
                    raise DomainError('request_conflict', '提交标识已用于不同内容。', 409)
                return (len(json.loads(old['items'])) - len(json.loads(old['verdicts']))) if old['status'] == 'error' else 0
            paper = self._row(con, data['paper_id'])
            indices = ([data['index']] if data.get('index') is not None and not self._hard_expired(paper)
                       else [i for i, item in enumerate(paper['items']) if not item.get('result')])
            return sum(self._answered(paper['items'][i]) for i in indices) if self._hard_expired(paper) else len(indices)

    def submit(self, data, subject=None):
        with self.transaction() as con:
            self._expire(con)
            paper = self._row(con, data['paper_id'], subject)
            if data.get('index') is not None and self._mode(paper) != 'question':
                raise DomainError('review_mode_forbidden', '当前模式需要完成整卷后交卷。', 403)
            old = con.execute('SELECT * FROM paper_submissions WHERE id=?', (data['request_id'],)).fetchone()
            if old:
                if old['learner'] != self.learner_id or old['fingerprint'] != fingerprint(data):
                    raise DomainError('request_conflict', '提交标识已用于不同内容。', 409)
                if old['status'] != 'error':
                    return old['id']
                if paper['job'] != old['id'] or paper['phase'] not in ('grading', 'answer'):
                    raise DomainError('stale_revision', '这次提交已失效。', 409)
                con.execute("UPDATE paper_submissions SET status='pending',reason='' WHERE id=?", (old['id'],))
                con.execute("UPDATE review_papers SET phase='grading' WHERE id=?", (paper['id'],))
                return old['id']
            self._editable(paper, data['expected_revision'])
            timed_out = self._hard_expired(paper)
            index = None if timed_out else data.get('index')
            if index is not None and self._mode(paper) != 'question':
                raise DomainError('review_mode_forbidden', '当前模式需要完成整卷后交卷。', 403)
            indices = [index] if index is not None else [i for i, item in enumerate(paper['items']) if not item.get('result')]
            if not indices or any(not 0 <= i < len(paper['items']) or paper['items'][i].get('result') for i in indices):
                raise DomainError('invalid_answer', '没有可提交的题目。', 422)
            verdicts = {}
            for i in indices:
                item = paper['items'][i]
                if item.get('self_rating') is not None:
                    if not self_rating_enabled(self.access, con):
                        raise DomainError('feature_forbidden', '自主评分权限已变化。', 403)
                    verdicts[str(i)] = [item['self_rating'] > 1, '自主评分：' + str(item['self_rating'])]
                    continue
                if not self._answered(paper['items'][i]):
                    if timed_out:
                        verdicts[str(i)] = [False, '限时已到，本题未完成作答。']
                        continue
                    raise DomainError('incomplete_paper', f'请先完成第 {i + 1} 题。', 422)
            con.execute('INSERT INTO paper_submissions VALUES (?,?,?,?,?,?,?,?,?)',
                        (data['request_id'], self.learner_id, paper['id'], fingerprint(data), 'pending', packed(indices), packed(verdicts), '', packed(data)))
            if set(indices) == {i for i, item in enumerate(paper['items']) if not item.get('result')}:
                paper['metadata']['submitted_at'] = paper['metadata']['timing']['deadline_at'] if timed_out else self._now().isoformat()
            con.execute("UPDATE review_papers SET phase='grading',revision=revision+1,job=?,metadata=? WHERE id=?",
                        (data['request_id'], packed(paper['metadata']), paper['id']))
        return data['request_id']

    def instant_submission(self, submission_id):
        from .judging import instant_question
        with closing(self._connect()) as con:
            job = con.execute('SELECT * FROM paper_submissions WHERE id=? AND learner=?',
                              (submission_id, self.learner_id)).fetchone()
            if not job:
                return False
            paper = self._row(con, job['paper'])
            verdicts = json.loads(job['verdicts'])
            return all(str(i) in verdicts or instant_question(paper['items'][i]['question'])
                       for i in json.loads(job['items']))

    def process_submission(self, submission_id):
        with self.transaction() as con:
            job = con.execute('SELECT * FROM paper_submissions WHERE id=? AND learner=?', (submission_id, self.learner_id)).fetchone()
            if not job or job['status'] != 'pending':
                return
            paper = self._row(con, job['paper'])
            con.execute("UPDATE paper_submissions SET status='running' WHERE id=?", (submission_id,))
        try:
            verdicts = json.loads(job['verdicts'])
            for index in json.loads(job['items']):
                if str(index) in verdicts:
                    continue
                item = paper['items'][index]
                correct, reason = judge_answer(self.training.judge, item['question'], item['answer'])
                if correct is None:
                    raise DomainError('judge_error', reason, 503)
                verdicts[str(index)] = [correct, reason]
                with self.transaction() as con:
                    con.execute('UPDATE paper_submissions SET verdicts=? WHERE id=?', (packed(verdicts), submission_id))
            if self.refresh_access:
                self.access = self.refresh_access()
                self.sources.access = self.access
            with self.transaction() as con:
                paper = self._row(con, job['paper'])
                indices = json.loads(job['items'])
                if ('submit_answer' not in self.access['features'] or (self._mode(paper) == 'paper'
                        and set(indices) != {i for i, item in enumerate(paper['items']) if not item.get('result')})):
                    raise DomainError('feature_forbidden', '提交权限已变化，请重新进入复习。', 403)
                if any(paper['items'][i].get('self_rating') is not None for i in indices) and not self_rating_enabled(self.access, con):
                    raise DomainError('feature_forbidden', '自主评分权限已变化，请重新进入复习。', 403)
                if paper['phase'] != 'grading' or paper['job'] != submission_id:
                    return
                if not all(self.sources.unchanged(con, paper['items'][i]) for i in indices):
                    con.execute("UPDATE review_papers SET phase='conflict',revision=revision+1 WHERE id=?", (paper['id'],))
                    con.execute('DELETE FROM review_reservations WHERE paper=?', (paper['id'],))
                    con.execute("UPDATE paper_submissions SET status='error',reason=? WHERE id=?", ('记忆点或学习权限已变化，未更新排期；请重新选择复习。', submission_id))
                    return
                for i in indices:
                    item = paper['items'][i]
                    item['result'] = self.sources.apply(con, paper, i, item, verdicts[str(i)], self._now().isoformat())
                    for point in item['point_ids']:
                        con.execute('DELETE FROM review_reservations WHERE learner=? AND point=? AND paper=?', (self.learner_id, point, paper['id']))
                phase = 'done' if all(i.get('result') for i in paper['items']) else 'answer'
                con.execute('UPDATE review_papers SET items=?,phase=?,revision=revision+1,expires_at=? WHERE id=?',
                            (packed(paper['items']), phase, (self._now() + timedelta(hours=24)).isoformat(), paper['id']))
                con.execute("UPDATE paper_submissions SET status='judged',reason='' WHERE id=?", (submission_id,))
        except Exception as error:
            reason = error.message if isinstance(error, DomainError) else '判题服务暂时不可用，请重试；本次未计入作答。'
            with self.transaction() as con:
                con.execute("UPDATE paper_submissions SET status='error',reason=? WHERE id=?", (reason, submission_id))

    def cancel(self, paper_id, subject=None):
        with self.transaction() as con:
            paper = self._row(con, paper_id, subject)
            if self._hard_expired(paper) and paper['phase'] == 'answer':
                raise DomainError('review_time_elapsed', '限时已到，请先等待自动交卷。', 409)
            job = con.execute('SELECT status FROM paper_submissions WHERE id=?', (paper['job'],)).fetchone()
            if paper['phase'] == 'grading' and job and job[0] in ('pending', 'running'):
                raise DomainError('submission_pending', '正在判题，请等待结果后再结束复习。', 409)
            if paper['phase'] in ('answer', 'grading'):
                paper['metadata']['ended_at'] = self._now().isoformat()
                con.execute("UPDATE review_papers SET phase='cancelled',revision=revision+1,metadata=? WHERE id=?", (packed(paper['metadata']), paper_id))
                con.execute('DELETE FROM review_reservations WHERE paper=?', (paper_id,))

    @staticmethod
    def recover(con):
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'paper_submissions' in tables:
            con.execute("UPDATE paper_submissions SET status='error',reason=? WHERE status IN ('pending','running')",
                        ('服务重新启动，请重试；已完成的判题结果保留。',))
