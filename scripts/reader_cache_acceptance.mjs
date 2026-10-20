// Isolated real API + real browser. Delay the network after eager loading and
// verify local navigation, then exercise actual writes and authorization.
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
const api = async (route, body) => {
  const response = await fetch(base + '/api/' + route, {headers, ...(body === undefined ? {} : {method: 'POST', body: JSON.stringify(body)})});
  assert(response.ok, `${route}: ${response.status}`);
  return response.json();
};
const login = await api('access/login', {invitation_code: account.invitation_code});
headers['X-Learning-Identity'] = login.identity_token;
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {checks: {}, errors: [], timings: {}};
let page;
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 960}});
  await context.addInitScript(({base, token}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
    localStorage.setItem('graspmemoedu:language', 'zh-CN');
  }, {base, token: login.identity_token});
  page = await context.newPage();
  const requests = [], sizes = [];
  page.on('pageerror', error => report.errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname.startsWith('/api/')) requests.push({path: url.pathname, method: request.method()});
  });
  page.on('response', async response => {
    if (new URL(response.url()).pathname !== '/api/state') return;
    try {
      const body = await response.body(), parsed = JSON.parse(body);
      sizes.push({bytes: body.length, unchanged: parsed.$gme_state === 1 && !Object.hasOwn(parsed, 'value')});
    } catch {}
  });
  await page.goto(base + '/#/subjects/math/topic/lesson-math');
  await page.locator('#courseShell:not([hidden])').waitFor();
  const choice = page.getByRole('button', {name: '未掌握，先学习', exact: true});
  if (await choice.isVisible()) await choice.click();
  await page.locator('#stepCard .stepNavigation .primaryButton').click();
  await page.waitForFunction(() => document.querySelectorAll('#historyPanel .historyItem').length >= 2);
  let delay = true;
  await page.route('**/api/**', async route => {
    if (delay) await new Promise(resolve => setTimeout(resolve, 1200));
    try { await route.continue(); } catch {}
  });
  const before = requests.length;
  report.timings.topic = await page.evaluate(async () => {
    const times = [];
    for (let i = 0; i < 30; i++) {
      const buttons = [...document.querySelectorAll('#historyPanel .historyItem')];
      const target = buttons[i % 2];
      const start = performance.now();
      target.click();
      await new Promise(requestAnimationFrame);
      times.push(performance.now() - start);
    }
    return times;
  });
  assert.equal(requests.slice(before).filter(r => r.path === '/api/select').length, 0);
  report.checks.topic_navigation_without_server_selection = true;
  const selectedLabel = await page.locator('#historyPanel .historyItem[aria-current="step"]').innerText();
  await page.waitForTimeout(11500);
  assert.equal(await page.locator('#historyPanel .historyItem[aria-current="step"]').innerText(), selectedLabel);
  report.checks.background_validation_keeps_local_selection = true;
  delay = false;
  await page.evaluate(() => {location.hash = '#/subjects/math/banks/bank-math/group-a';});
  await page.locator('#trainingAnswer').waitFor();
  const initial = await api('training/group?bank_id=bank-math&group_id=group-a');
  assert(initial.questions.every(q => q.latest_attempt_id === null));
  report.checks.open_group_does_not_create_learning_records = true;
  delay = true;
  const bankStart = requests.length;
  report.timings.training = await page.evaluate(async () => {
    const ids = [...document.querySelectorAll('.trainingQuestionLink')].map(b => b.dataset.questionId);
    const times = [];
    for (let i = 0; i < 30; i++) {
      const start = performance.now();
      document.querySelector(`.trainingQuestionLink[data-question-id="${ids[i % ids.length]}"]`).click();
      await new Promise(requestAnimationFrame);
      times.push(performance.now() - start);
    }
    return times;
  });
  assert.equal(requests.slice(bankStart).filter(r => ['/api/training/open', '/api/training/state', '/api/training/history'].includes(r.path)).length, 0);
  report.checks.all_question_navigation_uses_preloaded_memory = true;
  await page.waitForTimeout(21000);
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  report.timings.training_after_dwell = await page.evaluate(async () => {
    const start = performance.now();
    document.querySelector('.trainingQuestionLink[data-question-id="exact"]').click();
    await new Promise(requestAnimationFrame);
    return performance.now() - start;
  });
  assert.equal(await page.locator('#trainingAnswer').isEnabled(), true);
  delay = false;
  await page.locator('#trainingAnswer').fill('2');
  await page.locator('#trainingSubmit').click();
  await page.locator('.trainingVerdict').waitFor({timeout: 30000});
  assert.match(await page.locator('#trainingprogressCaption').innerText(), /1\s*\/\s*5/);
  const completed = await api('training/group?bank_id=bank-math&group_id=group-a&reader=1');
  assert.equal(completed.questions.filter(q => q.latest_attempt_id).length, 1);
  assert.equal(completed.reader_states.exact.phase, 'done');
  report.checks.first_answer_opens_only_selected_attempt_and_is_judged = true;
  await page.locator('.trainingQuestionLink[data-question-id="essay"]').click();
  await page.locator('#trainingAnswer').fill('unsent browser draft');
  await page.locator('.trainingQuestionLink[data-question-id="exact"]').click();
  await page.locator('.trainingQuestionLink[data-question-id="essay"]').click();
  assert.equal(await page.locator('#trainingAnswer').inputValue(), 'unsent browser draft');
  report.checks.unsent_draft_survives_local_switching = true;
  const compact = sizes.filter(r => r.unchanged);
  assert(compact.length);
  assert(compact.every(r => r.bytes < 1000));
  report.checks.unchanged_topic_checks_under_one_kilobyte = true;
  assert(!await page.evaluate(() => performance.getEntriesByType('resource').some(r => r.name.includes('/vendor/chat/chat.js'))));
  report.checks.chat_bundle_absent_from_learning_startup = true;
  for (const [name, times] of Object.entries(report.timings)) {
    const values = (Array.isArray(times) ? times : [times]).slice().sort((a,b) => a-b);
    const p95 = values[Math.ceil(values.length * .95) - 1];
    report.timings[name] = {samples: values.length, p95_ms: p95, max_ms: values.at(-1)};
    assert(p95 < 100, `${name} P95 ${p95}ms`);
  }
  await page.screenshot({path: path.join(output, 'training.png'), fullPage: false});
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = error.stack;
  if (page) {
    report.visible = (await page.locator('body').innerText()).slice(-4000);
    await page.screenshot({path: path.join(output, 'failure.png')}).catch(() => {});
  }
  throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
  await api('access/logout', {});
  console.log(JSON.stringify({status: report.status, checks: report.checks, timings: report.timings}));
}
