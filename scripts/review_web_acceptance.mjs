/** Isolated Chrome + real HTTP review acceptance. See review_web_fixture.py. */
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base,out] = process.argv.slice(2);
assert.equal(new URL(base).hostname,'127.0.0.1');
assert.ok(!['8765','8767',''].includes(new URL(base).port));
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE || path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
await fs.mkdir(out,{recursive:true});
const topic='review-web-canary';
const report={checks:[],rounds:[],errors:[],http_mocked:false,formal_data_opened:false};
const pass=name=>{report.checks.push(name);process.stdout.write(`PASS ${name}\n`);};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
try {
 const session=await (await fetch(base+'/api/session',{headers:{Origin:base}})).json();
 async function device(){
   const context=await browser.newContext({viewport:{width:1365,height:960},locale:'zh-CN'});
   const page=await context.newPage();page.setDefaultTimeout(25000);
   const device={context,page,token:null};
   page.on('pageerror',e=>report.errors.push(e.message));
   page.on('request',r=>{const token=r.headers()['x-learning-identity'];if(token)device.token=token;});
   await page.goto(base,{waitUntil:'networkidle'});
   await page.locator('.courseLearn').first().click();
   await page.waitForFunction(()=>!!document.querySelector('#dashboardTasks'));
   for(let i=0;!device.token&&i<50;i++)await sleep(100);
   assert.ok(device.token,'Guest identity established');
   await api(device,`state?topic_id=${topic}`);
   return device;
 }
 async function api(d,route,body){
   const response=await fetch(base+'/api/'+route,{method:body?'POST':'GET',headers:{Origin:base,'X-Learning-Session':session.session_token,'X-Learning-Identity':d.token,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
   const result=await response.json();assert.equal(response.status,200,`${route}: ${JSON.stringify(result)}`);return result;
 }
 async function seed(d,module='module-1',delay=-10){
   const response=await fetch(base+'/__qa/seed',{method:'POST',headers:{Origin:base,'X-Learning-Identity':d.token,'Content-Type':'application/json'},body:JSON.stringify({module_id:module,due_in_seconds:delay})});
   assert.equal(response.status,200);const result=await response.json();report.real_judge=result.real_judge;return result;
 }
 async function current(d,id){return api(d,`review/sessions/${id}?topic_id=${topic}`);}
 async function begin(d){
   await d.page.goto(base+`#/review/${topic}/module-1`,{waitUntil:'networkidle'});
   await d.page.locator('#reviewAnswerInput').waitFor();
   const state=await api(d,`review/begin?topic_id=${topic}`,{module_id:'module-1',request_id:crypto.randomUUID()});
   const text=await d.page.locator('#reviewContent').innerText();
   assert.doesNotMatch(text,/EXAMPLE_SENTINEL|INTRODUCTION_SENTINEL|Example/);
   return state;
 }
 for(const [name,sequence,mastery] of [['easy',[true],4],['hard',[false,true,true],2],['again',[false,false,true,true,true],1],['paused',[false,false,false],null]]){
   const d=await device();await seed(d);await d.page.reload({waitUntil:'networkidle'});
   await d.page.locator('#dashboardTasks .portalTask').filter({hasText:'复习'}).first().waitFor();
   if(name==='easy')pass('Chinese homepage labels due task 复习');
   let state=await begin(d);const questions=[];
   if(name==='easy'){
     await d.page.locator('#reviewPauseButton').click();
     await d.page.getByText('该功能需注册账号才能使用',{exact:false}).first().waitFor();
     const denied=await fetch(base+`/api/review/sessions/${state.session_id}/pause?topic_id=${topic}`,{method:'POST',headers:{Origin:base,'X-Learning-Session':session.session_token,'X-Learning-Identity':d.token,'Content-Type':'application/json'},body:JSON.stringify({request_id:crypto.randomUUID()})});
     assert.equal(denied.status,403);pass('Guest manual pause UI notice and API 403');
     await d.page.setViewportSize({width:390,height:844});
     assert.equal(await d.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
     await d.page.screenshot({path:path.join(out,'review-mobile-390.png'),fullPage:true});
     pass('390px review page has no horizontal overflow');
     await d.page.setViewportSize({width:1365,height:960});
   }
   for(let i=0;i<sequence.length;i++){
     questions.push(state.practice.id);
     await d.page.locator('#reviewAnswerInput').fill(sequence[i]?'42':'0');
     const sent=d.page.waitForResponse(r=>r.url().includes(`/review/sessions/${state.session_id}/submit`)&&r.request().method()==='POST');
     await d.page.locator('#reviewSubmitButton').click();
     const response=await sent;assert.equal(response.status(),200);const submitted=await response.json();
     if(name==='hard'&&i===0)await d.page.reload({waitUntil:'networkidle'});
     for(let poll=0;poll<180;poll++){
       state=await current(d,state.session_id);
       if(!state.pending_submission_id&&state.practice_answered_count===i+1)break;
       await sleep(500);
     }
     assert.equal(state.practice_answered_count,i+1);
     assert.equal(state.practice_error_count,sequence.slice(0,i+1).filter(v=>!v).length);
     const replay=await api(d,`review/sessions/${state.session_id}/submit?topic_id=${topic}`,response.request().postDataJSON());
     assert.equal(replay.submission_id,submitted.submission_id);
     assert.equal(replay.state.practice_answered_count,i+1);
     if(i+1<sequence.length){
       await d.page.locator('#reviewContinueButton').click();
       await d.page.waitForFunction(()=>!!document.querySelector('#reviewAnswerInput'));
       state=await current(d,state.session_id);
     }
   }
   assert.equal(state.mastery,mastery);assert.equal(state.status,mastery===null?'paused':'completed');
   assert.equal(new Set(questions).size,questions.length);
   if(mastery!==null)assert.ok(Date.parse(state.due_at)>Date.now());
   const stable=await current(d,state.session_id);await d.page.reload({waitUntil:'networkidle'});
   assert.deepEqual(await current(d,state.session_id),stable);
   const auditResponse=await fetch(base+'/__qa/audit',{headers:{Origin:base,'X-Learning-Identity':d.token}});
   assert.equal(auditResponse.status,200);const audit=await auditResponse.json();
   assert.equal(audit.integrity,'ok');
   assert.deepEqual(audit.ratings.map(row=>row.mastery),mastery===null?[1]:[1,mastery],'Exactly one FSRS call per completed round; none for paused round');
   await d.page.screenshot({path:path.join(out,`review-${name}.png`),fullPage:true});
   report.rounds.push({name,status:state.status,mastery,errors:state.practice_error_count,answered:state.practice_answered_count,due_at:state.due_at,ratings:audit.ratings});
   pass(`${name}: ${sequence.length} distinct practices, mastery ${mastery}, replay/refresh stable`);
   await d.context.close();
 }
 const d=await device();const upcoming=await seed(d,'module-1',8);
 await d.page.reload({waitUntil:'networkidle'});
 assert.equal(await d.page.locator('#dashboardTasks .portalTask').filter({hasText:'复习'}).count(),0);
 await d.page.locator('#dashboardTasks .portalTask').filter({hasText:'复习'}).first().waitFor({timeout:35000});
 assert.ok(Date.now()>=Date.parse(upcoming.due_at));pass('Homepage automatically reveals task at calculated due time');
 await d.page.screenshot({path:path.join(out,'review-homepage.png'),fullPage:true});
 await d.context.close();
 assert.deepEqual(report.errors,[]);report.status='passed';
} catch(error){
 report.status='failed';report.failure=String(error);
 for(const [index,page] of browser.contexts().flatMap(context=>context.pages()).entries()){
   await page.screenshot({path:path.join(out,`failure-${index}.png`),fullPage:true}).catch(()=>{});
   await fs.writeFile(path.join(out,`failure-${index}.txt`),await page.locator('body').innerText().catch(()=>''));
 }
 throw error;
}
finally{await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();}
