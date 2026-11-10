/** Real browser acceptance for per-step Show Answer; isolated loopback only. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, fixture, release] = process.argv.slice(2);
const paperOnly = process.argv.includes('--paper-only');
assert.equal(new URL(base).hostname, '127.0.0.1');
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const output = path.join(fixture, 'show-answer-browser'); await fs.mkdir(output, {recursive: true});
const report = {passed: false, checks: [], errors: [], real_judge: true, production_requests: 0};
const browser = await chromium.launch({channel: 'chrome', headless: true});
const page = await browser.newPage({viewport: {width: 1280, height: 950}, locale: 'zh-CN'});
page.setDefaultTimeout(30000);
await page.route('**/*', async route => {
  const url = new URL(route.request().url()); assert.equal(url.origin, base, 'Fixture must not contact production');
  const relative = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname.slice(1));
  if (release && !relative.startsWith('api/') && relative !== 'deployment.json' && !relative.includes('..')) {
    const file = path.join(release, relative);
    try { if ((await fs.stat(file)).isFile()) return route.fulfill({path: file}); } catch {}
  }
  return route.continue();
});
page.on('pageerror', error => report.errors.push(error.message));
let token, session;
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
async function api(route, body) {
  if (!route.includes('subject_id=')) route += (route.includes('?') ? '&' : '?') + 'subject_id=math';
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET', headers: {Origin: base,
    'Content-Type': 'application/json', 'X-Learning-Session': session, 'X-Learning-Identity': token},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result)); return result;
}
const pass = text => { report.checks.push(text); console.log('PASS ' + text); };
const shot = name => page.screenshot({path: path.join(output, name + '.png'), fullPage: true});
const go = id => page.goto(base + '#/subjects/math/banks/training-acceptance/group-a?question=' + id, {waitUntil: 'networkidle'});
const current = (id, reader = 'training') => page.locator(`#${reader}stepCard .multiStepCurrent[data-step-id=step-${id}]`);
const rating = (value, reader = 'training') => page.locator(`#${reader}stepCard .trainingRatings [data-rating="${value}"]`);
async function showTraining(step) {
  await current(step).waitFor();
  const response = page.waitForResponse(r => r.url().includes('/training/reveal') && r.request().method() === 'POST');
  await page.locator('#trainingSubmit').click();
  const result = await (await response).json(); await rating(5).waitFor();
  assert.equal(result.phase, 'shown'); assert.equal(result.multi_step.current_step_id, 'step-' + step);
  assert.equal(result.multi_step.finished, false); assert.equal(result.multi_step.steps.length, step);
  assert.equal(await page.locator('#trainingstepCard .trainingRatings button').count(), 2);
  assert.equal(await current(step).locator('.multiStepAnswer').count(), 1);
  assert.equal(await current(step).locator('input,textarea').count(), 0); return result;
}
async function paperState(id) { return api('review-papers/state?paper_id=' + encodeURIComponent(id)); }
async function paperShow(step) {
  await current(step, 'paper').waitFor();
  await page.locator('#paperstepCard .showAnswer').click(); await rating(5, 'paper').waitFor();
  assert.equal(await page.locator('#paperstepCard .multiStepPart').count(), step);
}
async function openPaper(mode) {
  await api('review-papers/preferences', {mode});
  const inbox = await api('review-papers/inbox');
  const candidate = inbox.items.find(i => i.paper_type_id === 'multi-step-pair'); assert(candidate);
  const paper = await api('review-papers/open', {candidate_id: candidate.id, request_id: crypto.randomUUID()});
  await page.goto(base + '#/subjects/math/reviews/' + paper.id, {waitUntil: 'networkidle'});
  const index = paper.items.findIndex(i => i.multi_step); assert(index >= 0);
  if (index !== paper.items.findIndex(i => !i.completed)) await page.locator(`[data-question-index="${index}"]`).click();
  return {id: paper.id, index};
}
try {
  await go('multi');
  const identities = JSON.parse(await fs.readFile(path.join(fixture, 'identities.json'), 'utf8'));
  await page.locator('#userMenuButton').hover(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities.advanced); await page.locator('#identitySubmitButton').click();
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
  await go('multi');
  if (!paperOnly) {
  const shown = await showTraining(1);
  assert(shown.reference_answer.includes('5')); assert(!JSON.stringify(shown).includes('求两数之积'));
  await page.reload({waitUntil: 'networkidle'}); await rating(5).waitFor();
  assert.equal(await page.locator('.multiStepPart').count(), 1);
  await shot('training-fill-shown');
  await rating(5).click(); await current(2).waitFor();
  assert.equal(await page.locator('#trainingstepCard .trainingRatings').count(), 0);
  const proof = await showTraining(2); assert(proof.explanation_html.includes('加法满足交换律'));
  for (const width of [320, 375, 414, 768, 1280]) {
    await page.setViewportSize({width, height: 950});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (width === 375 || width === 1280) await shot('training-proof-shown-' + width);
  }
  await rating(1).click(); await page.locator('.trainingVerdict.incorrect').waitFor();
  assert.equal(await page.locator('.multiStepPart').count(), 3);
  const failed = await api('training/state?attempt_id=' + shown.attempt_id);
  assert.equal(failed.result.multi_step.first_failed_step_id, 'step-2');
  await shot('training-self-rated-failure');
  pass('Fill and proof steps reveal only their own answers, survive reload, and self-rated failure reveals the full question');
  await page.locator('.trainingRedo').click();
  const blank = current(1).locator('input[data-blank-id]');
  await blank.fill('first draft'); await blank.fill('changed draft');
  assert.equal(await blank.inputValue(), 'changed draft');
  assert.equal(await current(1).locator('input[data-blank-id]').count(), 1);
  await page.reload({waitUntil: 'networkidle'}); assert.equal(await blank.inputValue(), 'changed draft');
  await blank.fill(''); await showTraining(1); await rating(5).click();
  await current(2).waitFor(); await page.locator('#trainingAnswer').fill('和不变，因为加法满足交换律。');
  await page.locator('#trainingSubmit').click(); await current(3).waitFor({timeout: 240000});
  const last = await showTraining(3); await rating(5).click(); await page.locator('.trainingVerdict.correct').waitFor();
  const completed = await api('training/state?attempt_id=' + last.attempt_id);
  assert.equal(completed.result.self_rating, 5); assert.equal(completed.result.rating, 5);
  assert.equal(completed.result.stopped, true); assert.equal(completed.result.scheduled, false); assert.equal(completed.result.multi_step.mode, 'whole');
  assert.equal(completed.multi_step.steps[1].result.self_rating, undefined);
  await shot('training-mixed-completed');
  pass('Self-rated fill, real-judged proof and self-rated final fill complete one whole-question result');
  } else {
  const prime = await api('training/open', {bank_id:'training-acceptance', group_id:'group-a', question_id:'multi', request_id:crypto.randomUUID()});
  await api('training/reveal', {attempt_id:prime.attempt_id, step_id:'step-1'});
  await api('training/rate', {attempt_id:prime.attempt_id, step_id:'step-1', rating:1});
  await go('choice'); await page.locator('.choiceOption input[value=b]').check(); await page.locator('#trainingSubmit').click();
  await page.locator('.trainingVerdict.incorrect').waitFor();
  await fetch(base + '/__qa/advance', {method: 'POST', headers: {Origin: base}});
  const firstPaper = await openPaper('paper'); await paperShow(1);
  await page.reload({waitUntil: 'networkidle'}); await rating(5, 'paper').waitFor();
  await rating(5, 'paper').click(); await current(2, 'paper').waitFor();
  let paper = await paperState(firstPaper.id);
  assert.equal(paper.items[firstPaper.index].revealed, false); assert.equal(paper.items[firstPaper.index].self_rating, null);
  await paperShow(2); await page.setViewportSize({width: 375, height: 950}); await shot('paper-proof-shown-375');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await rating(1, 'paper').click(); await page.locator('#paperstepCard .multiStepFailed').waitFor();
  paper = await paperState(firstPaper.id); assert.equal(paper.items[firstPaper.index].result.correct, false);
  const other = paper.items.find(i => i.index !== firstPaper.index); assert.equal(other.explanation_html, null);
  await page.setViewportSize({width: 1280, height: 950}); await page.locator(`[data-question-index="${other.index}"]`).click();
  await page.locator('.choiceOption input[value=b]').check(); await page.locator('#paperSubmit').click();
  await page.getByText('本次复习已完成', {exact: true}).waitFor();
  pass('Whole-paper mode self-rates each step immediately, clears the prior rating, and keeps the other answer private');
  await fetch(base + '/__qa/advance', {method: 'POST', headers: {Origin: base}});
  const secondPaper = await openPaper('question');
  for (const step of [1, 2, 3]) {
    await paperShow(step); await rating(5, 'paper').click();
    if (step < 3) await current(step + 1, 'paper').waitFor();
  }
  await page.locator('#paperstepCard .multiStepPassed').nth(2).waitFor();
  paper = await paperState(secondPaper.id);
  assert.equal(paper.items[secondPaper.index].result.correct, true);
  assert.equal(paper.items[secondPaper.index].result.self_rating, 5);
  assert.equal(paper.items[secondPaper.index].result.rating, 5);
  assert.equal(paper.items[secondPaper.index].result.stopped, true);
  assert.equal(paper.items[secondPaper.index].result.scheduled, false);
  assert.equal(paper.items[secondPaper.index].result.multi_step.mode, 'whole');
  await shot('question-mode-all-self-rated');
  pass('Question mode also completes all three self-rated steps without skipping or prematurely finishing the paper');
  }
  assert.deepEqual(report.errors, []); report.passed = true;
} finally {
  if (!report.passed) await shot('failure');
  await fs.writeFile(path.join(output, 'acceptance.json'), JSON.stringify(report, null, 2)); await browser.close();
}
