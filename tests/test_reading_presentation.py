from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from graspmemoedu.reading_book_import import ReadingPresentationRevision
from graspmemoedu.topic_install import encoded
from tests.fixtures import temporary_test_directory
from tests.topic_format_fixtures import introduction_document,node

class ReadingPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp=temporary_test_directory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.old=self.root/'old';self.package=self.root/'package'
        self.old.mkdir();self.package.mkdir()
        doc=introduction_document();doc['nodes']=[node('i','Introduction',[node('p','Original heading')])]
        self.doc=doc
        self.course={'id':'book','title':'Book','description':'','topics':[{'id':doc['id'],'prerequisites':[]}],
                     'units':[{'id':'section','title':'Section','parent_id':None,'topic_ids':[doc['id']]}]}
        self.previous={'id':doc['id'],'title':doc['title'],'topic_kind':'introduction','content_version':'unchanged',
                       'version':'before','source_sha256':'old','introduction':{'html':'<p>Original heading</p>'},
                       'introductions':[{'id':'i','title':'Intro','html':'<p>Original heading</p>'}]}
        self.incoming=deepcopy(self.previous);self.incoming.update(version='after',source_sha256='new',introduction={'html':'<h3>Original heading</h3>'})
        self.incoming['introductions'][0]['html']=self.incoming['introduction']['html']
        self.catalog=SimpleNamespace(courses={'book':self.course},topics={doc['id']:self.previous},topic_paths={doc['id']:self.old/'topic.json'})
        self.write_source(self.old,doc)
        revised=deepcopy(doc);revised['nodes'][0]['children'][0]['presentation']={'role':'heading','level':3}
        self.write_source(self.package,revised)
        (self.package/'manifest.json').write_bytes(encoded({'presentation_previous_versions':{doc['id']:'before'}}))
        incoming_course=deepcopy(self.course);incoming_course['topics'][0]['path']='topic.json'
        (self.package/'catalog.json').write_bytes(encoded({'courses':[incoming_course]}))
        self.write_topic()
    def write_source(self,folder,doc):
        raw=encoded(doc);(folder/'source.json').write_bytes(raw)
        (folder/'topic.sources.json').write_bytes(encoded({'source_path':'source.json','source_sha256':hashlib.sha256(raw).hexdigest()}))
    def write_topic(self):(self.package/'topic.json').write_bytes(encoded(self.incoming))
    def revision(self):
        value=ReadingPresentationRevision(self.package,self.root);value.prepare(self.catalog);return value
    def test_presentation_changes_only_and_preserves_progress_contract(self):
        revision=self.revision();self.assertEqual(revision.replacements,{self.doc['id']});self.assertEqual(revision.counts,{})
        self.catalog.topics[self.doc['id']]=deepcopy(self.incoming)
        self.assertTrue(self.revision().unchanged())
    def test_changed_source_and_pinned_version_are_rejected(self):
        changed=deepcopy(self.doc);changed['nodes'][0]['children'][0]['text']='Rewritten'
        self.write_source(self.package,changed)
        with self.assertRaisesRegex(ValueError,'original source'):self.revision()
        self.previous['version']='concurrent-change'
        with self.assertRaisesRegex(ValueError,'Pinned'):self.revision()
    def test_missing_display_text_or_changed_content_version_is_rejected(self):
        self.incoming['introduction']['html']='<h3>Missing</h3>';self.write_topic()
        with self.assertRaisesRegex(ValueError,'rendered text'):self.revision()
        self.incoming['content_version']='changed';self.write_topic()
        with self.assertRaisesRegex(ValueError,'learning content'):self.revision()

if __name__=='__main__':unittest.main()
