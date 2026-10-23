"""Adapters from existing learning records to shared point/card presentations."""
from datetime import timedelta
import hashlib
import json

from .atomic_content import GAP_SECONDS, is_atomic
from .fsrs_engine import utc_time
from .question_types import present_choice
from .review_cards import point_id, register_point, eligible_presentations
from .training_content import permission_id
from .dependency_nodes import group_key
from .topic_structure import atomic_state, sectioned_training


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
                if not self.dependencies.ready(con, self.access, group_key(bank_id, group['id']), current_time=now.isoformat()):
                    continue
                for question in group.get('questions', []):
                    if question.get('academy') or question.get('hyperskill'):
                        # The shared bank owns its schedule; its answer provider is the IDE.
                        continue
                    if question.get('point_ids'):
                        from .linked_points import snapshot, pinned
                        rows = snapshot(con, self.catalog, self.learner, question['point_ids'], self.access)
                        if any(not r['learned'] or not r['available'] or r['point_id'] in reserved
                               or r['state'].get('active') or r['state'].get('pending_submission_id')
                               or not r['memory'].get('due_at') for r in rows) or question['id'] in pending:
                            continue
                        due = max(utc_time(r['memory']['due_at']) for r in rows)
                        for r in rows:
                            backend = self.atoms.get(r['topic_id'])
                            if backend is None:
                                break
                            last = r['state']['chunk_last_seen'].get(backend.point_chunks[r['local_id']])
                            if last:
                                due = max(due, utc_time(last) + timedelta(seconds=GAP_SECONDS))
                        else:
                            if due > now:
                                next_dates.append(due.isoformat())
                                continue
                            cid = f'training:{bank_id}:{bank["version"]}:{question["id"]}'
                            cards.append({'id': cid, 'point_ids': [r['point_id'] for r in rows], 'question': question,
                                'source': {'kind': 'training', 'container': bank_id, 'version': bank['version'],
                                    'local_points': question['point_ids'], 'local_card': question['id'],
                                    'group_id': group['id'], 'linked_points': pinned(rows)},
                                'due_at': due.isoformat(), 'source_title': bank['title'] + ' · ' + group['title'],
                                'math_css': bank.get('math_css', ''), 'uses': uses.get(cid, 0)})
                        continue
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
                                      'uses': uses.get(cid, 0) + int(cid.startswith('training:'))})
        for topic_id, backend in self.atoms.items():
            topic = backend.course
            if topic.get('academy') or topic.get('source_adapter') == 'hyperskill':
                # IDE rounds use the same atomic state, but cannot be answered in a paper.
                continue
            if topic['subject_id'] != subject or topic_id not in allowed:
                continue
            row = con.execute('SELECT body,version FROM topics WHERE learner=? AND topic=?', (self.learner, topic_id)).fetchone()
            if not row or row['version'] != topic['version']:
                continue
            state = atomic_state(json.loads(row['body']))
            if ((not sectioned_training(topic) and not state.get('completed_at')) or state['pause'] or state.get('pending_submission_id')
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
            if source.get('linked_points'):
                for row in source['linked_points']:
                    register_point(con, 'atomic', row['topic_id'], row['version'], row['local_id'], subject, now)
                    backend = self.atoms[row['topic_id']]
                    state = touched.setdefault(backend.topic_id, backend._load(con))
                    state['chunk_last_seen'][backend.point_chunks[row['local_id']]] = now
            else:
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
            if source.get('linked_points') and any(r['topic_id'] not in allowed for r in source['linked_points']):
                return False
        return True

    def unchanged(self, con, item):
        source = item['source']
        if source.get('linked_points'):
            from .linked_points import snapshot, unchanged
            bank = self.training.catalog.banks.get(source['container'])
            return bool(bank and bank['version'] == source['version']
                and self.dependencies.ready(con, self.access, group_key(source['container'], source['group_id']),
                                            current_time=utc_time(self.training.clock()).isoformat())
                and unchanged(snapshot(con, self.catalog, self.learner, source['local_points'], self.access), source['linked_points']))
        if source['kind'] == 'training':
            if self.training.catalog.bank(source['container'])['version'] != source['version']:
                return False
            local = source['local_points'][0]
            memory = self.training._memory(con, source['container'], local, source['version'])
            return (memory is not None and fingerprint(dict(memory)) == source['memory'][item['point_ids'][0]]
                    and self.dependencies.ready(con, self.access, group_key(source['container'], source['group_id']))
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
            if source.get('linked_points'):
                local = source['local_card']
                item['question'] = {**item['question'], '_linked_points': source['linked_points'], '_review_paper': paper['id']}
                self.training.atoms.update(self.atoms)
            memory = self.training._memory(con, source['container'], local, source['version'])
            con.execute('''INSERT INTO training_attempts
                (id,learner,bank,group_id,question,version,snapshot,mode,memory_revision,phase,created_at,submission_id)
                VALUES (?,?,?,?,?,?,?,?,?,'answer',?,?)''',
                (request_id, self.learner, source['container'], source['group_id'], local, source['version'],
                 json.dumps(item['question']), 'review' if source.get('linked_points') or memory['mastered'] or memory['card_json'] is None else 'initial', memory['revision'] if memory else 0,
                 paper['created_at'], request_id))
            con.execute('INSERT INTO training_submissions VALUES (?,?,?,?,?,?,?,?)',
                        (request_id, self.learner, request_id, fingerprint(item['answer']), item['answer'],
                         item.get('elapsed_ms'), 'judged', reason))
            row = con.execute('SELECT * FROM training_attempts WHERE id=?', (request_id,)).fetchone()
            self.training._complete(con, row, correct=correct, reason=reason, elapsed_ms=item.get('elapsed_ms'),
                                    rating=item.get('self_rating') if source.get('linked_points') else None,
                                    stop=bool(source.get('linked_points') and item.get('self_rating') == 5))
            result = json.loads(con.execute('SELECT result FROM training_attempts WHERE id=?', (request_id,)).fetchone()[0])
            if source.get('linked_points'):
                return {**result, 'ratings': {p: result['ratings'].get(ref) for p, ref in zip(item['point_ids'], source['local_points'])}}
            return {**result, 'ratings': {item['point_ids'][0]: result['rating']}}
        backend = self.atoms[source['container']]
        from .atomic_review import apply_review
        result = apply_review(con, backend, item['question'], source['local_points'], request_id,
            item['answer'], correct, reason, paper['created_at'], item.get('elapsed_ms'), item.get('self_rating'))
        result['ratings'] = {pid: result['ratings'][local] for pid, local in zip(item['point_ids'], source['local_points'])}
        return result
