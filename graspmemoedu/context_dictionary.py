"""Optional, offline dictionary evidence for any content with word lookup.

Wn owns the lexicon model. This adapter does not create learning points or
choose a contextual sense. Its only private-library hook supplies a read-only
SQLite connection to the pinned Wn 1.1.1 connection pool.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import json
from pathlib import Path
import sqlite3
import threading
import unicodedata

WN_VERSION = '1.1.1'
LEXICON = 'oewn:2025'
MANIFEST = 'dictionary-manifest.json'
MAX_QUERY = 160
MAX_SENSES = 20
_LOCK = threading.RLock()
SOURCE = {
    'name': 'Open English WordNet', 'version': '2025', 'lexicon': LEXICON,
    'url': 'https://github.com/globalwordnet/english-wordnet/releases/tag/2025-edition',
    'license': 'CC BY 4.0; underlying Princeton WordNet license',
    'license_url': 'https://github.com/globalwordnet/english-wordnet/blob/2025-edition/LICENSE.md',
    'underlying_license_url': 'https://github.com/globalwordnet/english-wordnet/blob/2025-edition/WNDB_License.txt',
    'attribution': 'Open English WordNet Team; based on Princeton University WordNet.',
    'changes': 'Selected fields and bounded relation lists; original English definitions retained.',
}
SYNSET_RELATIONS = ('hypernym', 'hyponym', 'similar', 'meronym', 'holonym', 'also')
SENSE_RELATIONS = ('antonym', 'derivation', 'pertainym', 'also')


def _base(surface: str, status: str, reason: str | None = None) -> dict:
    result = {'status': status, 'surface': surface, 'lemma_candidates': [],
              'senses': [], 'source': dict(SOURCE), 'selected_sense_id': None,
              'contextual_sense_selected': False, 'truncated': False}
    if reason:
        result['reason'] = reason
    return result


def _strings(values, *, count=12, length=500):
    return list(dict.fromkeys(str(v)[:length] for v in values if v))[:count]


@contextmanager
def _read_dictionary(wn, directory: Path):
    """Keep Wn configuration and its read-only connection within one lock.

Wn 1.1.1 has a process-wide connection pool, with no public read-only option.
Do not let its default connection initializer create a database during lookup.
No private hooks outside this small, version-checked boundary are used.
"""
    db = importlib.import_module('wn._db')
    with _LOCK:
        previous = wn.config.database_path.parent
        dbpath = directory / 'wn.db'
        existing = db.pool.get(dbpath)
        connection = sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True,
                                     detect_types=sqlite3.PARSE_DECLTYPES)
        try:
            connection.execute('PRAGMA query_only = ON')
            db._check_schema_compatibility(connection, dbpath)
            wn.config.data_directory = directory
            db.pool[dbpath] = connection
            yield wn.Wordnet(LEXICON, expand='')
        finally:
            if existing is None:
                db.pool.pop(dbpath, None)
            else:
                db.pool[dbpath] = existing
            wn.config.data_directory = previous
            connection.close()


def _evidence(sense, word) -> dict:
    synset = sense.synset()
    relations = []
    for relation, targets in synset.relations(*SYNSET_RELATIONS).items():
        for target in targets[:5]:
            relations.append({'type': relation, 'scope': 'synset',
                              'target_id': target.id[:256],
                              'lemmas': _strings(target.lemmas())})
    for relation, targets in sense.relations(*SENSE_RELATIONS).items():
        for target in targets[:5]:
            relations.append({'type': relation, 'scope': 'sense',
                              'target_id': target.id[:256],
                              'lemmas': _strings([target.word().lemma()])})
    return {
        'sense_id': sense.id[:256], 'synset_id': synset.id[:256],
        'lemma': word.lemma()[:500], 'pos': word.pos[:16],
        'definition': (synset.definition() or '')[:2000],
        'lemmas': _strings(synset.lemmas()),
        'examples': _strings([*sense.examples(), *synset.examples()], count=3, length=1000),
        'relations': relations[:24],
    }


def lookup_word(surface: str, *, data_dir: str | Path | None,
                limit: int = 12) -> dict:
    """Return bounded lexical candidates, without network access or writes.

The configured directory must contain a prepared manifest and ``wn.db``.
Missing packages, incompatible indexes and absent configuration are optional
capability failures, so callers can keep showing the reader and annotations.
"""
    if not isinstance(surface, str) or not surface.strip() or len(surface) > MAX_QUERY:
        return _base('', 'not_available', 'invalid_query')
    if any(unicodedata.category(ch) == 'Cc' for ch in surface):
        return _base('', 'not_available', 'invalid_query')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SENSES:
        return _base(surface, 'not_available', 'invalid_limit')
    if data_dir is None:
        return _base(surface, 'not_available', 'not_configured')
    try:
        directory = Path(data_dir)
        if not directory.is_absolute():
            return _base(surface, 'not_available', 'invalid_directory')
        directory = directory.resolve()
    except (TypeError, ValueError, OSError, RuntimeError):
        return _base(surface, 'not_available', 'invalid_directory')
    try:
        manifest_path = directory / MANIFEST
        if not (directory / 'wn.db').is_file() or not manifest_path.is_file():
            return _base(surface, 'not_available', 'dictionary_not_prepared')
        if manifest_path.stat().st_size > 65536:
            return _base(surface, 'not_available', 'invalid_manifest')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
                or manifest.get('lexicon') != LEXICON
                or manifest.get('wn_version') != WN_VERSION):
            return _base(surface, 'not_available', 'incompatible_dictionary')
        try:
            wn = importlib.import_module('wn')
        except ImportError:
            return _base(surface, 'not_available', 'dependency_missing')
        if getattr(wn, '__version__', None) != WN_VERSION:
            return _base(surface, 'not_available', 'incompatible_dependency')
        morphy = importlib.import_module('wn.morphy').Morphy()
        query = unicodedata.normalize('NFC', ' '.join(surface.split())).casefold()
        with _read_dictionary(wn, directory) as dictionary:
            words = {word.id: word for word in dictionary.words(query)}
            # Query morphology explicitly even when the surface is also a
            # separate entry (e.g. designed adjective versus design verb).
            for pos, candidates in morphy(query).items():
                for lemma in sorted(candidates)[:24]:
                    for word in dictionary.words(lemma, pos=pos):
                        words[word.id] = word
            ordered = sorted(words.values(), key=lambda word: (word.lemma(), word.pos, word.id))[:24]
            candidates = [{'lemma': word.lemma()[:500], 'pos': word.pos[:16]} for word in ordered]
            pairs = [(sense, word) for word in ordered for sense in word.senses()]
            result = _base(surface, 'available' if pairs else 'not_found')
            result.update(lemma_candidates=candidates,
                          senses=[_evidence(sense, word) for sense, word in pairs[:limit]],
                          total_senses=len(pairs), truncated=len(pairs) > limit)
            return result
    except (OSError, ValueError, sqlite3.Error):
        return _base(surface, 'not_available', 'dictionary_unreadable')
    except Exception:
        # Wn raises its own errors for missing lexicons/schema incompatibility.
        # Neither exception text nor local filenames enter a public response.
        return _base(surface, 'not_available', 'dictionary_unavailable')
