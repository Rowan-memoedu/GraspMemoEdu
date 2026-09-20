/** Two isolated dashboard regressions; deliberate timer and HTTP fault injection. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base,out]=process.argv.slice(2);
assert.equal(new URL(base).hostname,'127.0.0.1');
assert.ok(!['8765','8767',''].includes(new URL(base).port));
assert.ok(out,'A new output directory is required');
await fs.mkdir(out); // Preserve previous success and failure receipts.
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE||path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
const report={checks:[],errors:[],formal_data_opened:false,real_judge:false,
 test_controls:{timer_acceleration:'Only window.setTimeout delay exactly 15000 ms is changed to 300 ms.',
 http_fault_injection:'Exactly one dashboard GET receives a synthetic HTTP 503. All other HTTP requests use the actual isolated backend.',
 reset:'A separate HTTP client resets the current synthetic guest, simulating a reset in another tab.',
 judge:'Synthetic arithmetic oracle from review_web_fixture.py; no new external judging calls.'}};
const topic='review-web-canary';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let currentPage;
async function device(){
 const context=await browser.newContext();const page=await context.newPage();currentPage=page;
 page.setDefaultTimeout(15000);const d={context,page,token:null};
 page.on('pageerror',e=>report.errors.push(e.message));
 page.on('request',r=>{d.token=r.headers()['x-learning-identity']||d.token;});
 await page.addInitScript(()=>{const original=window.setTimeout;window.setTimeout=(fn,delay,...args)=>original(fn,delay===15000?300:delay,...args);});
 await page.goto(base+'#/subjects/math/courses');await page.locator('.courseLearn').first().click();await page.locator('#dashboardTasks').waitFor();
 return d;
}
try{
 const d=await device(),page=d.page;
 const initial=await page.locator('.incompleteTasks .portalTask').evaluateAll(nodes=>nodes.map(n=>n.dataset.taskId));
 assert.ok(initial.length>0);
 let faults=0,recovered=0;
 await page.route('**/api/dashboard?*',async route=>{
   if(!faults){faults++;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:{message:'TEST transient dashboard failure'}})});}
   else{recovered++;await route.continue();}
 });
 await page.locator('.taskRefreshError').waitFor();
 assert.deepEqual(await page.locator('.incompleteTasks .portalTask').evaluateAll(nodes=>nodes.map(n=>n.dataset.taskId)),initial,'Tasks retained while refresh error is shown');
 await page.screenshot({path:path.join(out,'network-transient.png'),fullPage:true});
 await page.locator('.taskRefreshError').waitFor({state:'detached'});
 assert.ok(recovered>=1);assert.equal(faults,1);
 assert.deepEqual(await page.locator('.incompleteTasks .portalTask').evaluateAll(nodes=>nodes.map(n=>n.dataset.taskId)),initial,'Tasks remain after recovery');
 await page.screenshot({path:path.join(out,'network-recovered.png'),fullPage:true});
 report.network={faults,successful_requests_started:recovered,retained_task_ids:initial,error_cleared:true};
 report.checks.push('One 503 preserves existing tasks; subsequent successful refresh clears the error');
 console.log('PASS transient dashboard recovery');await d.context.close();

 const g=await device();const transport=await(await fetch(base+'/api/session',{headers:{Origin:base}})).json();
 async function api(route,body){
   const response=await fetch(base+route,{method:body?'POST':'GET',headers:{Origin:base,'X-Learning-Session':transport.session_token,'X-Learning-Identity':g.token,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
   const data=await response.json();assert.equal(response.status,200,JSON.stringify(data));return data;
 }
 await api('/api/state?topic_id='+topic);
 const seed=await api('/__qa/seed',{module_id:'module-1',due_in_seconds:-10});assert.equal(seed.real_judge,false);
 const state=await api('/api/review/begin?topic_id='+topic,{request_id:crypto.randomUUID(),module_id:'module-1'});
 const submission=await api(`/api/review/sessions/${state.session_id}/submit?topic_id=${topic}`,{request_id:crypto.randomUUID(),expected_revision:state.revision,question_id:state.practice.id,answer:'42'});
 let result;
 for(let i=0;i<100;i++){
   result=await api(`/api/review/submissions/${submission.submission_id}?topic_id=${topic}`);
   if(result.status==='judged')break;await sleep(100);
 }
 assert.equal(result.status,'judged');assert.equal(result.state.mastery,4);
 await g.page.locator('#completedTasks .taskCompleted').first().waitFor();
 const before=await api('/api/dashboard?course_id=arithmetic-revisited');
 assert.ok(before.history_revision);assert.equal(before.history.length,1);
 await g.page.screenshot({path:path.join(out,'history-before-reset.png'),fullPage:true});
 const lesson=await api('/api/state?topic_id='+topic);
 await api('/api/guest/reset?topic_id='+topic,{request_id:crypto.randomUUID(),expected_revision:lesson.revision,attempt_id:lesson.attempt_id,step_id:lesson.active_step_id});
 const after=await api('/api/dashboard?course_id=arithmetic-revisited');
 assert.equal(after.history.length,0);assert.notEqual(after.history_revision,before.history_revision);
 await g.page.locator('#completedTasks .taskCompleted').waitFor({state:'detached'});
 assert.equal(await g.page.locator('#completedTasks .taskCompleted').count(),0);
 await g.page.screenshot({path:path.join(out,'history-after-reset.png'),fullPage:true});
 report.reset={before_history_count:before.history.length,after_history_count:after.history.length,
 history_revision_changed:true,browser_history_count:0,no_reload_after_reset:true};
 report.checks.push('Separate-client guest reset changes history_revision and clears cached browser history without reload');
 console.log('PASS separate-client reset invalidates cached history');await g.context.close();
 assert.deepEqual(report.errors,[]);report.status='passed';
}catch(error){
 report.status='failed';report.failure=String(error);
 if(currentPage&&!currentPage.isClosed()){
   await currentPage.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
   await fs.writeFile(path.join(out,'failure.txt'),await currentPage.locator('body').innerText().catch(()=>''));
 }
 throw error;
}finally{await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();}
