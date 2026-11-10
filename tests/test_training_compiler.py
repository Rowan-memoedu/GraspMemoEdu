import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

from tests.fixtures import temporary_test_directory

ROOT=Path(__file__).resolve().parents[1]


class TrainingCompilerTests(unittest.TestCase):
    def test_lossless_image_encoding_preserves_pixels_alpha_and_source(self):
        import base64
        import hashlib
        import io
        from PIL import Image, ImageDraw
        with temporary_test_directory() as folder:
            root = Path(folder)
            image = Image.new('RGBA', (240, 160), (30, 80, 140, 0))
            draw = ImageDraw.Draw(image)
            draw.rectangle((15, 20, 190, 130), fill=(120, 80, 25, 180))
            draw.line((10, 10, 230, 150), fill=(0, 0, 0, 255), width=3)
            picture = root / 'original.png'
            image.save(picture, compress_level=0)
            source_hash = hashlib.sha256(picture.read_bytes()).hexdigest()
            source = {'id': 'lossless-image-fixture', 'subject_id': 'physics', 'title': '原图检验',
                      'groups': [{'id': 'group', 'title': '图示', 'questions': [
                          {'id': 'q1', 'question': '观察原图。\n\n![原图](original.png)',
                           'explanation': '根据原图作答。', 'reference_answer': '原图'}]}]}
            file = root / 'source.json'
            file.write_text(json.dumps(source, ensure_ascii=False), encoding='utf-8')
            output = root / 'compiled.json'
            result = subprocess.run(['node', str(ROOT / 'scripts/import_training_bank.mjs'),
                                     str(file), str(output), '--lossless-images'], cwd=ROOT,
                                    env=dict(os.environ, GRASPMEMOEDU_PYTHON=sys.executable),
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = json.loads(output.read_text('utf-8'))['groups'][0]['questions'][0]['html']
            encoded = re.search(r'data:image/webp;base64,([A-Za-z0-9+/=]+)', rendered)
            self.assertIsNotNone(encoded)
            with Image.open(io.BytesIO(base64.b64decode(encoded[1]))) as decoded:
                self.assertEqual(decoded.convert('RGBA').tobytes(), image.tobytes())
                self.assertEqual(decoded.size, image.size)
            self.assertEqual(hashlib.sha256(picture.read_bytes()).hexdigest(), source_hash)
            self.assertLess(output.stat().st_size, picture.stat().st_size)

    def test_cached_formula_fragments_are_smaller_and_self_contained(self):
        script = r'''
        import {renderer} from './scripts/import_course.mjs';
        const text = Array(12).fill('$x^2+x+1$').join(' ');
        const node = {head:text, lines:[], children:[], line:1};
        const plain = await renderer();
        const cached = await renderer(new Map(), {cacheGlyphs:true});
        console.log(JSON.stringify({plain:plain.nodes([node]),
          first:cached.nodes([node]), second:cached.nodes([node])}));
        '''
        run=subprocess.run(['node','--input-type=module','-e',script],cwd=ROOT,
                           capture_output=True,text=True,encoding='utf-8',check=True)
        rendered=json.loads(run.stdout)
        self.assertLess(len(rendered['first']),len(rendered['plain']) * 0.65)
        fragments=[]
        for key in ('first','second'):
            definitions=set(re.findall(r'<path id="([^"]+)"',rendered[key]))
            references=set(re.findall(r'xlink:href="#([^"]+)"',rendered[key]))
            self.assertTrue(references)
            self.assertEqual(references,definitions)
            fragments.append(definitions)
        self.assertFalse(fragments[0] & fragments[1])

    def test_literal_prose_preserves_sources_and_does_not_activate_markup(self):
        with temporary_test_directory() as folder:
            root=Path(folder)
            question='1.[某次练习](1)设 a<b<c>0，求 $a^2+1$。'
            answer='1. 原书解析：若 a<b<0，则 $a^2>ab$。区间 $\\left(0,1)$。'
            source={'id':'isolated-literal','subject_id':'math','title':'编译样本','groups':[
                {'id':'group','title':'原章节','questions':[{'id':'q1','question':question,
                 'explanation':answer,'reference_answer':answer}]}]}
            file=root/'source.json'; file.write_text(json.dumps(source,ensure_ascii=False),encoding='utf-8')
            output=root/'compiled.json'
            env=dict(os.environ,GRASPMEMOEDU_PYTHON=sys.executable)
            command=['node',str(ROOT/'scripts/import_training_bank.mjs'),str(file),str(output)]
            default=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(default.returncode,0)
            compiled=subprocess.run(command+['--literal-prose'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(compiled.returncode,0,compiled.stderr)
            row=json.loads(output.read_text(encoding='utf-8'))['groups'][0]['questions'][0]
            self.assertEqual(row['question'],question)
            self.assertEqual(row['reference_answer'],answer)
            self.assertNotIn('<img',row['html'])
            self.assertNotIn('<a ',row['html'])
            self.assertIn('<svg',row['html'])
            source['groups'][0]['questions'][0]['question']='<img src=x onerror="alert(1)">'
            file.write_text(json.dumps(source),encoding='utf-8')
            rejected=subprocess.run(command+['--literal-prose'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
            self.assertNotEqual(rejected.returncode,0)


if __name__=='__main__':
    unittest.main()
