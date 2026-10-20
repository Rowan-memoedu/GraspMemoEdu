"""Question-bank navigation over existing atomic cards; never a second memory store."""
from contextlib import closing
import json

from .service import DomainError
from .topic_runtime import is_introduction


class AtomicBanks:
    def __init__(self, catalog, subjects, policy):
        self.catalog, self.subjects, self.policy = catalog, subjects, policy
        self.banks = {cid: c for cid, c in catalog.courses.items()
                      if c.get('placement') == 'question_bank'}

    def members(self, bank, access):
        allowed = {t['id'] for t in access['topics']}
        return [t['id'] for t in bank['topics']
                if t['id'] in allowed and self.policy.topic_visible(t['id'], access)]

    def visible(self, access, subject_id):
        return [{**{k: bank.get(k, '') for k in ('id', 'title', 'description')},
                 'kind': 'atomic', 'subject_id': self.subjects.course_subjects[bank['id']]}
                for bank in self.banks.values()
                if (subject_id is None or self.subjects.course_subjects[bank['id']] == subject_id)
                and self.policy.course_visible(bank, access) and self.members(bank, access)]

    def bank(self, bank_id, access, subject_id, backend):
        bank = self.banks[bank_id]
        if subject_id is not None and self.subjects.course_subjects[bank_id] != subject_id:
            raise DomainError('subject_bank_mismatch', '此题库不属于当前学科。', 403)
        members = set(self.members(bank, access))
        if not members or not self.policy.course_visible(bank, access):
            raise DomainError('topic_forbidden', '当前身份没有访问此题库的权限。', 403)
        # TrainingService supplies the same connection scope as the learner:
        # test-admin previews use memory, formal students use their existing DB.
        with closing(backend._connect()) as con:
            states = {row['topic']: json.loads(row['body']) for row in con.execute(
                'SELECT topic,body FROM topics WHERE learner=?', (backend.learner_id,))
                if row['topic'] in members}
        parents = {u['id']: u.get('parent_id') for u in bank['units']}
        included = {u['id'] for u in bank['units'] if members.intersection(u['topic_ids'])}
        for uid in list(included):
            while parents.get(uid) is not None:
                uid = parents[uid]
                included.add(uid)
        groups = []
        for unit in bank['units']:
            if unit['id'] not in included:
                continue
            groups.append({k: unit[k] for k in ('id', 'title', 'parent_id') if k in unit}
                          | {'question_count': 0, 'introduction_count': 0, 'completed_count': 0})
            for tid in unit['topic_ids']:
                if tid not in members:
                    continue
                topic = self.catalog.topic(tid)
                reading = is_introduction(topic)
                state = states.get(tid, {})
                completed = bool(state.get('completed_at')) if reading else any(
                    p.get('mastered') for p in state.get('points', {}).values())
                groups.append({'id': tid, 'topic_id': tid, 'title': topic['title'], 'parent_id': unit['id'],
                               'question_count': int(not reading), 'introduction_count': int(reading),
                               'completed_count': int(completed and not reading),
                               'progress': 100 if completed else 0})
        return {'id': bank_id, 'title': bank['title'], 'kind': 'atomic', 'groups': groups}
