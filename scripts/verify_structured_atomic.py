"""Independent package install/restore and real-judge smoke test in a new local directory."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
import argparse
import json
from pathlib import Path
import sqlite3
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu.academy_import import install
from graspmemoedu.atomic import AtomicLearningService
from graspmemoedu.atomic_reading_upgrade import verify_transition
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.judge import ExistingJudge
from graspmemoedu.question_types import demo_answer
from graspmemoedu.service import create_learning_service
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.topic_install import encoded


def verify(package, output):
    output.mkdir(parents=True, exist_ok=False)
    data = output / 'data'; data.mkdir()
    incoming = json.loads((package / 'catalog.json').read_bytes())['courses'][0]
    course = deepcopy(incoming)
    topics = {}
    for declaration in course['topics']:
        topic = json.loads((package / declaration['path']).read_bytes())
        topics[topic['id']] = topic
        old = {k: deepcopy(v) for k, v in topic.items() if k not in ('memory_chunks', 'memory_points', 'flashcards')}
        old.update(topic_kind='introduction', training_mode='paradigm', version='isolated-reading-before-' + topic['id'])
        name = topic['id'] + '.json'; (data / name).write_bytes(encoded(old)); declaration['path'] = name
    parents = {u.get('parent_id') for u in course.get('units', []) if u.get('parent_id')}
    for parent in parents:
        if not any(u['id'] == parent for u in course['units']):
            course['units'].append({'id':parent,'title':'Isolated parent','topic_ids':[]})
    (data / 'catalog.json').write_bytes(encoded({'courses':[course]}))
    subject = next(iter(topics.values()))['subject_id']
    (data / 'subjects.json').write_bytes(encoded({'subjects':[{'id':subject,'course_ids':[course['id']]}]}))
    before = Catalog.load(Settings.load(data))
    for tid in list(topics)[:3]: create_learning_service(before.topic(tid), data, learner_id='isolated-reading').state()
    snapshot = output / 'before.sqlite3'
    with closing(sqlite3.connect(data / 'learning.sqlite3')) as source, closing(sqlite3.connect(snapshot)) as target:
        source.backup(target)
    installed = install(package, data, extend_course=True)
    transition = verify_transition(snapshot, data / 'learning.sqlite3', set(topics))
    current = Catalog.load(Settings.load(data))
    assert all(current.topic(tid) == topic for tid, topic in topics.items())
    assert install(package, data, extend_course=True)['status'] == 'unchanged'
    archive = output / 'installed-site.zip'; backup_site(Settings.load(data), archive)
    restored = output / 'restored'; restore_site(archive, restored)
    assert all(Catalog.load(Settings.load(restored)).topic(tid) == topic for tid, topic in topics.items())
    # A one-point projection uses unchanged authored question/answer text and the
    # exact same point engine. Its clock and records belong only to this directory.
    projected = deepcopy(next(iter(topics.values())))
    chunk = deepcopy(projected['memory_chunks'][0]); chunk['points'] = chunk['points'][:1]
    point = chunk['points'][0]['id']; chunk.pop('introduction_id', None)
    projected['memory_chunks'] = [chunk]
    projected['introduction'] = projected['introductions'][0]
    projected['flashcards'] = [c for c in projected['flashcards'] if c['point_ids'] == [point]]
    for key in ('topic_format_version','topic_kind','introductions','content_order','content_version'):
        projected.pop(key, None)
    projected['id'] = 'isolated-real-judge'
    when = datetime(2026, 10, 20, tzinfo=timezone.utc)
    backend = AtomicLearningService(projected, output / 'real-judge-data', ExistingJudge(Path('D:/Projects/MathAnswerJudge')),
                                    learner_id='isolated', clock=lambda: when)
    def payload(**extra):
        s = backend.state()
        return {'request_id':str(uuid.uuid4()), 'expected_revision':s['revision'], 'attempt_id':s['attempt_id'],
                'step_id':s['active_step_id'], **extra}
    def answer():
        s = backend.state(); card = backend.questions[s['current']['card_id']]
        receipt = backend.submit(payload(answer=demo_answer(card))); backend.process_submission(receipt['submission_id'])
        result = backend.submission(receipt['submission_id'])
        if result['status'] != 'judged' or not result['correct']:
            raise ValueError('Real judging failed: ' + json.dumps(result, ensure_ascii=False)[:1000])
        return card['id'], result
    backend.choose(payload(choice='learn'))
    while backend.state()['phase'] != 'answer': backend.advance(payload())
    assert 'learn' in backend.questions[backend.state()['current']['card_id']].get('learning_phases', ['learn','review'])
    initial_id, first = answer()
    backend.advance(payload())
    when += timedelta(hours=7)
    backend.choose(payload(choice='review'))
    while backend.state()['phase'] != 'answer': backend.advance(payload())
    review_id, reviewed = answer()
    assert backend.questions[review_id].get('learning_phases') == ['review']
    report = {'passed':True, 'install':installed, 'database_transition':transition,
              'backup_restore_verified':True, 'replay_unchanged':True,
              'real_judge':{'initial_card':initial_id,'review_card':review_id,'correct':True,
                            'initial_rating':first['state']['current']['ratings'],
                            'review_rating':reviewed['state']['current']['ratings']},
              'formal_learning_data_opened':False}
    (output / 'verification.json').write_bytes(encoded(report))
    body = []
    for topic in topics.values():
        body.append('<details><summary>' + escape(topic['title']) + '</summary>')
        chunks = {c['id']:c for c in topic['memory_chunks']}
        for chunk in chunks.values():
            body.append('<details><summary>' + escape(chunk['title']) + '</summary>' + chunk['html'])
            for p in chunk['points']:
                body.append('<details><summary>' + escape(p['text']) + '</summary>')
                for card in topic['flashcards']:
                    if card['point_ids'] != [p['id']]: continue
                    body.append('<h4>' + ('可变设问' if card.get('learning_phases') == ['review'] else '原始单点卡') + '</h4>' + card['html'])
                    spec = card.get('interaction') or {}
                    for key in ('options','statements','left','right'):
                        if key in spec: body.append('<ul>' + ''.join('<li>' + x.get('html',escape(x['text'])) + '</li>' for x in spec[key]) + '</ul>')
                    body.append('<details><summary>答案与解析</summary><pre>' + escape(card['reference_answer']) + '</pre>' + card['explanation_html'] + '</details>')
                body.append('</details>')
            body.append('</details>')
        body.append('</details>')
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Data types and variables：原子设问</title><style>body{max-width:1000px;margin:40px auto;padding:0 20px;font:17px/1.65 system-ui}summary{cursor:pointer;padding:9px 0}details{margin:10px 0 10px 18px}pre{white-space:pre-wrap;background:#f4f3ef;padding:12px}img{max-width:100%}</style><h1>Data types and variables：原子设问</h1>' + ''.join(body) + '</html>'
    (output / '阅读稿.html').write_text(html, encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True); args=parser.parse_args(); verify(args.package,args.output)
