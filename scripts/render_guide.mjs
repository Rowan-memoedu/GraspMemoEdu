/** Render local Guide Markdown using the existing Marked/MathJax runtimes. */
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const bundled = process.env.CODEX_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
const mathRuntime = process.env.MATH_TOPIC_PDF_RUNTIME || 'D:/Caches/MathTopicPdf/node_modules';
const guideRuntime = process.env.GRASPMEMOEDU_NODE_MODULES || 'D:/Caches/GraspMemoEdu/node-runtime/node_modules';
const br = createRequire(path.join(bundled, '_entry.cjs'));
const mr = createRequire(path.join(mathRuntime, '_entry.cjs'));
const gr = createRequire(path.join(guideRuntime, '_entry.cjs'));
const { Marked } = await import(pathToFileURL(br.resolve('marked')).href);
const sanitizeHtml = gr('sanitize-html');
const escape = value => String(value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let adaptor, svg, document;
const formulas = [];
const resources = new Set();
const headings = new Map();
const ASSET_ROOT = 'https://guide-asset.invalid/';

function math(latex, display) {
  if (/\\(?:href|url|html\w*|includegraphics|class|style|cssId|require)\b/i.test(latex)) throw new Error('Active TeX is not supported');
  if (!document) {
    const { mathjax } = mr('mathjax-full/js/mathjax.js');
    const { TeX } = mr('mathjax-full/js/input/tex.js');
    const { SVG } = mr('mathjax-full/js/output/svg.js');
    const { liteAdaptor } = mr('mathjax-full/js/adaptors/liteAdaptor.js');
    const { RegisterHTMLHandler } = mr('mathjax-full/js/handlers/html.js');
    const { AllPackages } = mr('mathjax-full/js/input/tex/AllPackages.js');
    adaptor = liteAdaptor();
    RegisterHTMLHandler(adaptor);
    const tex = new TeX({
      packages: AllPackages.filter(name => !['noundefined', 'noerrors', 'html', 'require', 'autoload'].includes(name)),
      formatError: (_jax, error) => { throw error; }
    });
    svg = new SVG({ fontCache: 'none' });
    document = mathjax.document('', { InputJax: tex, OutputJax: svg });
  }
  const rendered = adaptor.outerHTML(document.convert(latex, { display, em: 15, ex: 7.5, containerWidth: 732 }));
  if (/data-mml-node="merror"|data-mjx-error|<(?:script|iframe|foreignObject|image)\b|\son\w+\s*=|(?:href|src)="(?:https?:|\/\/|data:|javascript:)|(?:url|expression)\s*\(|@import/i.test(rendered)) throw new Error('Invalid math');
  const marker = `GUIDEMATHPLACEHOLDER${formulas.length}END`;
  const tag = display ? 'div' : 'span';
  formulas.push({ marker, html: `<${tag} class="${display ? 'mathDisplay' : 'mathInline'}" aria-label="${escape(latex)}">${rendered}</${tag}>` });
  return display ? `<p>${marker}</p>\n` : marker;
}

function resourceUrl(raw, isImage) {
  const value = String(raw || '').trim();
  if (!value || /[\u0000-\u001f\\]/.test(value) || value.startsWith('//')) return '';
  if (value.startsWith('#')) return isImage ? '' : value;
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) {
    try {
      const url = new URL(value);
      if (url.username || url.password) return '';
      return (isImage ? url.protocol === 'https:' : ['https:', 'http:', 'mailto:'].includes(url.protocol)) ? url.href : '';
    } catch { return ''; }
  }
  let decoded;
  try { decoded = decodeURIComponent(value.split(/[?#]/)[0]); } catch { return ''; }
  if (decoded.startsWith('/') || /[\u0000-\u001f\\:]/.test(decoded)) return '';
  const pieces = decoded.split('/').filter(piece => piece && piece !== '.');
  if (!pieces.length || pieces.some(piece => piece === '..')) return '';
  const relative = pieces.join('/');
  const extension = path.posix.extname(relative).toLowerCase();
  if (!(isImage ? ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp'] : ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp', '.pdf', '.txt', '.csv']).includes(extension)) return '';
  resources.add(relative);
  return ASSET_ROOT + pieces.map(encodeURIComponent).join('/') + '?v=GUIDEASSETVERSION';
}

const marked = new Marked({
  gfm: true,
  renderer: {
    html(token) { return escape(token.text); },
    heading(token) {
      const slug = token.text.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s+/g, '-') || 'section';
      const count = (headings.get(slug) || 0) + 1;
      headings.set(slug, count);
      return `<h${token.depth} id="${escape(slug + (count > 1 ? '-' + count : ''))}">${this.parser.parseInline(token.tokens)}</h${token.depth}>\n`;
    }
  },
  walkTokens(token) {
    if (token.type === 'image' || token.type === 'link') token.href = resourceUrl(token.href, token.type === 'image');
  },
  extensions: [{
    name: 'guideBlockMath', level: 'block',
    start(source) { const match = /\$\$|\\\[/.exec(source); return match?.index; },
    tokenizer(source) {
      const match = /^(?:\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\])(?:\n|$)/.exec(source);
      if (match) return { type: 'guideBlockMath', raw: match[0], latex: (match[1] || match[2]).trim() };
    },
    renderer(token) { return math(token.latex, true); }
  }, {
    name: 'guideInlineMath', level: 'inline',
    start(source) { const match = /\$|\\\(/.exec(source); return match?.index; },
    tokenizer(source) {
      const match = /^(?:\$(?!\$)([^$\n]+?)\$|\\\(([^\n]+?)\\\))/.exec(source);
      if (match) return { type: 'guideInlineMath', raw: match[0], latex: (match[1] || match[2]).trim() };
    },
    renderer(token) { return math(token.latex, false); }
  }]
});

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { markdown } = JSON.parse(input);
if (typeof markdown !== 'string' || Buffer.byteLength(markdown) > 1024 * 1024) throw new Error('Guide source is too large');
let html = sanitizeHtml(marked.parse(markdown), {
  allowedTags: [...sanitizeHtml.defaults.allowedTags, 'img', 'del', 'details', 'summary'],
  allowedAttributes: {
    a: ['href', 'title', 'target', 'rel'], img: ['src', 'alt', 'title', 'loading'],
    h1: ['id'], h2: ['id'], h3: ['id'], h4: ['id'], h5: ['id'], h6: ['id'],
    code: ['class'], th: ['align'], td: ['align']
  },
  allowedSchemes: ['http', 'https', 'mailto'],
  allowProtocolRelative: false,
  transformTags: {
    a: (_tag, attributes) => ({ tagName: 'a', attribs: { ...attributes, rel: 'noopener noreferrer', ...(attributes.href?.startsWith('#') ? {} : { target: '_blank' }) } }),
    img: (_tag, attributes) => ({ tagName: 'img', attribs: { ...attributes, loading: 'lazy' } })
  }
});
for (const formula of formulas) html = html.replaceAll(`<p>${formula.marker}</p>`, formula.html).replaceAll(formula.marker, formula.html);
process.stdout.write(JSON.stringify({ html, math_css: document ? adaptor.textContent(svg.styleSheet(document)) : '', resources: [...resources] }));
