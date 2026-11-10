// Independent question-bank authoring; never imports or changes course Topics.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {renderer, loadAssets} from './import_course.mjs';
import {literalProse} from './literal_prose.mjs';

const [sourcePath, outputPath, ...options] = process.argv.slice(2);
if (!sourcePath || !outputPath) throw new Error('Usage: node scripts/import_training_bank.mjs SOURCE.json OUTPUT.json');
const mappingIndex = options.indexOf('--multi-step-file');
const mappingPath = mappingIndex >= 0 ? options.splice(mappingIndex, 2)[1] : null;
if (options.some(option => !['--literal-prose', '--lossless-images'].includes(option))) throw new Error('Unknown training-bank compiler option');
const raw = await fs.readFile(sourcePath);
const source = JSON.parse(raw.toString('utf8').replace(/^\uFEFF/, ''));
let stepMapping = mappingPath ? JSON.parse(await fs.readFile(mappingPath, 'utf8')) : null;
if (stepMapping && stepMapping.source_sha256 !== crypto.createHash('sha256').update(raw).digest('hex')) throw Error('Multi-step source binding differs');
if (!stepMapping) {
  const normalized = spawnSync(process.env.GRASPMEMOEDU_PYTHON || 'python', ['-m', 'graspmemoedu.multi_step_import'],
    {cwd: path.dirname(path.dirname(fileURLToPath(import.meta.url))), input: JSON.stringify(source), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024});
  if (normalized.status !== 0) throw Error(normalized.stderr || 'Multi-step preparation failed; use bank_import.py to align source answers');
  stepMapping = JSON.parse(normalized.stdout);
}
const texts = source.groups.flatMap(g => [...(g.introductions || []).map(i => i.text),
  ...(g.questions || []).flatMap(q => [q.question, q.explanation,
  ...['options', 'statements', 'left', 'right'].flatMap(key => (q.interaction?.[key] || []).map(x => x.text)),
  ...(stepMapping?.questions[q.id]?.multi_step || q.multi_step)?.steps?.flatMap(s => [s.question,s.explanation,s.reference_answer]) || []])]);
const assets = await loadAssets(sourcePath, texts.join('\n'));
if (options.includes('--lossless-images') && assets.size) {
  const optimized = spawnSync(process.env.GRASPMEMOEDU_PYTHON || 'python',
    [fileURLToPath(new URL('./optimize_bank_images.py', import.meta.url)), path.join(path.dirname(path.resolve(outputPath)), 'compile-media')],
    {input:JSON.stringify([...assets].map(([name, a]) => ({name, source:path.resolve(path.dirname(sourcePath),name),
      sha256:a.sha256, width:a.width, height:a.height}))), encoding:'utf8', maxBuffer:4*1024*1024});
  if (optimized.status !== 0) throw new Error(optimized.stderr || optimized.error?.message || 'Image optimization failed');
  const rows = JSON.parse(optimized.stdout);
  if (rows.length !== assets.size || new Set(rows.map(r=>r.name)).size !== assets.size) throw new Error('Image optimization coverage differs');
  for (const row of rows) {
    const source = assets.get(row.name), data = await fs.readFile(row.path);
    if (!source || row.source_sha256 !== source.sha256 || !row.pixels_equal || row.width !== source.width || row.height !== source.height
        || !['image/png','image/webp'].includes(row.mime) || crypto.createHash('sha256').update(data).digest('hex') !== row.sha256)
      throw new Error('Optimized image differs from validated source');
    assets.set(row.name, {...source, data, mime:row.mime});
  }
}
const render = await renderer(assets, {cacheGlyphs:true});
const renderErrors = [];
const html = (value, context) => {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Question and explanation are required');
  try {
    return render.nodes([{head: options.includes('--literal-prose') ? literalProse(value) : value, lines: [], children: [], line: 1}]);
  } catch (error) {
    renderErrors.push({context, message: error.message, code: error.id});
    return '';
  }
};
const bank = {schema: 1, id: source.id, subject_id: source.subject_id, title: source.title,
  description: source.description || '', source_sha256: crypto.createHash('sha256').update(raw).digest('hex'),
  groups: source.groups.map(g => ({id: g.id, title: g.title, parent_id: g.parent_id || null,
    ...(g.unlock_policy ? {unlock_policy: structuredClone(g.unlock_policy)} : {}),
    ...(g.introductions ? {introductions: g.introductions.map(i => ({...i, html: html(i.text, `${g.id}/${i.id}/introduction`)})),
      content_order: structuredClone(g.content_order)} : {}),
    questions: (g.questions || []).map(q => {
      const compiled = {id: q.id, title: q.title || q.id, question: q.question, html: html(q.question, `${g.id}/${q.id}/question`),
        explanation_html: html(q.explanation, `${g.id}/${q.id}/explanation`), reference_answer: q.reference_answer || q.explanation,
        answer_type: q.answer_type || 'text', constraints: q.constraints || '按题意作答。'};
      for (const key of ['point_ids', 'assessment'])
        if (q[key] !== undefined) compiled[key] = structuredClone(q[key]);
      if (q.interaction) {
        compiled.interaction = structuredClone(q.interaction);
        for (const key of ['options', 'statements', 'left', 'right'])
          for (const item of compiled.interaction[key] || []) item.html = html(item.text, `${g.id}/${q.id}/${key}/${item.id}`);
      }
      const multi = stepMapping?.questions[q.id]?.multi_step || q.multi_step;
      if (multi) compiled.multi_step = {schema: 1, context: multi.context,
        context_html: multi.context.trim() ? html(multi.context, `${g.id}/${q.id}/context`) : '<p></p>', review_policy: 'whole',
        steps: multi.steps.map(step => ({id: step.id, label: step.label, question: step.question,
          html: html(step.question, `${g.id}/${q.id}/${step.id}`), reference_answer: step.reference_answer,
          explanation_html: html(step.explanation, `${g.id}/${q.id}/${step.id}/explanation`), interaction: structuredClone(step.interaction)}))};
      return compiled;
    })})), math_css: ''};
