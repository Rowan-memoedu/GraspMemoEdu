/** Read-only published course/bank outlines, including singleton projection. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [root,output,...names]=process.argv.slice(2);
const api='https://124.221.144.155',base='https://rowan-memoedu.github.io/GraspMemoEdu-Pages/';
const assetVersion=(await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8')).match(/app\.js\?v=([a-f0-9]+)/)[1];
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext();await fs.mkdir(output,{recursive:true});
const accounts=JSON.parse(await fs.readFile('D:/AppData/GraspMemoEdu/admin/testing-20261005-admin-1.json'));
const account=accounts.accounts.find(a=>a.purpose==='test'&&a.is_admin);
const report={passed:false,samples:[],errors:[],learning_mutations:[]};let token;
try{
 const login=await context.request.post(api+'/api/access/login',{headers:{Origin:new URL(base).origin},data:{invitation_code:account.invitation_code}});
 assert.equal(login.status(),200);token=(await login.json()).identity_token;
 await context.addInitScript(({api,token})=>{localStorage.setItem('graspmemoedu:identity:'+api,token);
  localStorage.setItem('graspmemoedu:identity-role:'+api,'account');},{api,token});
 await context.route(api+'/**',async route=>{if(route.request().method()==='POST'){
  report.learning_mutations.push(new URL(route.request().url()).pathname);await route.abort();
 }else await route.continue();});
 for(const name of names){
  const plan=JSON.parse(await fs.readFile(path.join(root,name,'course-install-plan.json')));
  for(const kind of ['courses','banks'])for(const width of [375,1280]){
   const id=plan.course_id+(kind==='banks'?'-practice':'');
   const page=await context.newPage();page.setDefaultTimeout(90000);page.on('pageerror',e=>report.errors.push(e.message));
   await page.setViewportSize({width,height:1000});
   await page.goto(base+'?v='+assetVersion+'#/subjects/'+plan.subject_id+'/'+kind+'/'+id,{waitUntil:'domcontentloaded',timeout:90000});
   await page.waitForFunction(()=>document.querySelector('.incompleteTasks [data-task-id]'));
   const actual=await page.locator('.incompleteTasks').evaluate(root=>({
    tasks:[...root.querySelectorAll('[data-task-id]')].map(n=>n.dataset.taskId),
    flattened:[...root.querySelectorAll('[data-unit-id][data-task-id]')].map(n=>n.dataset.unitId),
    nested:[...root.querySelectorAll('details[data-unit-id]')].map(n=>n.dataset.unitId),
    terminal_singleton_wrappers:[...root.querySelectorAll('details[data-unit-id]')]
      .filter(n=>!n.querySelector('details[data-unit-id]')&&n.querySelectorAll('[data-task-id]').length===1)
      .map(n=>n.dataset.unitId),
    overflow:document.documentElement.scrollWidth>innerWidth,
   }));
   assert.equal(actual.overflow,false);assert.ok(actual.tasks.length>0);
   if(kind==='courses'){
    const parents=new Set(plan.units.map(u=>u.parent_id));
    const single=plan.units.filter(u=>!parents.has(u.id)&&u.topic_ids.length===1
      &&actual.tasks.some(id=>id===u.topic_ids[0]||id.endsWith(':'+u.topic_ids[0])));
    assert.ok(single.length>0,'No original terminal singletons were actually checked');
    for(const u of single){assert.ok(actual.flattened.includes(u.id));assert.ok(!actual.nested.includes(u.id));}
    actual.terminal_singletons_checked=single.length;
   }else assert.deepEqual(actual.terminal_singleton_wrappers,[],'A terminal bank group still wraps one content card');
   await page.locator('.incompleteTasks details').evaluateAll(nodes=>nodes.forEach(n=>n.open=true));
   const screenshot=path.join(output,name+'-'+kind+'-'+width+'.png');await page.screenshot({path:screenshot});
   report.samples.push({book:name,kind,width,...actual,screenshot});await page.close();
   console.log('Verified published '+kind+' outline: '+name+' '+width+'px');
  }
 }
 assert.deepEqual(report.errors,[]);assert.deepEqual(report.learning_mutations,[]);report.passed=true;
}finally{
 if(token)await context.request.post(api+'/api/access/logout',{headers:{Origin:new URL(base).origin,'X-Learning-Identity':token},data:{}});
 await fs.writeFile(path.join(output,'outline-verification.json'),JSON.stringify(report,null,2));await browser.close();
}
console.log(JSON.stringify({passed:report.passed,samples:report.samples.length}));
