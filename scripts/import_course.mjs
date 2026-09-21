/** Import an audited outline without copying private content into the repository.
 * Usage: node scripts/import_course.mjs <source.md> <answers.json> <course.json> <audit.json>
 * Dependencies: CODEX_NODE_MODULES (marked), MATH_TOPIC_PDF_RUNTIME (mathjax-full).
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { inflateSync } from 'node:zlib';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const imagePattern = /!\[([^\]\n]*)\]\(([^)\n]+)\)/g;
const diagramComment = /<!-- topic-diagrams:[a-f0-9]{64} -->/g;
const imagePaths = text => [...new Set([...text.matchAll(imagePattern)].map(match => match[2]))];

export async function loadAssets(sourcePath, source) {
  const root = await fs.realpath(path.dirname(path.resolve(sourcePath)));
  const assets = new Map();
  for (const relative of imagePaths(source)) {
    if (!/^[^\\:%?#<>"|*\x00-\x1f]+\.png$/i.test(relative) || path.posix.isAbsolute(relative)
        || relative.split('/').some(part => !part || part === '.' || part === '..' || part !== part.trim())) {
      throw new Error(`Unsupported local PNG path: ${relative}`);
    }
    let current = root;
    for (const part of relative.split('/')) {
      current = path.join(current, part);
      if ((await fs.lstat(current)).isSymbolicLink()) throw new Error('Image symlinks and junctions are not supported');
    }
    const resolved = await fs.realpath(current);
    if (path.relative(root, resolved).startsWith('..') || path.isAbsolute(path.relative(root, resolved))) throw new Error('Image escapes source directory');
    const stat = await fs.stat(resolved);
    if (!stat.isFile() || stat.size > 8 * 1024 * 1024) throw new Error('PNG exceeds size limit');
    const data = await fs.readFile(resolved);
    if (data.length < 45 || !data.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))
        || data.readUInt32BE(8) !== 13 || data.toString('ascii', 12, 16) !== 'IHDR') throw new Error('Invalid PNG header');
    const width = data.readUInt32BE(16), height = data.readUInt32BE(20);
    if (!width || !height || width > 10000 || height > 10000 || width * height > 20000000) throw new Error('PNG dimensions exceed limit');
    let offset = 8, ended = false;
    const compressed = [];
    while (offset + 12 <= data.length) {
      const size = data.readUInt32BE(offset), end = offset + 12 + size;
      if (end > data.length) throw new Error('Truncated PNG');
      const type = data.toString('ascii', offset + 4, offset + 8);
      let crc = 0xffffffff;
      for (const byte of data.subarray(offset + 4, end - 4)) {
        crc ^= byte;
        for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
      }
      if (((crc ^ 0xffffffff) >>> 0) !== data.readUInt32BE(end - 4)) throw new Error('PNG checksum mismatch');
      if (type === 'IDAT') compressed.push(data.subarray(offset + 8, end - 4));
      offset = end;
      if (type === 'IEND') { ended = size === 0 && offset === data.length; break; }
    }
    if (!ended || !compressed.length) throw new Error('Incomplete PNG');
    const depth = data[24], colour = data[25], channels = {0:1, 2:3, 3:1, 4:2, 6:4}[colour];
    const depths = {0:[1,2,4,8,16], 2:[8,16], 3:[1,2,4,8], 4:[8,16], 6:[8,16]};
    if (!channels || !depths[colour].includes(depth) || data[26] || data[27] || data[28]) throw new Error('Unsupported PNG encoding');
    const rowBytes = Math.ceil(width * channels * depth / 8) + 1;
    const decoded = inflateSync(Buffer.concat(compressed), {maxOutputLength:160 * 1024 * 1024});
    if (decoded.length !== rowBytes * height) throw new Error('PNG pixel data length mismatch');
    for (let row = 0; row < height; row++) if (decoded[row * rowBytes] > 4) throw new Error('Invalid PNG scanline filter');
    assets.set(relative, {path:relative, sha256:digest(data), size:data.length, mime:'image/png',
      width, height, data, occurrences:[...source.matchAll(imagePattern)].filter(match => match[2] === relative).length});
  }
  return assets;
}

const assetRecords = assets => [...assets.values()].map(({data, ...record}) => record);

export function parseOutline(source) {
  const root = {indent:-2, children:[], lines:[], line:0};
  const stack = [root];
  source.replace(/^\uFEFF/, '').split(/\r?\n/).forEach((line, index) => {
    const match = line.match(/^( *)- (.*)$/);
    if (match) {
      const indent = match[1].length;
      while (stack.at(-1).indent >= indent) stack.pop();
      if (indent !== stack.at(-1).indent + 2) throw new Error(`Invalid indentation at line ${index + 1}`);
      const node = {head:match[2], indent, line:index + 1, lines:[], children:[]};
      stack.at(-1).children.push(node);
      stack.push(node);
    } else {
      const node = stack.at(-1);
      if (line.trim() && line.match(/^ */)[0].length < node.indent + 2) throw new Error(`Invalid continuation at line ${index + 1}`);
      node.lines.push(line.slice(node.indent + 2));
    }
  });
  if (root.children.length !== 1) throw new Error('Expected one Topic root');
  return root.children[0];
}

