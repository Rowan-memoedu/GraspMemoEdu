/** Real Chrome acceptance with only the isolated loopback fixture and synthetic text. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, out] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1'); await fs.mkdir(out);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context = await browser.newContext({viewport: {width: 1365, height: 960}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], formal_data_opened: false, synthetic_content: true};
let token, session;
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
page.on('pageerror', error => report.errors.push(error.message));
const pass = message => { report.checks.push(message); console.log('PASS ' + message); };
const go = route => page.goto(base + '/#' + route, {waitUntil: 'networkidle'});
const main = () => page.locator('.atomicMain');
const api = async (route, body, qa = false) => {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + (qa ? '/__qa/' : '/api/') + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'X-Learning-Identity': token, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result)); return result;
};
const tick = seconds => api('advance', {seconds}, true);
const next = () => main().getByRole('button', {name: '继续', exact: true}).click();
const answer = async (value, correct = true) => {
  await page.locator('#atomicAnswer').fill(value);
  await page.locator('#atomicAnswer').press('Control+Enter');
  await main().locator('.feedback.' + (correct ? 'correct' : 'incorrect')).waitFor({timeout: 120000});
};
try {
  for (const subject of ['chinese', 'biology', 'chemistry', 'english']) {
    await go(`/subjects/${subject}/topic/atomic-${subject}`);
    await main().getByText('请确认已理解讲解内容，开始训练后不得随意返回查看', {exact: true}).waitFor();
    assert.equal(await main().getByRole('button', {name: '已理解，开始训练', exact: true}).count(), 1);
    assert.equal(await page.locator('[data-navigation=reviews]').isVisible(), true);
  }
  await main().getByRole('button', {name: '已理解，开始训练', exact: true}).click();
  await page.locator('.atomicChunkNote').waitFor();
  assert.equal(await page.locator('.atomicHistory').getByRole('button', {name: '指导', exact: true}).isDisabled(), true);
  const colors = await page.locator('.atomicAnnotation').evaluate(el => ({
    underline: getComputedStyle(el.querySelector('.atomicAnchor')).textDecorationColor,
    dash: getComputedStyle(el.querySelector('.atomicAnchor')).textDecorationStyle,
    arrow: getComputedStyle(el.querySelector('.atomicArrow')).color,
    border: getComputedStyle(el.querySelector('.atomicChunkNote')).borderTopColor,
    fill: getComputedStyle(el.querySelector('.atomicChunkNote')).backgroundColor}));
  assert.equal(colors.dash, 'dashed'); assert.equal(colors.underline, colors.arrow); assert.equal(colors.arrow, colors.border);
  await page.screenshot({path: path.join(out, 'chunk-desktop.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await page.screenshot({path: path.join(out, 'chunk-mobile.png'), fullPage: true});
  await page.setViewportSize({width: 1365, height: 960});
  pass('Four-subject routing, one Introduction action, locked history and themed dashed annotation on desktop/mobile');
  await main().getByRole('button', {name: '开始原子卡训练', exact: true}).click();
  await page.locator('#atomicAnswer').fill('错误标签'); await page.reload({waitUntil: 'networkidle'});
  assert.equal(await page.locator('#atomicAnswer').inputValue(), '错误标签');
  await page.route('**/api/submit?*', route => route.abort('failed'), {times: 1});
  await main().getByRole('button', {name: '提交答案', exact: true}).click();
  await main().locator('#atomicNotice:not([hidden])').waitFor();
  assert.equal(await page.locator('#atomicAnswer').inputValue(), '错误标签');
  await main().getByRole('button', {name: '提交答案', exact: true}).click();
  await main().locator('.feedback.incorrect').waitFor(); await next();
  assert.equal(await page.locator('.atomicHistory button:disabled .atomicUnmastered').count(), 1);
  const locked = await api('state?topic_id=atomic-english&subject_id=english');
  assert.equal(locked.introduction, null); assert.equal(locked.history[0].html, undefined);
  await main().getByRole('button', {name: '开始原子卡训练', exact: true}).click();
  await answer('圆形'); await next();
  await main().locator('.atomicWaiting').waitFor();
  await tick(179);
  assert.equal((await api('state?topic_id=atomic-english&subject_id=english')).phase, 'waiting');
  await tick(1); await page.locator('#atomicAnswer').waitFor();
  assert.equal(await page.locator('.atomicQuestion').getAttribute('data-card-id'), 'a-2');
  await answer('红色'); await next(); await main().locator('.atomicWaiting').waitFor();
  await tick(180); await page.locator('#atomicAnswer').waitFor(); await answer('蓝色'); await next();
  await main().locator('.atomicComplete').waitFor();
  const before = await api('audit', undefined, true);
  assert.equal(before.points.b.card_json, null); assert.equal(before.points.c.card_json, null);
  assert.notEqual(before.points.a.card_json, null);
  for (const point of Object.values(before.points)) assert.equal(Date.parse(point.due_at) - Date.parse(point.first_passed_at), 21600000);
  assert.equal(before.history.length, 4);
  const seen = before.history.filter(r => ['a', 'b'].some(p => r.point_ids.includes(p))).map(r => Date.parse(r.shown_at));
  for (let i = 1; i < seen.length; i++) assert.ok(seen[i] - seen[i - 1] >= 180000);
  assert.ok(await page.locator('.atomicMastered').count());
  await page.locator('.atomicHistory').getByRole('button', {name: '指导', exact: true}).click();
  assert.ok((await main().innerText()).includes('甲标签是红色'));
  pass('Draft reload and uncertain submit retry, locked unsuccessful cards, 180-second server spacing, semantic judge and six-hour first reviews');
  await go('/subjects/english/reviews');
  await page.getByText('暂无到期的原子卡。', {exact: true}).waitFor();
  await tick(21601); await page.reload({waitUntil: 'networkidle'});
  await page.getByText('3 个记忆点到期', {exact: true}).waitFor();
  await page.getByRole('link', {name: '开始复习', exact: true}).click();
  await page.locator('.atomicQuestion[data-card-id=ab-bundle]').waitFor();
  await page.locator('[data-blank-id=a]').fill('红色'); await page.locator('[data-blank-id=b]').fill('蓝色');
  await page.screenshot({path: path.join(out, 'bundled-review.png'), fullPage: true});
  await page.locator('[data-blank-id=b]').press('Control+Enter'); await main().locator('.feedback.correct').waitFor();
  const after = await api('audit', undefined, true);
  assert.deepEqual(after.history.at(-1).ratings, {a: 3, b: 3});
  assert.notEqual(JSON.parse(after.points.a.card_json).card_id, JSON.parse(after.points.b.card_json).card_id);
  assert.equal(after.points.c.card_json, null); assert.equal(after.integrity, 'ok');
  report.real_judge = after.real_judge;
  pass('Dedicated due inbox, shared input widgets, two-point bundled review and independent FSRS memory updates');
  assert.deepEqual(report.errors, []);
  report.status = 'PASS'; await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
} catch (error) {
  report.status = 'FAIL'; report.failure = error.message;
  await page.screenshot({path: path.join(out, 'failure.png'), fullPage: true});
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); throw error;
} finally { await browser.close(); }
