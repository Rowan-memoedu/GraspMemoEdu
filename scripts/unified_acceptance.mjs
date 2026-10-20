/** Isolated Chrome acceptance for composed Topics and unchanged legacy views. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const [base, out] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
await fs.mkdir(out);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context = await browser.newContext({viewport: {width: 1365, height: 960}, locale: 'zh-CN'});
const page = await context.newPage();
page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], synthetic_content: true, formal_data_opened: false};
let token, session;
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
page.on('pageerror', error => report.errors.push(error.message));
const pass = label => { report.checks.push(label); console.log('PASS ' + label); };
const go = topic => page.goto(`${base}/#/subjects/math/topic/${topic}`, {waitUntil: 'networkidle'});
const api = async (route, body, qa = false) => {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + (qa ? '/__qa/' : '/api/') + route, {
    method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'X-Learning-Identity': token, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json();
  assert.equal(response.status, 200, JSON.stringify(result));
  return result;
};
const state = () => api('state?topic_id=synthetic-course&subject_id=math');
const tick = seconds => api('advance', {seconds}, true);
const next = () => page.locator('#stepCard .stepNavigation .continueRow .primaryButton').click();
const atomNext = () => page.locator('.atomicMain').getByRole('button', {name: '继续', exact: true}).click();
const answerAtom = async value => {
  await page.locator('#atomicAnswer').fill(value);
  await page.locator('#atomicAnswer').press('Control+Enter');
  await page.locator('.atomicMain .feedback.correct').waitFor({timeout: 120000});
};
const screenshot = name => page.screenshot({path: path.join(out, name + '.png'), fullPage: true});
try {
  await go('synthetic-course');
  await page.locator('#stepTitle').filter({hasText: '第一段指导'}).waitFor();
  await screenshot('introduction-desktop');
  await next();
  await page.locator('.atomicChunkNote').waitFor();
  await screenshot('chunk-desktop');
  await page.locator('.atomicMain').getByRole('button', {name: '开始原子卡训练', exact: true}).click();
  await answerAtom('红色');
  await atomNext();
  await page.locator('.atomicWaiting').waitFor();
  await tick(180);
  await page.locator('#atomicAnswer').waitFor();
  await answerAtom('蓝色');
  await atomNext();
  await page.locator('#stepCard').getByRole('button', {name: '未掌握，先学习', exact: true}).waitFor();
  assert.equal((await page.locator('#historyPanel').textContent()).includes('测试原子卡'), true);
  pass('Introduction, annotated chunk, atomic learning, then Example in the same Topic');
  await page.locator('#stepCard').getByRole('button', {name: '未掌握，先学习', exact: true}).click();
  await next();
  const before = await state();
  await screenshot('example-desktop');
  await page.setViewportSize({width: 390, height: 844});
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await screenshot('example-mobile');
  await page.setViewportSize({width: 1365, height: 960});
  await tick(7 * 3600);
  const unchanged = await state();
  assert.equal(unchanged.active_step_id, before.active_step_id);
  await next();
  await page.locator('#atomicAnswer').waitFor();
  assert.equal((await state()).introduction, null);
  await screenshot('due-card-locked');
  await answerAtom('红色');
  await atomNext();
  await page.locator('#answerInput').waitFor();
  let current = await state();
  assert.equal(current.steps.find(s => s.active).question_id, 'module-1-practice-1');
  pass('Due card inserts only on Continue; guidance locks; spacing permits resuming the exact next Practice');
  await page.locator('#answerInput').fill('5');
  await page.reload({waitUntil: 'networkidle'});
  assert.equal(await page.locator('#answerInput').inputValue(), '5');
  for (let i = 0; i < 3; i++) {
    await page.locator('#answerInput').fill('5');
    await page.locator('#answerInput').press('Control+Enter');
    await page.locator('#stepCard .feedback.correct').waitFor();
    await next();
  }
  await page.locator('#stepTitle').filter({hasText: '第二段指导'}).waitFor();
  await screenshot('second-introduction');
  await next();
  await page.locator('.atomicMain').getByRole('button', {name: '开始原子卡训练', exact: true}).click();
  current = await state();
  assert.deepEqual(current.current.point_ids, ['c']);
  assert.equal(current.unified_history.length, 1);
  await screenshot('second-section-card');
  await answerAtom('圆形');
  await atomNext();
  await page.locator('#stepCard').getByRole('button', {name: '已掌握，试着作答', exact: true}).click();
  await page.locator('#answerInput').fill('5');
  await page.locator('#answerInput').press('Control+Enter');
  await page.locator('#stepCard .feedback.correct').waitFor();
  await next();
  await page.locator('#stepTitle').filter({hasText: '本主题已完成'}).waitFor();
  current = await state();
  assert.equal(current.status, 'completed');
  const audit = await api('audit', undefined, true);
  assert.equal(audit.integrity, 'ok');
  assert.equal(audit.state.atomic.points.b.mastered, true);
  assert.ok(audit.judge_calls.some(call => call.status === 'judged' && call.correct === true));
  await screenshot('completed');
  pass('Draft survives reload; prior-section due cards do not interrupt the next Introduction; real semantic judging passes');
  await go('legacy-paradigm');
  await page.locator('#stepCard').getByRole('button', {name: '已掌握，试着作答', exact: true}).waitFor();
  assert.equal(await page.locator('#topicAtomicProgress').count(), 0);
  await go('legacy-atomic');
  await page.locator('.atomicMain').getByRole('button', {name: '已理解，开始训练', exact: true}).waitFor();
  await go('legacy-introduction');
  await page.locator('#stepCard').getByText('先读 A', {exact: true}).waitFor();
  assert.equal(await page.locator('#topicAtomicProgress').count(), 0);
  pass('Legacy paradigm, atomic and reading-only Topics retain their existing entry screens and omit empty statistics');
  assert.deepEqual(report.errors, []);
} catch (error) {
  report.failure = error.stack;
  report.visible_buttons = await page.locator('button:visible').evaluateAll(buttons => buttons.map(button => ({
    text: button.textContent, label: button.getAttribute('aria-label'), disabled: button.disabled}))).catch(() => []);
  await screenshot('failure').catch(() => {});
  throw error;
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
