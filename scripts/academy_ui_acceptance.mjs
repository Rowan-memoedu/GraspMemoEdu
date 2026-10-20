// Non-interactive rendering in a disposable headless browser. No user profile or login.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = process.argv[2];
if (!output) throw new Error('Provide an isolated output directory');
await fs.mkdir(output, {recursive:true});
const req = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = req('playwright');
const state = {training_mode:'atomic_retrieval', topic_id:'synthetic', course_version:'fixture', learner_id:'isolated',
  revision:1, status:'in_progress', mode:'review', phase:'answer', attempt_id:'round', active_step_id:'step',
  pending_submission_id:null, pause:null, history:[], point_count:1, learned_count:1, can_learn:true, can_submit:true,
  title:'十进制转七进制', server_time:new Date().toISOString(), completed_at:new Date().toISOString(),
  current:{id:'step',card_id:'task',title:'十进制转七进制',phase:'answer',html:'<p>读取整数，并输出其七进制表示。</p><pre><code>Input: 49\nOutput: 100</code></pre>',
    point_ids:['task'],academy:{task_key:'synthetic',kind:'automated',round_id:null,checks:0,opened:false}}};
const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url,'http://127.0.0.1');
    if (url.pathname === '/fixture.html') {
      const scenario = url.searchParams.get('case'); const current = structuredClone(state);
      if (scenario === 'manual') current.current.academy = {...current.current.academy, kind:'manual',opened:true,round_id:'round'};
      if (scenario === 'waiting') current.current.academy = {...current.current.academy, opened:true,round_id:'round',checks:2};
      if (scenario === 'feedback') Object.assign(current.current,{phase:'feedback',reason:'首次通过前的错误负担：0.75 · Hard',correct:true,ratings:{task:2}});
      response.setHeader('Content-Type','text/html;charset=utf-8');
      response.end(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/portal.css"><link rel="stylesheet" href="/subjects.css"><link rel="stylesheet" href="/training.css"><div id="root" class="portalPage"></div><script type="module">
        import {createAtomicView} from '/atomic.js';
        const state=${JSON.stringify(current)}, access={learner_id:'isolated',features:['learn','submit_answer']};
        const view=createAtomicView({root:document.getElementById('root'),getAccess:()=>access,getApiOrigin:()=>location.origin,
          setAccess:()=>{},request:async()=>state,progressChanged:()=>{},homeHref:()=>'/courses',reviewHref:()=>'/reviews',formatDate:v=>v});
        await view.open('synthetic','learn',state); window.ready=true;
      </script></html>`); return;
    }
    const relative = decodeURIComponent(url.pathname).slice(1);
    const file = path.resolve(root,'web',relative);
    if (!file.startsWith(path.join(root,'web')+path.sep)) throw new Error('Path escape');
    response.setHeader('Content-Type',file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream');
    response.end(await fs.readFile(file));
  } catch {response.statusCode=404;response.end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser = await chromium.launch({channel:'chrome',headless:true});
const failures=[];
try {
  const page=await browser.newPage({viewport:{width:1200,height:850}});
  page.on('pageerror',error=>{failures.push(error.message); console.error(error.message);});
  for (const scenario of ['initial','waiting','manual','feedback']) {
    await page.goto(`http://127.0.0.1:${server.address().port}/fixture.html?case=${scenario}`);
    await page.waitForFunction(()=>window.ready);
    assert.equal(await page.locator('textarea').count(),0,'IDE exercise must not offer web text grading');
    if (scenario === 'manual') assert.equal(await page.locator('.trainingRating').count(),4);
    if (scenario === 'feedback') assert.match(await page.locator('.feedback').innerText(),/Hard/);
    if (scenario === 'initial') assert.match(await page.locator('.primaryButton').innerText(),/IDEA/);
    await page.screenshot({path:path.join(output,scenario+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto(`http://127.0.0.1:${server.address().port}/fixture.html?case=manual`); await page.waitForFunction(()=>window.ready);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'No horizontal overflow');
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
  assert.deepEqual(failures,[]);
  await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify({scenarios:5,page_errors:failures,profile:'disposable',interactions:0},null,2));
  console.log('PASS 5 read-only render scenarios; no page errors or mobile overflow');
} finally {await browser.close(); await new Promise(resolve=>server.close(resolve));}
