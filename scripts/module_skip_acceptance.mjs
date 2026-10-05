/** Own-site UI acceptance in disposable, headless Google Chrome contexts. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, fixture] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const identities = JSON.parse(await fs.readFile(path.join(fixture, 'identities.json'), 'utf8'));
const output = path.join(fixture, 'browser'); await fs.mkdir(output, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const report = {checks: [], errors: [], formal_data_opened: false};
const pass = text => {report.checks.push(text); console.log('PASS ' + text);};
const session = (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
async function user(name, width = 1365) {
  const context = await browser.newContext({viewport: {width, height: 950}, locale: 'zh-CN'});
  const page = await context.newPage(); page.setDefaultTimeout(25000); let token;
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', req => {token = req.headers()['x-learning-identity'] || token;});
  const go = route => page.goto(base + '#' + route, {waitUntil: 'networkidle'});
  await go('/subjects/math/topic/skip-parent');
  await page.locator('#userMenuButton').hover(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities[name]); await page.locator('#identitySubmitButton').click();
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
  async function api(route, body) {
    const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET', headers: {
      Origin: base, 'Content-Type': 'application/json', 'X-Learning-Session': session, 'X-Learning-Identity': token},
      ...(body ? {body: JSON.stringify(body)} : {})});
    const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result)); return result;
  }
  await api('catalog/select?subject_id=math', {course_id: 'skip-course'});
  await page.reload({waitUntil: 'networkidle'});
  const shot = name => page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
  return {context, page, api, go, shot, headers: () => ({Origin: base, 'Content-Type': 'application/json', 'X-Learning-Identity': token})};
}
try {
  const student = await user('student'); const {page, api, go, shot} = student;
  await page.getByRole('button', {name: '跳过此模块', exact: true}).waitFor();
  assert.equal(await page.locator('#pauseHint').textContent(), '未掌握前置知识，感到困难，可选择后延该模块');
  assert.match(await page.locator('#pauseButton path').getAttribute('d'), /a7 7/);
  await shot('desktop-skip');
  for (const module of ['module-2', 'module-3']) {
    await page.locator('#pauseButton').click();
    await page.waitForFunction(id => [...document.querySelectorAll('#historyPanel .moduleName')]
      .some(item => item.textContent === '算术模块 ' + id.slice(-1)), module);
  }
  const action = state => ({request_id: crypto.randomUUID(), expected_revision: state.revision, attempt_id: state.attempt_id, step_id: state.active_step_id});
  let state = await api('state?topic_id=skip-parent');
  state = await api('choose?topic_id=skip-parent', {...action(state), choice: 'mastered'});
  const result = await api('submit?topic_id=skip-parent', {...action(state), answer: '5'});
  for (let i = 0; i < 40; i++) {
    const submitted = await api('submissions/' + result.submission_id + '?topic_id=skip-parent');
    if (submitted.status === 'judged') {state = submitted.state; break;}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  await api('continue?topic_id=skip-parent', action(state));
  await go('/subjects/math/learn');
  await page.locator('.taskCompleted .taskIcon[aria-label="已学过，含待补学模块"]').waitFor();
  assert.equal(await page.locator('.taskLocked').count(), 1);
  await shot('desktop-half-check'); pass('Skip advances current module, preserves gray hint, and shows half check with downstream locked');
  const admin = await user('admin');
  await admin.go('/admin/pauses'); await admin.page.getByRole('heading', {name: '暂停管理', exact: true}).waitFor();
  assert.equal(await admin.page.getByRole('checkbox', {name: '允许推送下游 Topic'}).count(), 2);
  await admin.page.getByRole('checkbox', {name: '允许推送下游 Topic'}).first().check();
  await admin.page.waitForFunction(() => [...document.querySelectorAll('input[type=checkbox]')].filter(i => i.checked).length === 2);
  await admin.shot('admin-downstream');
  const inbox = await admin.api('admin/pauses');
  for (const item of inbox.items) await admin.api('admin/pauses/unlock', {
    learner_id: item.learner_id, topic_id: item.topic_id, module_id: item.module_id, pause_event_id: item.pause_event_id});
  const dashboard = await api('dashboard?course_id=skip-course');
  assert.equal(dashboard.tasks.filter(t => t.topic_id === 'skip-parent' && t.type === 'Lesson').length, 1);
  assert.equal(dashboard.tasks.find(t => t.topic_id === 'skip-child').dependency_ready, true);
  pass('Admin checkbox allows downstream; two unlocked modules form one catch-up Lesson');
  const reviewResponse = await fetch(base + '/__qa/review', {method: 'POST', headers: student.headers(), body: '{}'});
  assert.equal(reviewResponse.status, 200); const review = await reviewResponse.json();
  await go('/subjects/math/review/skip-parent/module-3?session=' + review.session_id);
  await page.locator('#reviewPractice').waitFor();
  assert.equal(await page.locator('#portalContent .pauseControl, #reviewPauseButton').count(), 0);
  await shot('review-no-pause');
  await go('/subjects/biology/topic/skip-atomic');
  await page.locator('.atomicLayout').waitFor();
  assert.equal(await page.locator('.atomicPause').count(), 0);
  await shot('atomic-no-pause'); pass('Review and atomic pages have no manual pause controls');
  const ordinary = await user('ordinary', 390);
  await ordinary.page.locator('#historyDrawerToggle').click().catch(() => {});
  if (await ordinary.page.locator('#historyScrim').isVisible()) await ordinary.page.locator('#historyScrim').click();
  await ordinary.page.locator('#pauseButton').click();
  await ordinary.page.getByText('该功能仅对学员开放', {exact: true}).waitFor();
  await ordinary.shot('mobile-student-only'); pass('Nonstudent can click skip and receives exact student-only message on mobile');
  const pauseResponse = await fetch(base + '/__qa/pause', {method: 'POST', headers: ordinary.headers(), body: '{}'});
  assert.equal(pauseResponse.status, 200);
  await ordinary.go('/subjects/math/learn');
  await ordinary.page.locator('.taskLocked[data-task-id="lesson:skip-parent"]').waitFor();
  await ordinary.page.locator('.taskLocked[data-task-id="lesson:skip-parent"] .taskToggle').click();
  assert.equal(await ordinary.page.getByRole('button', {name: '已暂停', exact: true}).isDisabled(), true);
  await ordinary.page.getByText(/6 小时后可再次学习/).waitFor();
  await ordinary.shot('mobile-cooldown'); pass('Automatic pause renders a locked disabled Lesson with six-hour recovery time');
  assert.deepEqual(report.errors, []);
} finally {
  for (const [i, context] of browser.contexts().entries()) for (const page of context.pages())
    await page.screenshot({path: path.join(output, 'final-' + i + '.png'), fullPage: true}).catch(() => {});
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close();
}
