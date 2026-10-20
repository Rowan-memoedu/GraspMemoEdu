"""Adapters from existing learning records to shared point/card presentations."""
from datetime import timedelta
import hashlib
import json

from .atomic_content import GAP_SECONDS, is_atomic
from .fsrs_engine import utc_time
from .question_types import present_choice
from .review_cards import point_id, register_point, eligible_presentations
from .training_content import permission_id


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ReviewSources:
    def __init__(self, training, catalog, atoms, dependencies, access):
        self.training, self.catalog, self.atoms = training, catalog, atoms
        self.dependencies, self.access = dependencies, access
        self.learner = access['learner_id']

    def inventory(self, con, subject, now):
        allowed = {x['id'] for x in self.access['topics']}
        reserved = {r[0] for r in con.execute('SELECT point FROM review_reservations WHERE learner=?', (self.learner,))}
        uses = {r['card']: r['count'] for r in con.execute('SELECT * FROM review_card_uses WHERE learner=?', (self.learner,))}
        cards, next_dates = [], []
        for bank_id in self.training.catalog.banks:
            bank = self.training.catalog.bank(bank_id)
            if bank['subject_id'] != subject or permission_id(bank_id) not in allowed:
                continue
            memories = {r['question']: dict(r) for r in con.execute('''SELECT m.* FROM training_memory m
                WHERE learner=? AND bank=? AND version=? AND NOT EXISTS (SELECT 1 FROM training_suspensions s
                WHERE s.learner=m.learner AND s.bank=m.bank AND s.question=m.question AND s.version=m.version)''',
                (self.learner, bank_id, bank['version']))}
            pending = {r[0] for r in con.execute('''SELECT a.question FROM training_attempts a
                JOIN training_submissions s ON s.attempt=a.id WHERE a.learner=? AND a.bank=?
                AND s.status IN ('pending','running')''', (self.learner, bank_id))}
            for group in bank['groups']:
                for question in group.get('questions', []):
                    memory = memories.get(question['id'])
                    if not memory:
                        continue
                    pid = point_id('training', bank_id, bank['version'], question['id'])
                    if pid in reserved or question['id'] in pending:
                        continue
                    if utc_time(memory['due_at']) > now:
                        next_dates.append(memory['due_at'])
                        continue
                    source = {'kind': 'training', 'container': bank_id, 'version': bank['version'],
                              'local_points': [question['id']], 'group_id': group['id'],
                              'memory': {pid: fingerprint(memory)}}
                    presentations = [(f'training:{bank_id}:{bank["version"]}:{question["id"]}', question)]
                    presentations += [(r['id'], json.loads(r['snapshot'])) for r in con.execute(
                        'SELECT id,snapshot FROM review_variants WHERE point=? ORDER BY id', (pid,))]
                    for cid, q in presentations:
                        cards.append({'id': cid, 'point_ids': [pid], 'question': q, 'source': source,
                                      'due_at': memory['due_at'], 'source_title': bank['title'] + ' · ' + group['title'],
                                      'math_css': bank.get('math_css', ''),
                                      # The source was already shown when its memory was created.
                                      'uses': uses.get(cid, 0) + (1 if subject == 'math' and cid.startswith('training:') else 0)})
        for topic_id, backend in self.atoms.items():
            topic = backend.course
            if topic.get('academy'):
                # IDE rounds use the same atomic state, but cannot be answered in a paper.
                continue
            if topic['subject_id'] != subject or topic_id not in allowed:
                continue
            row = con.execute('SELECT body,version FROM topics WHERE learner=? AND topic=?', (self.learner, topic_id)).fetchone()
            if not row or row['version'] != topic['version']:
                continue
            state = json.loads(row['body'])
            if (not state.get('completed_at') or state['pause'] or state.get('pending_submission_id')
                    or state.get('active') or not self.dependencies.ready(con, self.access, topic_id)):
                continue
            eligible, global_ids = set(), {}
            for local_id, memory in state['points'].items():
                pid = point_id('atomic', topic_id, topic['version'], local_id)
                global_ids[local_id] = pid
                if not memory['mastered'] or not memory['due_at'] or pid in reserved:
                    continue
                due = utc_time(memory['due_at'])
                last = state['chunk_last_seen'].get(backend.point_chunks[local_id])
                if last:
                    due = max(due, utc_time(last) + timedelta(seconds=GAP_SECONDS))
                if due <= now:
                    eligible.add(local_id)
                else:
                    next_dates.append(due.isoformat())
            for q in eligible_presentations(topic['flashcards'], eligible, state['card_uses']):
                ids = [global_ids[p] for p in q['point_ids']]
                cid = f'atomic:{topic_id}:{topic["version"]}:{q["id"]}'
                source = {'kind': 'atomic', 'container': topic_id, 'version': topic['version'],
                          'local_points': q['point_ids'], 'local_card': q['id'],
                          'memory': {global_ids[p]: fingerprint(state['points'][p]) for p in q['point_ids']}}
                cards.append({'id': cid, 'point_ids': ids, 'question': q, 'source': source,
                              'due_at': max(state['points'][p]['due_at'] for p in q['point_ids']),
                              'source_title': topic['title'], 'math_css': topic.get('math_css', ''),
                              'uses': state['card_uses'].get(q['id'], 0)})
        cards.sort(key=lambda c: (c['due_at'], c['uses'], c['id']))
        return cards, min(next_dates, default=None)

    def begin(self, con, items, subject, paper_id, now):
        touched = {}
        for index, item in enumerate(items):
            source = item['source']
            for local in source['local_points']:
                register_point(con, source['kind'], source['container'], source['version'], local, subject, now)
            item['question'] = present_choice(item['question'], f'{paper_id}:{index}')
            con.execute('''INSERT INTO review_card_uses VALUES (?,?,1)
                ON CONFLICT(learner,card) DO UPDATE SET count=count+1''', (self.learner, item['id']))
            if source['kind'] == 'atomic':
                backend = self.atoms[source['container']]
                state = touched.setdefault(backend.topic_id, backend._load(con))
                for local in source['local_points']:
                    state['chunk_last_seen'][backend.point_chunks[local]] = now
                cid = source['local_card']
                state['card_uses'][cid] = state['card_uses'].get(cid, 0) + 1
        for tid, state in touched.items():
            self.atoms[tid]._save(con, state)

    def authorized(self, items):
        allowed = {x['id'] for x in self.access['topics']}
        for item in items:
            source = item['source']
            target = permission_id(source['container']) if source['kind'] == 'training' else source['container']
            if target not in allowed:
                return False
        return True

    def unchanged(self, con, item):
        source = item['source']
        if source['kind'] == 'training':
            if self.training.catalog.bank(source['container'])['version'] != source['version']:
                return False
            local = source['local_points'][0]
            memory = self.training._memory(con, source['container'], local, source['version'])
            return (memory is not None and fingerprint(dict(memory)) == source['memory'][item['point_ids'][0]]
                    and not self.training._stopped(con, source['container'], local, source['version']))
        backend = self.atoms.get(source['container'])
        if not backend or backend.course['version'] != source['version']:
            return False
        state = backend._load(con)
        return (not state['pause'] and not state['pending_submission_id'] and not state.get('active')
                and self.dependencies.ready(con, self.access, backend.topic_id)
                and all(fingerprint(state['points'][p]) == source['memory'][pid]
                        for p, pid in zip(source['local_points'], item['point_ids'])))

    def apply(self, con, paper, index, item, verdict, now):
        """Delegate ratings to the original source policy in the paper transaction."""
        source, request_id = item['source'], f'{paper["id"]}:{index}'
        correct, reason = verdict
        if source['kind'] == 'training':
            local = source['local_points'][0]
            memory = self.training._memory(con, source['container'], local, source['version'])
            con.execute('''INSERT INTO training_attempts
                (id,learner,bank,group_id,question,version,snapshot,mode,memory_revision,phase,created_at,submission_id)
                VALUES (?,?,?,?,?,?,?,?,?,'answer',?,?)''',
                (request_id, self.learner, source['container'], source['group_id'], local, source['version'],
                 json.dumps(item['question']), 'review' if memory['mastered'] or memory['card_json'] is None else 'initial', memory['revision'],
                 paper['created_at'], request_id))
            con.execute('INSERT INTO training_submissions VALUES (?,?,?,?,?,?,?,?)',
                        (request_id, self.learner, request_id, fingerprint(item['answer']), item['answer'],
                         item.get('elapsed_ms'), 'judged', reason))
            row = con.execute('SELECT * FROM training_attempts WHERE id=?', (request_id,)).fetchone()
            self.training._complete(con, row, correct=correct, reason=reason, elapsed_ms=item.get('elapsed_ms'))
            result = json.loads(con.execute('SELECT result FROM training_attempts WHERE id=?', (request_id,)).fetchone()[0])
            return {**result, 'ratings': {item['point_ids'][0]: result['rating']}}
        backend = self.atoms[source['container']]
        state = backend._load(con)
        saved = {key: state[key] for key in ('active', 'mode', 'phase')}
        state['mode'] = 'review'
        state['active'] = {'id': request_id, 'card_id': source['local_card'], 'point_ids': source['local_points'],
                           'mode': 'review', 'shown_at': paper['created_at'], 'phase': 'answer', 'snapshot': item['question']}
        backend._grade(con, state, {'id': request_id, 'answer': item['answer'], 'elapsed_ms': item.get('elapsed_ms')}, correct, reason)
        ratings = dict(state['active']['ratings'])
        state.update(saved)
        backend._save(con, state)
        return {'correct': correct, 'reason': reason, 'scheduled': True,
                'ratings': {pid: ratings[local] for pid, local in zip(item['point_ids'], source['local_points'])},
                'due_at': min(state['points'][p]['due_at'] for p in source['local_points'])}