function text(node) {
  const body = [node.head, ...node.lines].join('\n').trim();
  return body + (node.children.length ? '\n' + node.children.map(text).join('\n') : '');
}

const practiceHead = /^Practice\s+(\d+)(?:\.(\d+))?$/i;

function practiceNodes(module) {
  const nodes = module.children.filter(node => /^Practice(?:\s|$)/i.test(node.head));
  const matches = nodes.map(node => node.head.match(practiceHead));
  if (matches.some(match => !match)) throw new Error(`Invalid Practice heading in ${module.head}`);
  if (matches.some(match => match[2] !== undefined)) {
    if (nodes.length !== 15 || matches.some((match, index) =>
      Number(match[1]) !== Math.floor(index / 3) + 1 || Number(match[2]) !== index % 3 + 1)) {
      throw new Error(`Grouped Practice numbers must be consecutive 1.1 through 5.3 in ${module.head}`);
    }
  } else if (matches.some((match, index) => Number(match[1]) !== index + 1)) {
    throw new Error(`Practice numbers must be consecutive in ${module.head}`);
  }
  return nodes;
}

export function sourceQuestions(topic) {
  const introduction = topic.children.filter(node => /^Introduction$/i.test(node.head));
  if (introduction.length !== 1) throw new Error('Expected one Introduction');
  const modules = topic.children.filter(node => /^Example\s+\d+/i.test(node.head));
  if (modules.length + 1 !== topic.children.length) throw new Error('Unknown top-level sections');
  const questions = [];
  for (const [index, module] of modules.entries()) {
    const moduleId = `example-${index + 1}`;
    if (Number(module.head.match(/^Example\s+(\d+)/i)[1]) !== index + 1) throw new Error('Example numbers must be consecutive');
    const practices = practiceNodes(module);
    for (const [i, node] of [module, ...practices].entries()) {
      const id = i === 0 ? `${moduleId}-question` : `${moduleId}-practice-${i}`;
      const explanation = node.children.filter(child => /^Explanation$/i.test(child.head));
      const question = node.children.filter(child => !/^Explanation$/i.test(child.head) && !practices.includes(child));
      if (explanation.length !== 1 || !question.length) throw new Error(`Missing question or Explanation: ${id}`);
      if (!explanation[0].children.length) throw new Error(`Empty Explanation: ${id}`);
      const match = i ? node.head.match(practiceHead) : null;
      const grouped = match && match[2] !== undefined;
      questions.push({id, moduleId, title:grouped ? `Practice ${i}` : node.head,
        ...(grouped ? {source_practice:{label:`${match[1]}.${match[2]}`, group:Number(match[1]), member:Number(match[2])}} : {}),
        line:node.line, node, questionNodes:question,
        explanationNodes:explanation[0].children, question:question.map(text).join('\n')});
    }
  }
  return {introduction:introduction[0], modules, questions};
}

