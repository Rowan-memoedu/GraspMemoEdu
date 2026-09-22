/** Compile a developer JSON source with the shared safe Markdown/MathJax renderer.
 * node scripts/import_atomic_topic.mjs source.json topic.json
 * No catalog, account or learning progress is modified.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {renderer, loadAssets} from './import_course.mjs';

const [sourcePath, outputPath] = process.argv.slice(2);
if (!sourcePath || !outputPath) throw new Error('Provide a source JSON and a new output JSON path');
try { await fs.access(outputPath); throw new Error('Output already exists'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
const bytes = await fs.readFile(sourcePath), source = JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''));
const allText = [source.introduction?.map(s => s.text).join('\n'), source.memory_chunks?.map(c => c.content).join('\n'),
  source.flashcards?.map(q => [q.question, q.explanation].join('\n')).join('\n')].join('\n');
const render = await renderer(await loadAssets(sourcePath, allText));
const html = value => {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Missing source text');
  return render.nodes([{head: value, lines: [], children: [], line: 1}]);
};
const statements = source.introduction.map(statement => ({id: statement.id, html: html(statement.text)}));
const topic = {id: source.id, title: source.title, training_mode: 'atomic_retrieval', subject_id: source.subject_id,
  source_sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
  introduction: {html: statements.map(s => s.html).join('')},
  memory_chunks: source.memory_chunks.map(chunk => ({id: chunk.id, title: chunk.title, html: html(chunk.content),
    points: chunk.points, statement_ids: chunk.statement_ids})),
  flashcards: source.flashcards.map(card => ({...card, html: html(card.question), explanation_html: html(card.explanation),
    reference_answer: card.reference_answer || card.explanation, answer_type: card.answer_type || 'text',
    constraints: card.constraints || '按题干要求作答。'})),
  ...(source.atomic_policy ? {atomic_policy: source.atomic_policy} : {})};
for (const card of topic.flashcards) {
  delete card.explanation;
  const spec = card.interaction;
  for (const item of [...(spec?.left || []), ...(spec?.right || []), ...(spec?.statements || [])]) item.html = html(item.text);
}
topic.math_css = render.css;
const repo = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const result = spawnSync(process.env.MATH_LEARNING_PYTHON || 'python', ['-m', 'math_learning.atomic_author'],
  {cwd: repo, input: JSON.stringify({topic, statements}), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024});
if (result.status !== 0) throw new Error(result.stderr || 'Atomic topic validation failed');
await fs.mkdir(path.dirname(path.resolve(outputPath)), {recursive: true});
await fs.writeFile(outputPath, result.stdout, {encoding: 'utf8', flag: 'wx'});
console.log('PASS compiled atomic topic with validated statement anchors; catalog and progress unchanged');