bank.math_css = render.css;
if (renderErrors.length) {
  const report = JSON.stringify(renderErrors, null, 2);
  const digest = crypto.createHash('sha256').update(report).digest('hex').slice(0, 16);
  const directory = path.join(path.dirname(path.resolve(outputPath)), 'compile-errors');
  await fs.mkdir(directory, {recursive: true});
  const reportPath = path.join(directory, digest + '.json');
  try { await fs.writeFile(reportPath, report, {flag: 'wx'}); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  throw new Error(`Rendering failed for ${renderErrors.length} fields; diagnostics: ${reportPath}`);
}
const repository = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sizes = {source_sha256: bank.source_sha256, serialized_bytes: Buffer.byteLength(JSON.stringify(bank)),
  fields_over_100000: bank.groups.flatMap(group => group.questions.flatMap(question =>
    ['question', 'html', 'reference_answer', 'explanation_html'].filter(key => question[key].length > 100000)
      .map(key => ({group: group.id, question: question.id, field: key, length: question[key].length}))))};
const sizeDirectory = path.join(path.dirname(path.resolve(outputPath)), 'compile-checks');
await fs.mkdir(sizeDirectory, {recursive: true});
const sizeJSON = JSON.stringify(sizes, null, 2);
const sizePath = path.join(sizeDirectory, crypto.createHash('sha256').update(sizeJSON).digest('hex').slice(0, 16) + '.json');
try { await fs.writeFile(sizePath, sizeJSON, {flag: 'wx'}); } catch (error) { if (error.code !== 'EEXIST') throw error; }
console.log(`Compiled markup size: ${sizes.serialized_bytes} bytes; size report: ${sizePath}`);
if (sizes.serialized_bytes > 64000000) throw new Error('Compiled bank exceeds the 64 MB file limit');
const checked = spawnSync(process.env.GRASPMEMOEDU_PYTHON || 'python', ['-m', 'graspmemoedu.training_content'],
  {cwd: repository, input: JSON.stringify(bank), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024});
if (checked.status !== 0) throw new Error(checked.stderr || checked.error?.message || 'Bank validation failed');
if (Buffer.byteLength(checked.stdout) > 64000000) throw new Error('Validated bank exceeds the 64 MB file limit');
await fs.mkdir(path.dirname(path.resolve(outputPath)), {recursive: true});
await fs.writeFile(outputPath, checked.stdout, {flag: 'wx'});
console.log(`PASS independent training bank: ${bank.id}`);
