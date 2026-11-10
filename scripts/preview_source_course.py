"""Prepare or serve an isolated native-course preview; never opens production."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from graspmemoedu.academy_import import install
from graspmemoedu.account_admin import _private_permissions
from graspmemoedu.api import create_app
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.course_graph import import_graph
from graspmemoedu.service import LearningService
from graspmemoedu.test_materials import TestMaterials
from tests.fixtures import DeterministicJudge
from tests.subject_matrix_fixture import write_matrix


def main(args):
    root = args.output.resolve()
    if not root.is_relative_to(Path('D:/Artifacts/GraspMemoEdu').resolve()):
        raise ValueError('Preview must use an isolated artifact directory')
    data = root / 'data'
    if args.prepare:
        root.mkdir(parents=True, exist_ok=False); data.mkdir()
        write_matrix(data)
        result = install(args.bundle / 'course-package', data)
        settings = Settings.load(data); catalog = Catalog.load(settings)
        declarations = json.loads((args.bundle / 'course-package/catalog.json').read_text(encoding='utf-8'))['courses'][0]
        course_id = declarations['id']
        policy = TestMaterials(data, catalog)
        assert course_id in policy.courses
        assert install(args.bundle / 'course-package', data)['status'] == 'unchanged'
        import_graph(settings, args.bundle / 'graph.json', apply=True)
        topics = [catalog.topic(row['id']) for row in declarations['topics']]
        (root / 'preview-native.json').write_text(json.dumps({'topics': topics}, ensure_ascii=False, indent=2), encoding='utf-8')
        report = {'status': 'installed_isolated', 'data': str(data), 'course_id': course_id,
            'subject_id': args.subject, 'production_opened': False, 'test_material': True,
            'native_snapshot': str(root / 'preview-native.json'), 'real_judge_validation': False,
            'source_binding': {'bundle': str(args.bundle.resolve()),
                'compilation_report_sha256': hashlib.sha256((args.bundle / 'compilation-report.json').read_bytes()).hexdigest(),
                'course_manifest_sha256': hashlib.sha256((args.bundle / 'course-package/manifest.json').read_bytes()).hexdigest(),
                'native_snapshot_sha256': hashlib.sha256((root / 'preview-native.json').read_bytes()).hexdigest()},
            'installation': result, 'topics': [{'id': t['id'], 'title': t['title'],
                'introductions': len(t['introductions']), 'chunks': len(t.get('memory_chunks', [])),
                'points': sum(len(c['points']) for c in t.get('memory_chunks', [])),
                'flashcards': len(t.get('flashcards', [])),
                'bundles': sum(len(c['point_ids']) > 1 for c in t.get('flashcards', [])),
                'examples': len(t['modules']), 'practices': sum(len(m['practices']) for m in t['modules'])}
                for t in topics]}
        (root / 'preview-catalog.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return
    if not (root / 'preview-catalog.json').exists() or (data / 'cloud-authoritative.json').exists():
        raise ValueError('Serve only a prepared isolated preview')
    import uvicorn
    settings = Settings(data_dir=data, port=args.port)
    catalog = Catalog.load(settings)
    app = create_app(settings, LearningService(catalog.topic('lesson-math'), data, DeterministicJudge()))
    store = app.state.access_store
    with store._transaction() as con:
        account = store._insert_account(con, 'Native course preview', purpose='test', is_admin=True)
    private = root / 'preview-identity.private.json'; private.touch()
    _private_permissions(private, apply=True)
    private.write_text(json.dumps({'invitation_code': account['invitation_code']}), encoding='utf-8')
    print('READY isolated native-course preview on ' + str(args.port), flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--subject', default='english')
    parser.add_argument('--port', type=int, default=18863)
    main(parser.parse_args())
