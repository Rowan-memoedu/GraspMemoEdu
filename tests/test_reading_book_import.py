"""Native reading imports preserve unrelated content, policy and learning data."""
import hashlib
import json
from pathlib import Path
import unittest

from graspmemoedu.academy_import import install
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.test_materials import TestMaterials
from graspmemoedu.topic_install import encoded
from graspmemoedu.training_content import TrainingCatalog, fingerprint
from tests.fixtures import temporary_test_directory
from tests.subject_matrix_fixture import write_matrix
from tests.test_ordered_learning import ordered_topic
from tests.training_fixtures import training_bank


class ReadingImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=temporary_test_directory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);self.data=root/'data';self.package=root/'package'
        write_matrix(self.data);self.package.mkdir()
        topic=ordered_topic(intro_only=True);topic['id']='reading-import-fixture'
        self.bank=training_bank('math');self.bank['id']='reading-book-practice';self.bank['version']=fingerprint(self.bank)
        units=[{k:g[k] for k in ('id','title','parent_id')}|{'topic_ids':[topic['id']] if i==0 else []}
               for i,g in enumerate(self.bank['groups'])]
        self.course={'id':'reading-book','title':'Reading fixture','topics':[{'id':topic['id'],'path':'topic.json','prerequisites':[]}], 'units':units}
        self.manifest={'schema':1,'adapter':'reading-book','subject_id':'math',
                       'test_materials':{'courses':['reading-book'],'banks':[self.bank['id']]},'files':{}}
        self.put('topic.json',topic);self.put('catalog.json',{'courses':[self.course]})
        self.put('training-banks/'+self.bank['id']+'.json',self.bank)
        (self.data/'learning.sqlite3').write_bytes(b'Unchanged existing learning records')

    def put(self,name,value):
        target=self.package/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(encoded(value))
        self.manifest['files'][name]=hashlib.sha256(target.read_bytes()).hexdigest()
        (self.package/'manifest.json').write_bytes(encoded(self.manifest))

    def test_atomic_reading_and_bank_are_restricted_and_idempotent(self):
        before=Catalog.load(Settings.load(self.data))
        result=install(self.package,self.data)
        actual=Catalog.load(Settings.load(self.data));policy=TestMaterials(self.data,actual)
        self.assertEqual(result['status'],'installed')
        for key,value in before.courses.items():self.assertEqual(actual.courses[key],value)
        self.assertIn('reading-book',policy.courses);self.assertIn(self.bank['id'],policy.banks)
        self.assertEqual(TrainingCatalog(self.data).bank(self.bank['id']),self.bank)
        self.assertEqual(install(self.package,self.data)['status'],'unchanged')
        self.assertEqual((self.data/'learning.sqlite3').read_bytes(),b'Unchanged existing learning records')

    def test_mismatched_hierarchy_and_missing_test_mark_fail_before_activation(self):
        self.bank['groups'][0]['title']='Different chapter';self.bank['version']=fingerprint(self.bank)
        self.put('training-banks/'+self.bank['id']+'.json',self.bank)
        with self.assertRaisesRegex(ValueError,'hierarchy differ'):install(self.package,self.data)
        self.assertFalse((self.data/'active-content.json').exists())
        self.manifest['test_materials']['courses']=[];self.put('catalog.json',{'courses':[self.course]})
        with self.assertRaisesRegex(ValueError,'test courses'):install(self.package,self.data)
        self.assertFalse((self.data/'active-content.json').exists())

    def test_changed_existing_bank_and_package_hash_fail_before_activation(self):
        incoming=dict(self.bank);incoming['title']='Existing different bank';incoming['version']=fingerprint(incoming)
        (self.data/'training-banks'/ (self.bank['id']+'.json')).write_bytes(encoded(incoming))
        with self.assertRaisesRegex(ValueError,'must not be overwritten'):install(self.package,self.data)
        self.assertFalse((self.data/'active-content.json').exists())
