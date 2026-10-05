/** Disposable Chrome acceptance: reproduction controls, paused timers and subject Q&A. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, fixture] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const identities = JSON.parse(await fs.readFile(path.join(fixture, 'identities.json'), 'utf8'));
const output = path.join(fixture, 'fixes-browser'); await fs.mkdir(output, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context = await browser.newContext({viewport: {width: 1365, height: 950}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], screenshots: [], formal_data_opened: false};
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
  await page.locator('#invitationCode').fill(identities[name]); await page.locator('#identitySubmitButton').click();
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
}
async function shot(name) {
  const file = path.join(output, name + '.png'); await page.screenshot({path: file, fullPage: true}); report.screenshots.push(file);
}
async function expand() {
  for (let i = 0; i < 50 && await page.locator('details.courseUnitGroup:not([open]) > summary').count(); i++)
    await page.locator('details.courseUnitGroup:not([open]) > summary').first().click();
}
async function wait(check, seconds, label) {
  const end = Date.now() + seconds * 1000; let logAt = 0;
  while (Date.now() < end) {
    if (await check()) return;
    if (Date.now() > logAt) { console.log('WAIT ' + label); logAt = Date.now() + 10000; }
    await page.waitForTimeout(300);
  }
  throw new Error('Timed out: ' + label + '\n' + await page.locator('body').innerText());
}
const bank = q => '/subjects/math/banks/tree-bank-a/section?review=1&question=' + q;
try {
  await go('/subjects/math/reviews'); await login('standard'); await go('/subjects/math/reviews');
  await page.getByRole('heading', {name: '我的错题本', exact: true}).waitFor(); await expand();
  assert.ok(await page.locator('.taskHeading strong').count());
  assert.deepEqual([...new Set(await page.locator('.taskHeading strong').allTextContents())], ['复习任务']);
  pass('Review task headings are unified; 我的错题本 remains the section name');
  assert.equal(await page.locator('#qaNavLink').isVisible(), true);
  assert.equal(await page.locator('#qaNavLink').getAttribute('href'), '#/subjects/math/qa');
  await go('/'); assert.equal(await page.locator('#qaNavLink').isVisible(), false);
  await go('/subjects/english/learn'); assert.equal(await page.locator('#qaNavLink').isVisible(), true);
  assert.equal(await page.locator('#qaNavLink').getAttribute('href'), '#/subjects/english/qa');
  await go('/qa'); await page.waitForURL(/#\/$/); assert.equal(await page.locator('#qaNavLink').isVisible(), false);
  pass('Q&A navigation is absent on global routes and follows the selected subject');
  await go(bank('semantic-blank')); await page.locator('#trainingSubmit').waitFor();
  assert.equal(await page.locator('#trainingSubmit').innerText(), 'Submit');
  await login('advanced'); await go('/subjects/math/reviews');
  const inbox = await api('review-papers/inbox?subject_id=math');
  const candidate = inbox.items.find(i => i.kind === 'single' && i.title === '复现填空'); assert.ok(candidate);
  const paper = await api('review-papers/open?subject_id=math', {candidate_id: candidate.id, request_id: crypto.randomUUID()});
  await go('/subjects/math/reviews/' + paper.id); await page.locator('.showAnswer').click();
  await page.locator('.selfAssessment .referenceAnswer').waitFor();
  assert.equal(await page.locator('.selfAssessment .referenceAnswer').innerText(), '1. 2');
  await shot('reproduction-blank-review');
  await page.locator('.selfAssessment [data-rating="3"]').click();
  await page.getByText('本次复习已完成', {exact: true}).waitFor();
  assert.equal((await api('review-papers/state?paper_id=' + paper.id)).items[0].result.correct, true);
  pass('Advanced semantic fill-blank review reveals its reference and grades once');
  await go(bank('semantic-blank')); await page.locator('#trainingSubmit').waitFor();
  assert.equal(await page.locator('#trainingSubmit').innerText(), 'Show Answer');
  await page.locator('input.inlineBlank').fill('2'); assert.equal(await page.locator('#trainingSubmit').innerText(), 'Submit');
  await page.locator('input.inlineBlank').fill(' '); assert.equal(await page.locator('#trainingSubmit').innerText(), 'Show Answer');
  await page.locator('#trainingSubmit').click(); await page.locator('.trainingRatings [data-rating="3"]').waitFor();
  await shot('reproduction-blank-bank'); await page.locator('.trainingRatings [data-rating="3"]').click();
  await wait(async () => (await page.locator('.trainingVerdict').count()) > 0, 10, 'bank assessment saved');
  await go(bank('exact-text')); await page.locator('#trainingSubmit').waitFor();
  assert.equal(await page.locator('#trainingSubmit').innerText(), 'Submit');
  assert.equal(await page.locator('.trainingRatings').count(), 0);
  await go(bank('semantic')); await page.locator('#trainingSubmit').waitFor();
  assert.equal(await page.locator('#trainingSubmit').innerText(), 'Show Answer');
  pass('Empty structured blanks and semantic text qualify; exact text does not');
  await go('/subjects/math/topic/synthetic-course');
  await page.getByRole('button', {name: '已掌握，试着作答', exact: true}).click();
  await page.locator('#stepCard .showAnswer').click(); await page.locator('#stepCard .selfAssessment [data-rating="3"]').waitFor();
  await shot('lesson-reproduction-controls'); await page.locator('#stepCard .selfAssessment [data-rating="3"]').click();
  await page.locator('#stepCard .feedback.correct').waitFor(); pass('Lesson reproduction uses shared Show Answer controls and the original completion policy');
  await go('/subjects/english/topic/assessment-atomic');
  await page.locator('.atomicMain .primaryButton').first().click();
  await page.locator('.atomicMain .primaryButton').first().click();
  await page.locator('.atomicMain .showAnswer').click(); await page.locator('.atomicMain .selfAssessment [data-rating="3"]').waitFor();
  await shot('atomic-reproduction-controls'); await page.locator('.atomicMain .selfAssessment [data-rating="3"]').click();
  await page.locator('.atomicMain .feedback.correct').waitFor(); pass('Atomic reproduction uses the same controls without changing point scheduling rules');
  await login('standard'); await go('/subjects/math/reviews');
  const timed = (await api('review-papers/inbox?subject_id=math')).items.find(i => i.kind === 'paper');
  const timedPaper = await api('review-papers/open?subject_id=math', {candidate_id: timed.id, request_id: crypto.randomUUID()});
  await go('/subjects/math/reviews/' + timedPaper.id); await page.locator('#paperTimeLimit').waitFor();
  await page.waitForTimeout(3500); await page.locator('#papertopicHomeLink').click();
  await wait(async () => (await api('review-papers/state?paper_id=' + timedPaper.id)).timing.paused, 5, 'pause saved');
  const remaining = (await api('review-papers/state?paper_id=' + timedPaper.id)).timing.remaining_seconds;
  assert.ok(remaining < 60 && remaining > 40);
  await page.waitForTimeout(20000);
  assert.equal((await api('review-papers/state?paper_id=' + timedPaper.id)).timing.remaining_seconds, remaining);
  await go('/subjects/math/reviews/' + timedPaper.id); await page.locator('#paperTimeLimit').waitFor();
  const returned = (await api('review-papers/state?paper_id=' + timedPaper.id)).timing.remaining_seconds;
  assert.ok(returned <= remaining && returned >= remaining - 4);
  await page.reload({waitUntil: 'networkidle'}); await page.locator('#paperTimeLimit').waitFor();
  assert.ok((await api('review-papers/state?paper_id=' + timedPaper.id)).timing.remaining_seconds <= remaining);
  await shot('resumed-remaining-time'); pass('Exit pauses exactly, 20 seconds of absence consume no allowance, and refresh never resets the limit');
  await wait(() => page.getByText('本次复习已完成', {exact: true}).isVisible(), 80, 'active-time automatic expiry');
  assert.equal((await api('review-papers/state?paper_id=' + timedPaper.id)).phase, 'done');
  await shot('active-time-expired'); pass('The accumulated active-time limit still enforces automatic submission');
  await login('advanced'); await page.setViewportSize({width: 390, height: 844}); await go(bank('semantic'));
  await page.locator('#trainingSubmit').waitFor(); await page.keyboard.press('Escape');
  await page.locator('#trainingSubmit').click(); await page.locator('.trainingRatings [data-rating="3"]').waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
  await shot('mobile-reproduction');
  assert.deepEqual(report.errors, []);
} catch (error) { report.failure = String(error.stack || error); await shot('failure'); throw error; }
finally { await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close(); }
