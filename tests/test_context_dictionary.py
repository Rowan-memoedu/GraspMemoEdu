"""A synthetic lexicon exercises lookup without downloading teaching material."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from graspmemoedu.context_dictionary import LEXICON, MANIFEST, WN_VERSION, lookup_word
from tests.fixtures import temporary_test_directory


# Deliberately invented definitions: these are not copied OEWN content.
SYNTHETIC_LMF = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE LexicalResource SYSTEM "http://globalwordnet.github.io/schemas/WN-LMF-1.3.dtd">
<LexicalResource xmlns:dc="http://purl.org/dc/elements/1.1/">
  <Lexicon id="oewn" label="Synthetic test fixture" language="en" email="test@example.org"
           license="https://creativecommons.org/publicdomain/zero/1.0/" version="2025">
    <LexicalEntry id="oewn-design-v">
      <Lemma writtenForm="design" partOfSpeech="v"/>
      <Sense id="oewn-design-1" synset="oewn-plan-v">
        <SenseRelation target="oewn-planner-1" relType="derivation"/>
        <Example>Fixture usage with a specific plan.</Example>
      </Sense>
      <Sense id="oewn-design-2" synset="oewn-make-v"/>
    </LexicalEntry>
    <LexicalEntry id="oewn-designed-a">
      <Lemma writtenForm="designed" partOfSpeech="a"/>
      <Sense id="oewn-designed-1" synset="oewn-designed-a"/>
    </LexicalEntry>
    <LexicalEntry id="oewn-planner-n">
      <Lemma writtenForm="planner" partOfSpeech="n"/>
      <Sense id="oewn-planner-1" synset="oewn-planner-n"/>
    </LexicalEntry>
    <LexicalEntry id="oewn-go-v">
      <Lemma writtenForm="go" partOfSpeech="v"/>
      <Form writtenForm="went"/>
      <Sense id="oewn-go-1" synset="oewn-go-v"/>
    </LexicalEntry>
    <Synset id="oewn-plan-v" ili="" partOfSpeech="v">
      <Definition>Fixture meaning about planning.</Definition>
      <Example>Fixture usage with a specific plan.</Example>
      <Example>Another fixture plan example.</Example>
      <SynsetRelation target="oewn-make-v" relType="hypernym"/>
    </Synset>
    <Synset id="oewn-make-v" ili="" partOfSpeech="v">
      <Definition>Fixture alternative about creating.</Definition>
    </Synset>
    <Synset id="oewn-designed-a" ili="" partOfSpeech="a">
      <Definition>Fixture adjective with deliberate purpose.</Definition>
    </Synset>
    <Synset id="oewn-planner-n" ili="" partOfSpeech="n">
      <Definition>Fixture person who plans.</Definition>
    </Synset>
    <Synset id="oewn-go-v" ili="" partOfSpeech="v">
      <Definition>Fixture movement.</Definition>
    </Synset>
  </Lexicon>
</LexicalResource>
'''


def write_manifest(directory):
    (directory / MANIFEST).write_text(json.dumps({
        'schema_version': 1, 'lexicon': LEXICON, 'wn_version': WN_VERSION,
    }), encoding='utf-8')


class OptionalDictionaryTests(unittest.TestCase):
    def setUp(self):
        temp = temporary_test_directory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_absence_and_bad_configuration_never_create_a_database(self):
        missing = self.directory / 'not-created'
        for directory, reason in [(None, 'not_configured'), (missing, 'dictionary_not_prepared'),
                                  ('relative/path', 'invalid_directory'), (42, 'invalid_directory')]:
            with self.subTest(directory=directory):
                result = lookup_word('design', data_dir=directory)
                self.assertEqual((result['status'], result['reason']), ('not_available', reason))
        self.assertFalse(missing.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_query_and_response_limit_validation(self):
        for surface in [None, '', ' ', 'x' * 161, 'hello\nworld', 'x\x00y']:
            self.assertEqual(lookup_word(surface, data_dir=None)['reason'], 'invalid_query')
        for limit in [0, 21, True, 1.5, '3']:
            self.assertEqual(lookup_word('design', data_dir=None, limit=limit)['reason'], 'invalid_limit')

    def test_manifest_must_describe_the_pinned_index(self):
        (self.directory / 'wn.db').touch()
        for value in [[], {}, {'schema_version': 1, 'lexicon': 'other:1', 'wn_version': WN_VERSION}]:
            (self.directory / MANIFEST).write_text(json.dumps(value), encoding='utf-8')
            self.assertEqual(lookup_word('design', data_dir=self.directory)['reason'], 'incompatible_dictionary')
        (self.directory / MANIFEST).write_text('x' * 65537, encoding='utf-8')
        self.assertEqual(lookup_word('design', data_dir=self.directory)['reason'], 'invalid_manifest')
        (self.directory / MANIFEST).write_text('{bad json', encoding='utf-8')
        self.assertEqual(lookup_word('design', data_dir=self.directory)['status'], 'not_available')

    def test_missing_or_incompatible_package_is_an_optional_capability(self):
        (self.directory / 'wn.db').touch()
        write_manifest(self.directory)
        with patch('graspmemoedu.context_dictionary.importlib.import_module', side_effect=ImportError):
            self.assertEqual(lookup_word('design', data_dir=self.directory)['reason'], 'dependency_missing')
        with patch('graspmemoedu.context_dictionary.importlib.import_module', return_value=SimpleNamespace(__version__='0')):
            self.assertEqual(lookup_word('design', data_dir=self.directory)['reason'], 'incompatible_dependency')


class IndexedDictionaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.wn = importlib.import_module('wn')
        except ImportError:
            raise unittest.SkipTest('Optional wn dependency is not installed')
        if cls.wn.__version__ != WN_VERSION:
            raise unittest.SkipTest(f'Requires wn=={WN_VERSION}')
        temp = temporary_test_directory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.directory = cls.root / 'dictionary'
        cls.directory.mkdir()
        cls.alternative = cls.root / 'alternative'
        cls.alternative.mkdir()
        cls.source = cls.root / 'fixture.xml'
        cls.source.write_text(SYNTHETIC_LMF, encoding='utf-8')
        previous = cls.wn.config.database_path.parent
        db = importlib.import_module('wn._db')
        try:
            cls.wn.config.data_directory = cls.directory
            cls.wn.add(cls.source, progress_handler=None)
            alternate_source = cls.root / 'alternative.xml'
            alternate_source.write_text(SYNTHETIC_LMF.replace('Fixture meaning about planning.',
                                                           'Distinct second database meaning.'), encoding='utf-8')
            cls.wn.config.data_directory = cls.alternative
            cls.wn.add(alternate_source, progress_handler=None)
        finally:
            db.clear_connections()
            cls.wn.config.data_directory = previous
        write_manifest(cls.directory)
        write_manifest(cls.alternative)

    def test_morphology_does_not_hide_an_existing_adjective_or_choose_a_sense(self):
        result = lookup_word('Designed', data_dir=self.directory)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['surface'], 'Designed')
        self.assertEqual(result['lemma_candidates'], [{'lemma': 'design', 'pos': 'v'},
                                                       {'lemma': 'designed', 'pos': 'a'}])
        self.assertEqual(result['total_senses'], 3)
        self.assertIsNone(result['selected_sense_id'])
        self.assertFalse(result['contextual_sense_selected'])
        self.assertEqual({sense['sense_id'] for sense in result['senses']},
                         {'oewn-design-1', 'oewn-design-2', 'oewn-designed-1'})

    def test_indexed_irregular_form_and_unknown_word_are_distinct(self):
        result = lookup_word('went', data_dir=self.directory)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['lemma_candidates'], [{'lemma': 'go', 'pos': 'v'}])
        missing = lookup_word('invented-unknown-token', data_dir=self.directory)
        self.assertEqual(missing['status'], 'not_found')
        self.assertEqual(missing['senses'], [])

    def test_evidence_preserves_ids_relation_type_and_attribution(self):
        result = lookup_word('design', data_dir=self.directory)
        first = next(s for s in result['senses'] if s['sense_id'] == 'oewn-design-1')
        self.assertEqual(first['synset_id'], 'oewn-plan-v')
        self.assertEqual(len(first['examples']), 2)
        self.assertIn({'type': 'hypernym', 'scope': 'synset', 'target_id': 'oewn-make-v',
                       'lemmas': ['design']}, first['relations'])
        self.assertIn({'type': 'derivation', 'scope': 'sense', 'target_id': 'oewn-planner-1',
                       'lemmas': ['planner']}, first['relations'])
        self.assertIn('CC BY 4.0', result['source']['license'])
        self.assertIn('Princeton', result['source']['attribution'])
        json.dumps(result)  # Public data contains no Wn/Path/connection objects.
        limited = lookup_word('Designed', data_dir=self.directory, limit=1)
        self.assertEqual(len(limited['senses']), 1)
        self.assertTrue(limited['truncated'])
        self.assertEqual(limited['total_senses'], 3)

    def test_lookup_is_read_only_restores_configuration_and_releases_connections(self):
        database = self.directory / 'wn.db'
        before = hashlib.sha256(database.read_bytes()).hexdigest()
        previous = self.wn.config.database_path.parent
        self.assertEqual(lookup_word('design', data_dir=self.directory)['status'], 'available')
        self.assertEqual(self.wn.config.database_path.parent, previous)
        self.assertEqual(before, hashlib.sha256(database.read_bytes()).hexdigest())
        self.assertNotIn(database, importlib.import_module('wn._db').pool)
        with closing(sqlite3.connect(database)) as con:
            con.execute('BEGIN EXCLUSIVE')
            con.rollback()

    def test_concurrent_queries_do_not_cross_data_directories(self):
        corrupt = self.root / 'corrupt'
        corrupt.mkdir(exist_ok=True)
        (corrupt / 'wn.db').write_text('not a database', encoding='utf-8')
        write_manifest(corrupt)
        previous = self.wn.config.database_path.parent
        jobs = [(self.directory, 'Fixture meaning about planning.'),
                (self.alternative, 'Distinct second database meaning.'), (corrupt, None)] * 10
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda job: lookup_word('design', data_dir=job[0]), jobs))
        for result, (_, definition) in zip(results, jobs):
            self.assertEqual(result['status'], 'available' if definition else 'not_available')
            if definition:
                self.assertEqual(next(s['definition'] for s in result['senses']
                                      if s['sense_id'] == 'oewn-design-1'), definition)
            self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(lookup_word('design', data_dir=self.directory)['status'], 'available')
        self.assertEqual(self.wn.config.database_path.parent, previous)

    def test_fresh_import_and_restoration_do_not_create_unused_default_directory(self):
        unused = self.root / 'unused-default'
        environment = os.environ.copy()
        environment['WN_DATA_DIR'] = str(unused)
        script = '''
from pathlib import Path
import sys
import wn
from graspmemoedu.context_dictionary import lookup_word
unused, dictionary = map(Path, sys.argv[1:])
assert wn.config.database_path == unused / 'wn.db'
assert not unused.exists(), 'import created an unused directory'
assert lookup_word('design', data_dir=dictionary)['status'] == 'available'
assert wn.config.database_path == unused / 'wn.db'
assert not unused.exists(), 'lookup restoration created an unused directory'
'''
        process = subprocess.run([sys.executable, '-B', '-c', script, str(unused), str(self.directory)],
                                 env=environment, cwd=Path(__file__).resolve().parents[1],
                                 text=True, capture_output=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse(unused.exists())


if __name__ == '__main__':
    unittest.main()
