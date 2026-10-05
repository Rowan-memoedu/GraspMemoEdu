"""Private import and immutable lookup of pre-produced training Practices."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .review_cards import attach_training_variant, point_id
from .service import DomainError
from .training_content import TrainingCatalog


def read_variant(data_dir, identifier, bank, question_id):
    """Resolve a Practice only under its original bank, version and memory point."""
    try:
        path = Path(data_dir) / 'learning.sqlite3'
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
            row = con.execute('''SELECT v.point,v.snapshot FROM review_variants v
                JOIN review_points p ON p.id=v.point
                WHERE v.id=? AND p.kind='training' AND p.container=? AND p.version=? AND p.source_point=?''',
                (identifier, bank['id'], bank['version'], question_id)).fetchone()
        if row is None:
            raise ValueError('Unknown variant')
        expected = 'variant-' + hashlib.sha256((row[0] + row[1]).encode()).hexdigest()
        if expected != identifier:
            raise ValueError('Variant was modified')
        return json.loads(row[1])
    except (ValueError, sqlite3.Error, OSError):
        raise DomainError('content_version_mismatch', '未找到此题对应的变式版本，请刷新后重试。', 409) from None


def install_variants(data_dir, path):
    """All-or-nothing, replay-safe import; never create an unlearned source point."""
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 32_000_000:
        raise ValueError('Invalid variant file')
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    if (not isinstance(raw, dict) or set(raw) != {'schema', 'bank_id', 'bank_version', 'variants'}
            or raw['schema'] != 1 or not isinstance(raw['variants'], list) or not 1 <= len(raw['variants']) <= 5000):
        raise ValueError('Invalid variant batch')
    bank = TrainingCatalog(data_dir).bank(raw['bank_id'])
    if bank['version'] != raw['bank_version']:
        raise ValueError('Bank version changed')
    known = {q['id'] for g in bank['groups'] for q in g.get('questions', [])}
    now = datetime.now(timezone.utc).isoformat()
    identifiers = []
    with closing(sqlite3.connect(Path(data_dir) / 'learning.sqlite3')) as con:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        with con:
            for item in raw['variants']:
                if (not isinstance(item, dict) or set(item) != {'source_question_id', 'question'}
                        or item['source_question_id'] not in known):
                    raise ValueError('Unknown variant source question')
                point = point_id('training', bank['id'], bank['version'], item['source_question_id'])
                identifier = attach_training_variant(con, point, item['question'], now)
                if identifier in identifiers:
                    raise ValueError('Duplicate Practice in batch')
                identifiers.append(identifier)
    return {'bank_id': bank['id'], 'variant_count': len(identifiers), 'variant_ids': identifiers}
