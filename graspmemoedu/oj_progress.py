"""Account-scoped external OJ facts. No learning attempts, ratings or cards are created."""
import hashlib
import json
from pathlib import Path

def location(data_dir,learner):
    return Path(data_dir)/'external-progress'/('hydro-'+hashlib.sha256(learner.encode()).hexdigest()+'.json')

def load(data_dir,learner,bank):
    path=location(data_dir,learner)
    if not path.exists():return {}
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('learner_id')!=learner or value.get('bank_id')!=bank:return {}
    return value.get('questions',{})

def group_status(questions,facts):
    values=[facts.get(q['id'],{}) for q in questions]
    return {'submitted_count':sum(bool(v.get('submitted')) for v in values),
            'accepted_count':sum(bool(v.get('accepted')) for v in values)}

def project_group(result,facts):
    for q in result['questions']:
        fact=facts.get(q['id'])
        if fact:
            q['oj_status']=fact
            q['completed']=q['completed'] or fact['accepted']
    return result
