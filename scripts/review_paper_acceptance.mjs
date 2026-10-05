/** Real Chrome and real semantic judging against a disposable loopback fixture. */
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
const context = await browser.newContext({viewport: {width: 1365, height: 950}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], screenshots: [], real_judge: true, formal_data_opened: false};
let token, session;
page.on('pageerror', error => { report.errors.push(error.message); console.log('PAGE ERROR ' + error.message); });
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
const pass = label => { report.checks.push(label); console.log('PASS ' + label); };
const go = route => page.goto(base + '#' + route, {waitUntil: 'networkidle'});
async function api(route, body) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'Content-Type': 'application/json', 'X-Learning-Session': session, 'X-Learning-Identity': token},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result)); return result;
}
async function login(name) {
  await page.locator('#userMenuButton').hover(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities[name]);
  const response = page.waitForResponse(r => r.url().includes('/api/access/login') && r.request().method() === 'POST');
  await page.locator('#identitySubmitButton').click(); assert.equal((await response).status(), 200);
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
}
async function shot(name) {
  const file = path.join(output, name + '.png'); await page.screenshot({path: file, fullPage: true}); report.screenshots.push(file);
}
function paperId() { return decodeURIComponent(new URL(page.url()).hash.split('/').at(-1)); }
async function current() { return api('review-papers/state?paper_id=' + encodeURIComponent(paperId())); }
async function choose(reference) {
  const inbox = await api('review-papers/inbox?subject_id=math');
  const item = inbox.items.find(item => item.paper_type_id === reference || item.title === reference);
  assert(item, 'Missing review candidate ' + reference);
  for (let i = 0; i < 50 && await page.locator('details.courseUnitGroup:not([open]) > summary').count(); i++) {
    await page.locator('details.courseUnitGroup:not([open]) > summary').first().click();
  }
  const card = page.locator(`[data-task-id="${item.id}"]`);
  await card.locator('.taskToggle').click();
  await card.getByRole('button', {name: '开始复习', exact: true}).click();
  await page.locator('#paperstepCard').waitFor();
}
async function pick(index) {
  await page.locator(`[data-question-index="${index}"]`).click();
  await page.locator('#paperstepCard .stepTitle').filter({hasText: new RegExp(`^习题 ${index + 1}$`)}).waitFor();
}
async function answer(item) {
  if (item.interaction?.type === 'choice') {
    await page.locator('#paperstepCard .choiceOption input[value="a"]').check();
    if (item.interaction.multiple) await page.locator('#paperstepCard .choiceOption input[value="b"]').check();
  } else if (item.interaction?.type === 'fill_blank') await page.locator('#paperstepCard .inlineBlank').fill('2');
  else await page.locator('#paperAnswer').fill('一加一等于二，也可以写成 2。');
}
async function completed() {
  // Poll actual UI progress; fail immediately on a rendering/service error.
  const deadline = Date.now() + 240000;
  while (Date.now() < deadline) {
    if (await page.getByText('本次复习已完成', {exact: true}).isVisible()) return;
    assert.equal(await page.locator('#paperstepCard .paperError').count(), 0,
      await page.locator('#paperstepCard').innerText());
    console.log('WAIT paper status: ' + (await current()).job_status);
    await page.waitForTimeout(2000);
  }
  throw new Error('Paper submission did not complete');
}
try {
  await go('/subjects/math/reviews'); await login('standard'); await go('/subjects/math/reviews');
  const inbox = await api('review-papers/inbox?subject_id=math');
  assert.equal(inbox.due_count, 15); assert.equal(inbox.items.filter(x => x.kind === 'paper').length, 5);
  await page.locator('[data-review-kind="paper"] .portalTask').first().waitFor();
  await shot('review-inbox-desktop'); pass('Five configured paper types coexist with overlapping points and single-card fallback');
  await go('/settings'); assert.equal(await page.locator('#reviewPaperMode').count(), 0);
  await go('/subjects/math/reviews');
  await choose('math-choice-8-multiple-3-fill-3');
  let state = await current(); assert.equal(state.mode, 'paper'); assert.equal(state.items.length, 14);
  assert.equal(await page.locator('#paperstepCard .requestHelpButton').count(), 1);
  await answer(state.items[0]);
  await page.waitForResponse(r => r.url().includes('/review-papers/draft') && r.request().method() === 'POST');
  await page.reload({waitUntil: 'networkidle'}); await page.locator('#paperstepCard').waitFor();
  assert.equal(await page.locator('#paperstepCard input[value="a"]').isChecked(), true);
  state = await current(); assert.equal(state.items[0].explanation_html, null);
  pass('Standard learner uses whole-paper mode; saved draft restores without exposing answers');
  for (let i = 0; i < state.items.length; i++) { if (i) await pick(i); await answer(state.items[i]); }
  await page.locator('#paperSubmit').click();
  await completed();
  state = await current(); assert(state.items.every(item => item.result.correct));
  await shot('paper-completed-desktop'); pass('Fourteen-item mixed paper submits once and updates all corresponding points independently');
  await go('/subjects/math/reviews'); assert.equal((await api('review-papers/inbox?subject_id=math')).due_count, 1);
  await choose('真实语义判题'); state = await current(); await answer(state.items[0]); await page.locator('#paperSubmit').click();
  await completed();
  assert.equal((await current()).items[0].result.correct, true); pass('Remaining single card uses the real semantic judge and preserves whole-paper submission');
  await login('advanced'); await go('/settings'); await page.locator('#reviewPaperMode').selectOption('question');
  await page.locator('.profileForm button[type="submit"]').click(); await page.getByText('设置已保存。', {exact: true}).waitFor();
  assert.equal((await api('review-papers/preferences')).mode, 'question');
  await go('/subjects/math/reviews'); await choose('math-multiple-3'); state = await current(); assert.equal(state.mode, 'question');
  await answer(state.items[0]); await page.locator('#paperSubmit').click();
  await page.locator('#paperstepCard .correctResult').waitFor(); state = await current();
  assert.equal(state.items[0].completed, true); assert.equal(state.items[1].completed, false);
  pass('Advanced setting enables per-question feedback and only the submitted point is rescheduled');
  await shot('question-mode-desktop');
  page.once('dialog', dialog => dialog.accept()); await page.getByRole('button', {name: '结束本次复习', exact: true}).click();
  await page.getByText('本次复习已结束，请返回选择新的复习。', {exact: true}).waitFor();
  await go('/settings'); await page.locator('#reviewPaperMode').selectOption('paper'); await page.locator('.profileForm button[type="submit"]').click();
  await page.getByText('设置已保存。', {exact: true}).waitFor(); await go('/subjects/math/reviews'); await choose('math-fill-3');
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('#paperhistoryScrim').waitFor({state: 'visible'});
  await page.keyboard.press('Escape');
  await page.locator('#paperhistoryScrim').waitFor({state: 'hidden'});
  await answer((await current()).items[0]);
  await shot('paper-mobile');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2);
  assert.equal(overflow, false); pass('Mobile reader, inline blanks and drawer fit the viewport');
  assert.deepEqual(report.errors, []);
} catch (error) {
  report.failure = String(error.stack || error); await shot('failure'); throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
