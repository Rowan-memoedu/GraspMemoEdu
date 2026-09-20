"""Isolated end-to-end canary for persistent grouped Practice delivery.

--real-judge uses ExistingJudge for the first five arithmetic answers (Lesson
and the first Review); the exhaustive queue checks use an explicit local oracle.
Never reads the production course, credentials, or learning database directly.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from math_learning.fsrs_engine import utc_time
from math_learning.judge import ExistingJudge
from math_learning.review import ReviewBackend
from math_learning.service import DomainError, LearningService


def make_course():
    def question(index):
        return {'id': f'q-{index}', 'title': f'Practice {index}',
                'question': f'Calculate {index + 20} + 3.',
                'html': f'<p>{index + 20} + 3 = ?</p>',
                'reference_answer': str(index + 23), 'answer_type': 'integer',
                'constraints': 'Give one integer.',
                'explanation_html': f'<p>The answer is {index + 23}.</p>'}
    practices = [question(i) for i in range(1, 16)]
    for index, question_data in enumerate(practices):
        group, member = index // 3 + 1, index % 3 + 1
        question_data['source_practice'] = {'group': group, 'member': member,
                                             'label': f'{group}.{member}'}
    result = {'id': 'isolated-grouped-canary', 'title': 'Grouped arithmetic canary',
              'introduction': {'html': '<p>Add integers.</p>'},
              'modules': [{'id': 'addition', 'title': 'Addition',
                           'example': question(0), 'practices': practices}]}
    result['version'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--real-judge', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    allowed = Path('D:/Artifacts/MathLearningWeb/grouped-practice').resolve()
    if not args.output.is_absolute() or root.exists() or not root.is_relative_to(allowed):
        raise ValueError('Use a new directory under D:/Artifacts/MathLearningWeb/grouped-practice')
    root.mkdir(parents=True)
    course = make_course()
    calls, delivered, rounds, snapshots = [], [], [], []
    clock = [datetime.now(timezone.utc)]
    external = ExistingJudge(Path('D:/Projects/MathAnswerJudge')) if args.real_judge else None

    def judge(question, reference, answer):
        real = external is not None and len(calls) < 5
        print(f'Judgement {len(calls) + 1}: {"real" if real else "local oracle"}', flush=True)
        result = external(question, reference, answer) if real else {
            'status': 'judged', 'correct': answer == reference, 'reason': 'Canary arithmetic oracle'}
        calls.append({'real': real, 'status': result['status'], 'correct': result['correct']})
        return result

    def services():
        service = LearningService(course, root / 'data', judge, learner_id='grouped-canary')
        return service, ReviewBackend(service, clock=lambda: clock[0])

    learning, review = services()

    def payload(**extra):
        state = learning.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def active():
        state = learning.state()
        return next(step for step in state['steps'] if step['id'] == state['active_step_id'])

    def queue():
        with closing(learning._connect()) as con:
            tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if 'practice' in row[0] and 'queue' in row[0]]
            assert len(tables) == 1, tables
            rows = [dict(row) for row in con.execute(f'SELECT * FROM {tables[0]}')]
        return {'table': tables[0], 'rows': rows}

    def group(qid):
        return learning.questions[qid]['source_practice']['group']

    learning.choose(payload(choice='unmastered'))
    while active()['kind'] != 'practice':
        learning.advance(payload())
    initial_body = json.loads(queue()['rows'][0]['body'])
    initial_five = [active()['question_id'], *initial_body['pending']]
    assert len(initial_five) == 5 and len(set(map(group, initial_five))) == 5
    for _ in range(2):
        qid = active()['question_id']
        delivered.append(qid)
        submission = learning.submit(payload(answer=learning.questions[qid]['reference_answer']))
        learning.process_submission(submission['submission_id'])
        result = learning.submission(submission['submission_id'])
        assert result['status'] == 'judged' and result['correct'] is True
        snapshots.append(queue())
        learning.advance(payload())
    assert learning.state()['status'] == 'completed'
    assert len(set(map(group, delivered))) == 2
    initial = queue()
    learning, review = services()
    assert queue() == initial, 'Restart changed persistent queue'
    first_wrong_group = None
    wrong_in_group = 0
    # Continue due pushes until every question has been delivered at least once.
    # Each group representative and later siblings are observed, never predicted.
    while len(set(delivered)) < 15:
        assert len(rounds) < 20
        clock[0] = utc_time(review.next_due_at())
        state = review.begin('addition', request_id=str(uuid.uuid4()))
        sequence = []
        while state['status'] == 'in_progress':
            qid = state['practice']['id']
            assert qid not in sequence, 'Duplicate within one round'
            assert qid not in delivered, 'Repeated before exhausting all fifteen questions'
            sequence.append(qid)
            delivered.append(qid)
            if first_wrong_group is None:
                first_wrong_group = group(qid)
            wrong = group(qid) == first_wrong_group and wrong_in_group < 2 and not state['practice_error_count']
            if wrong:
                wrong_in_group += 1
            before = queue()
            data = {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                    'question_id': qid, 'answer': '-999' if wrong else learning.questions[qid]['reference_answer']}
            submission = review.submit(state['session_id'], data)
            review.process_submission(submission['submission_id'])
            result = review.submission(submission['submission_id'])
            assert result['status'] == 'judged' and result['correct'] is not wrong
            state = result['state']
            after = queue()
            assert review.submit(state['session_id'], data)['state'] == state
            assert queue() == after, 'Submission replay duplicated queue mutation'
            if wrong:
                snapshots.append({'wrong': qid, 'before': before, 'after': after})
                old_body = json.loads(before['rows'][0]['body'])
                new_body = json.loads(after['rows'][0]['body'])
                if wrong_in_group == 1:
                    assert new_body['pending'][:len(old_body['pending'])] == old_body['pending']
                    additions = new_body['pending'][len(old_body['pending']):]
                    assert len(additions) == 2 and all(group(item) == group(qid) for item in additions)
                else:
                    assert new_body['pending'] == old_body['pending'], 'Same group expanded twice'
            assert review.state(state['session_id']) == state
            if state['status'] == 'in_progress':
                state = review.advance(state['session_id'], {'request_id': str(uuid.uuid4()),
                    'expected_revision': state['revision'], 'question_id': qid})
        assert state['mastery'] == (2 if state['practice_error_count'] else 4)
        rounds.append({'questions': sequence, 'mastery': state['mastery']})
    assert wrong_in_group == 2
    assert len(set(map(group, delivered[:5]))) == 5
    assert len(delivered) == 15
    clock[0] = utc_time(review.next_due_at())
    state = review.begin('addition', request_id=str(uuid.uuid4()))
    assert state['practice']['id'] == delivered[0], 'Exhausted pool must use oldest delivery first'
    before_pause = queue()
    paused = review.pause(state['session_id'], request_id=str(uuid.uuid4()))
    assert paused['status'] == 'paused' and paused['mastery'] is None
    assert queue() == before_pause, 'Pausing must preserve the persistent queue'
    review.unlock_topic(pause_event_id=paused['pause_event_id'])
    assert queue() == before_pause, 'Unlocking must preserve the persistent queue'
    learning, review = services()
    assert queue() == before_pause
    state = review.begin('addition', request_id=str(uuid.uuid4()))
    assert state['practice']['id'] == delivered[1], 'Next push must continue the oldest-delivery order'
    unchanged = queue()
    try:
        review.advance(state['session_id'], {'request_id': str(uuid.uuid4()),
            'expected_revision': state['revision'] - 1, 'question_id': state['practice']['id']})
    except DomainError as exc:
        assert exc.code == 'stale_revision'
    else:
        raise AssertionError('Stale revision was accepted')
    assert queue() == unchanged, 'Rejected action consumed a queue item'
    backup = learning.backup(root / 'queue-snapshot.sqlite3')
    current_queue = queue()
    with closing(sqlite3.connect(backup)) as con:
        con.row_factory = sqlite3.Row
        backed_up = [dict(row) for row in con.execute(f'SELECT * FROM {current_queue["table"]}')]
        assert backed_up == current_queue['rows']
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    # Reset removes only the chosen identity's queue; another learner persists.
    other = LearningService(course, root / 'data', judge, learner_id='other-canary')
    with other._transaction() as con:
        other.practice_queue.draw(con, course['modules'][0])
        other_before = other.practice_queue.snapshot(con, course['modules'][0])
    learning.reset_progress(payload())
    with closing(learning._connect()) as con:
        assert learning.practice_queue.snapshot(con, course['modules'][0]) is None
        assert other.practice_queue.snapshot(con, course['modules'][0]) == other_before
    receipt = {'status': 'passed', 'real_judge_requested': args.real_judge,
               'calls': calls, 'unique_first_pass': delivered, 'rounds': rounds,
               'snapshots': snapshots, 'persistent_queue': current_queue,
               'restart_pause_unlock_backup_verified': True,
               'stale_action_and_identity_scoped_reset_verified': True,
               'live_database_opened': False, 'backup': str(backup)}
    (root / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'passed', 'receipt': str(root / 'receipt.json')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
