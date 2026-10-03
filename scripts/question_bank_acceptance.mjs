/** Real Chrome/HTTP against the isolated fixture; no mocked grading. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, out] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
await fs.mkdir(out, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context = await browser.newContext({viewport: {width: 1365, height: 950}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], synthetic_course: true, formal_data_opened: false};
let token, session, submits = 0;
page.on('pageerror', error => report.errors.push(error.message));
page.on('request', request => {
  token = request.headers()['x-learning-identity'] || token;
  if (request.method() === 'POST' && request.url().includes('/api/submit?')) submits++;
});
const pass = text => { report.checks.push(text); console.log('PASS ' + text); };
const go = route => page.goto(base + '#' + route, {waitUntil: 'networkidle'});
const choose = () => page.locator('#stepCard').getByRole('button', {name: '已掌握，试着作答', exact: true}).click();
const next = () => page.locator('#stepCard .continueRow .primaryButton').click();
async function api(route, body, qa = false) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + (qa ? '/__qa/' : '/api/') + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'X-Learning-Identity': token, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const value = await response.json(); assert.equal(response.status, 200, JSON.stringify(value)); return value;
}
try {
  await go('/subjects/math/topic/synthetic-course');
  await choose();
  assert.equal(await page.locator('#stepCard .choiceOption').count(), 6);
  assert.equal(await page.locator('#stepCard .choiceOption svg').count(), 6);
  assert.equal(await page.locator('#submitButton').isDisabled(), true);
  const order = await page.locator('#stepCard .choiceOption input').evaluateAll(elements => elements.map(e => e.value));
  await page.locator('#stepCard input[value=ash]').check();
  await page.keyboard.press('ArrowDown');
  assert.notEqual(await page.locator('#stepCard input:checked').inputValue(), 'ash');
  await page.locator('#stepCard input[value=owl]').check();
  await page.reload({waitUntil: 'networkidle'});
  await page.locator('#stepCard input:checked').waitFor();
  assert.deepEqual(await page.locator('#stepCard .choiceOption input').evaluateAll(elements => elements.map(e => e.value)), order);
  assert.equal(await page.locator('#stepCard input:checked').inputValue(), 'owl');
  await page.screenshot({path: path.join(out, 'choice-desktop.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  if (await page.locator('#historyButton').getAttribute('aria-expanded') === 'true') await page.locator('#historyButton').click();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await page.screenshot({path: path.join(out, 'choice-mobile.png'), fullPage: true});
  await page.setViewportSize({width: 1365, height: 950});
  pass('Six math options; keyboard selection; stable order and restored draft; mobile layout');
  await page.locator('#submitButton').click();
  await page.locator('#stepCard .feedback.correct').waitFor();
  assert.equal(submits, 1);
  assert.equal(await page.locator('#stepCard .choiceOption input:not(:disabled)').count(), 0);
  const state = await api('state?topic_id=synthetic-course');
  assert.ok(!JSON.stringify(state).includes('correct_id'));
  assert.equal(state.modules[0].mastery, 4);
  pass('Deterministic choice grade, disabled feedback, mastery and no answer-key leak');
  await next(); await choose();
  await page.locator('#stepCard .inlineBlank').fill('10/2');
  await page.reload({waitUntil: 'networkidle'});
  await page.locator('#stepCard .inlineBlank').waitFor();
  assert.equal(await page.locator('#stepCard .inlineBlank').inputValue(), '10/2');
  await page.screenshot({path: path.join(out, 'fill-blank.png'), fullPage: true});
  await page.locator('#submitButton').click();
  await page.locator('#stepCard .feedback.correct').waitFor({timeout: 180000});
  pass('Blank accepts equivalent expression through the real judge and restores its draft');
  await next();
  await api('seed-review', {module_id: 'module-1'}, true);
  await go('/subjects/math/review/synthetic-course/module-1');
  await page.locator('#reviewPractice .choiceOption').first().waitFor();
  const reviewOrder = await page.locator('#reviewPractice .choiceOption input').evaluateAll(elements => elements.map(e => e.value));
  await page.locator('#reviewPractice input[value=ash]').check();
  await page.reload({waitUntil: 'networkidle'});
  await page.locator('#reviewPractice input:checked').waitFor();
  assert.deepEqual(await page.locator('#reviewPractice .choiceOption input').evaluateAll(elements => elements.map(e => e.value)), reviewOrder);
  assert.equal(await page.locator('#reviewPractice input:checked').inputValue(), 'ash');
  await page.locator('#reviewSubmitButton').click();
  await page.locator('#reviewPractice .feedback.incorrect').waitFor();
  await page.screenshot({path: path.join(out, 'choice-review.png'), fullPage: true});
  pass('Review uses the same choice interaction, preserves draft/order and counts a wrong answer');
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = String(error.stack || error);
  await page.screenshot({path: path.join(out, 'failure.png'), fullPage: true});
  throw error;
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
