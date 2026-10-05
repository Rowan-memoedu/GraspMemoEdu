"""Populate paired test accounts with retained, explicitly synthetic Q&A examples."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.cli import make_help_store
from graspmemoedu.config import Settings
from graspmemoedu.test_scenarios import validate_plan


def seed(data_dir, plan):
    store = make_help_store(Settings.load(data_dir))
    with closing(store.access_store._connect()) as con:
        validate_plan(data_dir, plan, con)
    scenarios = []
    for bank_id in plan['banks']:
        bank = store.training.bank(bank_id)
        group = next(g for g in bank['groups'] if g['questions'])
        q = group['questions'][0]
        base = {'topic_id': 'training.' + bank_id, 'bank_id': bank_id, 'group_id': group['id'],
                'question_id': q['id'], 'content_version': bank['version']}
        scenarios += [{**base, 'content_block_id': f'{kind}:' + q['id']} for kind in ('question', 'explanation')]
        with closing(store.access_store._connect()) as con:
            variants = con.execute('''SELECT v.id FROM review_variants v JOIN review_points p ON p.id=v.point
                WHERE p.container=? AND p.version=? AND p.source_point=? ORDER BY v.id''', (bank_id, bank['version'], q['id'])).fetchall()
        if variants:
            scenarios.append({**base, 'content_version': variants[0][0], 'content_block_id': 'question:' + q['id']})
    for tid in plan['topics']:
        topic = store.catalog.topics[tid]
        scenarios.append({'topic_id': tid, 'content_block_id': 'introduction', 'content_version': topic['version']})
        if topic.get('training_mode') == 'atomic_retrieval':
            q = topic['flashcards'][0]
            scenarios += [{'topic_id': tid, 'question_id': q['id'], 'content_block_id': f'{kind}:' + q['id'],
                           'content_version': topic['version']} for kind in ('question', 'explanation')]
    scenarios = scenarios[:12]
    created = []
    for learner in plan['learners']:
        with closing(store.access_store._connect()) as con:
            student = store.access_store._public(store.access_store._account(con, learner))
            owner = store._owner(con, learner)
            teacher = store.access_store._public(store.access_store._account(con, owner))
            if teacher.get('purpose') != 'test' or not teacher.get('is_admin'):
                raise ValueError('Test Q&A must be assigned to a test administrator')
        for index, context in enumerate(scenarios):
            request_id = f'test-scenarios-v1:{learner}:{index}'
            with closing(store.access_store._connect()) as con:
                old = con.execute('SELECT id FROM help_requests WHERE learner_id=? AND request_id=?', (learner, request_id)).fetchone()
            if old:
                continue  # Never overwrite a user's later edits or read state.
            label = ['待回复', '已回复未读', '已读回复', '回复已修订'][index % 4]
            instant = datetime.now(timezone.utc) - timedelta(minutes=30 - index)
            with patch('graspmemoedu.help_requests.now', return_value=instant.isoformat()):
                saved = store.save(student, {**context, 'request_id': request_id, 'kind': 'quality' if index == 10 else 'help',
                    'message': f'【测试预置 {index+1:02d} · {label}】请解释这段内容的依据。此条用于测试答疑，不是真实学习问题。'})
                if index % 4:
                    store.reply(teacher, {'report_id': saved['id'], 'request_id': request_id + ':reply',
                        'expected_revision': 0, 'message': '【测试回复】先确认题目条件，再按正文所示步骤推导。此回复供未读、已读和修订流程测试。'})
                if index % 4 in (2, 3):
                    store.mark_read(student, {'report_id': saved['id'], 'revision': 1})
                if index % 4 == 3:
                    store.reply(teacher, {'report_id': saved['id'], 'request_id': request_id + ':revision',
                        'expected_revision': 1, 'message': '【测试修订回复】这里补充了第二步的理由，请重新阅读。'})
            created.append({'learner_id': learner, 'id': saved['id'], 'scenario': label})
    return {'status': 'test_help_ready', 'created': len(created), 'records': created}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(seed(args.data_dir, json.loads(args.plan.read_text(encoding='utf-8-sig'))), ensure_ascii=False))


if __name__ == '__main__':
    main()
