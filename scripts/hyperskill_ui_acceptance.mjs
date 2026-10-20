// Read-only screenshots of source content and isolated interaction states.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
import {presentation} from './hyperskill_protocol.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const [packageDir,sourceFile,output,datasetDirectory]=process.argv.slice(2);
const topic=JSON.parse(await fs.readFile(path.join(packageDir,'topics/hyperskill-topic-308.json')));
const live=JSON.parse(await fs.readFile(sourceFile));
const extraTypes=['fill-blanks','sorting','parsons','table','code'],datasets={};
if(datasetDirectory)for(const type of extraTypes)
  datasets[type]=JSON.parse(await fs.readFile(path.join(datasetDirectory,`dataset-${type}.json`)));
await fs.mkdir(output,{recursive:true});
const req=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=req('playwright');
const server=http.createServer(async(request,response)=>{
  try {
    const url=new URL(request.url,'http://127.0.0.1');
    if(url.pathname==='/fixture') {
      const scenario=url.searchParams.get('case');
      const card=topic.flashcards[scenario==='matching'?5:0];
      const current={...card,card_id:card.id,phase:'answer',hyperskill:{...card.hyperskill,submissions:[]}};
      let data={session:'synthetic',attempt_id:1,interaction:presentation('choice',live.practices[0].attempt.dataset)};
      if(datasets[scenario]) {
        const source=datasets[scenario];
        data.interaction=presentation(scenario,source.attempt.dataset,undefined,source.block.options);
        current.html=(await import('./render_mathacademy.mjs')).renderSourceHtml(source.block.text).html;
      }
      if(scenario==='matching')data.interaction=presentation('matching',{pairs:[{first:'Integer literal',second:'123'},{first:'Long literal',second:'123L'},{first:'Float literal',second:'1.2F'}]});
      if(scenario==='text')data.interaction=null;
      if(scenario==='pending')Object.assign(data,{pending:true,message:'提交正在确认中。请点击刷新结果，不要重复提交。'});
      if(scenario==='wrong')data.last={status:'wrong'};
      if(scenario==='feedback')Object.assign(current,{phase:'feedback',answer:'0x2A',reason:'首次通过前的错误负担：0 · Good',
        hyperskill:{...current.hyperskill,submissions:[{submission_id:123,status:'correct',feedback:''}]}});
      const state={training_mode:'atomic_retrieval',topic_id:topic.id,course_version:topic.version,learner_id:'isolated',
        revision:1,status:'in_progress',mode:'learn',phase:scenario==='intro'?'introduction':'answer',
        attempt_id:'round',active_step_id:'step',pending_submission_id:null,pause:null,history:[],point_count:8,learned_count:0,
        can_learn:true,can_submit:true,title:topic.title,server_time:new Date().toISOString(),completed_at:null,current,
        introduction:topic.introduction,math_css:topic.math_css};
      response.setHeader('Content-Type','text/html;charset=utf-8');
      response.end(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/portal.css"><link rel="stylesheet" href="/topic-content.css"><div class="portalPage" id="root"></div>
        <script type="module">
        import {createAtomicView} from '/atomic.js';
        import {hyperskillQuestion} from '/hyperskill.js';
        const state=${JSON.stringify(state)},data=${JSON.stringify(data)};
        const access={learner_id:'isolated',features:['learn','submit_answer']};
        if(${JSON.stringify(scenario)}!=='connect')sessionStorage.setItem(
          'graspmemoedu:atomic:'+location.origin+':isolated:'+state.topic_id+':'+state.course_version+':hyperskill:step',JSON.stringify(data));
        else sessionStorage.clear();
        await createAtomicView({root:document.getElementById('root'),getAccess:()=>access,getApiOrigin:()=>location.origin,
          setAccess:()=>{},request:async()=>state,progressChanged:()=>{},homeHref:()=>'/',
          reviewHref:()=>'/reviews',formatDate:v=>v}).open(state.topic_id,'learn',state);
        window.ready=true;</script>`);
      return;
    }
    const file=path.resolve(root,'web',decodeURIComponent(url.pathname).slice(1));
    if(!file.startsWith(path.join(root,'web')+path.sep))throw Error('scope');
    response.setHeader('Content-Type',/\.m?js$/.test(file)?'text/javascript':file.endsWith('.css')?'text/css':'application/octet-stream');
    response.end(await fs.readFile(file));
  } catch {response.statusCode=404;response.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({channel:'chrome',headless:true});
const errors=[];
try {
  const page=await browser.newPage({viewport:{width:1200,height:850}});
  page.on('pageerror',error=>errors.push(error.message));
  const scenarios=['intro','connect','choice','matching','text','pending','wrong','feedback',...Object.keys(datasets)];
  for(const scenario of scenarios) {
    await page.goto(`http://127.0.0.1:${server.address().port}/fixture?case=${scenario}`);
    await page.waitForFunction(()=>window.ready);
    if(scenario==='choice')assert.equal(await page.locator('input[type=checkbox],input[type=radio]').count(),live.practices[0].attempt.dataset.options.length);
    if(scenario==='feedback')assert.match(await page.locator('.feedback').innerText(),/Good/);
    if(scenario==='intro') {
      assert.equal(await page.locator('.sourceOutlineSection').count(),7);
      assert.equal(await page.locator('.sourceCodeBlock').count(),14);
      const codes=await page.locator('.sourceCodeBlock pre').allTextContents();
      assert.ok(codes.some(text=>text.includes('1_000_000')));
      assert.ok(codes.some(text=>text.includes('*')));
    }
    await page.screenshot({path:path.join(output,scenario+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto(`http://127.0.0.1:${server.address().port}/fixture?case=choice`);
  await page.waitForFunction(()=>window.ready);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  for(const scenario of Object.keys(datasets))for(const width of [320,375,414,768]) {
    await page.setViewportSize({width,height:900});
    await page.goto(`http://127.0.0.1:${server.address().port}/fixture?case=${scenario}`);
    await page.waitForFunction(()=>window.ready);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${scenario} at ${width}`);
    if(scenario==='fill-blanks')assert.equal(await page.locator('.inlineBlank').count(),datasets[scenario].attempt.dataset.components.length-1);
    if(width===375)await page.screenshot({path:path.join(output,scenario+'-mobile.png'),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify({scenarios:scenarios.length+1+Object.keys(datasets).length*4,errors,source_topic:topic.id,interactions:0},null,2));
  console.log('PASS original theory/options, all supplied input types, desktop/mobile, no page errors');
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
