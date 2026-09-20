/** Multi-subject platform, real Chrome/HTTP, isolated synthetic fixture only.
 * python scripts/platform_fixture.py --output D:/Artifacts/MathLearningWeb/platform/fixture-UNIQUE
 * node scripts/platform_acceptance.mjs http://127.0.0.1:8779 D:/Artifacts/MathLearningWeb/platform/browser-UNIQUE
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
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
let token;const actions=[],courseSelections=[];
page.on('pageerror',error=>report.errors.push(error.message));
page.on('request',request=>{
 token=request.headers()['x-learning-identity']||token;
 const url=new URL(request.url());
 if(request.method()==='POST'&&url.pathname==='/api/catalog/select')courseSelections.push(url.search);
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
 async function api(route,status=200){
   const response=await fetch(base+'/api/'+route,{headers:{Origin:base,'X-Learning-Session':transport.session_token,'X-Learning-Identity':token}});
   const data=await response.json();assert.equal(response.status,status,`${route}: ${JSON.stringify(data)}`);return data;
 }
 const subjects=await api('subjects');assert.deepEqual((Array.isArray(subjects)?subjects:subjects.subjects).map(s=>s.id).sort(),[...ids].sort());
 const identity=await api('access');assert.equal(identity.role,'guest');
 await noOverflow('Desktop root overflow');await page.screenshot({path:path.join(out,'home-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});await noOverflow('Mobile root overflow');
 await page.screenshot({path:path.join(out,'home-mobile-390.png'),fullPage:true});await page.setViewportSize({width:1365,height:960});
 pass('Platform homepage has GraspMemoEdu and exactly six subjects; desktop/mobile have no overflow');

 await page.locator('a.subjectCard[data-subject-id="math"]').click();
 await page.locator('.portalLogo').waitFor();
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

 const blocked=[];
 for(const id of ids.slice(1)){
   await navigate('/');await page.locator(`a.subjectCard[data-subject-id="${id}"]`).click();
   await page.getByText('该功能需注册账号才能使用',{exact:false}).first().waitFor();
   await api(`catalog?subject_id=${id}`,403);
   for(const screen of ['learn','courses','guide']){
     await navigate(`/subjects/${id}/${screen}`);
     await page.getByText('该功能需注册账号才能使用',{exact:false}).first().waitFor();
     const text=await page.locator('#portalContent').innerText();
     assert.doesNotMatch(text,/平台隔离数学|再探小学算术|MATH_GUIDE_SENTINEL|EXAMPLE_SENTINEL/);
     assert.equal(await page.locator('.courseChoice,#dashboardTasks .portalTask').count(),0);
     assert.ok(text.trim(),'Protected subject shows an account-required state');
   }
   assert.equal((await api('access')).learner_id,identity.learner_id);
   assert.equal(await page.locator('#guestDemoCheckbox').isChecked(),true);
   blocked.push(id);
 }
 await page.setViewportSize({width:390,height:844});await noOverflow('Empty subject mobile overflow');
 await page.screenshot({path:path.join(out,'empty-subject-mobile.png'),fullPage:true});await page.setViewportSize({width:1365,height:960});
 report.guest_blocked_subjects=blocked;pass('Guests receive account-required UI and API 403 for other five subjects; guest identity/demo preference persist');

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
 assert.equal(await page.locator('body').evaluate(body=>body.classList.contains('platformTheme')),false);
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
   const initialAccount=JSON.parse(await fs.readFile(accountFile,'utf8'));
   assert.equal(initialAccount.account.purpose,'test');
   const dataDir=path.resolve(path.dirname(accountFile),'data');
   assert.ok(dataDir.toLowerCase().startsWith(path.resolve('D:/Artifacts/MathLearningWeb/platform').toLowerCase()+path.sep));
   const account=JSON.parse(execFileSync('D:/AppData/MathLearningWeb/venv/Scripts/python.exe',['-c',
     'import json,sys; from pathlib import Path; from math_learning.access import AccessStore; from math_learning.catalog import Catalog; from math_learning.config import Settings; p=Path(sys.argv[1]); c=Catalog.load(Settings.load(p)); a=AccessStore(p,list(c.topics.values())); print(json.dumps(a.create_account("Isolated browser test",purpose="test")))',dataDir],{encoding:'utf8',windowsHide:true}));
   await navigate('/');await page.locator('#userMenuButton').click();await page.locator('#identityButton').click();
   await page.locator('#invitationCode').fill(account.invitation_code);
   await page.locator('#identitySubmitButton').click();await page.locator('#identityDialog').waitFor({state:'hidden'});
   const loggedIn=await api('access');assert.equal(loggedIn.role,'account');assert.equal(loggedIn.purpose,'test');
   assert.notEqual(loggedIn.learner_id,identity.learner_id);
   for(const id of ids){
     await navigate(`/subjects/${id}/courses`);await page.locator('#portalContent:not([hidden])').waitFor();
     assert.equal((await api('access')).learner_id,loggedIn.learner_id);
     const courseId=id==='math'?'arithmetic-revisited':`test-${id}-course`;
     const topicId=id==='math'?'digits-to-letters':`test-${id}-topic`;
     const course=page.locator(`.courseChoice[data-course-id="${courseId}"]`);
     await course.waitFor();
     const selectionBefore=await api(`catalog?subject_id=${id}`),selectionCount=courseSelections.length;
     const accentBefore=await page.locator('[data-navigation="courses"]').evaluate(n=>getComputedStyle(n).color);
     if(id==='math'){
       assert.equal(accentBefore,'rgb(5, 119, 199)');
       const background=await page.evaluate(()=>({body:getComputedStyle(document.body).backgroundColor,html:getComputedStyle(document.documentElement).backgroundColor}));
       assert.ok(background.body==='rgb(255, 255, 255)'||(background.body==='rgba(0, 0, 0, 0)'&&background.html==='rgb(255, 255, 255)'),'Math retains original white canvas');
       assert.match(await page.locator('.portalLogo').innerText(),/数学学习/);
       assert.equal(await page.locator('.mainNavigation a:visible').count(),3);
     }
     await course.locator('.courseProgressLink').click();await page.locator('.progressTopic').waitFor();
     assert.equal(await page.locator('.progressTopic').count(),1);
     await page.reload({waitUntil:'networkidle'});await page.locator('.progressTopic').waitFor();
     assert.equal(await page.locator('.progressTopic').getAttribute('data-progress-id'),topicId);
     await page.goBack();await course.waitFor();await page.goForward();await page.locator('.progressTopic').waitFor();
     await page.goBack();await course.waitFor();
     await course.locator('.courseGraphButton').click();await page.locator('.graphDialog[open] .graphViewport svg').waitFor();
     assert.equal(await page.locator('.graphDialog[open] svg g').count(),1);
     await page.screenshot({path:path.join(out,`course-map-${id}.png`),fullPage:true});
     await page.keyboard.press('Escape');await page.locator('.graphDialog').waitFor({state:'hidden'});
     assert.equal(courseSelections.length,selectionCount,'Course progress/map must not select course');
     assert.equal((await api(`catalog?subject_id=${id}`)).selected_course_id,selectionBefore.selected_course_id);
     assert.equal(await page.locator('[data-navigation="courses"]').evaluate(n=>getComputedStyle(n).color),accentBefore);
     await course.locator('.courseLearn').click();await page.locator('#dashboardTasks').waitFor();
     const task=page.locator('#dashboardTasks .portalTask').first();await task.locator('.taskToggle').click();
     await task.locator('.portalStart').first().click();await page.locator('#courseShell:not([hidden])').waitFor();
     assert.match(page.url(),new RegExp(`/subjects/${id}/topic/${topicId}`));
     assert.equal(await page.locator('body').evaluate(body=>body.classList.contains('platformTheme')),false);
     const stateBefore=await api(`state?subject_id=${id}&topic_id=${topicId}`);
     const active=stateBefore.steps.find(s=>s.id===stateBefore.active_step_id);assert.equal(active.kind,'example');
     await page.getByRole('button',{name:'未掌握，先学习',exact:true}).click();
     const next=()=>page.locator('#stepCard button').filter({hasText:/^下一页$/});
     for(let step=0;step<3;step++){await next().click();await page.waitForLoadState('networkidle');}
     await page.locator('#answerInput').waitFor();await page.locator('#answerInput').fill('42');
     await page.locator('#submitButton').click();
     await page.getByText('回答正确',{exact:true}).waitFor();
     const stateAfter=await api(`state?subject_id=${id}&topic_id=${topicId}`);
     assert.equal(stateAfter.steps.find(s=>s.id===stateAfter.active_step_id).kind,'practice');
     await page.screenshot({path:path.join(out,`lesson-${id}.png`),fullPage:true});
     await page.locator('.platformHomeLink').click();await page.locator('a.subjectCard').first().waitFor();
   }
   await page.locator('#userMenuButton').click();await page.locator('#logoutButton').click();
   await page.waitForFunction(()=>!document.querySelector('#guestDemoOption')?.hidden);
   assert.equal((await api('access')).learner_id,identity.learner_id);
   assert.equal(await page.locator('#guestDemoCheckbox').isChecked(),true);
   report.account_fixture=true;pass('Six course progress/maps preserve selection, survive refresh/back/forward, close with Escape and preserve subject styling');
   pass('Test account opens six course dashboards and completes shared Example/Introduction/Practice flow; logout restores guest/demo');
 }
 assert.deepEqual(report.errors,[]);report.status='passed';
}catch(error){
 report.status='failed';report.failure=String(error);
 await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
 await fs.writeFile(path.join(out,'failure.txt'),await page.locator('body').innerText().catch(()=>''));
 throw error;
}finally{await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();}
