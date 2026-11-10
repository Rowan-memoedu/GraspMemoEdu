/** Verify the published canonical bank embed with an ephemeral test administrator. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {createRequire} from 'node:module';
const [root,output]=process.argv.slice(2);
const api='https://124.221.144.155',base='https://rowan-memoedu.github.io/GraspMemoEdu-Pages/';
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const account=JSON.parse(await fs.readFile('D:/AppData/GraspMemoEdu/admin/testing-20261005-admin-1.json','utf8')).accounts.find(a=>a.purpose==='test'&&a.is_admin);
await fs.mkdir(output,{recursive:true});const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext();let token;
const report={passed:false,published:true,learning_answers_submitted:0,samples:[],errors:[]};
async function call(route,data){
 const response=await context.request.fetch(api+'/api/'+route,{method:data?'POST':'GET',headers:{Origin:new URL(base).origin,...(token?{'X-Learning-Identity':token}:{})},...(data?{data}:{})});
 assert.equal(response.status(),200);return response.json();
}
try{
 const login=await call('access/login',{invitation_code:account.invitation_code});token=login.identity_token;
 assert.equal(login.access.is_admin,true);assert.equal(login.access.purpose,'test');
 await context.addInitScript(({api,token})=>{localStorage.setItem('graspmemoedu:identity:'+api,token);localStorage.setItem('graspmemoedu:identity-role:'+api,'account');},{api,token});
 for(const book of ['gaoxian','liwenwei']){
  const plan=JSON.parse(await fs.readFile(path.join(root,book,'course-install-plan.json')));let entry,topic;
  for(const row of plan.topics){const candidate=JSON.parse(await fs.readFile(path.join(row.package,'topic.json')));if(candidate.bank_questions?.length){entry=row;topic=candidate;break;}}
  const query=new URLSearchParams({subject_id:plan.subject_id,topic_id:entry.topic_id});
  let state=await call('state?'+query),step;
  for(let i=0;i<topic.content_order.length+1;i++){
   step=state.steps.find(s=>s.id===state.active_step_id);if(step.kind==='bank_question')break;
   state=await call('continue?'+query,{request_id:randomUUID(),expected_revision:state.revision,attempt_id:state.attempt_id,step_id:step.id});
  }
  assert.equal(step.kind,'bank_question');assert.equal(step.bank_completed,false);
  const page=await context.newPage();page.setDefaultTimeout(90000);page.on('pageerror',e=>report.errors.push(e.message));
  await page.goto(base+'#/subjects/'+plan.subject_id+'/topic/'+entry.topic_id,{waitUntil:'domcontentloaded',timeout:90000});
  await page.locator('.embeddedBankQuestion #trainingAnswer').waitFor({state:'attached'});
  assert.equal(await page.locator('#stepCard > .stepNavigation .primaryButton').count(),0);
  assert.ok(page.url().endsWith('/topic/'+entry.topic_id));
  for(const width of [375,1280]){
   await page.setViewportSize({width,height:1000});await page.evaluate(()=>document.fonts.ready);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.equal(await page.locator('.embeddedBankQuestion [data-mml-node="merror"]').count(),0);
   await page.screenshot({path:path.join(output,book+'-'+width+'.png')});
   report.samples.push({book,width,topic_id:entry.topic_id,reference:step.bank_reference,required:true});
  }
  await page.close();
 }
 assert.deepEqual(report.errors,[]);report.passed=true;
}finally{
 if(token)await call('access/logout',{});await browser.close();
 await fs.writeFile(path.join(output,'public-composition.json'),JSON.stringify(report,null,2));
}
console.log(JSON.stringify(report));
