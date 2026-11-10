// Compile only changed question fronts; reuse the bank's frozen answer side.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {renderer, loadAssets} from './import_course.mjs';
import {literalProse} from './literal_prose.mjs';

const [originalPath, formattedPath, auditPath, output] = process.argv.slice(2);
if (!output) throw new Error('Usage: compile_training_format.mjs ORIGINAL FORMATTED_SOURCE AUDIT OUTPUT');
const read = async file => JSON.parse((await fs.readFile(file, 'utf8')).replace(/^\uFEFF/, ''));
const original = await read(originalPath), source = await read(formattedPath), audit = await read(auditPath);
let previous;
try { previous = await fs.readFile(output); } catch (e) { if (e.code !== 'ENOENT') throw e; }
if (original.id !== source.id) throw new Error('Bank identity changed');
const originals = new Map(original.groups.flatMap(g => g.questions.map(q => [q.id, q])));
const changed = source.groups.flatMap(g => g.questions).filter(q => q.interaction);
const render = await renderer(await loadAssets(formattedPath, changed.map(q => q.question + '\n' + (q.interaction.options || []).map(o => o.text).join('\n')).join('\n')), {cacheGlyphs: true});
const html = text => render.nodes([{head: literalProse(text), lines: [], children: [], line: 1}]);
const format = {schema: 1, bank_id: original.id, bank_version: original.version, questions: {}, flatten: audit.flatten};
let previousQuestions = {};
if (previous) {
  const old = JSON.parse(previous.toString('utf8').replace(/^\uFEFF/, ''));
  if (old.bank_id === original.id && old.bank_version === original.version) {
    if (old.media) format.media = old.media;
    previousQuestions = old.questions;
    for (const [id, row] of Object.entries(old.questions)) if (row.multi_step) format.questions[id] = row;
  }
}
for (const q of changed) {
  const old = originals.get(q.id);
  if (!old || ['title', 'reference_answer', 'answer_type', 'constraints'].some(k => q[k] !== old[k])) throw new Error('Original answer/identity changed: ' + q.id);
  const interaction = structuredClone(q.interaction);
  for (const item of interaction.options || []) item.html = html(item.text);
  const multi = q.multi_step || previousQuestions[q.id]?.multi_step;
  format.questions[q.id] = {source_sha256: '', question: q.question, html: html(q.question), interaction,
    ...(multi ? {multi_step: structuredClone(multi)} : {})};
}
// Python owns the canonical source digest and shared interaction validation.
const code = `import sys,json\nfrom graspmemoedu.question_bank import digest\nfrom graspmemoedu.training_format import validate_format\nx=json.load(sys.stdin); b=x['bank']; f=x['format']\nfor g in b['groups']:\n for q in g['questions']:\n  if q['id'] in f['questions']: f['questions'][q['id']]['source_sha256']=digest(q)\nvalidate_format(f,b)\nsys.stdout.buffer.write((json.dumps(f,ensure_ascii=False,indent=2)+'\\n').encode())`;
const checked = spawnSync(process.env.GRASPMEMOEDU_PYTHON || 'python', ['-c', code], {
  cwd: path.dirname(path.dirname(fileURLToPath(import.meta.url))), input: JSON.stringify({bank: original, format}),
  encoding: 'utf8', maxBuffer: 64 * 1024 * 1024});
if (checked.status !== 0) throw new Error(checked.stderr || checked.error?.message || 'Format validation failed');
if (Buffer.byteLength(checked.stdout) > 64000000) throw new Error('Format exceeds file size limit');
if (previous && !previous.equals(Buffer.from(checked.stdout))) {
  const history = path.join(path.dirname(output), 'format-history'); await fs.mkdir(history, {recursive: true});
  const saved = path.join(history, 'compiled-' + crypto.createHash('sha256').update(previous).digest('hex') + '.json');
  try { await fs.writeFile(saved, previous, {flag: 'wx'}); } catch (e) { if (e.code !== 'EEXIST') throw e; }
}
if (!previous || !previous.equals(Buffer.from(checked.stdout))) {
  const temporary = output + '.pending'; await fs.writeFile(temporary, checked.stdout, {flag: 'wx'});
  await fs.rename(temporary, output);
}
console.log(JSON.stringify({questions: changed.length, flattened: audit.flatten, bytes: Buffer.byteLength(checked.stdout), sha256: crypto.createHash('sha256').update(checked.stdout).digest('hex')}));
