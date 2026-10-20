/** Isolated Chrome acceptance: shared trees, real countdown and real semantic judge. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, fixture, mode] = process.argv.slice(2);
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
page.on('pageerror', error => report.errors.push(error.message));
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
const pass = text => { report.checks.push(text); console.log('PASS ' + text); };
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
  await page.locator('#identitySubmitButton').click(); await page.locator('#identityDialog').waitFor({state: 'hidden'});
}
async function shot(name) {
  const file = path.join(output, name + '.png'); await page.screenshot({path: file, fullPage: true}); report.screenshots.push(file);
}
async function expandTrees() {
  await page.locator('[data-review-kind]').first().waitFor();
  for (let i = 0; i < 50 && await page.locator('details.courseUnitGroup:not([open]) > summary').count(); i++) {
    await page.locator('details.courseUnitGroup:not([open]) > summary').first().click();
  }
}
async function choosePaper() {
  const group = page.locator('summary.courseUnitHeading').filter({hasText: /^隔离限时卷$/}).locator('..');
  const card = group.locator('.portalTask').first();
  await card.locator('.taskToggle').click(); await card.getByRole('button', {name: '开始复习', exact: true}).click();
  await page.locator('#paperstepCard').waitFor();
}
async function current() { return api('review-papers/state?paper_id=' + encodeURIComponent(new URL(page.url()).hash.split('/').at(-1))); }
async function waitUntil(check, seconds, label) {
  const end = Date.now() + seconds * 1000; let nextLog = 0;
  while (Date.now() < end) {
    if (await check()) return;
    if (Date.now() > nextLog) { console.log('WAIT ' + label); nextLog = Date.now() + 10000; }
    await page.waitForTimeout(500);
  }
  throw new Error('Timed out: ' + label + '\n' + await page.locator('body').innerText());
}
try {
  await go('/subjects/math/reviews'); await login('standard'); await go('/subjects/math/reviews');
  const inbox = await api('review-papers/inbox?subject_id=math');
  assert.equal(inbox.items.filter(i => i.kind === 'paper').length, 2);
  assert.equal(inbox.trees.length, 2);
  await page.getByRole('heading', {name: '原子卡片', exact: true}).waitFor();
  await expandTrees();
  assert.equal(await page.getByRole('heading', {name: '原子卡片', exact: true}).count(), 1);
  assert.equal(await page.locator('.taskTitle').filter({hasText: /^卷一$/}).count(), 1);
  assert.equal(await page.locator('.taskTitle').filter({hasText: /^卷二$/}).count(), 1);
  await shot('shared-review-tree-desktop'); pass('Multiple bank roots retain chapters and groups; one paper-type root contains 卷一 and 卷二');
  await page.locator('#catalogToggle').click();
  await page.locator('#catalogPanel [data-course-id="tree-course"]').waitFor();
  assert.equal(await page.locator('#catalogPanel [data-course-id="tree-course"]').count(), 1);
  assert.equal(await page.locator('#catalogPanel [data-bank-id]').count(), 2);
  await page.locator('#catalogPanel [data-bank-id="tree-bank-b"]').click();
  await page.waitForURL(/#\/subjects\/math\/banks\/tree-bank-b$/);
  pass('The same catalog picker selects either a course or a question bank');
  if (mode !== 'directory') {
  await go('/settings'); await page.locator('#profileDisplayName').waitFor(); assert.equal(await page.locator('#reviewTimeoutAction').count(), 0);
  await go('/subjects/math/reviews'); await choosePaper();
  let state = await current(); const paperId = state.id;
  await page.locator('#paperstepCard input[value="a"]').check();
  await page.waitForResponse(r => r.url().includes('/review-papers/draft') && r.request().method() === 'POST');
  await page.reload({waitUntil: 'networkidle'}); await page.locator('#paperstepCard').waitFor();
  const beforeRefresh = state.timing.remaining_seconds;
  assert.ok((await current()).timing.remaining_seconds <= beforeRefresh + 1);
  assert.equal(await page.locator('#paperstepCard input[value="a"]').isChecked(), true);
  await page.locator('#papertopicHomeLink').click();
  await page.locator('[data-review-kind]').first().waitFor();
  await waitUntil(async () => (await api('review-papers/state?paper_id=' + paperId)).timing.paused, 5, 'exit pause persisted');
  const paused = await api('review-papers/state?paper_id=' + paperId);
  await page.waitForTimeout(20000);
  const absent = await api('review-papers/state?paper_id=' + paperId);
  assert.equal(absent.timing.remaining_seconds, paused.timing.remaining_seconds);
  await go('/subjects/math/reviews/' + paperId); await page.locator('#paperstepCard').waitFor();
  assert.ok((await current()).timing.remaining_seconds <= paused.timing.remaining_seconds);
  assert.ok((await current()).timing.remaining_seconds >= paused.timing.remaining_seconds - 4);
  pass('Exiting and returning preserves remaining allowance and excludes absence; refresh does not reset the paper');
  await shot('countdown-restored');
  await waitUntil(() => page.getByText('本次复习已完成', {exact: true}).isVisible(), 85, 'browser countdown to automatic submission');
  state = await current(); assert.equal(state.items.filter(i => i.result.correct).length, 1);
  assert.equal(state.items.length, 8); assert.equal(state.timing.timed_out, true);
  await shot('automatic-submission'); pass('A real one-minute timer survives refresh and automatically grades seven empty answers wrong');
  await go('/subjects/math/reviews'); await expandTrees();
  const semantic = page.locator('.taskTitle').filter({hasText: /^真实语义判题$/}).locator('..').locator('..').locator('..');
  await semantic.locator('.taskToggle').click(); await semantic.getByRole('button', {name: '开始复习', exact: true}).click();
  await page.locator('#paperAnswer').fill('一加一等于二，也可以写成 2。');
  await page.locator('#paperSubmit').click();
  await waitUntil(() => page.getByText('本次复习已完成', {exact: true}).isVisible(), 180, 'real semantic judge');
  assert.equal((await current()).items[0].result.correct, true); pass('Untimed single-card review still uses the real semantic judge');
  await login('advanced'); await go('/settings');
  await page.locator('#reviewTimeoutAction').selectOption('mark');
  await page.locator('.profileForm button[type="submit"]').click(); await page.getByText('设置已保存。', {exact: true}).waitFor();
  assert.equal((await api('review-papers/preferences')).timeout_action, 'mark');
  await go('/subjects/math/reviews'); await page.setViewportSize({width: 390, height: 844}); await expandTrees();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
  await shot('shared-review-tree-mobile'); await choosePaper();
  if (await page.locator('#paperhistoryScrim').isVisible()) await page.locator('#paperhistoryButton').click();
  await waitUntil(() => page.getByText('已超时，可以继续作答。', {exact: true}).isVisible(), 85, 'advanced mark-only countdown');
  state = await current(); assert.equal(state.phase, 'answer'); assert.equal(state.can_edit, true);
  await page.locator('#paperstepCard input[value="a"]').check();
  assert.equal(await page.locator('#paperSubmit').isEnabled(), true);
  await shot('mark-only-mobile'); pass('Advanced mark-only mode shows overdue status and keeps the mobile answer controls active');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
  }
  if (identities.admin) {
    await page.setViewportSize({width: 1365, height: 950}); await login('admin');
    await go('/subjects/math/reviews?materials=1'); await expandTrees();
    assert.equal(await page.locator('[data-review-kind="paper"]').count(), 0);
    const previews = await api('review-papers/inbox?subject_id=math&materials=1');
    assert.equal(previews.due_count, 18); assert.equal(previews.trees.length, 2);
    await shot('administrator-shared-tree');
    const card = page.locator('[data-review-kind="training"] .portalTask').first();
    await card.locator('.taskToggle').click();
    await card.locator('.taskStartRow button').click();
    await page.waitForURL(/#\/subjects\/math\/banks\/tree-bank-a\/section\?question=choice-0$/);
    await page.locator('#trainingstepCard').waitFor();
    pass('Administrator all-card preview shares the tree and opens the original question-bank card');
  }
  assert.deepEqual(report.errors, []);
} catch (error) {
  report.failure = String(error.stack || error); await shot('failure'); throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
