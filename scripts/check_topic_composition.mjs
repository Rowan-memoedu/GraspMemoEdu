/** Real API + actual Lesson/bank controllers, using only disposable local data. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const [output]=process.argv.slice(2); if(!output)throw Error('Expected isolated output directory');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
await fs.mkdir(output,{recursive:true});
const port=18879, origin=`http://127.0.0.1:${port}`;
const server=spawn('D:/AppData/GraspMemoEdu/venv/Scripts/python.exe',[path.join(root,'scripts/serve_composition_canary.py'),path.join(output,'data'),String(port)],{windowsHide:true,cwd:root});
let logs='';server.stdout.on('data',b=>logs+=b);server.stderr.on('data',b=>logs+=b);
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');let browser;
const report={passed:false,real_api:true,external_requests:[],checks:[],errors:[]};
try{
 for(let i=0;i<120;i++){
  if(server.exitCode!==null)throw Error('Canary API failed: '+logs);
  try{if((await fetch(origin+'/api/health')).ok)break;}catch{}
  await new Promise(r=>setTimeout(r,250));
 }
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1280,height:1000}});page.setDefaultTimeout(30000);
 let identity, session;
 page.on('request',request=>{const headers=request.headers();if(headers['x-learning-identity'])identity=headers['x-learning-identity'];if(headers['x-learning-session'])session=headers['x-learning-session'];});
 page.on('pageerror',e=>report.errors.push(e.message));
 await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():(report.external_requests.push(route.request().url()),route.abort()));
 await page.goto(origin+'/#/subjects/math/topic/composition-ui',{waitUntil:'networkidle'});
 await page.locator('#stepCard [data-field="example"]').waitFor();
 assert.equal(await page.locator('#stepCard [data-field="footnotes"]').count(),1);
 assert.equal(await page.locator('#stepCard .readingSectionHeading').textContent(),'一、小标题');
 report.checks.push('Original heading, separate example and footnotes fields are visible in the actual Topic');
 await page.locator('#stepCard > .stepNavigation .primaryButton').click();
 await page.locator('#trainingAnswer').waitFor({state:'attached'});
 assert.ok(page.url().endsWith('/topic/composition-ui'));
 assert.equal(await page.locator('#stepCard > .stepNavigation .primaryButton').count(),0);
 await page.locator('#trainingAnswer').fill('2');
 await page.waitForTimeout(5500);
 assert.equal(await page.locator('#trainingAnswer').inputValue(),'2');
 await page.locator('#trainingSubmit').click();
 await page.locator('.embeddedBankQuestion .feedback.correct').waitFor();
 await page.locator('#stepCard > .stepNavigation .primaryButton').waitFor();
 report.checks.push('Canonical bank grading unlocks Topic continuation; periodic validation preserves the draft and Topic URL');
 for(const width of [320,375,414,768,1280]){
  await page.setViewportSize({width,height:1000});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:path.join(output,`shared-bank-${width}.png`),fullPage:false});
 }
 await page.locator('#stepCard > .stepNavigation .primaryButton').click();
 await page.getByText('完成例题后才能阅读这一段。',{exact:true}).waitFor();
 await Promise.all([page.waitForResponse(r=>r.url().includes('/api/continue')&&r.request().method()==='POST'),page.locator('#stepCard > .stepNavigation .primaryButton').click()]);
 const complete=await page.request.get(origin+'/api/state?topic_id=composition-ui',{headers:{'X-Learning-Identity':identity,'X-Learning-Session':session,'Origin':origin}});
 assert.equal(complete.status(),200,await complete.text());assert.equal((await complete.json()).status,'completed');
 await page.goto(origin+'/#/subjects/math/banks/isolated-bank/group-a?question=exact',{waitUntil:'networkidle'});
 await page.locator('#trainingstepCard .feedback.correct').waitFor();
 report.checks.push('The normal bank reader opens the same completed attempt and answer');
 assert.ok(identity);
 const response=await page.request.get(origin+'/api/training/history?bank_id=isolated-bank&question_id=exact',{headers:{'X-Learning-Identity':identity,'X-Learning-Session':session,'Origin':origin}});
 assert.equal(response.status(),200,await response.text()); const history=await response.json();assert.equal(history.items.length,1);
 report.checks.push('Only one canonical bank attempt exists across course and bank');
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.external_requests,[]);report.passed=true;
}catch(error){report.failure=String(error);process.exitCode=1;}
finally{await browser?.close();server.kill();await fs.writeFile(path.join(output,'server.log'),logs);await fs.writeFile(path.join(output,'composition-ui.json'),JSON.stringify(report,null,2));}
console.log(JSON.stringify(report));
