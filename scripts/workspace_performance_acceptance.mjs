// Real isolated API; delay graph transport to expose navigation dependencies.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, output, accountPath] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert(!['8765', '18768'].includes(new URL(base).port));
await fs.mkdir(output, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const account = JSON.parse(await fs.readFile(accountPath, 'utf8'));
const session = (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
const headers = {Origin: base, 'X-Learning-Session': session, 'Content-Type': 'application/json'};
const login = await (await fetch(base + '/api/access/login', {method: 'POST', headers,
  body: JSON.stringify({invitation_code: account.invitation_code})})).json();
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {checks: {}, pages: [], errors: []};
let page, releaseGraph;
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 900}});
  await context.addInitScript(({base, token}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
  }, {base, token: login.identity_token});
  const graphGate = new Promise(resolve => { releaseGraph = resolve; });
  let graphPending = false;
  await context.route('**/api/graphs?**', async route => {
    graphPending = true;
    await graphGate;
    await route.continue();
  });
  page = await context.newPage();
  const requests = [];
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) requests.push({path: url.pathname, method: request.method()});
  });
  const ready = selector => page.waitForFunction(selector => {
    const target = document.querySelector(selector);
    return target?.getClientRects().length && !target.closest('[hidden], [inert]')
      && !document.querySelector('#portalContent[aria-busy="true"]');
  }, selector, {timeout: 15000});
  async function measure(name, action, selector) {
    const start = performance.now(), before = requests.length;
    await action(); await ready(selector);
    report.pages.push({name, ms: Math.round(performance.now() - start), requests: requests.slice(before)});
  }
  const go = route => page.evaluate(hash => new Promise(resolve => {
    addEventListener('hashchange', resolve, {once: true}); location.hash = hash;
  }), '#/subjects/math/' + route);
  await measure('courses_while_graph_pending',
    () => page.goto(base + '/#/subjects/math/courses', {waitUntil: 'domcontentloaded'}), '.courseLearn');
  assert(graphPending);
  await measure('course_enter_while_graph_pending', () => page.locator('.courseLearn').first().click(), '.dashboardLayout');
  report.checks.slow_graph_does_not_block_course_choices_or_learning = true;
  const graphDone = page.waitForResponse(response => new URL(response.url()).pathname === '/api/graphs');
  releaseGraph(); await graphDone;
  await page.evaluate(() => new Promise(requestAnimationFrame));
  assert.equal(await page.locator('.graphLibrary').count(), 0);
  assert.equal(await page.locator('.dashboardLayout').count(), 1);
  report.checks.late_graph_cannot_replace_new_workspace = true;
  for (const [name, route, selector] of [
    ['banks_cold', 'banks', '.courseGrid'],
    ['bank_cold', 'banks/bank-math', '.dashboardLayout'],
    ['group_cold', 'banks/bank-math/group-a', '#trainingcourseShell:not([hidden])'],
  ]) await measure(name, () => go(route), selector);
  await measure('courses_return', () => go('courses'), '.courseLearn');
  await measure('course_select', () => page.locator('.courseLearn').first().click(), '.dashboardLayout');
  const beforeWarm = requests.length;
  for (const [name, route, selector] of [
    ['banks_after_course_selection', 'banks', '.courseGrid'],
    ['bank_after_course_selection', 'banks/bank-math', '.dashboardLayout'],
    ['group_after_course_selection', 'banks/bank-math/group-a', '#trainingcourseShell:not([hidden])'],
  ]) await measure(name, () => go(route), selector);
  assert.equal(requests.slice(beforeWarm).filter(row => row.path.startsWith('/api/training/')).length, 0);
  report.checks.course_selection_keeps_bank_directory_dashboard_and_reader_cached = true;
  await measure('lesson', () => go('topic/lesson-math'), '#courseShell:not([hidden])');
  const write = page.waitForResponse(response => new URL(response.url()).pathname === '/api/choose');
  await page.getByRole('button', {name: '未掌握，先学习', exact: true}).click();
  assert.equal((await write).status(), 200);
  const beforeWriteReturn = requests.length;
  await measure('bank_after_learning_write', () => go('banks/bank-math'), '.dashboardLayout');
  assert.equal(requests.slice(beforeWriteReturn).filter(row => row.path === '/api/training/bank').length, 1);
  report.checks.learning_write_still_revalidates_progress = true;
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.failure = error.message;
  if (page) {
    await page.screenshot({path: path.join(output, 'failure.png')}).catch(() => {});
    await fs.writeFile(path.join(output, 'failure.txt'), await page.locator('body').innerText()).catch(() => {});
  }
  throw error;
} finally {
  releaseGraph?.();
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify(report, null, 2));
