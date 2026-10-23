"""Automatically produce explicit Topics from original articles with Codex Exec.

prepare-input: raw articles / private reading export + explicit known catalog.
run: ordered analysis and teaching production, each independently reviewed.
prepare: immutable schema-2 source + reusable known points + optional package.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from graspmemoedu import context_explicit as ce
from graspmemoedu import context_production as cp


def prepare_input(args):
    raw, known = cp.read(args.input), ce.load_known(args.points)
    document = ce.prepare_input(raw,known,args.topic_prefix)
    cp.save(args.output,document)
    cp.save(args.output.with_suffix('.source.json'),raw)
    print(cp.json.dumps({'tasks':len(document['tasks']),'output':str(args.output)},ensure_ascii=False))


def make_factory(args):
    # Import only when a new model call is actually needed; approved reuse is
    # offline and must not require an executable, login or model configuration.
    def factory(path):
        from graspmemoedu.codex_exec_client import CodexExecClient
        return CodexExecClient(path,executable=args.executable,model=args.model,timeout=args.timeout)
    return factory


def run(args, factory=None):
    document = cp.read(args.input); ce.validate_input(document)
    cp.save(args.run_root/'input-snapshot.json',document)
    cp.save(args.run_root/'prompts.json',{'backend':ce.BACKEND,'analysis':ce.ANALYZE,
        'analysis_review':ce.ANALYSIS_REVIEW,'author':ce.AUTHOR,'author_review':ce.AUTHOR_REVIEW,
        'quality_policy':cp.QUALITY_POLICY,'author_policy':ce.AUTHOR_POLICY,
        'learner_review':cp.LEARNER_REVIEW})
    factory = factory or make_factory(args)
    additions, rows = [], []
    for task in document['tasks']:
        source = ce.effective_source(document,task,additions)
        record,origin = ce.run_source(source,args.run_root/task['id'],args.library,factory,args.attempts,
                                     getattr(args,'reuse_analysis_runs',()))
        if 'record_sha256' not in record:
            rows.append({'id':task['id'],'status':record['status'],'origin':origin,'details':record})
            # Later sources must see every preceding accepted target. Stop at
            # a failed source rather than generating against an incomplete set.
            break
        topic = ce.record_topic(record)
        points = ce.exported_points(topic,document['subject_id'])
        additions.extend(points['points'])
        cp.save(args.run_root/task['id']/'known-points.json',points)
        if topic:
            cp.save(args.run_root/task['id']/'structured-topics.json',{'schema':2,'topics':[topic]})
        rows.append({'id':task['id'],'status':'accepted','origin':origin,'topic_id':task['topic_id'],
                     'points':len(points['points']),'record_sha256':record['record_sha256'],
                     'decisions':[{k:d[k] for k in ('point_id','status','reason')} for d in record['candidate']['decisions']]})
    report = {'backend':ce.BACKEND,'quality_policy':cp.QUALITY_POLICY,'author_policy':ce.AUTHOR_POLICY,
        'total':len(document['tasks']),'finished':len(rows),
        'accepted':sum(r['status']=='accepted' for r in rows),'topics':sum(r.get('points',0)>0 for r in rows),
        'new_points':len(additions),'reused':sum(r['origin'] in ('library','retained') for r in rows),
        'complete':len(rows)==len(document['tasks']) and all(r['status']=='accepted' for r in rows),'results':rows}
    cp.save(args.run_root/('report-'+cp.digest(report)[:12]+'.json'),report)
    print(cp.json.dumps(report,ensure_ascii=False),flush=True)
    return 0 if report['complete'] else 2


def prepare(args):
    document = cp.read(args.input); ce.validate_input(document)
    additions, topics, records, decisions = [], [], [], []
    for task in document['tasks']:
        source = ce.effective_source(document,task,additions)
        saved = []
        for root in args.runs:
            path = root/task['id']/'result.json'
            if path.exists():
                record = cp.read(path); ce.require_quality(record)
                if record['source'] != source:
                    raise ValueError('Accepted source does not match the ordered known point catalog')
                saved.append(record)
        if not saved or any(r != saved[0] for r in saved[1:]):
            raise ValueError('Every source needs one consistent accepted result')
        record = saved[0]
        library_record = cp.read(args.library/'explicit'/(record['record_sha256']+'.json'))
        ce.require_quality(library_record)
        if record != library_record:
            raise ValueError('Run result differs from the approved library record')
        topic = ce.record_topic(record)
        points = ce.exported_points(topic,document['subject_id'])
        additions.extend(points['points'])
        if topic: topics.append(topic)
        records.append({'task_id':task['id'],'record_sha256':record['record_sha256'],
                        'quality_policy':cp.QUALITY_POLICY,'author_policy':ce.AUTHOR_POLICY,
                        'analysis_record_sha256':record['analysis_record']['record_sha256']})
        decisions.append({'task_id':task['id'],'decisions':record['candidate']['decisions']})
    args.output.mkdir(parents=True,exist_ok=False)
    output = {'schema':2,'topics':topics}
    cp.save(args.output/'structured-topics.json',output)
    for topic in topics:
        cp.save(args.output/'known-points'/(topic['id']+'.json'),ce.exported_points(topic,document['subject_id']))
    cp.save(args.output/'known-points.json',{'schema':1,'subject_id':document['subject_id'],
                                          'points':additions,'tasks':[]})
    cp.save(args.output/'decisions.json',decisions)
    cp.save(args.output/'records.json',records)
    cp.save(args.output/'input-snapshot.json',document)
    if args.compile:
        if not topics:
            raise ValueError('No new grounded Topic was produced; inspect decisions instead of inventing one')
        from scripts.prepare_structured_atomic import prepare as compile_source
        compile_source(argparse.Namespace(input=args.output/'structured-topics.json',output=args.output/'bundle',
            subject=document['subject_id'],course_id=args.course_id,course_title=args.course_title,
            unit_id=args.unit_id,unit_title=args.unit_title,unit_parent=None,topic_prefix='',title_suffix='',
            node=args.node,reset_reading_progress=False,replace_atomic_and_reset_progress=False,
            test_material=args.test_material))
    print(cp.json.dumps({'topics':len(topics),'points':len(additions),'output':str(args.output)},ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('prepare-input')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--points',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--topic-prefix',default='exec-explicit-')
    p = sub.add_parser('run')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--reuse-analysis-runs',type=Path,nargs='+',default=[],
                   help='Reuse a completed matching raw analysis generation; review it anew in this run')
    p.add_argument('--attempts',type=int,choices=range(1,7),default=3)
    p.add_argument('--executable')
    p.add_argument('--model',help='Omit to inherit the configured Codex model')
    p.add_argument('--timeout',type=int,default=900)
    p = sub.add_parser('prepare')
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--runs',type=Path,nargs='+',required=True)
    p.add_argument('--library',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--compile',action='store_true')
    p.add_argument('--test-material',action='store_true')
    p.add_argument('--course-id',required=True)
    p.add_argument('--course-title',required=True)
    p.add_argument('--unit-id',required=True)
    p.add_argument('--unit-title',required=True)
    p.add_argument('--node',default='node')
    args = parser.parse_args()
    if args.command == 'prepare-input': prepare_input(args)
    elif args.command == 'run': return run(args)
    else: prepare(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
