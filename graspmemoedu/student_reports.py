"""Read-only student reports over one SQLite snapshot, without learner services.

Canonical attempts are counted once: paper reviews already write through to
training attempts / atomic history. Paper items only supply missing metadata.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .question_bank import QuestionBank
from .question_types import public_interaction, display_answer
from .reference_answers import reference_fields
from .service import DomainError
from .topic_runtime import introductions, is_introduction
from .training_content import permission_id
from .course_hierarchy import project_hierarchy
from .atomic_content import point_groups
from .topic_structure import atomic_state


def unpack(value, default=None):
    return json.loads(value) if value else (default if default is not None else {})


def time_zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise DomainError('invalid_timezone', '请选择有效的时区。', 422) from None


def metrics(events):
    judged = [e for e in events if e['self_rating'] is None and e['correct'] is not None]
    assessed = [e for e in events if e['self_rating'] is not None]
    durations = [e['elapsed_ms'] for e in events if e['elapsed_ms'] is not None]
    first = {}
    for event in sorted(judged, key=lambda e: (e['at'], e['id'])):
        first.setdefault((event['kind'], event['container'], event['version'], event['question']), event)
    return {'attempts': len(events), 'judged': len(judged), 'correct': sum(e['correct'] for e in judged),
            'wrong': sum(not e['correct'] for e in judged), 'self_rated': len(assessed),
            'self_passed': sum(e['self_rating'] > 1 for e in assessed),
            'elapsed_ms': sum(durations), 'timed_attempts': len(durations),
            'accuracy': sum(e['correct'] for e in judged) / len(judged) if judged else None,
            'first_accuracy': sum(e['correct'] for e in first.values()) / len(first) if first else None,
            'last_at': max((e['at'] for e in events), default=None)}


class StudentReport:
    def __init__(self, owner, con, access, student, rows, tables, current, own=False):
        self.owner, self.con, self.access, self.student = owner, con, access, student
        self.rows, self.tables, self.current = rows, tables, current
        self.own = own
        self.states = {key: unpack(row['body']) for key, row in rows.items()}
        self.learner = student['learner_id']
        self.definitions, self.groups = {}, {}
        self._index_content()
        self.definition_order = {key: index for index, key in enumerate(self.definitions)}
        self.paper_items = {}
        if 'review_papers' in tables:
            for row in self.select('review_papers'):
                for index, item in enumerate(unpack(row['items'], [])):
                    if item.get('result'):
                        self.paper_items[f'{row["id"]}:{index}'] = item
        self.events = self._events()
        self.by_question = defaultdict(list)
        self.by_container = defaultdict(list)
        for event in self.events:
            self.by_question[(event['kind'], event['container'], event['version'], event['question'])].append(event)
            self.by_container[(event['kind'], event['container'])].append(event)
        self.memory, self.stopped = {}, set()
        if 'training_memory' in tables:
            self.memory = {(r['bank'], r['version'], r['question']): dict(r) for r in self.select('training_memory')
                           if self.visible('training', r['bank'])}
        if 'training_suspensions' in tables:
            self.stopped = {(r['bank'], r['version'], r['question']) for r in self.select('training_suspensions')
                            if self.visible('training', r['bank'])}

    def select(self, table):
        # All callers use fixed table names, never a request-supplied identifier.
        return self.con.execute(f'SELECT * FROM {table} WHERE learner=?', (self.learner,))

    def visible(self, kind, container):
        if kind == 'topic' and container in self.owner.training.topic_redirects:
            return False
        return self.owner.access.test_materials.topic_visible(
            permission_id(container) if kind == 'training' else container, self.access)

    def _index_content(self):
        for tid, topic in self.owner.catalog.topics.items():
            if not self.visible('topic', tid):
                continue
            for module in topic.get('modules', []):
                for question in [module['example'], *module['practices']]:
                    self.definitions['topic', tid, topic['version'], question['id']] = {
                        'title': question.get('title', question['id']), 'group_id': module['id'],
                        'group_title': module['title'], 'points': [module['id']]}
            for card in topic.get('flashcards', []):
                self.definitions['topic', tid, topic['version'], card['id']] = {
                    'title': card['title'], 'group_id': '', 'group_title': '', 'points': card['point_ids']}
        for bid, bank in self.owner.training.banks.items():
            if not self.visible('training', bid):
                continue
            self.groups[bid] = bank['groups']
            for group in bank['groups']:
                for q in group.get('questions', []):
                    self.definitions['training', bid, bank['version'], q['id']] = {
                        'title': q.get('title', q['id']), 'group_id': group['id'],
                        'group_title': group['title'], 'points': [q['id']]}

    def _event(self, identifier, kind, container, version, question, at, answer, correct, reason,
               *, mode='learn', elapsed=None, self_rating=None, snapshot=None, presentation=None,
               revision=None, points=None, module=None):
        if not at or not self.visible(kind, container):
            return None
        key = (kind, container, version, question)
        info = self.definitions.get(key, {})
        if self_rating is None:
            match = re.fullmatch(r'自主评分：([1-5])', reason or '')
            if match:
                self_rating = int(match[1])
        return {'id': identifier, 'kind': kind, 'container': container, 'version': version,
                'question': question, 'title': (snapshot or {}).get('title') or info.get('title', question),
                'group_id': module or info.get('group_id', ''), 'group_title': info.get('group_title', ''),
                'points': points or info.get('points', [question]), 'at': at, 'mode': mode,
                'correct': bool(correct) if correct is not None else None, 'self_rating': self_rating,
                'elapsed_ms': elapsed if type(elapsed) is int and elapsed >= 0 else None,
                '_answer': answer, '_reason': reason, '_snapshot': snapshot,
                '_presentation': presentation, '_revision': revision}

    def _events(self):
        result = []
        submissions = list(self.select('submissions')) if 'submissions' in self.tables else []
        by_step = {(r['topic'], r['step']): r for r in submissions if r['status'] == 'judged'}
        atomic_steps = set()
        for tid, state in self.states.items():
            for record in atomic_state(state).get('history', []):
                if record.get('correct') is None:
                    continue
                if (record.get('snapshot') or {}).get('training_source'):
                    # Cross-Topic point updates belong to one bank attempt.
                    # The canonical bank row below owns counts and elapsed time.
                    continue
                atomic_steps.add((tid, record['id']))
                submission = by_step.get((tid, record['id']))
                paper = self.paper_items.get(record['id'], {})
                event = self._event('atomic:' + record['id'], 'topic', tid, self.rows[tid]['version'],
                    record['card_id'], record.get('answered_at'), record.get('answer'), record['correct'], record.get('reason'),
                    mode='review' if record.get('mode') == 'review' else 'learn',
                    elapsed=submission['elapsed_ms'] if submission else paper.get('elapsed_ms'),
                    self_rating=(record.get('self_assessment') or {}).get('rating') or paper.get('self_rating'),
                    snapshot=record.get('snapshot'), points=record.get('point_ids'))
                if event:
                    result.append(event)
        for row in submissions:
            if row['status'] != 'judged' or row['correct'] is None or (row['topic'], row['step']) in atomic_steps:
                continue
            tid = row['topic']
            if tid not in self.rows:
                continue
            step = next((s for s in self.states[tid].get('steps', []) if s['id'] == row['step']), {})
            event = self._event('lesson:' + row['id'], 'topic', tid, self.rows[tid]['version'], row['question'],
                row['updated_at'], row['answer'], row['correct'], row['reason'], elapsed=row['elapsed_ms'],
                self_rating=(step.get('self_assessment') or {}).get('rating'),
                presentation=row['step'], revision=step.get('question_bank_revision'), module=step.get('module_id'))
            if event:
                result.append(event)
        if 'review_submissions' in self.tables:
            sessions = {r['id']: r for r in self.select('review_sessions')}
            for row in self.select('review_submissions'):
                if row['status'] != 'judged' or row['correct'] is None or row['session'] not in sessions:
                    continue
                session = sessions[row['session']]; state = unpack(session['body'])
                event = self._event('review:' + row['id'], 'topic', row['topic'], session['version'], row['question'],
                    row['updated_at'], row['answer'], row['correct'], row['reason'], mode='review', elapsed=row['elapsed_ms'],
                    presentation=row['session'] + ':' + row['question'], revision=state.get('question_bank_revision'), module=session['module'])
                if event:
                    result.append(event)
        if 'training_attempts' in self.tables:
            answers = {r['id']: r for r in self.select('training_submissions')} if 'training_submissions' in self.tables else {}
            for row in self.select('training_attempts'):
                outcome = unpack(row['result'])
                if not row['completed_at'] or outcome.get('stop_requested') or (outcome.get('correct') is None and outcome.get('self_rating') is None):
                    continue
                answer = answers.get(row['submission_id'])
                paper = self.paper_items.get(row['id'], {})
                event = self._event('training:' + row['id'], 'training', row['bank'], row['version'], row['question'],
                    row['completed_at'], answer['answer'] if answer else None, outcome.get('correct'), outcome.get('reason'),
                    mode=row['mode'], elapsed=answer['elapsed_ms'] if answer else None,
                    self_rating=outcome.get('self_rating') if outcome.get('self_rating') is not None else paper.get('self_rating'),
                    snapshot=unpack(row['snapshot']), module=row['group_id'])
                if event:
                    result.append(event)
        return sorted(result, key=lambda e: (e['at'], e['id']))

    def question_rows(self, kind=None, container=None, *, notebook=False):
        keys = set(self.definitions) if not notebook else {('training', b, v, q) for b, v, q in self.memory}
        if not notebook:
            keys.update(self.by_question)
            keys.update(('training', b, v, q) for b, v, q in self.memory)
        rows = []
        for k, cid, version, qid in keys:
            if (kind and kind != k) or (container and cid != container):
                continue
            events = self.by_question.get((k, cid, version, qid), [])
            info = self.definitions.get((k, cid, version, qid), {})
            bank = self.owner.training.banks.get(cid, {}) if k == 'training' else {}
            topic = self.owner.catalog.topics.get(cid, {}) if k == 'topic' else {}
            memory = self.memory.get((cid, version, qid), {}) if k == 'training' else {}
            title = info.get('title') or (events[-1] if events else {}).get('title') or qid
            if self.own and qid in {card['id'] for card in topic.get('flashcards', [])}:
                point_state = atomic_state(self.states.get(cid, {})).get('points', {})
                if not all(point_state.get(pid, {}).get('mastered') for pid in info.get('points', [qid])):
                    title = qid
            stopped = (cid, version, qid) in self.stopped if k == 'training' else False
            stats = metrics(events)
            latest = events[-1] if events else None
            due = memory.get('due_at')
            rows.append({'kind': k, 'container': cid, 'version': version, 'question': qid,
                'title': title,
                'container_title': (bank or topic).get('title', cid),
                'subject_id': bank.get('subject_id') if bank else self.owner.context(cid)['subject_id'],
                'group_id': info.get('group_id') or (latest or {}).get('group_id', ''),
                'group_title': info.get('group_title') or (latest or {}).get('group_title', ''),
                'version_current': version == (bank or topic).get('version'), 'stats': stats,
                'in_notebook': bool(memory), 'stopped': stopped, 'due_at': due,
                'due': bool(due and datetime.fromisoformat(due) <= self.current and not stopped),
                'mastered': bool(memory.get('mastered')), 'latest_correct': latest['correct'] if latest else None,
                'latest_self_rating': latest['self_rating'] if latest else None,
                'status': 'stopped' if stopped else 'unanswered' if not events else 'answered'})
        rows.sort(key=lambda r: (self.definition_order.get((r['kind'], r['container'], r['version'], r['question']), len(self.definition_order)),
                                 r['container_title'], r['group_title'], r['title'], r['version']))
        return rows

    def topic_rows(self):
        result = []
        for tid in dict.fromkeys([*self.owner.catalog.topics, *self.rows]):
            if not self.visible('topic', tid):
                continue
            topic = self.owner.catalog.topics.get(tid, {})
            state = self.states.get(tid, {})
            row = self.rows.get(tid)
            version = row['version'] if row else topic.get('version')
            current = bool(topic and version == topic['version'])
            # Old-version completion must never be projected onto a new syllabus.
            progress = state if current else {}
            started = self.owner.started(state)
            modules, points = [], []
            for definition in topic.get('modules', []):
                attempts = [a for a in progress.get('attempts', []) if a['module_id'] == definition['id']]
                done = next((a for a in reversed(attempts) if a['status'] == 'completed'), None)
                latest = attempts[-1] if attempts else {}
                skipped = definition['id'] in progress.get('skipped_modules', {})
                modules.append({'id': definition['id'], 'title': definition['title'],
                    'status': 'completed' if done else 'skipped' if skipped else 'in_progress' if latest.get('learning_path') or latest.get('example_result') else 'not_started',
                    'mastery': done.get('mastery') if done else None})
            for chunk in point_groups(topic):
                for point in chunk['points']:
                    memory = atomic_state(progress).get('points', {}).get(point['id'], {})
                    points.append({'id': point['id'], 'title': point['id'] if self.own and not memory.get('mastered') else point['text'], 'chunk_title': chunk['title'],
                                   'mastered': bool(memory.get('mastered')), 'due_at': memory.get('due_at')})
            guidance = [{'id': i.get('id', 'introduction'), 'title': i.get('title', 'Introduction'),
                         'read': bool(progress.get('introduction_read') or i.get('id') in progress.get('read_introductions', [])
                                      or topic.get('training_mode') == 'atomic_retrieval' and progress.get('started_at'))}
                        for i in introductions(topic)]
            if points and modules:
                total = len(points) + len(modules)
                done = sum(p['mastered'] for p in points) + sum(m['status'] == 'completed' for m in modules)
                unit = 'items'
            elif points:
                total, done, unit = len(points), sum(p['mastered'] for p in points), 'points'
            elif is_introduction(topic):
                total, done, unit = len(guidance), sum(i['read'] for i in guidance), 'guidance'
            else:
                total, done, unit = len(modules), sum(m['status'] == 'completed' for m in modules), 'modules'
            context = self.owner.context(tid)
            status = state.get('status', 'not_started') if started else 'not_started'
            if current and status == 'completed' and done < total:
                status = 'in_progress'
            result.append(context | {'course_id': self.owner.catalog.course_context(tid)['id'] if tid in self.owner.catalog.topics else None,
                'course_ids': sorted(self.owner.catalog.topic_courses.get(tid, ())),
                'version': version, 'version_current': current, 'total': total, 'done': done, 'unit': unit,
                'status': status,
                'started_at': state.get('started_at'), 'completed_at': state.get('completed_at'),
                'modules': modules, 'points': points, 'guidance': guidance,
                'blockers': self.owner.blockers(self.con, self.student, tid, state, version, self.tables, self.current),
                'stats': metrics(self.by_container.get(('topic', tid), []))})
        return result

    def build(self, reviews, days=30, timezone='UTC'):
        zone = time_zone(timezone)
        today = self.current.astimezone(zone).date()
        start = today - timedelta(days=days - 1)
        daily = defaultdict(list)
        for event in self.events:
            date = datetime.fromisoformat(event['at']).astimezone(zone).date()
            if start <= date <= today:
                daily[date.isoformat()].append(event)
        period = [e for rows in daily.values() for e in rows]
        questions = self.question_rows()
        topics = self.topic_rows()
        banks = []
        for bid, bank in self.owner.training.banks.items():
            if not self.visible('training', bid):
                continue
            rows = [q for q in questions if q['kind'] == 'training' and q['container'] == bid and q['version_current']]
            groups = []
            for group in bank['groups']:
                own = [q for q in rows if q['group_id'] == group['id']]
                groups.append({'id': group['id'], 'title': group['title'], 'parent_id': group.get('parent_id'),
                    'total': len(own), 'answered': sum(q['stats']['attempts'] > 0 for q in own),
                    'due': sum(q['due'] for q in own)})
            events = self.by_container.get(('training', bid), [])
            banks.append({'id': bid, 'title': bank['title'], 'subject_id': bank['subject_id'], 'groups': groups,
                'total': len(rows), 'answered': sum(q['stats']['attempts'] > 0 for q in rows),
                'due': sum(q['due'] for q in rows), 'notebook': sum(q['in_notebook'] for q in rows),
                'stats': metrics(events), 'allowed': permission_id(bid) in {t['id'] for t in self.student['topics']}})
        errors = defaultdict(list)
        for event in self.events:
            failed = event['self_rating'] == 1 if event['self_rating'] is not None else event['correct'] is False
            if failed:
                for point in event['points']:
                    errors[event['kind'], event['container'], event['version'], point].append(event)
        weak = []
        for (kind, cid, version, point), events in errors.items():
            if len(events) < 2:
                continue
            last = events[-1]
            title = last['group_title'] if kind == 'topic' else last['title']
            for chunk in point_groups(self.owner.catalog.topics.get(cid, {})):
                title = next((p['text'] for p in chunk['points'] if p['id'] == point), title)
            if self.own and self.locked(last): title = point
            weak.append({'kind': kind, 'container': cid, 'version': version, 'point': point, 'question': last['question'],
                         'title': title or last['title'], 'failures': len(events), 'last_at': last['at']})
        weak.sort(key=lambda e: (-e['failures'], e['title']))
        calendar = defaultdict(lambda: {'count': 0, 'blocked': 0})
        for review in reviews:
            date = datetime.fromisoformat(review['due_at']).astimezone(zone).date().isoformat()
            calendar[date]['count'] += 1
            calendar[date]['blocked'] += bool(review['blockers'])
        notebook = self.question_rows(notebook=True)
        return {'days': days, 'timezone': timezone, 'period': metrics(period) | {'active_days': len(daily)},
            'lifetime': metrics(self.events), 'topics': topics, 'banks': banks,
            'courses': [{'id': c['id'], 'title': c['title'], 'subject_id': self.owner.subjects.course_subjects[c['id']],
                         'units': self.course_units(c, topics)}
                        for c in self.owner.catalog.courses.values() if any(t['course_id'] == c['id'] for t in topics)],
            'activity': [{'date': (start + timedelta(days=i)).isoformat(),
                          **metrics(daily[(start + timedelta(days=i)).isoformat()])} for i in range(days)],
            'calendar': [{'date': date, **counts} for date, counts in sorted(calendar.items())],
            'weak_points': weak[:20], 'notebook': {'total': len(notebook), 'due': sum(q['due'] for q in notebook),
                'stopped': sum(q['stopped'] for q in notebook), 'unanswered': sum(not q['stats']['attempts'] for q in notebook)},
            'permissions': {'features': self.student['features'], 'defaults': self.student.get('permission_defaults', {}),
                            'topic_count': sum(t['topic_id'] in {a['id'] for a in self.student['topics']} for t in topics)}}

    def course_units(self, course, topics):
        visible = {t['topic_id'] for t in topics}
        return project_hierarchy(course['units'], visible, {course['id']})

    def history(self, kind, container, question=None, version=None, point=None, offset=0, limit=20):
        events = [e for e in reversed(self.events) if e['kind'] == kind and e['container'] == container
                  and (not question or e['question'] == question) and (not version or e['version'] == version)
                  and (not point or point in e['points'])]
        return {'items': [self.present(e) for e in events[offset:offset + limit]], 'total': len(events),
                'next_offset': offset + limit if offset + limit < len(events) else None}

    def present(self, event):
        # Restore precisely the frozen question or its immutable revision. Never
        # attach a current question to an older answer when its content is missing.
        question, css = event['_snapshot'], ''
        topic = self.owner.catalog.topics.get(event['container'], {}) if event['kind'] == 'topic' else {}
        if topic:
            css = topic.get('math_css', '')
        if not question and topic and event['version'] == topic['version']:
            try:
                if event['question'] in {card['id'] for card in topic.get('flashcards', [])}:
                    question = next(q for q in topic['flashcards'] if q['id'] == event['question'])
                else:
                    bank = QuestionBank(topic, self.owner.access.data_dir)
                    question = bank.question(event['question'], event['_revision'], event['_presentation'] or '')
                    css += bank.css([event['_revision']])
            except (KeyError, ValueError, OSError, StopIteration):
                question = None
        if event['kind'] == 'training':
            css = self.owner.training.banks.get(event['container'], {}).get('math_css', '')
        result = {key: value for key, value in event.items() if not key.startswith('_')}
        if self.locked(event):
            return {**result, 'title': event['question'], 'group_title': '', 'locked': True, 'content_available': False, 'answer': None, 'reason': None}
        result.update(answer=event['_answer'], reason=event['_reason'], content_available=bool(question), math_css=css)
        if question:
            result.update(html=question['html'], explanation_html=question.get('explanation_html', ''),
                          interaction=public_interaction(question), answer_display=display_answer(question, event['_answer'] or ''))
            result.update(reference_fields(question, True))
        return result

    def locked(self, event):
        if not self.own or event['kind'] != 'topic': return False
        topic = self.owner.catalog.topics.get(event['container'], {})
        if event['question'] not in {card['id'] for card in topic.get('flashcards', [])}: return False
        if event['mode'] == 'review': return not event['correct']
        points = atomic_state(self.states.get(event['container'], {})).get('points', {})
        return not all(points.get(point, {}).get('mastered') for point in event['points'])