function applyErrata(nodes, errata) {
  const clone = structuredClone(nodes);
  for (const correction of errata) {
    let count = 0;
    function walk(node) {
      const replace = value => value.split(correction.original).map((part, index) => {
        if (index) count++;
        return part;
      }).join(correction.replacement);
      node.head = replace(node.head);
      node.lines = node.lines.map(replace);
      node.children.forEach(walk);
    }
    clone.forEach(walk);
    if (count !== 1) throw new Error(`Erratum must match exactly once: ${correction.question_id}`);
  }
  return clone;
}

async function renderer(assets = new Map()) {
  const bundle = process.env.CODEX_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
  const runtime = process.env.MATH_TOPIC_PDF_RUNTIME || 'D:/Caches/MathTopicPdf/node_modules';
  const br = createRequire(path.join(bundle, '_entry.cjs'));
  const mr = createRequire(path.join(runtime, '_entry.cjs'));
  const { marked } = await import(pathToFileURL(br.resolve('marked')).href);
  const { mathjax } = mr('mathjax-full/js/mathjax.js');
  const { TeX } = mr('mathjax-full/js/input/tex.js');
  const { SVG } = mr('mathjax-full/js/output/svg.js');
  const { liteAdaptor } = mr('mathjax-full/js/adaptors/liteAdaptor.js');
  const { RegisterHTMLHandler } = mr('mathjax-full/js/handlers/html.js');
  const { AllPackages } = mr('mathjax-full/js/input/tex/AllPackages.js');
  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const tex = new TeX({packages:AllPackages, formatError:(_jax, error) => { throw error; }});
  const svg = new SVG({fontCache:'none'});
  const document = mathjax.document('', {InputJax:tex, OutputJax:svg});
  const formulas = [];
  const prose = [];
  function body(node) {
    let raw = [node.head, ...node.lines].join('\n').trim().replace(diagramComment, '');
    if (/<!--|-->|(?:MATH|IMAGE)IMPORTTOKEN\d+END/.test(raw)) throw new Error('Unsupported comment or reserved placeholder');
    const replacements = [];
    const images = [];
    raw = raw.replace(imagePattern, (_all, alt, relative) => {
      const asset = assets.get(relative);
      if (!asset) throw new Error('Image was not validated');
      const token = `IMAGEIMPORTTOKEN${images.length}END`;
      images.push({token, html:`<img class="topicDiagram" alt="${escape(alt)}" src="data:image/png;base64,${asset.data.toString('base64')}" width="${asset.width}" height="${asset.height}" style="max-width:100%;height:auto" loading="lazy">`});
      return token;
    });
    const safe = raw.replace(/\$\$([\s\S]*?)\$\$|\$([^$\n]+)\$/g, (_whole, block, inline) => {
      const display = block !== undefined;
      const latex = (display ? block : inline).trim();
      if (/\\(?:href|url|htmlClass|htmlId|htmlStyle|class|style|cssId|require)\b/.test(latex)) throw new Error('Active TeX content is not supported');
      const rendered = adaptor.outerHTML(document.convert(latex, {display, em:15, ex:7.5, containerWidth:655}));
      if (/data-mml-node="merror"|data-mjx-error|<script|(?:href|src)="(?:https?:|\/\/)/i.test(rendered)) throw new Error(`Invalid math at line ${node.line}`);
      const id = formulas.length;
      const token = `MATHIMPORTTOKEN${id}END`;
      const tag = display ? 'div' : 'span';
      const html = `<${tag} class="${display ? 'mathDisplay' : 'mathInline'}" data-math-id="${id}" data-tex="${escape(latex)}">${rendered}</${tag}>`;
      replacements.push({token, html, display});
      formulas.push({id, latex, display, source_line:node.line});
      return display ? `\n\n${token}\n\n` : token;
    });
    // The curriculum format is local prose and mathematics. Reject markup,
    // links and arbitrary source HTML. Validated PNGs are injected afterwards.
    if (/<\/?[a-z][^>]*>|!?\[[^\]]*\]\s*\(/i.test(safe)) throw new Error(`Unsupported active Markdown at line ${node.line}`);
    const plain = safe.replace(/MATHIMPORTTOKEN\d+END/g, '').replace(/^\s*\d+\.\s*/gm, '').replace(/\s/g, '');
    if (plain) prose.push({source_line:node.line, text:plain});
    let html = marked.parse(safe, {gfm:true, breaks:false});
    if (/<(?:script|iframe|img|a|style|link|object|embed|input|form)\b|\son\w+\s*=/i.test(html)) throw new Error('Unsupported rendered Markdown');
    for (const item of replacements) html = item.display ? html.replace(`<p>${item.token}</p>`, item.html) : html.replace(item.token, item.html);
    for (const item of images) html = html.replace(item.token, item.html);
    if (/MATHIMPORTTOKEN\d+END/.test(html)) throw new Error('A formula placeholder was not rendered');
    return html;
  }
  function nodes(list, asList=false) {
    if (asList) return `<ul>${list.map(node => `<li data-source-line="${node.line}">${body(node)}${node.children.length ? nodes(node.children, true) : ''}</li>`).join('')}</ul>`;
    return list.map(node => `<div class="outlineNode" data-source-line="${node.line}">${body(node)}${node.children.length ? nodes(node.children, true) : ''}</div>`).join('');
  }
  return {nodes, formulas, prose, css:adaptor.textContent(svg.styleSheet(document)),
    versions:{marked:br('marked/package.json').version, mathjax:mr('mathjax-full/package.json').version}};
}

async function writeAtomic(filename, value) {
  await fs.mkdir(path.dirname(path.resolve(filename)), {recursive:true});
  const temporary = `${filename}.tmp-${process.pid}`;
  await fs.writeFile(temporary, value, 'utf8');
  await fs.rename(temporary, filename);
}

export async function inspectCourse(sourcePath) {
  const raw = await fs.readFile(sourcePath);
  const topic = parseOutline(raw.toString('utf8'));
  const content = sourceQuestions(topic);
  const assets = await loadAssets(sourcePath, raw.toString('utf8'));
  if (!content.modules.length) throw new Error('Expected at least one Example module');
  for (const [index] of content.modules.entries()) {
    if (content.questions.filter(q => q.moduleId === `example-${index + 1}`).length < 8) throw new Error('At least seven Practices per module required');
  }
  return {title:topic.head, source_sha256:digest(raw), introduction:content.introduction.children.map(text).join('\n'),
    assets:assetRecords(assets), introduction_image_paths:imagePaths(content.introduction.children.map(text).join('\n')),
    modules:content.modules.map((module, index) => ({id:`example-${index + 1}`, title:module.head,
      question_ids:content.questions.filter(q => q.moduleId === `example-${index + 1}`).map(q => q.id)})),
    questions:content.questions.map(q => ({id:q.id, module_id:q.moduleId, title:q.title, line:q.line,
      ...(q.source_practice ? {source_practice:q.source_practice} : {}),
      image_paths:imagePaths(q.question + '\n' + q.explanationNodes.map(text).join('\n')),
      question_image_paths:imagePaths(q.question), explanation_image_paths:imagePaths(q.explanationNodes.map(text).join('\n')),
      question:q.question, explanation:q.explanationNodes.map(text).join('\n'), question_sha256:digest(q.question)}))};
}

export async function importCourse(sourcePath, sidecarPath, outputPath, auditPath, reviewPath = null) {
  const sourceManifestPath = outputPath.replace(/\.json$/i, '.sources.json');
  if (sourceManifestPath === outputPath) throw new Error('Course output must end with .json');
  const paths = [sourcePath, sidecarPath, outputPath, sourceManifestPath, auditPath, ...(reviewPath ? [reviewPath] : [])].map(p => path.resolve(p));
  if (new Set(paths.map(p => p.toLowerCase())).size !== paths.length) throw new Error('Input and output paths must be distinct');
  const sourceBuffer = await fs.readFile(sourcePath);
  const source = sourceBuffer.toString('utf8');
  const sidecarBuffer = await fs.readFile(sidecarPath);
  const sidecar = JSON.parse(sidecarBuffer.toString('utf8').replace(/^\uFEFF/, ''));
  const reviewHash = reviewPath ? digest(await fs.readFile(reviewPath)) : null;
  const sourceHash = digest(sourceBuffer);
  if (sourceHash !== sidecar.source_sha256) throw new Error('Source changed since the answer sidecar was reviewed');
  const check = spawnSync(process.env.MATH_LEARNING_PYTHON || 'python', [path.join(here, 'verify_content.py'), sourcePath, sidecarPath, ...(reviewPath ? ['--review', reviewPath] : [])],
    {encoding:'utf8', maxBuffer:32 * 1024 * 1024, env:{...process.env, PYTHONIOENCODING:'utf-8'}, windowsHide:true});
  if (check.status !== 0) throw new Error(`${reviewPath ? 'Reviewed content' : 'Independent arithmetic'} verification failed: ${check.stderr || check.error || check.status}`);
  const audit = JSON.parse(check.stdout);
  if (audit.source_sha256 !== sourceHash || audit.sidecar_sha256 !== digest(sidecarBuffer)
      || (reviewPath && audit.review_sha256 !== reviewHash)) throw new Error('Inputs changed during verification');
  const topic = parseOutline(source);
  const content = sourceQuestions(topic);
  if (content.modules.length !== sidecar.expected_module_count || content.questions.length !== sidecar.expected_question_count) throw new Error('Unexpected module or question count');
  const assets = await loadAssets(sourcePath, source);
  if (assets.size && reviewPath) {
    const reviewedAssets = JSON.parse(await fs.readFile(reviewPath, 'utf8')).assets;
    const binding = list => list.map(asset => ({path:asset.path, sha256:asset.sha256, size:asset.size, mime:asset.mime}));
    if (!Array.isArray(reviewedAssets) || JSON.stringify(binding(reviewedAssets)) !== JSON.stringify(binding(assetRecords(assets)))) {
      throw new Error('Reviewed images differ from source assets; review again');
    }
  }
  const render = await renderer(assets);
  const course = {id:sidecar.course_id, title:topic.head, source_sha256:sourceHash,
    introduction:{id:'introduction', title:'Introduction', html:render.nodes(content.introduction.children, true)}, modules:[], math_css:render.css};
  const formulaChanges = [];
  for (const [index, module] of content.modules.entries()) {
    const moduleId = `example-${index + 1}`;
    const members = content.questions.filter(q => q.moduleId === moduleId);
    if (!(reviewPath && sidecar.expected_practices_per_module == null) && members.length !== sidecar.expected_practices_per_module + 1) throw new Error(`Unexpected Practice count in ${moduleId}`);
    const built = members.map(question => {
      const answer = sidecar.answers[question.id];
      if (digest(question.question) !== answer.question_sha256) throw new Error(`Question content changed: ${question.id}`);
      const corrections = (sidecar.errata || []).filter(correction => correction.question_id === question.id);
      const explanationNodes = applyErrata(question.explanationNodes, corrections);
      formulaChanges.push(...corrections);
      const html = render.nodes(question.questionNodes);
      const notice = corrections.length ? `<p class="contentErratum" role="note">${escape(corrections.map(c => c.display_note).join(' '))}</p>` : '';
      return {id:question.id, title:question.title, question:question.question, html,
        explanation_html:notice + render.nodes(explanationNodes), reference_answer:answer.reference_answer,
        answer_type:answer.answer_type, constraints:answer.constraints,
        source_line:question.line, ...(question.source_practice ? {source_practice:question.source_practice} : {}),
        ...(corrections.length ? {errata:corrections.map(c => ({original:c.original, replacement:c.replacement, reason:c.reason}))} : {})};
    });
    course.modules.push({id:moduleId, title:module.head, example:built[0], practices:built.slice(1)});
  }
  const originalFormulas = [...source.matchAll(/\$\$([\s\S]*?)\$\$|\$([^$\n]+)\$/g)].map(match => (match[1] ?? match[2]).replace(/^ +/gm, '').trim());
  if (originalFormulas.length !== render.formulas.length) throw new Error('Formula count changed');
  const differences = [];
  for (let index = 0; index < originalFormulas.length; index++) {
    const actual = render.formulas[index].latex.replace(/^ +/gm, '').trim();
    if (originalFormulas[index] !== actual) differences.push({index, original:originalFormulas[index], actual});
  }
  if (differences.length !== formulaChanges.length) throw new Error('Unexpected formula changes outside reviewed errata');
  for (const difference of differences) {
    if (!formulaChanges.some(c => difference.original.includes(c.original) && difference.original.replace(c.original, c.replacement) === difference.actual)) throw new Error('Unreviewed formula mutation');
  }
  course.version = digest(JSON.stringify(course));
  if (digest(await fs.readFile(sourcePath)) !== sourceHash) throw new Error('Source changed during import');
  if (digest(await fs.readFile(sidecarPath)) !== digest(sidecarBuffer)) throw new Error('Sidecar changed during import');
  if (reviewPath && digest(await fs.readFile(reviewPath)) !== reviewHash) throw new Error('Review changed during import');
  if (JSON.stringify(assetRecords(await loadAssets(sourcePath, source))) !== JSON.stringify(assetRecords(assets))) throw new Error('Images changed during import');
  const report = {...audit, course_version:course.version, course_path:path.resolve(outputPath),
    module_count:course.modules.length, practice_count:course.modules.reduce((count, m) => count + m.practices.length, 0),
    formula_count:render.formulas.length, rendered_formula_count:render.formulas.length,
    reviewed_formula_change_count:differences.length, math_errors:0,
    introduction_hierarchy_preserved:true, source_unchanged:true, external_math_requests:0,
    renderer_versions:render.versions, formulas:render.formulas, prose_records:render.prose, assets:assetRecords(assets)};
  if (reviewPath) {
    const review = JSON.parse(await fs.readFile(reviewPath, 'utf8'));
    const resolved = review.questions.filter(question => question.review_origin === 'human_resolution');
    if (resolved.length && review.mode !== 'mechanical') {
      report.method = 'llm-reviewed-with-local-resolution';
      report.human_resolution_count = resolved.length;
      const resolvedIds = new Set(resolved.map(question => question.id));
      report.records = report.records.map(record => resolvedIds.has(record.id)
        ? {...record, method:'human-resolution', review_origin:'human_resolution'} : record);
    }
    if (digest(await fs.readFile(reviewPath)) !== reviewHash) throw new Error('Review changed during audit');
  }
  if (report.method === 'mechanical-import') {
    delete report.math_errors;
    report.math_render_errors = 0;
  }
  await writeAtomic(outputPath, JSON.stringify(course, null, 2) + '\n');
  await writeAtomic(sourceManifestPath, JSON.stringify({
    source_path:path.resolve(sourcePath), answers_path:path.resolve(sidecarPath), source_sha256:sourceHash,
    ...(reviewPath ? {review_path:path.resolve(reviewPath), review_sha256:reviewHash} : {})
  }, null, 2) + '\n');
  await writeAtomic(auditPath, JSON.stringify(report, null, 2) + '\n');
  return {course_path:path.resolve(outputPath), audit_path:path.resolve(auditPath), course_version:course.version,
    question_count:report.question_count, formula_count:report.formula_count, errata_count:report.errata.length};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [source, sidecar, output, audit, review] = process.argv.slice(2);
  if (source === '--inspect' && sidecar) console.log(JSON.stringify(await inspectCourse(sidecar), null, 2));
  else {
    if (!source || !sidecar || !output || !audit) throw new Error('Usage: node import_course.mjs <source.md> <answers.json> <course.json> <audit.json> [review.json], or --inspect <source.md>');
    console.log(JSON.stringify(await importCourse(source, sidecar, output, audit, review), null, 2));
  }
}
