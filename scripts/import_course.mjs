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
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const digest = value => crypto.createHash('sha256').update(value).digest('hex');
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));

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

export function sourceQuestions(topic) {
  const introduction = topic.children.filter(node => /^Introduction$/i.test(node.head));
  if (introduction.length !== 1) throw new Error('Expected one Introduction');
  const modules = topic.children.filter(node => /^Example\s+\d+/i.test(node.head));
  if (modules.length + 1 !== topic.children.length) throw new Error('Unknown top-level sections');
  const questions = [];
  for (const [index, module] of modules.entries()) {
    const moduleId = `example-${index + 1}`;
    if (Number(module.head.match(/^Example\s+(\d+)/i)[1]) !== index + 1) throw new Error('Example numbers must be consecutive');
    const practices = module.children.filter(node => /^Practice\s+\d+$/i.test(node.head));
    for (const [i, node] of [module, ...practices].entries()) {
      const id = i === 0 ? `${moduleId}-question` : `${moduleId}-practice-${i}`;
      if (i && Number(node.head.match(/\d+/)[0]) !== i) throw new Error(`Practice numbers must be consecutive: ${id}`);
      const explanation = node.children.filter(child => /^Explanation$/i.test(child.head));
      const question = node.children.filter(child => !/^Explanation$|^Practice\s+\d+$/i.test(child.head));
      if (explanation.length !== 1 || !question.length) throw new Error(`Missing question or Explanation: ${id}`);
      questions.push({id, moduleId, title:node.head, line:node.line, node, questionNodes:question,
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

async function renderer() {
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
    const raw = [node.head, ...node.lines].join('\n').trim();
    const replacements = [];
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
    // links and images instead of allowing arbitrary source HTML into the app.
    if (/<\/?[a-z][^>]*>|!?\[[^\]]*\]\s*\(/i.test(safe)) throw new Error(`Unsupported active Markdown at line ${node.line}`);
    const plain = safe.replace(/MATHIMPORTTOKEN\d+END/g, '').replace(/^\s*\d+\.\s*/gm, '').replace(/\s/g, '');
    if (plain) prose.push({source_line:node.line, text:plain});
    let html = marked.parse(safe, {gfm:true, breaks:false});
    if (/<(?:script|iframe|img|a|style|link|object|embed|input|form)\b|\son\w+\s*=/i.test(html)) throw new Error('Unsupported rendered Markdown');
    for (const item of replacements) html = item.display ? html.replace(`<p>${item.token}</p>`, item.html) : html.replace(item.token, item.html);
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

export async function importCourse(sourcePath, sidecarPath, outputPath, auditPath) {
  const paths = [sourcePath, sidecarPath, outputPath, auditPath].map(p => path.resolve(p));
  if (new Set(paths.map(p => p.toLowerCase())).size !== 4) throw new Error('Input and output paths must be distinct');
  const sourceBuffer = await fs.readFile(sourcePath);
  const source = sourceBuffer.toString('utf8');
  const sidecar = JSON.parse(await fs.readFile(sidecarPath, 'utf8'));
  const sourceHash = digest(sourceBuffer);
  if (sourceHash !== sidecar.source_sha256) throw new Error('Source changed since the answer sidecar was reviewed');
  const check = spawnSync(process.env.MATH_LEARNING_PYTHON || 'python', [path.join(here, 'verify_content.py'), sourcePath, sidecarPath],
    {encoding:'utf8', maxBuffer:32 * 1024 * 1024, env:{...process.env, PYTHONIOENCODING:'utf-8'}, windowsHide:true});
  if (check.status !== 0) throw new Error(`Independent arithmetic verification failed: ${check.stderr || check.error || check.status}`);
  const audit = JSON.parse(check.stdout);
  const topic = parseOutline(source);
  const content = sourceQuestions(topic);
  if (content.modules.length !== sidecar.expected_module_count || content.questions.length !== sidecar.expected_question_count) throw new Error('Unexpected module or question count');
  const render = await renderer();
  const course = {id:sidecar.course_id, title:topic.head, source_sha256:sourceHash,
    introduction:{id:'introduction', title:'Introduction', html:render.nodes(content.introduction.children, true)}, modules:[], math_css:render.css};
  const formulaChanges = [];
  for (const [index, module] of content.modules.entries()) {
    const moduleId = `example-${index + 1}`;
    const members = content.questions.filter(q => q.moduleId === moduleId);
    if (members.length !== sidecar.expected_practices_per_module + 1) throw new Error(`Unexpected Practice count in ${moduleId}`);
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
        source_line:question.line, ...(corrections.length ? {errata:corrections.map(c => ({original:c.original, replacement:c.replacement, reason:c.reason}))} : {})};
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
  const report = {...audit, course_version:course.version, course_path:path.resolve(outputPath),
    module_count:course.modules.length, practice_count:course.modules.reduce((count, m) => count + m.practices.length, 0),
    formula_count:render.formulas.length, rendered_formula_count:render.formulas.length,
    reviewed_formula_change_count:differences.length, math_errors:0,
    introduction_hierarchy_preserved:true, source_unchanged:true, external_math_requests:0,
    renderer_versions:render.versions, formulas:render.formulas, prose_records:render.prose};
  await writeAtomic(outputPath, JSON.stringify(course, null, 2) + '\n');
  await writeAtomic(outputPath.replace(/\.json$/i, '.sources.json'), JSON.stringify({
    source_path:path.resolve(sourcePath), answers_path:path.resolve(sidecarPath), source_sha256:sourceHash
  }, null, 2) + '\n');
  await writeAtomic(auditPath, JSON.stringify(report, null, 2) + '\n');
  return {course_path:path.resolve(outputPath), audit_path:path.resolve(auditPath), course_version:course.version,
    question_count:report.question_count, formula_count:report.formula_count, errata_count:report.errata.length};
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [source, sidecar, output, audit] = process.argv.slice(2);
  if (!source || !sidecar || !output || !audit) throw new Error('Usage: node import_course.mjs <source.md> <answers.json> <course.json> <audit.json>');
  console.log(JSON.stringify(await importCourse(source, sidecar, output, audit), null, 2));
}
