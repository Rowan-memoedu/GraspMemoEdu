import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from graspmemoedu.service import DomainError
from graspmemoedu.test_materials import TestMaterials,validate
from tests.fixtures import temporary_test_directory


class AccountMaterialGrantTests(unittest.TestCase):
    def setUp(self):
        temporary=temporary_test_directory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.courses={'selected-course':{'id':'selected-course','topics':[{'id':'selected-topic'}]},
                      'other-course':{'id':'other-course','topics':[{'id':'other-topic'}]}}
        self.raw={'schema':1,'courses':list(self.courses),'banks':['selected-bank','other-bank'],'guides':['math'],
                  'account_grants':{'account-selected':{'courses':['selected-course'],'banks':['selected-bank']}}}
        (self.root/'test-materials.json').write_text(json.dumps(self.raw),encoding='utf-8')
        self.policy=TestMaterials(self.root,SimpleNamespace(content_dir=self.root,courses=self.courses))
        self.access={'role':'account','purpose':'learner','learner_id':'account-selected','is_student':True,
                     'topics':[{'id':'selected-topic'},{'id':'training.selected-bank'}]}

    def test_exact_account_and_material_only(self):
        self.assertFalse(self.policy.allowed(self.access))
        self.assertTrue(self.policy.topic_visible('selected-topic',self.access))
        self.assertTrue(self.policy.topic_visible('training.selected-bank',self.access))
        self.assertTrue(self.policy.course_visible(self.courses['selected-course'],self.access))
        self.assertTrue(self.policy.graph_topic_visible('selected-course','selected-topic',self.access))
        for key in ('other-topic','training.other-bank'):
            self.assertFalse(self.policy.topic_visible(key,self.access))
        self.assertFalse(self.policy.course_visible(self.courses['other-course'],self.access))
        self.assertFalse(self.policy.graph_topic_visible('selected-course','other-topic',self.access))
        for actor in (None,{'role':'guest','learner_id':'account-selected'},dict(self.access,learner_id='account-other')):
            self.assertFalse(self.policy.topic_visible('selected-topic',actor))
            self.assertFalse(self.policy.course_visible(self.courses['selected-course'],actor))
        with self.assertRaises(DomainError):self.policy.require_guide('math',self.access)

    def test_snapshot_requires_the_existing_topic_permission_too(self):
        self.assertTrue(self.policy.snapshot_visible('selected-topic',self.access))
        self.assertFalse(self.policy.snapshot_visible('selected-topic',dict(self.access,topics=[])))
        self.assertFalse(self.policy.snapshot_visible('other-topic',self.access))

    def test_revocation_restores_normal_test_isolation(self):
        self.raw['account_grants']={}
        (self.root/'test-materials.json').write_text(json.dumps(self.raw),encoding='utf-8')
        policy=TestMaterials(self.root,SimpleNamespace(content_dir=self.root,courses=self.courses))
        self.assertFalse(policy.topic_visible('selected-topic',self.access))
        self.assertFalse(policy.topic_visible('training.selected-bank',self.access))

    def test_invalid_account_grants_fail_closed(self):
        for grants in ([],{'../account':{}},{'account-selected':{'guides':['math']}},
                       {'account-selected':{'courses':['selected-course','selected-course']}},
                       {'account-selected':{'topics':'selected-topic'}}):
            with self.subTest(grants=grants),self.assertRaises(ValueError):
                validate(dict(self.raw,account_grants=grants))
