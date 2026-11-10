"""Exercise the native backup and restore without changing live learning data."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.guide import GuideReader
from graspmemoedu.site_backup import backup_site, restore_site
from graspmemoedu.subjects import Subjects
from graspmemoedu.training_content import TrainingCatalog


def verify(data, stage):
    settings = Settings.load(data); before = Catalog.load(settings)
    if settings.guide_path.is_file() and GuideReader(settings.guide_path).read(
            'https://guide-assets.invalid', {'role': 'account', 'is_admin': True})['status'] == 'unavailable':
        raise ValueError('Run with the same Node PATH and module environment as the native backup service')
    stage.mkdir(parents=True, exist_ok=True)
    archive = stage/'native-site.zip'; destination = stage/'restored'
    backup_site(settings, archive); restore_site(archive, destination)
    restored = Settings.load(destination); actual = Catalog.load(restored)
    banks = TrainingCatalog(destination); banks.validate_ownership(actual, Subjects(restored, actual))
    if before.topics != actual.topics or before.courses != actual.courses:
        raise ValueError('Restored content differs from the active source')
    books = []
    for course_id in ('gaoxian-classical-mechanics', 'liwenwei-algebra'):
        topics = [actual.topic(t['id']) for t in actual.courses[course_id]['topics']]
        if any(not t.get('sections') for t in topics): raise ValueError('Missing restored sections')
        books.append({'id': course_id, 'topics': len(topics),
            'bank_references': sum(len(t.get('bank_questions', [])) for t in topics),
            'footnote_groups': sum(len(i.get('footnotes', [])) for t in topics for i in t['introductions'])})
    with sqlite3.connect(destination/'learning.sqlite3') as con:
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('Restored database failed')
    report = {'status': 'native_backup_and_real_restore_passed', 'books': books,
        'learning_records_modified': False, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    (stage/'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--data', type=Path, required=True)
    p.add_argument('--stage', type=Path, required=True); a = p.parse_args(); verify(a.data, a.stage)
