"""Project reviewed structured atomic points into the existing context input contract."""
import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from graspmemoedu import context_production as cp
from graspmemoedu.context_explicit import exported_points
from graspmemoedu.structured_atomic import FORMAT, normalize
from scripts.prepare_structured_atomic import frozen_source_images


def project(document, subject):
    if document.get('schema') != 2 or subject not in cp.SUBJECTS:
        raise ValueError('Expected structured schema 2 and a registered subject')
    points, seen = [],set()
    images = frozen_source_images(document)
    for topic in document['topics']:
        # Reuse the import contract and the existing context projection; no author call.
        normalize({**deepcopy(topic),'source_format':FORMAT,'subject_id':subject,
                   'embedded_images':images})
        for point in exported_points(topic,subject)['points']:
            if not cp.ID.fullmatch(point['id']) or point['id'] in seen:
                raise ValueError('Duplicate or unsafe context point ID')
            cp.text(point['target']); cp.text(point['context'])
            seen.add(point['id']); points.append(point)
    return {'schema':1,'subject_id':subject,'points':points,'tasks':[]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--subject',required=True)
    args = parser.parse_args()
    value = project(cp.read(args.input),args.subject)
    cp.save(args.output,value)
    print(cp.json.dumps({'points':len(value['points']),'production_started':False,'output':str(args.output)}))


if __name__ == '__main__': main()
