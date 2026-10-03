// Independent question-bank authoring; never imports or changes course Topics.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {renderer, loadAssets} from './import_course.mjs';

const [sourcePath, outputPath] = process.argv.slice(2);
if (!sourcePath || !outputPath) throw new Error('Usage: node scripts/import_training_bank.mjs SOURCE.json OUTPUT.json');
const raw = await fs.readFile(sourcePath);
const source = JSON.parse(raw.toString('utf8').replace(/^\uFEFF/, ''));
const texts = source.groups.flatMap(g => (g.questions || []).flatMap(q => [q.question, q.explanation,
  ...['options', 'statements', 'left', 'right'].flatMap(key => (q.interaction?.[key] || []).map(x => x.text))]));
const render = await renderer(await loadAssets(sourcePath, texts.join('\n')));
const html = value => {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Question and explanation are required');
  return render.nodes([{head: value, lines: [], children: [], line: 1}]);
};
const bank = {schema: 1, id: source.id, subject_id: source.subject_id, title: source.title,
  description: source.description || '', source_sha256: crypto.createHash('sha256').update(raw).digest('hex'),
  groups: source.groups.map(g => ({id: g.id, title: g.title, parent_id: g.parent_id || null,
    questions: (g.questions || []).map(q => {
      const compiled = {id: q.id, title: q.title || q.id, question: q.question, html: html(q.question),
        explanation_html: html(q.explanation), reference_answer: q.reference_answer || q.explanation,
        answer_type: q.answer_type || 'text', constraints: q.constraints || '按题意作答。'};
      if (q.interaction) {
        compiled.interaction = structuredClone(q.interaction);
        for (const key of ['options', 'statements', 'left', 'right'])
          for (const item of compiled.interaction[key] || []) item.html = html(item.text);
      }
      return compiled;
    })})), math_css: ''};
bank.math_css = render.css;
const repository = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const checked = spawnSync(process.env.GRASPMEMOEDU_PYTHON || 'python', ['-m', 'graspmemoedu.training_content'],
  {cwd: repository, input: JSON.stringify(bank), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024});
if (checked.status !== 0) throw new Error(checked.stderr || checked.error?.message || 'Bank validation failed');
await fs.mkdir(path.dirname(path.resolve(outputPath)), {recursive: true});
await fs.writeFile(outputPath, checked.stdout, {flag: 'wx'});
console.log(`PASS independent training bank: ${bank.id}`);
