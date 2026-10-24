"""Software-only fixtures across the shared workflow, renderer and learning service."""
from argparse import Namespace
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from graspmemoedu import context_production as cp
from graspmemoedu.context_explicit import prepare_input
from graspmemoedu.structured_atomic import normalize, FORMAT
from scripts.export_atomic_context_points import project
from scripts.prepare_structured_atomic import frozen_source_images, prepare
from scripts import produce_atomic_questions as variants
import tests.test_structured_atomic as structured_fixtures


class MaterialCompatibilityTests(unittest.TestCase):
    def source(self):
        return structured_fixtures.StructuredAtomicTests().source()

    def test_context_projection_reuses_point_identity_without_authoring(self):
        original=self.source(); before=deepcopy(original)
        known=project(original,'english')
        self.assertEqual(original,before)
        section=original['topics'][0]['sections'][0]
        self.assertEqual(known['points'],[{'id':'p','target':'A label.','context':'A label.'}])
        document={**known,'tasks':[{'id':'task','kind':'cards','topic_id':'new',
                  'point_ids':['p'],'format':'source question','count':1,'requirements':'Retain original evidence.'}]}
        source=cp.task_source(document,document['tasks'][0])
        self.assertEqual(source['points'][0]['id'],section['memory_points'][0]['id'])
        explicit=prepare_input({'schema':1,'articles':[{'id':'article','title':'Synthetic source',
                    'paragraphs':[{'id':'paragraph','text':'A label appears here.'}]}]},known)
        self.assertEqual(explicit['points'],known['points'])
        self.assertEqual(explicit['tasks'][0]['point_ids'],['p'])

    def test_unreviewed_and_duplicate_points_cannot_enter_context_interface(self):
        document=self.source()
        document['topics'][0]['sections'][0]['status']='needs_human'
        with self.assertRaisesRegex(ValueError,'Unapproved'): project(document,'english')
        document=self.source()
        document['topics'].append(deepcopy(document['topics'][0]))
        with self.assertRaisesRegex(ValueError,'Duplicate'): project(document,'english')

    def test_frozen_source_pixels_are_validated_and_conflicts_fail(self):
        import hashlib
        document=self.source()
        data=b'fixture image bytes'
        record={'data_url':'data:image/png;base64,'+base64.b64encode(data).decode(),
                'sha256':hashlib.sha256(data).hexdigest()}
        document['topics'][0]['sections'][0]['embedded_images']={'https://example.org/image.png':record}
        self.assertEqual(frozen_source_images(document)['https://example.org/image.png'],record)
        second=deepcopy(document['topics'][0]['sections'][0])
        second['embedded_images']['https://example.org/image.png']['data_url']='data:image/png;base64,Yg=='
        second['embedded_images']['https://example.org/image.png']['sha256']=hashlib.sha256(b'b').hexdigest()
        document['topics'][0]['sections'].append(second)
        with self.assertRaisesRegex(ValueError,'Conflicting'): frozen_source_images(document)
        document=self.source()
        document['topics'][0]['sections'][0]['embedded_images']={'x':{'data_url':record['data_url'],'sha256':'0'*64}}
        with self.assertRaises(ValueError): frozen_source_images(document)

    def test_archived_image_compiles_offline_without_replacing_reviewed_pixels(self):
        import hashlib
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/a0AAAAASUVORK5CYII=')
        document=self.source(); section=document['topics'][0]['sections'][0]
        section['introduction']['html']+='<p><img src="https://example.org/original.png" alt="Source illustration"></p>'
        section['embedded_images']={'https://example.org/original.png':{
            'data_url':'data:image/png;base64,'+base64.b64encode(png).decode(),'sha256':hashlib.sha256(png).hexdigest()}}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'source.json'
            source.write_text(json.dumps(document),encoding='utf-8')
            with patch('httpx.Client',side_effect=AssertionError('A frozen image must not be downloaded again')):
                prepare(Namespace(input=source,output=root/'bundle',subject='english',topic_prefix='',title_suffix='',
                    course_id='fixture',course_title='Fixture',unit_id='unit',unit_title='Fixture',unit_parent=None,node='node'))
            compiled=cp.read(root/'bundle/topics/new.json')
            self.assertIn(section['embedded_images']['https://example.org/original.png']['data_url'],
                          compiled['introductions'][0]['html'])

    @unittest.skipUnless(os.environ.get('ATOMIC_WORKFLOW_ROOT') and os.environ.get('ATOMIC_WORKFLOW_PYTHON'),
                         'Cross-repository acceptance needs ATOMIC_WORKFLOW_ROOT and ATOMIC_WORKFLOW_PYTHON')
    def test_real_shared_path_through_variants_compiler_install_and_learning(self):
        workflow=Path(os.environ['ATOMIC_WORKFLOW_ROOT']).resolve()
        from graspmemoedu.academy_import import install
        from graspmemoedu.catalog import Catalog
        from graspmemoedu.config import Settings
        from graspmemoedu.service import create_learning_service
        from datetime import datetime,timezone

        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            program='''
from pathlib import Path
import sys
from atomic_pipeline.preparation import prepare
from atomic_pipeline.workflow import section_job
from atomic_pipeline.verification import verify
from test_atomic_materials import material_fixture, FixtureExecutor
root=Path(sys.argv[1]); _,candidate=material_fixture(root)
manifest=prepare(root/'material.json',root/'rules.md',root/'examples',root/'batch')
executor=FixtureExecutor(root/'batch',candidate)
for row in manifest['sections']:
    assert section_job(root/'batch',row,executor)['status']=='ai_passed'
assert verify(root/'batch')['verified']
'''
            completed=subprocess.run([os.environ['ATOMIC_WORKFLOW_PYTHON'],'-c',program,str(root)],
                cwd=workflow,capture_output=True,text=True,timeout=120)
            self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
            result=cp.read(root/'batch/verification.json')
            structured=cp.read(root/'batch/structured-topics.json')
            known=project(structured,'english')
            self.assertEqual(len(known['points']),4)
            tasks=variants.source_tasks(structured,'english',2)
            results=[]
            for task in tasks:
                ident=task['point']['id']
                candidate={'point_id':ident,'status':'ok','reason':'','variants':[
                    {'point_ids':[ident],'question':'Synthetic alternate '+str(index)+' for '+ident+'?',
                     'reference_answer':task['seed_card']['reference_answer'],
                     'explanation':task['memory_chunk']['content'],
                     'interaction':{'version':1,'type':'text','grading':'semantic'}} for index in (1,2)]}
                review={'point_id':ident,'approved':True,'checks':[
                    {'index':index,'approved':True,'issues':[],'rationale':'Synthetic transport fixture.'} for index in (1,2)]}
                # Only the provider transport is simulated; the existing run/validation/export execute.
                with patch.object(variants,'CompatibleClient'), \
                     patch.object(variants.trial,'generation_payload',return_value={}), \
                     patch.object(variants.trial,'call',side_effect=[json.dumps(candidate),json.dumps(review)]):
                    results.append(variants.run_point(task,root/'variants',(),'generate','review',{},1))
            enriched=variants.assemble(structured,tasks,results)
            path=root/'enriched.json'; path.write_text(json.dumps(enriched),encoding='utf-8')
            prepare(Namespace(input=path,output=root/'bundle',subject='english',topic_prefix='',title_suffix='',
                course_id='fixture-course',course_title='Fixture',unit_id='fixture-unit',unit_title='Fixture',
                unit_parent=None,node='node',test_material=True))
            data=root/'site'; data.mkdir()
            from tests.atomic_fixtures import atomic_topic
            existing=atomic_topic('cs','protected-fixture')
            (data/'existing.json').write_text(json.dumps(existing),encoding='utf-8')
            (data/'catalog.json').write_text(json.dumps({'courses':[{'id':'protected-course','title':'Protected fixture',
                'topics':[{'id':'protected-fixture','path':'existing.json','prerequisites':[]}],
                'units':[{'id':'protected-unit','title':'Protected','topic_ids':['protected-fixture']}]}]}),encoding='utf-8')
            (data/'subjects.json').write_text(json.dumps({'subjects':[{'id':'cs','course_ids':['protected-course']}]}),encoding='utf-8')
            protected=create_learning_service(existing,data,learner_id='protected-fixture',
                clock=lambda:datetime(2026,10,24,tzinfo=timezone.utc))
            before=protected.state()
            self.assertEqual(install(root/'bundle',data)['status'],'installed')
            self.assertEqual(install(root/'bundle',data)['status'],'unchanged')
            self.assertEqual(protected.state(),before)
            topic=Catalog.load(Settings.load(data)).topic('fixture')
            self.assertEqual(len(topic['flashcards']),12)
            self.assertEqual(len(topic['memory_chunks']),2)
            self.assertEqual(topic['subject_id'],'english')
            self.assertEqual({p['id'] for c in topic['memory_chunks'] for p in c['points']},
                             {p['id'] for p in known['points']})
            self.assertTrue(all(c.get('learning_phases')==['review'] for c in topic['flashcards'] if '-v-' in c['id']))
            backend=create_learning_service(topic,data,learner_id='isolated-fixture',
                clock=lambda:datetime(2026,10,24,tzinfo=timezone.utc))
            state=backend.state()
            self.assertTrue(state)
            import uuid
            for _ in range(5):
                if state.get('phase')=='answer': break
                backend.advance({'request_id':str(uuid.uuid4()),'expected_revision':state['revision'],
                    'attempt_id':state['attempt_id'],'step_id':state['active_step_id']})
                state=backend.state()
            self.assertEqual(state['phase'],'answer')
            self.assertTrue(state['current']['html'])
            self.assertNotIn('reference_answer',state['current'])
            self.assertFalse(result['human_accepted'])


if __name__ == '__main__': unittest.main()
