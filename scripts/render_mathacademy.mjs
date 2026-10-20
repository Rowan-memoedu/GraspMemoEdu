/** Deterministic source renderer. JSON-lines input; one response per Topic. */
import path from 'node:path';
import os from 'node:os';
import readline from 'node:readline';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
const req = dir => createRequire(path.join(dir, '_entry.cjs'));
const br = req(process.env.CODEX_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'));
const mr = req(process.env.MATH_TOPIC_PDF_RUNTIME || 'D:/Caches/MathTopicPdf/node_modules');
const gr = req(process.env.GRASPMEMOEDU_NODE_MODULES || 'D:/Caches/GraspMemoEdu/node-runtime/node_modules');
const {Marked} = br('marked'), sanitize = gr('sanitize-html');
const {mathjax} = mr('mathjax-full/js/mathjax.js'), {TeX} = mr('mathjax-full/js/input/tex.js');
const {SVG} = mr('mathjax-full/js/output/svg.js'), {liteAdaptor} = mr('mathjax-full/js/adaptors/liteAdaptor.js');
const {RegisterHTMLHandler} = mr('mathjax-full/js/handlers/html.js'), {AllPackages} = mr('mathjax-full/js/input/tex/AllPackages.js');
const adaptor = liteAdaptor(); RegisterHTMLHandler(adaptor);
const tex = new TeX({packages: AllPackages.filter(x => !['noundefined','noerrors','html','require','autoload'].includes(x)),
  formatError: (_jax, error) => {throw error;}});
const svg = new SVG({fontCache: 'none'}), doc = mathjax.document('', {InputJax: tex, OutputJax: svg});
const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hash = s => crypto.createHash('sha256').update(s).digest('hex').slice(0,20);
let formulas = [], references = [], occurrence = 0;
function math(source, display) {
  // These packages are already loaded locally; never execute dynamic TeX loads.
  source = source.replace(/\\require\{(enclose|cancel|color|bbox)\}/g, '')
    .replace(/&#(?:x([0-9a-f]+)|(\d+));/gi, (_s,h,d)=>String.fromCodePoint(parseInt(h||d,h?16:10)));
  if (/\\(?:href|url|html\w*|includegraphics|class|style|cssId|require)\b/i.test(source)) throw Error('Active TeX is not supported');
  source = source.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
    .replace(/\\phantom\{\\textrm\{([^{}]*_[^{}]*)\}\}/g, (_a,s) => `\\phantom{\\mathrm{${s}}}`)
    .replace(/\\(text|textrm)\{([^{}]*)\}/g, (_a,cmd,s) => `\\${cmd}{${s.replace(/(?<!\\)[_#]/g,c=>'\\'+c)}}`);
  // Bare display alignments use the same MathJax aligned environment as align*.
  if (display && /(?<!\\)&/.test(source) && !/\\begin\{/.test(source)) source = `\\begin{aligned}${source}\\end{aligned}`;
  let html;
  try { html = adaptor.outerHTML(doc.convert(source, {display, em:16, ex:8, containerWidth:680})); }
  catch(error) { throw Error(`Math conversion: ${error.message}; source=${source.slice(0,800)}`); }
  if (/merror|data-mjx-error/.test(html)) throw Error('Math rendering failed');
  const token = `MAFORMULA${formulas.length}END`;
  formulas.push(`<span class="${display ? 'mathDisplay' : 'mathInline'}">${html}</span>`);
  return token;
}
function delimitedMath(source) {
  const environment = /^\\begin\{(align\*?|aligned|equation\*?|gather\*?|cases|bmatrix|pmatrix)\}([\s\S]*?)\\end\{\1\}/.exec(source);
  if (environment) return {raw:environment[0],latex:environment[0],display:true};
  for (const [open, close, display] of [['\\[','\\]',true],['\\(','\\)',false],['$$','$$',true],['$','$',false]]) {
    if (!source.startsWith(open)) continue;
    let depth = 0;
    for (let i = open.length; i < source.length; i++) {
      if (depth === 0 && source.startsWith(close, i) && source[i-1] !== '\\')
        return {raw:source.slice(0,i+close.length),latex:source.slice(open.length,i),display};
      if (source[i] === '{' && source[i-1] !== '\\') depth++;
      if (source[i] === '}' && source[i-1] !== '\\') depth--;
    }
  }
  return null;
}
function mathInHtml(html) {
  const saved = [];
  html = html.replace(/<(svg|pre|code)\b[\s\S]*?<\/\1>/gi, s => {saved.push(s);return `MAPROTECT${saved.length-1}END`;});
  let result = '';
  for (let i = 0; i < html.length;) {
    const token = (html[i] === '$' || html[i] === '\\') && html[i-1] !== '\\' ? delimitedMath(html.slice(i)) : null;
    if (token) { result += math(token.latex, token.display); i += token.raw.length; }
    else result += html[i++];
  }
  html = result;
  return html.replace(/MAPROTECT(\d+)END/g, (_s,n) => saved[+n]);
}
const marked = new Marked({gfm:true, renderer:{html(token){return mathInHtml(token.text);}}, extensions:[
  {name:'maBlockMath',level:'block',start:s=>/\$\$|(?<!\\)\\\[|\\begin\{/.exec(s)?.index,
    tokenizer(s){const m=delimitedMath(s);if(m?.display)return {type:'maBlockMath',...m};},
    renderer:t=>math(t.latex,true)},
  {name:'maInlineMath',level:'inline',start:s=>/\$|(?<!\\)\\[([]|\\begin\{/.exec(s)?.index,
    tokenizer(s){const m=delimitedMath(s);if(m)return {type:'maInlineMath',...m};},
    renderer:t=>math(t.latex,t.display)}
]});
function safeHtml(html, prefix) {
  const svgTags = ['svg','g','defs','path','use','rect','line','polyline','polygon','circle','ellipse','text','tspan','title','desc','clipPath'];
  const cleaned = sanitize(html, {parser:{lowerCaseTags:false,lowerCaseAttributeNames:false},
    allowedTags:[...sanitize.defaults.allowedTags,'aside','img','span','pre','code','br','mjx-container',...svgTags],
    allowedAttributes:{'*':['class','id'], a:['href','title'], img:['src','alt','width','height'],
      span:['class','id','data-control','aria-label'], aside:['class','data-callout'],
      table:['style'], td:['rowspan','colspan','align','style'], th:['rowspan','colspan','align','style'],
      ol:['start','type'], li:['value'], 'mjx-container':['class','jax','display'],
      ...Object.fromEntries(svgTags.map(t=>[t,['id','viewBox','width','height','x','y','x1','x2','y1','y2','d','points','r','rx','ry','cx','cy','fill','stroke','stroke-width','transform','href','xlink:href','xmlns','xmlns:xlink','preserveAspectRatio','role','aria-hidden','focusable','style','data-mml-node']]))},
    allowedStyles:{svg:{'vertical-align':[/^-?[\d.]+(?:ex|em|px)$/],overflow:[/^visible$/]},
      g:{fill:[/^(?:currentColor|none|#[\da-fA-F]+)$/],stroke:[/^(?:currentColor|none|#[\da-fA-F]+)$/]},
      '*':{'text-align':[/^(?:left|center|right)$/],'vertical-align':[/^(?:top|middle|bottom)$/],
        'border-collapse':[/^collapse$/],'border':[/^[0-3]px (?:solid|dashed) (?:black|gray|#[0-9a-fA-F]{3,6})$/],
        'padding':[/^[0-9.]+(?:px|em)$/]}},
    allowedSchemes:['data'], allowedSchemesByTag:{a:['https','http'],use:[]},allowProtocolRelative:false,
    transformTags:{
      a:(_t,a)=>{if(/^https?:\/\//.test(a.href||''))references.push({url:a.href,label:a.title||a.href});return {tagName:'span',attribs:{class:'sourceReferenceText'}};},
      img:(_t,a)=>({tagName:'img',attribs:/^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+$/.test(a.src||'')?a:{alt:a.alt||'原材料图片缺失'}}),
      use:(_t,a)=>({tagName:'use',attribs:Object.fromEntries(Object.entries(a).filter(([k,v])=>!['href','xlink:href'].includes(k)||/^#[\w:.-]+$/.test(v)))})
    }, nonTextTags:['script','style','textarea','option','iframe','object']});
  const ids = new Map([...cleaned.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],`${prefix}-${m[1]}`]));
  return cleaned.replace(/\bid="([^"]+)"/g, (_s,id)=>`id="${ids.get(id)}"`)
    .replace(/((?:xlink:)?href)="#([^"]+)"/g,(_s,k,id)=>ids.has(id)?`${k}="#${ids.get(id)}"`:'')
    .replace(/url\(#([^)]*)\)/g,(_s,id)=>ids.has(id)?`url(#${ids.get(id)})`:'none');
}
function render(source, sourceHtml='') {
  formulas = [];
  source = String(source||'').replace(/\[math\]([\s\S]*?)\[\/math\]/g, (_s,t)=>`$${t}$`);
  let html;
  if (sourceHtml?.trim() && /<(?:p|div|h[1-6]|table|ul|ol|blockquote|pre|svg|img|section|body|html|span)\b/i.test(sourceHtml)) {
    const body = sourceHtml.match(/<body\b[^>]*>([\s\S]*?)<\/body>/i);
    html = mathInHtml(body ? body[1] : sourceHtml);
  } else html = marked.parse(source);
  html = html.replace(/MAFORMULA(\d+)END/g,(_s,n)=>formulas[+n]);
  html = html.replace(/\{\{control:([A-Za-z0-9_-]+)\}\}/g, '<span data-control="$1"></span>');
  return safeHtml(html, `ma-${hash(source+sourceHtml)}-${occurrence++}`);
}
function transform(value) {
  if (Array.isArray(value)) return value.map(transform);
  if (value && typeof value === 'object') {
    if (Object.hasOwn(value,'_render')) return render(value._render, value._html);
    return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,transform(v)]));
  }
  return value;
}
export function renderSourceMarkdown(source) {
  references=[]; occurrence=0;
  const html=render(source);
  return {html, css:adaptor.textContent(svg.styleSheet(doc)), references};
}
export function renderSourceHtml(source) {
  references=[]; occurrence=0;
  // HTML must bypass Markdown punctuation parsing, including inside source code.
  const html=render('',`<div>${source}</div>`);
  return {html, css:adaptor.textContent(svg.styleSheet(doc)), references};
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
const lines=readline.createInterface({input:process.stdin,crlfDelay:Infinity});
for await(const line of lines){
  try {references=[];occurrence=0;const topic=transform(JSON.parse(line));
    topic.external_references=[...new Map(references.map(r=>[r.url,r])).values()];
    topic.math_css=adaptor.textContent(svg.styleSheet(doc));
    process.stdout.write(JSON.stringify({ok:true,topic})+'\n');
  } catch(error){process.stdout.write(JSON.stringify({ok:false,error:error.message})+'\n');}
}
}
