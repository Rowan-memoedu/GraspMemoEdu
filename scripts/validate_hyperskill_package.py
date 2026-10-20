"""Read and install one source package in an isolated directory."""
import json
from pathlib import Path
import sys
from graspmemoedu.catalog import Catalog
from graspmemoedu.config import Settings
from graspmemoedu.academy_import import install
from graspmemoedu.test_materials import TestMaterials
from graspmemoedu.atomic_content import plain_text
from graspmemoedu.training_content import TrainingCatalog

package, data, translated = map(Path, sys.argv[1:])
data.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from atomic_fixtures import atomic_topic
existing = atomic_topic('cs', 'existing')
(data / 'existing.json').write_text(json.dumps(existing), encoding='utf-8')
(data / 'catalog.json').write_text(json.dumps({'courses':[{'id':'existing','title':'Existing',
    'topics':[{'id':'existing','path':'existing.json'}]}]}), encoding='utf-8')
(data / 'subjects.json').write_text('{"subjects":[{"id":"cs","course_ids":["existing"]}]}', encoding='utf-8')
result = install(package, data)
catalog = Catalog.load(Settings.load(data))
topic = catalog.topics['hyperskill-topic-308']
bank = TrainingCatalog(data).bank('hyperskill-practice')
assert sum(len(g['questions']) for g in bank['groups']) == 8
assert topic['flashcards'] == topic['memory_points'] == []
assert len(topic['introductions']) == 8
assert topic['memory_chunks'] == []
assert len(catalog.courses['hyperskill']['units']) == 5
assert catalog.courses['hyperskill']['units'][-1]['topic_ids'] == [topic['id']]
source = json.loads(translated.read_bytes())['step']['block']['text']
# The safe renderer may remove invisible lookup attributes; all source body text remains.
assert ''.join(plain_text(source).split()) == ''.join(plain_text(''.join(i['html'] for i in topic['introductions'])).split())
policy = TestMaterials(data, catalog)
assert not policy.topic_visible(topic['id'], None)
assert policy.topic_visible(topic['id'], {'role':'account','purpose':'test','is_advanced_learner':True,'is_student':True})
assert install(package, data)['status'] == 'unchanged'
print(json.dumps({**result,'theory_text_preserved':True,'independent_memories':8,'test_visibility':True,'idempotent':True}))
