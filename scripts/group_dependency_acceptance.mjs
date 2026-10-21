/** Shared list filtering, graph links and group completion against an isolated SPA. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, output, accountPath] = process.argv.slice(2);
const completedOnly = process.argv.includes('--completed-only');
assert.equal(new URL(base).hostname, '127.0.0.1');
assert(!['8765', '18768', ''].includes(new URL(base).port));
await fs.mkdir(output, {recursive: true});
const accounts = JSON.parse(await fs.readFile(accountPath, 'utf8'));
const session = (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
let token;
async function api(route, body) {
  const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'Content-Type': 'application/json',
      ...(token ? {'X-Learning-Identity': token} : {})}, ...(body ? {body: JSON.stringify(body)} : {})});
  const data = await response.json();
  assert.equal(response.status, 200, route + ': ' + JSON.stringify(data)); return data;
}
token = (await api('access/login', {invitation_code: accounts[0].invitation_code})).identity_token;
const subjects = (await api('subjects')).subjects.map(s => s.id);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {subjects, checks: [], errors: [], synthetic_content: true, formal_data_opened: false};
const pass = message => { report.checks.push(message); console.log('PASS ' + message); };
let page;
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 900}, locale: 'zh-CN'});
  await context.addInitScript(({token, base}) => {
    if (location.origin !== base) return;
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
    localStorage.setItem('graspmemoedu:language', 'zh-CN');
  }, {token, base});
  page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  const filter = () => page.locator('.hideLockedInput');
  async function checkExpansion(subject, area) {
    const toggle = page.locator('.taskTreeExpansion');
    const all = () => page.locator('.incompleteTasks .courseUnitGroup');
    assert(await all().count() > 0);
    if (await toggle.textContent() === '一键展开') await toggle.click();
    await page.waitForFunction(() => [...document.querySelectorAll('.incompleteTasks .courseUnitGroup')].every(g => g.open));
    assert.equal(await toggle.textContent(), '一键收起');
    await toggle.click();
    await page.waitForFunction(() => [...document.querySelectorAll('.incompleteTasks .courseUnitGroup')].every(g => !g.open));
    assert.equal(await toggle.textContent(), '一键展开');
    await toggle.click();
    await page.waitForFunction(() => [...document.querySelectorAll('.incompleteTasks .courseUnitGroup')].every(g => g.open));
    pass(subject + ': ' + area + ' expands and collapses every hierarchy level');
  }
  async function go(route, selector) {
    await page.evaluate(route => { location.hash = route; }, route);
    await page.locator(selector).first().waitFor({state: 'visible'});
    await page.waitForFunction(() => !document.querySelector('#portalContent:not([hidden]) > .portalLoading'));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  await page.goto(base, {waitUntil: 'networkidle'});
  for (const subject of subjects) {
    await api('catalog/select?subject_id=' + subject, {course_id: 'course-' + subject});
    const prefix = '#/subjects/' + subject;
    await go(prefix + '/learn', '.hideLockedInput');
    await filter().uncheck();
    await checkExpansion(subject, 'learning');
    const atomic = page.locator('[data-task-id="lesson:atomic-' + subject + '"]');
    assert(await atomic.isVisible(), 'Locked Topic visible before filtering');
    await filter().check();
    assert(!await atomic.isVisible(), 'Locked Topic hidden');
    assert(await page.locator('[data-task-id="lesson:lesson-' + subject + '"]').isVisible());
    pass(subject + ': learning filter preserves available Topics');
    await go(prefix + '/banks/bank-' + subject, '.hideLockedInput');
    assert(await filter().isChecked(), 'Preference shared with banks');
    assert(!await page.locator('[data-task-id="group-a"]').isVisible());
    assert(!await page.locator('[data-unit-id="chapter"]').isVisible(), 'Empty locked chapter hidden');
    await filter().uncheck();
    await checkExpansion(subject, 'bank');
    assert(await page.locator('[data-task-id="group-a"]').isVisible());
    await page.locator('[data-task-id="group-a"] .taskToggle').click();
    assert(await page.locator('[data-task-id="group-a"] .prerequisiteBlocked').isDisabled());
    assert.equal(await page.locator('[data-task-id="group-a"] .prerequisiteRow a').getAttribute('href'), prefix + '/topic/lesson-' + subject);
    await filter().check();
    await page.reload({waitUntil: 'networkidle'});
    assert(await filter().isChecked());
    assert(!await page.locator('[data-task-id="group-a"]').isVisible());
    pass(subject + ': bank filter, prerequisite link and refresh persistence');
    await go(prefix + '/banks/bank-' + subject + '/group-ready?question=last', completedOnly ? '.trainingFinish' : '#trainingAnswer');
    if (!completedOnly) {
      assert.equal(await page.locator('.trainingFinish').count(), 0, 'No premature completion');
      await page.locator('#trainingAnswer').fill('2');
      await page.locator('#trainingSubmit').click();
    }
    await page.locator('.trainingFinish').waitFor();
    assert.equal(await page.locator('.trainingFinish').textContent(), '完成学习');
    if (subject === 'cs') await page.screenshot({path: path.join(output, 'cs-group-complete.png'), fullPage: true});
    await page.locator('.trainingFinish').click();
    await page.waitForFunction(hash => location.hash === hash, prefix + '/banks/bank-' + subject + '?groupId=group-ready');
    await page.locator('[data-task-id="group-ready"] .taskToggle').waitFor();
    await page.waitForFunction(() => document.activeElement?.closest('[data-task-id]')?.dataset.taskId === 'group-ready');
    const position = await page.locator('[data-task-id="group-ready"]').boundingBox();
    assert(position.y >= 0 && position.y < 800, 'Completed group is in viewport');
    assert(await page.evaluate(() => scrollY > 500), 'Returned to actual group position');
    assert.equal(await page.locator('.incompleteTasks [data-task-id="group-ready"]').count(), 0);
    assert.equal(await page.locator('.incompleteTasks [data-unit-id="tail"]').count(), 0, 'Empty completed ancestors are pruned');
    const finished = page.locator('.completedTasks [data-task-id="group-ready"]');
    assert(await finished.isVisible());
    assert.equal(await finished.locator('.taskIcon').getAttribute('aria-label'), '已结束');
    assert.equal(await finished.locator('.taskCompletedAt').count(), 1);
    if (subject === 'cs') await page.screenshot({path: path.join(output, 'cs-completed-history.png'), fullPage: false});
    pass(subject + ': completion leaves pending hierarchy and returns to the shared history row');
    await page.evaluate(() => scrollTo(0, 120));
    await page.locator('.mainNavigation [data-navigation="guide"]').click();
    await page.locator('.guideBody').waitFor();
    await page.locator('.mainNavigation [data-navigation="banks"]').click();
    await page.locator('.hideLockedInput').waitFor();
    await page.waitForFunction(() => Math.abs(scrollY - 120) < 3);
    pass(subject + ': completion anchor does not override subsequent tab position');
    await go(prefix + '/banks/bank-' + subject + '/group-ready?question=last&card=1', '#trainingStepTitle');
    assert.equal(await page.locator('.trainingFinish').count(), 0, 'Single-card review is not group completion');
  }
  await go('#/subjects/cs/banks/bank-cs', '.hideLockedInput');
  await filter().uncheck();
  await page.locator('.coursePercent').click();
  await page.locator('.courseGraph [data-topic-id="group:bank-cs:group-a"]').waitFor();
  await page.locator('.courseGraph [data-topic-id="group:bank-cs:group-a"]').click();
  assert((await page.locator('.cgDetails').textContent()).includes('Prerequisite Topic'));
  await page.locator('.cgDetails button').filter({hasText: 'Prerequisite Topic'}).click();
  await page.locator('.cgDetails button').last().click();
  await page.waitForFunction(() => location.hash === '#/subjects/cs/topic/lesson-cs');
  pass('Mixed graph follows Topic links through the shared viewer');
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({width, height: 900});
    await go('#/subjects/cs/banks/bank-cs?width=' + width, '.hideLockedInput');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'No horizontal overflow at ' + width);
    const label = await page.locator('.hideLockedControl').boundingBox();
    assert(label.width < width && label.x >= 0);
    const expansion = await page.locator('.taskTreeExpansion').boundingBox();
    assert(expansion.y >= label.y + label.height, 'Expand control appears below the checkbox');
    await page.screenshot({path: path.join(output, 'bank-' + width + '.png'), fullPage: false});
    pass('Filter layout at width ' + width);
  }
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = error.stack;
  if (page) await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
  throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
