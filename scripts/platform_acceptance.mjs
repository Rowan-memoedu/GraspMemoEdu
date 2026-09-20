/** Multi-subject platform, real Chrome/HTTP, isolated synthetic fixture only.
 * python scripts/platform_fixture.py --output D:/Artifacts/MathLearningWeb/platform/fixture-UNIQUE
 * node scripts/platform_acceptance.mjs http://127.0.0.1:8779 D:/Artifacts/MathLearningWeb/platform/browser-UNIQUE
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base,out,accountFile]=process.argv.slice(2);
assert.equal(new URL(base).hostname,'127.0.0.1');
assert.ok(!['8765','8767',''].includes(new URL(base).port));
assert.ok(out);await fs.mkdir(out);
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE||path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context=await browser.newContext({viewport:{width:1365,height:960},locale:'zh-CN'});
const page=await context.newPage();page.setDefaultTimeout(20000);
const ids=['math','physics','english','chinese','biology','chemistry'];
const report={checks:[],errors:[],formal_data_opened:false,real_judge:false,http_mocked:false};
let token;const actions=[];
page.on('pageerror',error=>report.errors.push(error.message));
page.on('request',request=>{
 token=request.headers()['x-learning-identity']||token;
 const url=new URL(request.url());
 if(request.method()==='POST'&&/^\/api\/(choose|continue|select|pause|submit|guest\/reset|review\/)/.test(url.pathname))actions.push(url.pathname);
});
const pass=label=>{report.checks.push(label);console.log('PASS '+label);};
async function navigate(route){await page.goto(base+'#'+route,{waitUntil:'networkidle'});}
async function noOverflow(label){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,label);}
try{
 await navigate('/');await page.locator('a.subjectCard').first().waitFor();
 assert.deepEqual((await page.locator('a.subjectCard').evaluateAll(nodes=>nodes.map(n=>n.dataset.subjectId))).sort(),[...ids].sort());
 assert.match(await page.locator('body').innerText(),/GraspMemoEdu/);
 const transport=await(await fetch(base+'/api/session',{headers:{Origin:base}})).json();
 async function api(route){
   const response=await fetch(base+'/api/'+route,{headers:{Origin:base,'X-Learning-Session':transport.session_token,'X-Learning-Identity':token}});
   const data=await response.json();assert.equal(response.status,200,`${route}: ${JSON.stringify(data)}`);return data;
 }
 const subjects=await api('subjects');assert.deepEqual((Array.isArray(subjects)?subjects:subjects.subjects).map(s=>s.id).sort(),[...ids].sort());
 const identity=await api('access');assert.equal(identity.role,'guest');
 await noOverflow('Desktop root overflow');await page.screenshot({path:path.join(out,'home-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await noOverflow('Mobile root overflow');
 await page.screenshot({path:path.join(out,'home-mobile-390.png'),fullPage:true});await page.setViewportSize({width:1365,height:960});
 pass('Platform homepage has GraspMemoEdu and exactly six subjects; desktop/mobile have no overflow');

 await page.locator('a.subjectCard[data-subject-id="math"]').click();
 await page.locator('a#subjectContext').waitFor();
 assert.match(page.url(),/#\/subjects\/math\//);
 await page.locator('[data-navigation="courses"]').click();await page.locator('.courseChoice').first().waitFor();
 assert.match(await page.locator('.courseChoice').first().innerText(),/再探小学算术/);
 await page.locator('.courseLearn').first().click();await page.locator('#dashboardTasks').waitFor();
 await navigate('/subjects/math/topic/digits-to-letters');await page.locator('#courseShell:not([hidden])').waitFor();
 assert.match(await page.locator('#lessonTitle').innerText(),/平台隔离数学/);
 await page.locator('#guestDemoCheckbox').check();assert.equal(await page.locator('#guestDemoCheckbox').isChecked(),true);
 await page.screenshot({path:path.join(out,'math-lesson.png'),fullPage:true});
 await navigate('/subjects/math/guide');await page.getByText('MATH_GUIDE_SENTINEL',{exact:false}).waitFor();
 assert.match(await page.locator('#portalContent').innerText(),/MATH_GUIDE_SENTINEL/);
 pass('Mathematics course, Lesson and subject-specific Guide open through original learning flow');

 const empty=[];
 for(const id of ids.slice(1)){
   await page.locator('.portalLogo').click();await page.locator(`a.subjectCard[data-subject-id="${id}"]`).click();
   await page.locator('#portalContent:not([hidden])').waitFor();assert.match(page.url(),new RegExp(`#/subjects/${id}/`));
   await page.waitForFunction(id=>document.querySelector('#subjectContext')?.getAttribute('href')?.includes(`subjects/${id}`),id);
   assert.match(await page.locator('#subjectContext').getAttribute('href'),new RegExp(`subjects/${id}`));
   const catalog=await api(`catalog?subject_id=${id}`);assert.deepEqual(catalog.courses,[]);
   for(const screen of ['learn','courses','guide']){
     await page.locator(`[data-navigation="${screen}"]`).click();
     await page.waitForURL(new RegExp(`#/subjects/${id}/${screen}`));
     await page.waitForLoadState('networkidle');
     const text=await page.locator('#portalContent').innerText();
     assert.doesNotMatch(text,/平台隔离数学|再探小学算术|MATH_GUIDE_SENTINEL|EXAMPLE_SENTINEL/);
     assert.equal(await page.locator('.courseChoice,#dashboardTasks .portalTask').count(),0);
     assert.ok(text.trim(),'Subject empty state has visible content');
   }
   assert.equal((await api('access')).learner_id,identity.learner_id);
   assert.equal(await page.locator('#guestDemoCheckbox').isChecked(),true);
   empty.push(id);
 }
 await page.setViewportSize({width:390,height:844});await noOverflow('Empty subject mobile overflow');
 await page.screenshot({path:path.join(out,'empty-subject-mobile.png'),fullPage:true});await page.setViewportSize({width:1365,height:960});
 report.empty_subjects=empty;pass('Other five subjects have empty learning/course/guide views without mathematics leakage; guest identity/demo preference persist');

 for(const old of ['/learn?taskId=missing-test-task','/courses?unitId=unit-test','/guide?keep=1','/topic/digits-to-letters?keep=1']){
   await navigate(old);
   const expected='/subjects/math'+old;
   await page.waitForURL(url=>url.hash==='#'+expected);
 }
 await page.locator('#courseShell:not([hidden])').waitFor();
 assert.equal(await page.locator('#guestDemoCheckbox').isChecked(),true);
 pass('Legacy Lesson/learn/course/guide hashes redirect to math and preserve query strings');

 await navigate('/settings');await Promise.all([page.waitForEvent('load'),page.locator('#profileLanguage').selectOption('en')]);
 await page.locator('#profileLanguage').waitFor();
 await page.waitForFunction(()=>document.documentElement.lang==='en-US');
 await navigate('/');await page.locator('a.subjectCard').first().waitFor();
 assert.equal(await page.locator('html').getAttribute('lang'),'en-US');
 await page.locator('a.subjectCard[data-subject-id="physics"]').click();
 assert.equal(await page.locator('html').getAttribute('lang'),'en-US');
 await page.locator('#userMenuButton').click();await page.locator('#identityButton').click();
 await page.locator('#identityDialog[open]').waitFor();await page.locator('#closeIdentityButton').click();
 await navigate('/settings');await Promise.all([page.waitForEvent('load'),page.locator('#profileLanguage').selectOption('zh-CN')]);
 await page.locator('#profileLanguage').waitFor();
 await page.waitForFunction(()=>document.documentElement.lang==='zh-CN');
 assert.equal((await api('access')).learner_id,identity.learner_id);
 pass('Global language setting and account dialog remain available across subjects');

 const beforeActions=actions.length;
 for(const route of ['/subjects/not-a-subject/learn','/subjects/physics/topic/digits-to-letters','/subjects/physics/review/digits-to-letters/module-1']){
   await navigate(route);await page.locator('#portalContent:not([hidden])').waitFor();
   await page.waitForLoadState('networkidle');
   assert.equal(await page.locator('#courseShell:not([hidden])').count(),0);
   assert.equal(await page.locator('#reviewAnswerInput').count(),0);
   assert.doesNotMatch(await page.locator('#portalContent').innerText(),/EXAMPLE_SENTINEL|INTRODUCTION_SENTINEL/);
 }
 assert.equal(actions.length,beforeActions,'Invalid/cross-subject paths must not trigger learning mutations');
 report.learning_mutations_on_invalid_routes=0;pass('Invalid subjects and cross-subject Topic/Review paths cannot trigger learning actions');
 if(accountFile){
   const account=JSON.parse(await fs.readFile(accountFile,'utf8'));
   assert.equal(account.account.purpose,'test');
   await navigate('/');await page.locator('#userMenuButton').click();await page.locator('#identityButton').click();
   await page.locator('#invitationCode').fill(account.invitation_code);
   await page.locator('#identitySubmitButton').click();await page.locator('#identityDialog').waitFor({state:'hidden'});
   const loggedIn=await api('access');assert.equal(loggedIn.role,'account');assert.equal(loggedIn.purpose,'test');
   assert.notEqual(loggedIn.learner_id,identity.learner_id);
   for(const id of ids){
     await navigate(`/subjects/${id}/courses`);await page.locator('#portalContent:not([hidden])').waitFor();
     assert.equal((await api('access')).learner_id,loggedIn.learner_id);
   }
   await page.locator('#userMenuButton').click();await page.locator('#logoutButton').click();
   await page.waitForFunction(()=>!document.querySelector('#guestDemoOption')?.hidden);
   assert.equal((await api('access')).learner_id,identity.learner_id);
   assert.equal(await page.locator('#guestDemoCheckbox').isChecked(),true);
   report.account_fixture=true;pass('Synthetic test account retains identity across all six subjects; logout restores original guest and demo preference');
 }
 assert.deepEqual(report.errors,[]);report.status='passed';
}catch(error){
 report.status='failed';report.failure=String(error);
 await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
 await fs.writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText().catch(()=>''));
 throw error;
}finally{await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();}
