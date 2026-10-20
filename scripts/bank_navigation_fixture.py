"""Synthetic seven-subject banks with all three graders; no external services."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import socket
import sys

repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(repo / 'tests')]
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.service import LearningService
from graspmemoedu.training_content import fingerprint, validate
from tests.fixtures import DeterministicJudge
from tests.subject_matrix_fixture import write_matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18873)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.exists() or not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or args.port in (8765, 18768):
        raise ValueError('Use a new isolated artifact directory and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    topics = write_matrix(root / 'data')
    for path in (root / 'data/training-banks').glob('*.json'):
        bank = json.loads(path.read_bytes())
        group = bank['groups'][1]
        long_text = ''.join(f'<p>Reading position {i}: preserve this paragraph across tab switches.</p>' for i in range(45))
        for question in group['questions'][:2]:
            question['html'] += long_text
        academy = deepcopy(group['questions'][0])
        academy.update(id='academy', title='IDE card', academy={'task_key': 'fixture', 'kind': 'automated', 'version': 'v1'})
        hyperskill = deepcopy(academy)
        hyperskill.pop('academy')
        hyperskill.update(id='hyperskill', title='Hyperskill card', answer_type='external',
            reference_answer='', explanation_html='',
            hyperskill={'topic_id': 101, 'theory_id': 102, 'step_id': 200, 'type': 'choice'})
        group['questions'].extend([academy, hyperskill])
        group['introductions'] = [{'id': 'reading', 'title': 'Reading', 'text': 'Reading position fixture', 'html': long_text}]
        for index in range(18):
            question = deepcopy(group['questions'][1])
            question.update(id=f'extra-{index}', title=f'Additional question {index}')
            group['questions'].append(question)
        group['content_order'] = [{'type': 'question', 'id': question['id']} for question in group['questions']]
        group['content_order'].append({'type': 'introduction', 'id': 'reading'})
        bank['version'] = fingerprint(bank)
        validate(bank)
        path.write_text(json.dumps(bank), encoding='utf-8')
    app = create_app(Settings(data_dir=root / 'data', port=args.port),
        LearningService(topics['lesson-math'], root / 'data', DeterministicJudge()))
    accounts = [app.state.access_store.create_account(f'Navigation fixture {i}', is_student=True, purpose='test')
                for i in range(2)]
    (root / 'accounts.private.json').write_text(json.dumps(accounts), encoding='utf-8')
    print('READY navigation fixture; synthetic content, no external judging', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
