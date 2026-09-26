/** Exercise the delivered page against the real, configured judge in isolated QA data. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';

const [base = 'http://127.0.0.1:8766', destination = 'D:/Artifacts/GraspMemoEdu/browser-live', courseFile = 'D:/AppData/GraspMemoEdu/courses/digits-to-letters.json'] = process.argv.slice(2);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const { chromium } = require('playwright');
const course = JSON.parse(await fs.readFile(courseFile, 'utf8'));
const report = {started_at:new Date().toISOString(), base, course_version:course.version, real_judge:true, events:[], errors:[], screenshots:[]};
await fs.mkdir(destination, {recursive:true});
const chrome = process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe');
const browser = await chromium.launch({executablePath:chrome, headless:true});
const page = await browser.newPage({viewport:{width:1100,height:1000}, deviceScaleFactor:1});
let identityToken = null;
page.on('request', request => {
  if (request.url().startsWith(base)) identityToken = request.headers()['x-learning-identity'] || identityToken;
});
page.on('pageerror', error => report.errors.push(String(error)));
page.setDefaultTimeout(20000);
async function state() {
  assert.ok(identityToken, 'Guest identity must be established by the page');
  return page.evaluate(async identityToken => {
    const {session_token} = await (await fetch('/api/session')).json();
    return (await fetch('/api/state', {headers:{'X-Learning-Session':session_token, 'X-Learning-Identity':identityToken}})).json();
  }, identityToken);
}
function active(s) {return s.steps.find(x=>x.id===s.active_step_id);}
async function shot(name) {
  const file = path.join(destination, name+'.png');
  await page.screenshot({path:file,fullPage:true});
  report.screenshots.push(file);
}
async function next() {
  const pending = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/continue' && r.request().method()==='POST');
  await page.getByRole('button', {name:'Continue，继续学习',exact:true}).click();
  const response = await pending;
  assert.equal(response.status(), 200);
  const s = await response.json();
  await page.waitForFunction(id=>document.querySelector('#stepCard')?.dataset.stepId===id, s.current_step_id).catch(async()=>{
    await page.waitForTimeout(300);
  });
  return s;
}
async function answer(value, expected, {reloadPending=false}={}) {
  const before = await state();
  const q = active(before);
  await page.locator('#answerInput').fill(value);
  const pending = page.waitForResponse(r=>new URL(r.url()).pathname==='/api/submit' && r.request().method()==='POST');
  await page.locator('#submitButton').click();
  const response = await pending;
  assert.equal(response.status(),200);
  const receipt = await response.json();
  assert.equal(receipt.status,'pending');
  if (reloadPending) await page.reload();
  await page.waitForFunction(()=>document.querySelector('.feedbackTitle') && !document.querySelector('.waiting'), {timeout:240000});
  const after = await state();
  const result = active(after);
  assert.equal(result.id,q.id,'Judging must not advance before Continue');
  assert.equal(result.feedback?.correct,expected);
  assert.equal(result.feedback?.submission_id,receipt.submission_id);
  assert.equal(after.pending_submission_id,null);
  report.events.push({question_id:q.question_id,submission_id:receipt.submission_id,expected_correct:expected,actual_correct:result.feedback.correct,
    module:after.modules.find(m=>m.id===q.module_id),reloadPending});
  console.log(`Real judgement: ${q.question_id}, correct=${result.feedback.correct}`);
  return after;
}
try {
  await page.goto(base+'#/courses',{waitUntil:'networkidle'});
  await page.locator('.courseChoice').filter({hasText:'再探小学算术'}).getByRole('button',{name:'Learn',exact:true}).click();
  await page.locator('.taskToggle').filter({hasText:course.title}).click();
  await page.getByRole('button',{name:'Start',exact:true}).click();
  await page.getByRole('button',{name:'已掌握，试着作答',exact:true}).waitFor();
  assert.equal(await page.locator('#answerInput').count(),0);
  assert.equal(await page.locator('.exampleExplanation').count(),0);
  const initial = await state();
  assert.equal(initial.steps.length,1,'QA data must be pristine');
  assert.equal(initial.access.role, 'guest');
  assert.equal(initial.access.features.includes('pause_topic'), false);
  await page.locator('#pauseButton').click();
  await page.getByText('该功能需注册账号才能使用', {exact:true}).first().waitFor();
  assert.equal((await state()).revision, initial.revision);
  assert.equal(await page.locator('mjx-container svg').count()>0,true);
  await shot('01-example');
  await page.getByRole('button',{name:'已掌握，试着作答',exact:true}).click();
  // A known incorrect result, independently different from the audited reference.
  const wrong = course.modules[0].example.reference_answer==='0'?'1':'0';
  await answer(wrong,false,{reloadPending:true});
  await shot('02-example-feedback');
  let s = await next();
  assert.equal(active(s).kind,'introduction');
  assert.equal(s.introduction_read,false);
  await page.reload();
  await page.getByRole('button',{name:'Continue，继续学习',exact:true}).waitFor();
  assert.equal(active(await state()).kind,'introduction');
  await shot('03-introduction');
  s = await next();
  assert.equal(active(s).kind,'example');
  assert.equal(active(s).phase,'reading');
  assert.equal(s.introduction_read,true);
  assert.equal(await page.getByRole('button',{name:'已掌握，试着作答',exact:true}).count(),0);
  await shot('04-explanation');
  await next();
  for (let i=0;i<2;i++) {
    const result = await answer(course.modules[0].practices[i].reference_answer,true);
    assert.equal(result.modules[0].practice_error_count,0);
    assert.equal(result.modules[0].practice_target_count,2);
    if (i===1) assert.equal(result.modules[0].mastery,4);
    await shot(`05-practice-${i+1}`);
    await next();
  }
  for (let i=1;i<course.modules.length;i++) {
    await page.getByRole('button',{name:'已掌握，试着作答',exact:true}).click();
    const result = await answer(course.modules[i].example.reference_answer,true);
    assert.equal(result.modules[i].mastery,4);
    assert.equal(result.modules[i].practice_answered_count,0);
    await next();
  }
  s = await state();
  assert.equal(s.status,'completed');
  assert.equal(s.steps.filter(x=>x.kind==='introduction').length,1);
  assert.equal(s.modules.every(m=>m.mastery===4),true);
  await page.getByRole('heading',{name:'本 Topic 已完成',exact:true}).waitFor();
  await shot('06-completed');
  await page.reload();
  await page.getByRole('heading',{name:'本 Topic 已完成',exact:true}).waitFor();
  assert.equal((await state()).status,'completed');
  await page.setViewportSize({width:390,height:844});
  const overflow = await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
  assert.equal(overflow,false,'Mobile page must not overflow horizontally');
  await shot('07-mobile-completed');
  await page.setViewportSize({width:1365,height:1000});
  await page.locator('[data-navigation="learn"]').click();
  await page.locator('.coursePercent').filter({hasText:'100%'}).waitFor();
  await page.locator('button.taskCompleted').filter({hasText:course.title}).click();
  await page.locator('.answerRecord').first().waitFor();
  assert.equal(await page.locator('.answerRecord').count(),report.events.length);
  assert.equal(await page.locator('.answerOutcome.incorrect').count(),1);
  assert.equal(await page.locator('.answerOutcome.correct').count(),report.events.length-1);
  assert.equal(await page.locator('.answerMetadata').filter({hasText:'Elapsed —'}).count(),0);
  assert.equal(await page.locator('.answerMetadata').filter({hasText:'Difficulty —'}).count(),report.events.length);
  const answerUrl=page.url();
  await shot('08-completed-answer-history');
  await page.reload({waitUntil:'networkidle'});
  await page.locator('.answerRecord').first().waitFor();
  assert.equal(page.url(),answerUrl);
  await page.locator('.taskBackButton').click();
  await page.locator('button.taskCompleted').waitFor();
  report.dashboard_history={progress:100,answers:report.events.length,incorrect:1,unknown_difficulty:true,elapsed_recorded:true,deep_link_refreshed:true};
  assert.deepEqual(report.errors,[]);
  report.status='passed';
  report.final_state={status:s.status,modules:s.modules,introduction_read:s.introduction_read};
  console.log('Full real-course browser journey passed.');
} catch(error) {
  report.status='failed';
  report.failure={message:String(error),stack:error.stack};
  await shot('failure').catch(()=>{});
  console.error(error);
  process.exitCode=1;
} finally {
  report.finished_at=new Date().toISOString();
  await fs.writeFile(path.join(destination,'report.json'),JSON.stringify(report,null,2));
  await browser.close();
}
