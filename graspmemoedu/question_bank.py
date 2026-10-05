"""Immutable question variants over a course, without migrating learning state.

Each presented step/round pins a revision. A missing pin means original content,
including when a bank is installed later. No source answers or explanations change.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .question_types import validate_interaction, present_choice
from .idea_presentation import present_question

REVISION = re.compile(r'[0-9a-f]{64}\Z')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def source_questions(course):
    return {q['id']: q for m in course.get('modules', []) for q in [m['example'], *m['practices']]}


def validate_bank(bank, course):
    if (set(bank) != {'schema', 'topic_id', 'course_version', 'questions', 'math_css', 'provenance'}
            or type(bank['schema']) is not int or bank['schema'] != 1
            or bank['topic_id'] != course['id'] or bank['course_version'] != course['version']
            or not isinstance(bank['math_css'], str) or not isinstance(bank['provenance'], dict)
            or not isinstance(bank['questions'], dict) or not bank['questions']):
        raise ValueError('Invalid question bank or source course binding')
    originals = source_questions(course)
    for qid, variant in bank['questions'].items():
        if (qid not in originals or not isinstance(variant, dict)
                or set(variant) != {'source_sha256', 'question', 'html', 'interaction'}
                or variant['source_sha256'] != digest(originals[qid])
                or not all(isinstance(variant[k], str) and variant[k].strip() for k in ('question', 'html'))
                or not isinstance(variant['interaction'], dict)):
            raise ValueError('Question bank must bind each variant to an unchanged source question')
        validate_interaction(variant['interaction'], variant['question'])
    return digest(bank)


class QuestionBank:
    def __init__(self, course, data_dir):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', course['id']):
            raise ValueError('Unsafe topic ID')
        self.course, self.originals = course, source_questions(course)
        self.root = Path(data_dir) / 'question-banks' / course['id']
        self._cache = {}

    def load(self, revision):
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise ValueError('Invalid question bank revision')
        if revision not in self._cache:
            bank = json.loads((self.root / (revision + '.json')).read_text(encoding='utf-8'))
            if validate_bank(bank, self.course) != revision:
                raise ValueError('Question bank content hash mismatch')
            self._cache[revision] = bank
        return self._cache[revision]

    def active_revision(self, question_id=None):
        pointer = self.root / 'active.json'
        if not pointer.exists():
            return None
        active = json.loads(pointer.read_text(encoding='utf-8'))
        if set(active) != {'revision'}:
            raise ValueError('Invalid question bank pointer')
        bank = self.load(active['revision'])
        return active['revision'] if question_id is None or question_id in bank['questions'] else None

    def question(self, question_id, revision=None, presentation_id=''):
        question = self.originals[question_id]
        if revision:
            variant = self.load(revision)['questions'].get(question_id)
            if variant:
                question = {**question, **{k: v for k, v in variant.items() if k != 'source_sha256'}}
        question = present_choice(question, presentation_id)
        return present_question(question, self.originals[question_id])

    def css(self, revisions):
        return '\n'.join(self.load(revision)['math_css'] for revision in sorted(set(filter(None, revisions))))


def install_bank(bank, course, data_dir):
    revision = validate_bank(bank, course)
    if bank['provenance'].get('pilot'):
        raise ValueError('A pilot bank cannot be installed')
    root = QuestionBank(course, data_dir).root
    root.mkdir(parents=True, exist_ok=True)
    target = root / (revision + '.json')
    raw = json.dumps(bank, ensure_ascii=False, indent=2) + '\n'
    if target.exists():
        if digest(json.loads(target.read_text(encoding='utf-8'))) != revision:
            raise ValueError('Existing immutable bank was modified')
    else:
        with target.open('x', encoding='utf-8') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    # Switch only after the immutable artifact is on disk and independently read.
    QuestionBank(course, data_dir).load(revision)
    temporary = root / ('.active-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps({'revision': revision}) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, root / 'active.json')
    return revision


def validate_archive(blobs, topics):
    """Allow only bound immutable revisions and an optional valid active pointer."""
    names = {name for name in blobs if name.startswith('question-banks/')}
    banks, pointers = {}, {}
    for name in names:
        parts = name.split('/')
        if len(parts) != 3 or parts[1] not in topics:
            raise ValueError('Unexpected question bank path')
        topic_id, filename = parts[1:]
        value = json.loads(blobs[name])
        if filename == 'active.json':
            if set(value) != {'revision'} or not isinstance(value['revision'], str) or not REVISION.fullmatch(value['revision']):
                raise ValueError('Invalid archived bank pointer')
            pointers[topic_id] = value['revision']
        elif not filename.endswith('.json') or validate_bank(value, topics[topic_id]) + '.json' != filename:
            raise ValueError('Invalid archived bank revision')
        else:
            banks[topic_id, filename[:-5]] = value
    if any((topic_id, revision) not in banks for topic_id, revision in pointers.items()):
        raise ValueError('Archived active bank is missing')
    return names


def validate_saved_revisions(con, topics, data_dir):
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('topics', 'review_sessions'):
        if table not in tables:
            continue
        for topic_id, body in con.execute(f'SELECT topic,body FROM {table}'):
            if topic_id not in topics:
                continue
            state = json.loads(body)
            revisions = [state.get('question_bank_revision'), *(s.get('question_bank_revision') for s in state.get('steps', []))]
            bank = QuestionBank(topics[topic_id], data_dir)
            for revision in set(filter(None, revisions)):
                bank.load(revision)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--course', type=Path, required=True)
    parser.add_argument('--bank', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    course = json.loads(args.course.read_text(encoding='utf-8'))
    bank = json.loads(args.bank.read_text(encoding='utf-8'))
    print(json.dumps({'revision': install_bank(bank, course, args.data_dir), 'questions': len(bank['questions'])}))


if __name__ == '__main__':
    main()
