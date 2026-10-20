"""Loopback-only composed Topic acceptance; never installs production content."""
import argparse
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException, Request
import uvicorn
from graspmemoedu.api import create_app
from graspmemoedu.config import Settings
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.service import create_learning_service
from tests.test_unified_learning import composed_topic
from tests.test_ordered_learning import ordered_topic
from tests.atomic_fixtures import atomic_topic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18864)
    parser.add_argument('--real-judge', action='store_true')
    parser.add_argument('--visual-chunks', action='store_true')
    args = parser.parse_args()
    root = args.output.resolve()
    if not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()) or root.exists() or args.port in (8765, 18768):
        raise ValueError('Use a new isolated artifact directory and port')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', args.port))
    root.mkdir(parents=True)
    topic = composed_topic()
    if args.visual_chunks:
        intro = topic['introductions'][0]
        intro['html'] += '<h3>完整指导的后续讲解</h3>' + ''.join(
            f'<p>补充讲解 {i}：标签、名称与属性应在上下文中理解。此段不属于当前组块的划线范围。</p>'
            for i in range(1, 16))
        intro['html'] += '<pre><code>const example = \"' + 'long_source_line_' * 12 + '\";</code></pre>'
        topic['memory_chunks'][0].update(
            content_html='<p>甲标签对应<strong>红色</strong>，乙标签对应<strong>蓝色</strong>。</p><p>记住名称与属性之间的对应关系。</p>',
            mnemonic_html='<p>把甲、乙想象成两个不同颜色的标签：<strong>甲红，乙蓝</strong>。</p>')
    topic['flashcards'] = [card for card in topic['flashcards'] if len(card['point_ids']) == 1]
    if args.real_judge:
        topic['flashcards'][0]['interaction'] = {'version': 1, 'type': 'text', 'grading': 'semantic'}
    legacy = ordered_topic()
    legacy.update(id='legacy-paradigm', subject_id='math')
    introduction = ordered_topic(True)
    introduction.update(id='legacy-introduction', subject_id='math')
    deferred = composed_topic()
    deferred.update(id='deferred-atoms', modules=[])
    deferred['content_order'] = [item for item in deferred['content_order'] if item['kind'] == 'introduction']
    topics = [topic, legacy, introduction, atomic_topic('math', 'legacy-atomic'), deferred]
    for item in topics:
        for module in item.get('modules', []):
            for question in [module['example'], *module['practices']]:
                question.update(reference_answer='5', interaction={
                    'version': 1, 'type': 'text', 'grading': 'exact', 'answers': ['5']})
        (root / (item['id'] + '.json')).write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
    (root / 'catalog.json').write_text(json.dumps({'courses': [{
        'id': 'unified-fixture', 'title': '统一 Topic 隔离验收',
        'topics': [{'id': item['id'], 'path': item['id'] + '.json'} for item in topics]}]}), encoding='utf-8')
    when = datetime.now(timezone.utc)
    judge = ExistingJudge(Path('D:/Projects/MathAnswerJudge'))
    calls = []

    def tracked_judge(question, reference, answer):
        result = judge(question, reference, answer)
        calls.append({'status': result['status'], 'correct': result.get('correct')})
        return result

    settings = Settings(data_dir=root, course_path=root / (topic['id'] + '.json'), port=args.port)
    app = create_app(settings, create_learning_service(topic, root, tracked_judge if args.real_judge else None),
                     atomic_clock=lambda: when)

    @asynccontextmanager
    async def lifespan(_):
        async with app.router.lifespan_context(app):
            yield

    harness = FastAPI(lifespan=lifespan)

    def learner(request):
        if request.client.host != '127.0.0.1' or request.headers.get('origin') != f'http://127.0.0.1:{args.port}':
            raise HTTPException(403)
        access = app.state.access_store.resolve(request.headers.get('x-learning-identity'))
        return app.state.learning_services[(access['learner_id'], topic['id'])]

    @harness.post('/__qa/advance')
    async def advance(request: Request):
        nonlocal when
        learner(request)
        seconds = (await request.json())['seconds']
        if type(seconds) is not int or not 0 <= seconds <= 86400:
            raise HTTPException(422)
        when += timedelta(seconds=seconds)
        return {'server_time': when.isoformat()}

    @harness.get('/__qa/audit')
    def audit(request: Request):
        backend = learner(request)
        with closing(backend._connect()) as con:
            saved = backend._load(con)
            return {'state': saved, 'integrity': con.execute('PRAGMA integrity_check').fetchone()[0],
                    'real_judge': args.real_judge, 'judge_calls': calls}

    @harness.post('/__qa/advanced')
    def advanced(request: Request):
        learner(request)
        store = app.state.access_store
        account = store.create_account('Synthetic advanced acceptance', is_advanced_learner=True)
        return store.login(account['invitation_code'])

    harness.mount('/', app)
    print('READY unified fixture; synthetic content only', flush=True)
    uvicorn.run(harness, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
