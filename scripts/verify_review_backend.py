"""Independent arithmetic canary for the callable review backend.

Uses a new isolated output directory, generated public arithmetic questions,
and optionally the existing real answer-judging service. Never opens the live DB.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graspmemoedu.fsrs_engine import utc_time
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.review import ReviewBackend
from graspmemoedu.service import LearningService


def make_course():
    def question(name, left, right):
        return {'id': name, 'title': 'Arithmetic check', 'question': f'Calculate {left} + {right}.',
                'html': f'<p>{left} + {right} = ?</p>', 'reference_answer': str(left + right),
                'answer_type': 'number', 'constraints': 'Give one number.',
                'explanation_html': f'<p>The sum is {left + right}.</p>'}
    course = {'id': 'isolated-review-canary', 'title': 'Generated arithmetic canary',
              'introduction': {'html': '<p>Add two integers.</p>'},
              'modules': [{'id': 'addition', 'title': 'Addition',
                           'example': question('addition-example', 7, 3),
                           'practices': [question(f'addition-practice-{i}', i + 2, i + 7) for i in range(1, 10)]}]}
    course['version'] = hashlib.sha256(json.dumps(course, sort_keys=True).encode()).hexdigest()
    return course


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--real-judge', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not args.output.is_absolute() or output.exists():
        raise ValueError('Use a new absolute output directory')
    output.mkdir(parents=True)
    course = make_course()
    calls = []
    external = ExistingJudge(Path('D:/Projects/MathAnswerJudge')) if args.real_judge else None

    def judge(question, reference, answer):
        print(f'Judgement {len(calls) + 1}: requesting {"real" if external else "local"} judge', flush=True)
        verdict = (external(question, reference, answer) if external else
                   {'status': 'judged', 'correct': answer == reference, 'reason': 'Arithmetic canary oracle'})
        calls.append({'status': verdict['status'], 'correct': verdict['correct']})
        return verdict

    current = [datetime.now(timezone.utc)]
    learning = LearningService(course, output / 'data', judge, learner_id='review-canary')
    review = ReviewBackend(learning, clock=lambda: current[0])
    def lesson_payload(**extra):
        state = learning.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}
    learning.choose(lesson_payload(choice='mastered'))
    payload = lesson_payload(answer='10', elapsed_ms=1200)
    for _ in range(3):
        submission = learning.submit(payload)
        learning.process_submission(submission['submission_id'])
        result = learning.submission(submission['submission_id'])
        if result['status'] == 'judged':
            break
    assert result['status'] == 'judged' and result['correct'] is True
    assert learning.state()['modules'][0]['mastery'] == 4
    initial_due = review.next_due_at()
    assert initial_due is not None
    print('Lesson result enrolled; checking persisted retention setting', flush=True)
    review.configure_scheduler(desired_retention=0.94)
    assert review.next_due_at() == initial_due
    current[0] = utc_time(initial_due)
    assert review.due()[0]['module_id'] == 'addition'
    state = review.begin('addition', request_id='canary-review-start')
    assert review.due() == []
    sequence = []
    final_payload = None
    for index in range(3):
        answer = '0' if index == 0 else learning.questions[state['practice']['id']]['reference_answer']
        payload = {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                   'question_id': state['practice']['id'], 'answer': answer, 'elapsed_ms': 2100}
        sequence.append(state['practice']['id'])
        for _ in range(3):
            submission = review.submit(state['session_id'], payload)
            review.process_submission(submission['submission_id'])
            result = review.submission(submission['submission_id'])
            if result['status'] == 'judged':
                break
        assert result['status'] == 'judged' and result['correct'] == (index != 0)
        state = result['state']
        assert state['practice_target_count'] == 3
        if index < 2:
            assert state['mastery'] is None
            state = review.advance(state['session_id'], {
                'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'question_id': state['practice']['id']})
        else:
            final_payload = payload
    assert len(sequence) == len(set(sequence))
    assert state['status'] == 'completed' and state['mastery'] == 2
    assert utc_time(state['due_at']) > current[0]
    assert review.submit(state['session_id'], final_payload)['state'] == state
    assert review.due() == []
    restored_learning = LearningService(course, output / 'data', judge, learner_id='review-canary')
    restored = ReviewBackend(restored_learning, clock=lambda: current[0])
    assert restored.state(state['session_id']) == state
    assert restored.scheduler_settings()['desired_retention'] == 0.94
    with closing(restored_learning._connect()) as con:
        ratings = [dict(row) for row in con.execute('SELECT source,mastery,due_at,algorithm FROM review_ratings ORDER BY rowid')]
        assert len(ratings) == 2 and [row['mastery'] for row in ratings] == [4, 2]
        assert con.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    backup = restored_learning.backup(output / 'learning-snapshot.sqlite3')
    receipt = {'status': 'passed', 'real_judge': args.real_judge, 'judge_calls': calls,
               'fsrs_version': version('fsrs'), 'clock': 'advanced in isolation to the scheduled due time',
               'initial_due_at': initial_due, 'next_due_at': state['due_at'],
               'review_result': {'mastery': state['mastery'], 'practice_error_count': state['practice_error_count'],
                                 'practice_answered_count': state['practice_answered_count'], 'distinct_practices': len(set(sequence))},
               'settings': restored.scheduler_settings(), 'ratings': ratings,
               'restart_verified': True, 'submission_replay_verified': True,
               'database_backup': str(backup), 'backup_sha256': hashlib.sha256(backup.read_bytes()).hexdigest(),
               'live_website_database_opened': False}
    (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'passed', 'receipt': str(output / 'receipt.json'),
                      'real_judge': args.real_judge, 'judgements': len(calls)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
