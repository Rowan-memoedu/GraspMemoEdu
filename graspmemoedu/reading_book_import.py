"""Validate pure reading courses and their source-aligned native question banks."""
import json
import hashlib
import re
from contextlib import nullcontext
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

from .training_content import TrainingCatalog, validate, install


def source_identity(value):
    """Only typography annotations may differ; original wording and nodes may not."""
    if isinstance(value, list):return [source_identity(v) for v in value]
    if isinstance(value, dict):return {k:source_identity(v) for k,v in value.items() if k not in ('presentation','_line')}
    return value


class ReadingHTML(HTMLParser):
    def __init__(self, html):
        super().__init__();self.text=[];self.media=[]
        # MathJax glyph paths are rendering machinery. Preserve and compare the
        # outer data-tex sequence; do not parse megabytes of duplicate SVG paths.
        html=re.sub(r'<svg\b[^>]*>[\s\S]*?</svg>',lambda m:'' if 'data-mml-node=' in m[0] else m[0],html,flags=re.I)
        self.feed(html)
    def handle_data(self, text):
        self.text.append(text)
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='img':self.media.append(('image',attrs.get('src'),attrs.get('alt')))
        if 'data-tex' in attrs:self.media.append(('math',attrs['data-tex']))
    def identity(self):
        return ''.join(''.join(self.text).split()),self.media


class ReadingPresentationRevision:
    """An atomic display-only update with immutable source and progress checks."""
    def __init__(self, package, data_dir):
        self.package=Path(package)
        manifest=json.loads((self.package/'manifest.json').read_bytes())
        self.pins=manifest.get('presentation_previous_versions')
        if not isinstance(self.pins,dict) or not self.pins:raise ValueError('Presentation update needs previous versions')
        self.replacements=set();self.counts={}

    def prepare(self, catalog):
        from .topic_format import parse_source
        courses=json.loads((self.package/'catalog.json').read_bytes())['courses'];seen=set()
        def source(folder):
            descriptor=json.loads((folder/'topic.sources.json').read_bytes())
            path=(folder/descriptor['source_path']).resolve()
            if not path.is_relative_to(folder.resolve()):raise ValueError('Source path leaves its package')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=descriptor['source_sha256']:raise ValueError('Source hash changed')
            doc,_=parse_source(raw.decode('utf-8-sig'))
            return source_identity(doc),descriptor.get('assets',[])
        for course in courses:
            current=catalog.courses.get(course['id'])
            shape={**course,'description':course.get('description',''),'topics':[
                {k:v for k,v in t.items() if k!='path'} for t in course['topics']]}
            if current!=shape:raise ValueError('Presentation update must preserve the complete course hierarchy')
            for entry in course['topics']:
                ident=entry['id'];seen.add(ident)
                incoming_path=self.package/entry['path'];incoming=json.loads(incoming_path.read_bytes())
                previous=catalog.topics[ident]
                if previous==incoming:continue
                if self.pins.get(ident)!=previous['version']:raise ValueError('Pinned reading content changed')
                if previous.get('topic_kind')!='introduction' or incoming.get('topic_kind')!='introduction':raise ValueError('Only pure readings support presentation updates')
                if source(catalog.topic_paths[ident].parent)!=source(incoming_path.parent):raise ValueError('Presentation update changed original source')
                def runtime(value):
                    value=deepcopy(value)
                    for key in ('source_sha256','version'):value.pop(key,None)
                    if isinstance(value.get('introduction'),dict):value['introduction'].pop('html',None)
                    else:value.pop('introduction',None)
                    for intro in value.get('introductions',[]):intro.pop('html',None)
                    return value
                if runtime(previous)!=runtime(incoming):raise ValueError('Presentation update changed learning content or identities')
                html=lambda value:value['html'] if isinstance(value,dict) else value
                old_html=[html(previous['introduction']),*[i['html'] for i in previous['introductions']]]
                new_html=[html(incoming['introduction']),*[i['html'] for i in incoming['introductions']]]
                pairs=set(zip(old_html,new_html))
                if any(ReadingHTML(a).identity()!=ReadingHTML(b).identity() for a,b in pairs):
                    raise ValueError('Presentation update changed rendered text, formulae or images')
                self.replacements.add(ident)
                if len(self.replacements)%25==0:print('Verified original reading presentation: '+str(len(self.replacements)),flush=True)
        if seen!=self.pins.keys():raise ValueError('Presentation pins must cover the complete update')

    def unchanged(self):return not self.replacements
    def activate(self, old, loaded):return nullcontext()


