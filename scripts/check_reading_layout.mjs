/** Read-only responsive checks of original compiled book content and shared typography. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const [bundle,output]=process.argv.slice(2);
if(!output)throw Error('Expected compiled source bundle and evidence directory');
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const plan=JSON.parse(await fs.readFile(path.join(bundle,'course-install-plan.json')));
const topics=await Promise.all(plan.topics.map(async row=>JSON.parse(await fs.readFile(path.join(row.package,'topic.json')))));
const bank=JSON.parse(await fs.readFile(plan.bank.path));
const blocks=topics.flatMap(t=>t.introductions.map(i=>({id:t.id+'-'+i.id,html:i.html,css:t.math_css,kind:'reading',
 step:{...i,kind:'introduction',section:t.sections?.find(s=>s.id===i.section_id)}})));
const questions=bank.groups.flatMap(g=>g.questions);
const selected=[blocks[0],blocks.find(b=>b.html.includes('<img')),blocks.find(b=>b.html.includes('mathDisplay'))];
selected.push(blocks.find(b=>b.html.includes('class="sourceLead"')),blocks.find(b=>b.html.includes('data-source-role="heading"')),
 blocks.find(b=>b.html.includes('data-figure-group=')));
selected.push(blocks.find(b=>b.step.example),blocks.find(b=>b.step.footnotes?.length),blocks.find(b=>b.step.section?.title));
for(const row of plan.topics){
 const source=await fs.readFile(row.source,'utf8');
 if(source.includes('assets/pdf-')){selected.push(blocks.find(b=>b.id.startsWith(row.topic_id+'-')));break;}
}
const question=questions.find(q=>q.html.includes('<img'))||questions[0];
if(question)selected.push({id:question.id+'-question',html:question.html,css:bank.math_css,kind:'question'},
 {id:question.id+'-answer',html:question.explanation_html,css:bank.math_css,kind:'answer'});
const fallbackQuestion=questions.find(q=>q.question.includes('assets/pdf-'));
if(fallbackQuestion)selected.push({id:fallbackQuestion.id+'-question',html:fallbackQuestion.html,css:bank.math_css,kind:'question'},
 {id:fallbackQuestion.id+'-answer',html:fallbackQuestion.explanation_html,css:bank.math_css,kind:'answer'});
const cases=[...new Map(selected.filter(Boolean).map(b=>[b.id,b])).values()];
const inlineImage='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/mWQAAAAASUVORK5CYII=';
cases.push({id:'independent-prose-list-and-inline-image',kind:'fixture',css:'',html:
 `<p>Inline icon <img src="${inlineImage}" alt="inline"> stays in this sentence.</p><ul><li>First original item<ul><li>Nested original item</li></ul></li></ul><p><img src="${inlineImage}" alt="block"></p><p>Figure 1: Original caption.</p>`});
await fs.mkdir(output,{recursive:true});
const server=http.createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://127.0.0.1');
  if(url.pathname==='/'){
   const item=cases[Number(url.searchParams.get('case')||0)];
   res.setHeader('Content-Type','text/html;charset=utf-8');
   res.end(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
   <link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/portal.css"><link rel="stylesheet" href="/topic-content.css"><style>${item.css}
   body{margin:0}main{max-width:880px;box-sizing:border-box;margin:auto;padding:24px 18px}</style>
   <main><article class="courseChoice materialBlock" id="source"></article></main>
   <script type="module">import {enhanceSourceContent} from '/topic-content.js'; import {renderReaderStep} from '/learning-ui.js';
   const root=document.getElementById('source'); const step=${JSON.stringify(item.step||{html:item.html,title:'原书内容'}).replaceAll('<','\\u003c')};
   renderReaderStep(root,step);window.original={text:root.textContent,images:root.querySelectorAll('img').length,lists:root.querySelectorAll('ul,ol').length};
   for(const body of root.querySelectorAll('.courseContent')){enhanceSourceContent(body);enhanceSourceContent(body);}window.ready=true;</script>`);return;
  }
  const file=path.resolve(repo,'web','.'+decodeURIComponent(url.pathname));
  if(!file.startsWith(path.join(repo,'web')+path.sep))throw Error('outside web');
  res.setHeader('Content-Type',/\.m?js$/.test(file)?'text/javascript':file.endsWith('.woff2')?'font/woff2':'text/css');res.end(await fs.readFile(file));
 }catch{res.statusCode=404;res.end();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
const report={passed:false,source_bundle:path.resolve(bundle),samples:[],errors:[],external_requests:[]};
try{
 const page=await browser.newPage();page.setDefaultTimeout(20000);page.setDefaultNavigationTimeout(20000);
 page.on('pageerror',e=>report.errors.push(e.message));
 await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():
  (report.external_requests.push(route.request().url()),route.abort()));
 for(const [index,item] of cases.entries())for(const width of [320,375,414,768,1280]){
  console.log('Checking '+item.id+' at '+width+'px');
  await page.setViewportSize({width,height:1000});
  await page.goto(`http://127.0.0.1:${server.address().port}/?case=${index}`);
  await page.waitForFunction(()=>window.ready);
  await page.evaluate(()=>document.fonts.ready);
  await page.evaluate(()=>Promise.all([...document.images].map(i=>{i.loading='eager';return i.decode();})));
  const actual=await page.evaluate(()=>{
   const root=document.getElementById('source');
   const centers=[...root.querySelectorAll('.sourceFigure img')].map(i=>{
    const a=i.getBoundingClientRect(),b=i.closest('figure').getBoundingClientRect();return Math.abs((a.left+a.right-b.left-b.right)/2);});
   return{text_unchanged:root.textContent===original.text,images_unchanged:root.querySelectorAll('img').length===original.images,
    lists_unchanged:root.querySelectorAll('ul,ol').length===original.lists,figures:centers.length,
    figures_centered:centers.every(d=>d<2),overflow:document.documentElement.scrollWidth>innerWidth,
    math_errors:root.querySelectorAll('[data-mml-node="merror"]').length,
    missing_images:[...root.querySelectorAll('img')].filter(i=>!i.naturalWidth).length};
  });
  report.samples.push({id:item.id,kind:item.kind,width,...actual});
  assert.equal(actual.text_unchanged,true);assert.equal(actual.images_unchanged,true);assert.equal(actual.lists_unchanged,true);
  assert.equal(actual.figures_centered,true);assert.equal(actual.overflow,false,`${item.id} overflow at ${width}`);
  assert.equal(actual.math_errors,0);assert.equal(actual.missing_images,0);
  if(item.kind==='fixture')assert.equal(actual.figures,1,'The inline image must stay inline');
  if(width===375||width===1280)await page.screenshot({path:path.join(output,item.id+'-'+width+'.png'),fullPage:false});
  if(width===1280&&item.step?.footnotes?.length)await page.locator('.readingFootnotes').screenshot({path:path.join(output,item.id+'-footnotes.png')});
 }
 assert.equal(report.errors.length,0);assert.equal(report.external_requests.length,0);report.passed=true;
}finally{
 await fs.writeFile(path.join(output,'reading-layout-verification.json'),JSON.stringify(report,null,2));
 await browser.close();await new Promise(r=>server.close(r));
}
console.log(JSON.stringify({passed:report.passed,samples:report.samples.length}));
