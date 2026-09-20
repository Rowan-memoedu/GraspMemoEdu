/** Imported physics Topic in real Chrome, isolated localhost fixture only.
 * node scripts/author_import_acceptance.mjs BASE COURSE_JSON PRIVATE_ACCOUNTS_JSON NEW_OUTPUT
 * Requires a pristine reserved test account and the real configured judge.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const [base, courseFile, credentialsFile, output] = process.argv.slice(2);
assert.ok([base, courseFile, credentialsFile, output].every(Boolean), 'Four arguments required');
const origin = new URL(base).origin;
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.ok(!['', '8765', '8767'].includes(new URL(base).port), 'Formal services are forbidden');
const course = JSON.parse(await fs.readFile(courseFile, 'utf8'));
const credentials = JSON.parse(await fs.readFile(credentialsFile, 'utf8'));
const account = credentials.accounts.find(item => item.purpose === 'test');
assert.ok(account?.invitation_code, 'Explicit test account required');
assert.equal(course.id, 'physics-uniform-speed-import-test');
assert.equal(course.modules.length, 1);
assert.equal(course.modules[0].practices.length, 7);
const questions = [course.modules[0].example, ...course.modules[0].practices];
const answers = new Map(questions.map(question => {
  const numbers = question.question.match(/(\d+) 秒内经过 (\d+) 米/);
  assert.ok(numbers, 'Independent speed calculation needs time and distance');
  const speed = Number(numbers[2]) / Number(numbers[1]);
  assert.ok(Number.isInteger(speed));
  assert.equal(Number(question.reference_answer), speed, 'Imported answer must agree with independent computation');
  return [question.id, String(speed)];
}));
await fs.mkdir(output);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const page = await browser.newPage({viewport:{width:1365,height:960}, locale:'zh-CN'});
page.setDefaultTimeout(25000);
const report = {base, topic_id:course.id, course_version:course.version, isolated:true, real_browser:'Chrome', mocked:false, checks:[], errors:[], judgements:[]};
let token;
page.on('request', request => {if (request.url().startsWith(origin)) token = request.headers()['x-learning-identity'] || token;});
page.on('pageerror', error => report.errors.push(error.message));
const pass = label => {report.checks.push(label); console.log('PASS ' + label);};
const active = state => state.steps.find(step => step.id === state.active_step_id);
async function state() {
  const session = await (await fetch(origin + '/api/session', {headers:{Origin:origin}})).json();
  const response = await fetch(`${origin}/api/state?topic_id=${course.id}&subject_id=physics`, {headers:{Origin:origin, 'X-Learning-Session':session.session_token, 'X-Learning-Identity':token}});
  assert.equal(response.status, 200);
  return response.json();
}
async function next() {
  const [response] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === '/api/continue' && response.request().method() === 'POST'),
    page.locator('#stepCard .continueRow button.primaryButton:not(#submitButton)').click(),
  ]);
  assert.equal(response.status(), 200);
  const result = await response.json();
  await page.waitForFunction(id => document.querySelector('#stepCard')?.dataset.stepId === id, result.current_step_id);
  return result;
}
async function answer(value, expected) {
  const question = active(await state());
  await page.locator('#answerInput').fill(value);
  const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/submit' && response.request().method() === 'POST');
  await page.locator('#submitButton').click();
  assert.equal((await responsePromise).status(), 200);
  await page.waitForFunction(() => document.querySelector('.feedbackTitle') && !document.querySelector('.waiting'), null, {timeout:240000});
  const result = await state();
  assert.equal(active(result).id, question.id);
  assert.equal(active(result).feedback?.correct, expected);
  report.judgements.push({question_id:question.question_id, correct:expected});
  return result;
}
try {
  await page.goto(base + '#/', {waitUntil:'networkidle'});
  await page.locator('#userMenuButton').click(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(account.invitation_code);
  const loginResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/access/login' && response.request().method() === 'POST');
  await page.locator('#identitySubmitButton').click();
  const login = await loginResponse; assert.equal(login.status(), 200);
  assert.equal((await login.json()).access.purpose, 'test');
  await page.locator('#identityDialog').waitFor({state:'hidden'});
  await page.goto(base + '#/subjects/physics/courses', {waitUntil:'networkidle'});
  await page.locator('.courseChoice').first().waitFor();
  await page.screenshot({path:path.join(output, 'physics-courses.png'), fullPage:true});
  await page.goto(base + '#/subjects/physics/topic/' + course.id, {waitUntil:'networkidle'});
  await page.getByRole('button', {name:'已掌握，试着作答', exact:true}).waitFor();
  assert.match(await page.locator('#lessonTitle').innerText(), /匀速直线运动/);
  assert.match(await page.locator('#stepCard').innerText(), /5 秒内经过 20 米/);
  assert.equal((await state()).steps.length, 1, 'Pristine isolated progress required');
  await page.screenshot({path:path.join(output, 'example.png'), fullPage:true});
  await page.getByRole('button', {name:'已掌握，试着作答', exact:true}).click();
  await answer('0', false);
  await page.screenshot({path:path.join(output, 'example-feedback.png'), fullPage:true});
  await next();
  assert.equal(active(await state()).kind, 'introduction');
  assert.ok(await page.locator('mjx-container svg').count() > 0, 'Introduction formulas render');
  await page.screenshot({path:path.join(output, 'introduction-math.png'), fullPage:true});
  await next();
  assert.match(await page.locator('#stepCard').innerText(), /速度为 4 m\/s/);
  assert.ok(await page.locator('mjx-container svg').count() > 0, 'Explanation formulas render');
  await next();
  for (let i = 0; i < 2; i++) {
    const current = active(await state()); assert.equal(current.kind, 'practice');
    assert.ok(answers.has(current.question_id));
    await answer(answers.get(current.question_id), true);
    await page.screenshot({path:path.join(output, `practice-${i + 1}-feedback.png`), fullPage:true});
    await next();
  }
  const completed = await state();
  assert.equal(completed.modules[0].mastery, 4);
  pass('Imported Example, Introduction, formulas, Explanation and two Practice questions render and real judgement completes Lesson');
  await page.setViewportSize({width:390, height:844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await page.screenshot({path:path.join(output, 'mobile-completion.png'), fullPage:true});
  pass('390px mobile layout has no horizontal overflow');
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = String(error);
  await page.screenshot({path:path.join(output, 'failure.png'), fullPage:true}).catch(() => {});
  throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