class ReadingCompositionRevision:
    """Lossless source partition with unchanged canonical banks and lazy progress carry."""
    def __init__(self, package, data_dir):
        self.package = Path(package)
        manifest = json.loads((self.package/'manifest.json').read_bytes())
        self.pins = manifest.get('composition_previous_versions')
        if not isinstance(self.pins, dict) or not self.pins:
            raise ValueError('Composition update needs pinned previous versions')
        self.proof = json.loads((self.package/'composition-proof.json').read_bytes())['topics']
        self.banks = TrainingCatalog(data_dir)
        self.replacements, self.counts = set(), {}

    def prepare(self, catalog):
        from .topic_format import parse_source
        from .topic_composition import validate_reading_lineage
        def source(folder):
            descriptor = json.loads((folder/'topic.sources.json').read_bytes())
            path = (folder/descriptor['source_path']).resolve()
            if not path.is_relative_to(folder.resolve()):
                raise ValueError('Source path leaves its package')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != descriptor['source_sha256']:
                raise ValueError('Source hash changed')
            return parse_source(raw.decode('utf-8-sig'))[0], descriptor.get('assets', [])
        compact = lambda value: ''.join(value.split())
        seen = set()
        for course in json.loads((self.package/'catalog.json').read_bytes())['courses']:
            shape = {**course, 'description': course.get('description', ''), 'topics': [
                {k:v for k,v in t.items() if k != 'path'} for t in course['topics']]}
            if catalog.courses.get(course['id']) != shape:
                raise ValueError('Composition must preserve the complete course hierarchy')
            bank = self.banks.bank(course['id']+'-practice')
            questions = {q['id']: (g, q) for g in bank['groups'] for q in g['questions']}
            for entry in course['topics']:
                ident = entry['id']; seen.add(ident)
                path = self.package/entry['path']; incoming = json.loads(path.read_bytes())
                previous = catalog.topics[ident]
                if incoming == previous:
                    continue
                if self.pins.get(ident) != previous['version'] or self.proof[ident]['previous_version'] != previous['version']:
                    raise ValueError('Pinned reading composition changed')
                if previous.get('topic_kind') != 'introduction' or incoming.get('topic_kind') != 'introduction':
                    raise ValueError('This composition migration preserves reading Topics only')
                old, old_assets = source(catalog.topic_paths[ident].parent)
                new, new_assets = source(path.parent)
                if (old['id'], old['title']) != (new['id'], new['title']):
                    raise ValueError('Original Topic identity changed')
                old_asset_map = {a['path']: a['sha256'] for a in old_assets}
                new_asset_map = {a['path']: a['sha256'] for a in new_assets}
                if any(old_asset_map.get(k) != v for k,v in new_asset_map.items()):
                    raise ValueError('Composition introduced or changed a source image')
                lineage = incoming.get('reading_lineage', {})
                validate_reading_lineage(lineage, {i['id'] for i in incoming['introductions']})
                if lineage['content_version'] != previous['content_version'] or set(lineage['introductions']) != {i['id'] for i in previous['introductions']}:
                    raise ValueError('Reading progress lineage differs from original sections')
                if new.get('reading_lineage') != lineage:
                    raise ValueError('Compiled progress lineage differs from its source')
                originals = {}
                for intro in old['nodes']:
                    for node in intro['children']:
                        if node['children'] or node['id'] in originals:
                            raise ValueError('Composition requires flat, uniquely identified original reading blocks')
                        originals[node['id']] = node['text']
                fresh = {}
                def collect(nodes):
                    for node in nodes:
                        if node['text'].strip(): fresh[node['id']] = node['text']
                        collect(node['children'])
                for intro in new['nodes']:
                    if intro.get('kind') == 'bank_question': continue
                    for field in ('children', 'example', 'footnotes'): collect(intro.get(field, []))
                headings = {s['id']: s['title'] for s in new['sections'] if s['title']}
                consumed, covered, bank_text = set(), {key: [] for key in originals}, {}
                for piece in self.proof[ident]['pieces']:
                    key, start, end = piece['source'], piece['start'], piece['end']
                    if key not in originals or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(originals[key]):
                        raise ValueError('Invalid source coverage interval')
                    text = originals[key][start:end]; covered[key].append((start, end))
                    kind, target = piece['kind'], piece.get('target')
                    if kind == 'whitespace':
                        if text.strip(): raise ValueError('Nonblank source was discarded')
                    elif kind in ('reading', 'example', 'footnotes', 'heading'):
                        expected = headings.get(target) if kind == 'heading' else fresh.get(target)
                        if expected != text or target in consumed:
                            raise ValueError('Reading text was changed, duplicated or unassigned')
                        consumed.add(target)
                    elif kind == 'bank':
                        if target not in questions or piece['field'] not in ('question', 'reference_answer', 'rendered_regions'):
                            raise ValueError('Source text has no canonical bank destination')
                        if piece['field'] == 'rendered_regions':
                            from PIL import Image
                            import base64
                            match = re.fullmatch(r'!\[[^\]]*\]\(([^)]+)\)', text)
                            if not match or match[1] not in old_asset_map:
                                raise ValueError('PDF image has no immutable source asset')
                            original_path = catalog.topic_paths[ident].parent/match[1]
                            original_image = Image.open(original_path).convert('RGB')
                            cursor = 0
                            for part in piece.get('image_parts', []):
                                asset = (self.package/part['path']).resolve()
                                if not asset.is_relative_to(self.package.resolve()) or hashlib.sha256(asset.read_bytes()).hexdigest() != part['sha256']:
                                    raise ValueError('PDF composition asset changed')
                                canonical_html = questions[target][1][part['field']]
                                hashes = {hashlib.sha256(base64.b64decode(raw)).hexdigest() for raw in re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', canonical_html)}
                                if part['sha256'] not in hashes:
                                    raise ValueError('PDF region is absent from the canonical bank')
                                image = Image.open(asset).convert('RGB'); y = part['y']
                                if (type(y) is not int or y < 0 or y > cursor or image.width != original_image.width
                                        or y+image.height > original_image.height
                                        or original_image.crop((0,y,image.width,y+image.height)).tobytes() != image.tobytes()):
                                    raise ValueError('PDF composition changed or lost source pixels')
                                cursor = max(cursor, y+image.height)
                            if cursor != original_image.height:
                                raise ValueError('PDF composition has uncovered source rows')
                            bank_text.setdefault((target, 'question'), [])
                        else:
                            bank_text.setdefault((target, piece['field']), []).append(text)
                    else:
                        raise ValueError('Unknown composition destination')
                for key, intervals in covered.items():
                    cursor = 0
                    for start, end in sorted(intervals):
                        if start != cursor: raise ValueError('Lost or overlapping original source range')
                        cursor = end
                    if cursor != len(originals[key]): raise ValueError('Original source has an uncovered suffix')
                if consumed != fresh.keys() | headings.keys():
                    raise ValueError('New text has no exact original source')
                for (qid, field), chunks in bank_text.items():
                    canonical = compact(questions[qid][1][field])
                    for text in chunks:
                        if compact(text) not in canonical:
                            raise ValueError('Removed reading text differs from canonical bank source: '+qid)
                for item in incoming.get('bank_questions', []):
                    ref = item['reference']; group, _ = questions.get(ref['question_id'], (None, None))
                    if (group is None or ref != {'bank_id': bank['id'], 'group_id': group['id'],
                            'question_id': ref['question_id'], 'version': bank['version']}
                            or not any(key[0] == ref['question_id'] for key in bank_text)):
                        raise ValueError('Topic references an unrelated bank question')
                self.replacements.add(ident)
                if len(self.replacements) % 25 == 0:
                    print('Verified lossless reading composition: '+str(len(self.replacements)), flush=True)
        if seen != self.pins.keys() or seen != self.proof.keys():
            raise ValueError('Composition proof must cover the complete update')

    def unchanged(self): return not self.replacements
    def activate(self, old, loaded): return nullcontext()


def preflight(package, manifest, data_dir):
    package=Path(package)
    courses=json.loads((package/'catalog.json').read_bytes())['courses']
    policy=manifest.get('test_materials',{})
    if not courses or set(policy.get('courses',[])) != {c['id'] for c in courses}:
        raise ValueError('Reading books must be explicitly marked as test courses')
    if any(name.startswith('graphs/') for name in manifest['files']):
        raise ValueError('Reading books must not supply prerequisite graphs')
    existing=TrainingCatalog(data_dir)
    banks=[]
    for course in courses:
        if course.get('placement') or not course.get('units'):
            raise ValueError('Reading books need native course hierarchy')
        for entry in course['topics']:
            path=(package/entry['path']).resolve()
            if not path.is_relative_to(package.resolve()) or entry['path'] not in manifest['files']:
                raise ValueError('Reading Topic leaves its source package')
            topic=json.loads(path.read_bytes())
            if topic.get('topic_kind')!='introduction' or topic.get('modules') or entry.get('prerequisites'):
                raise ValueError('Reading books accept pure introduction Topics only')
        bank_id=course['id']+'-practice'
        name='training-banks/'+bank_id+'.json'
        if name not in manifest['files'] or bank_id not in policy.get('banks',[]):
            raise ValueError('Reading bank and test restriction must accompany the course')
        bank=validate(json.loads((package/name).read_bytes()))
        shape=lambda rows:[{k:r.get(k) for k in ('id','title','parent_id')} for r in rows]
        if bank['id']!=bank_id or bank['subject_id']!=manifest['subject_id'] or shape(bank['groups'])!=shape(course['units']):
            raise ValueError('Reading course and native bank hierarchy differ')
        if bank_id in existing.banks and existing.banks[bank_id]!=bank:
            raise ValueError('An existing reading bank must not be overwritten')
        banks.append((name,bank))
    if set(policy.get('banks',[]))!={bank['id'] for _,bank in banks}:
        raise ValueError('Unexpected reading bank restriction')
    return banks


def install_banks(package, data_dir, banks):
    current=TrainingCatalog(data_dir)
    results=[]
    for name,bank in banks:
        if bank['id'] in current.banks:
            if current.banks[bank['id']]!=bank:
                raise ValueError('A reading bank changed concurrently')
            status='unchanged'
        else:
            install(Path(package)/name,data_dir)
            status='installed'
        results.append({'id':bank['id'],'status':status,'questions':sum(len(g['questions']) for g in bank['groups'])})
    return results
