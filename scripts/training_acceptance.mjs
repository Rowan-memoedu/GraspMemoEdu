/** Isolated real Chrome, HTTP and LLM acceptance for independent training banks. */
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
page.on('pageerror', error => report.errors.push(error.message));
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
const select = id => page.locator(`.trainingQuestionLink[data-question-id="${id}"]`).click();
const submit = () => page.locator('.trainingAnswerForm button[type=submit]').click();
const done = () => page.locator('.trainingVerdict').waitFor({timeout: 240000});
async function readCurrent() {
  const group = await api('training/group?subject_id=math&bank_id=training-acceptance&group_id=group-a');
  const qid = new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('question');
  return api('training/state?attempt_id=' + group.questions.find(q => q.id === qid).latest_attempt_id);
}
try {
  for (const subject of ['math', 'physics', 'english', 'chinese', 'biology', 'chemistry']) {
    await go(`/subjects/${subject}/banks`);
    await page.locator('.portalPageTitle').waitFor();
    assert.equal(await page.locator('[data-navigation=reviews]').isVisible(), true);
    const links = await page.locator('.mainNavigation [data-navigation]').evaluateAll(items => items.map(i => i.dataset.navigation));
    assert.deepEqual(links, ['learn', 'reviews', 'courses', 'banks', 'guide']);
    assert.equal((await page.locator('.mainNavigation :is(a,button)').evaluateAll(items => new Set(items.map(i => getComputedStyle(i).marginRight)).size)), 1);
  }
  pass('All six subjects expose Review and Question banks, with equal nav spacing and bank immediately before Guide');
  await go('/subjects/math/banks');
  await page.locator('.courseChoice').getByRole('link', {name: '训练', exact: true}).click();
  await page.locator('.trainingTree').waitFor();
  assert.equal(await page.locator('.trainingBranch').count(), 1); await shot('bank-tree');
  await page.getByRole('link', {name: '开始训练', exact: true}).first().click();
  await page.locator('#trainingAnswer').waitFor(); assert.equal(await page.locator('.trainingQuestionLink').count(), 4);
  assert.equal(await page.locator('.trainingAnswerForm button[type=submit]').textContent(), 'Submit');
  assert.equal(await page.locator('.trainingAnswerForm button[type=submit]').isDisabled(), true);
  await select('choice'); await page.locator('.choiceOption input[value=a]').check();
  await page.reload({waitUntil: 'networkidle'}); await page.locator('.choiceOption input:checked').waitFor();
  assert.equal(await page.locator('.choiceOption input:checked').inputValue(), 'a');
  await submit(); await done();
  const choice = await readCurrent(); assert.equal(choice.result.correct, true);
  pass('Arbitrary question order, structured choice control and draft restored after refresh');
  await select('essay'); await page.locator('#trainingAnswer').fill('5');
  await submit(); await page.reload({waitUntil: 'networkidle'}); await done();
  const initial = await readCurrent(); assert.equal(initial.result.correct, true); assert.equal(initial.result.rating, null);
  await page.locator('.trainingRedo').click(); await page.locator('#trainingAnswer').fill('99'); await submit(); await done();
  const repeated = await readCurrent(); assert.equal(repeated.result.correct, false); assert.equal(repeated.result.practice_only, true);
  assert.equal(repeated.due_at, initial.due_at);
  await page.locator('.trainingHistory summary').click(); await page.locator('.trainingHistoryItem').first().waitFor();
  assert.equal(await page.locator('.trainingHistoryItem').count(), 2);
  await shot('ordinary-history');
  pass('Real LLM correct and incorrect answers, pending refresh recovery, six-hour first review and no reschedule for early redo');
  await fetch(base + '/__qa/advance', {method: 'POST', headers: {Origin: base}});
  await go('/subjects/math/reviews');
  const cards = page.locator('.trainingReviewSection .courseChoice'); await cards.first().waitFor(); assert.equal(await cards.count(), 2);
  await cards.filter({hasText: '选择结果'}).getByRole('link', {name: '复习此题'}).click();
  await page.locator('.choiceOption input[value=a]').check(); await submit(); await done();
  assert.equal((await readCurrent()).result.rating, 3);
  pass('Due question cards appear in unified Review and correct review maps to FSRS 3');
  await login('advanced'); await go('/subjects/math/banks/training-acceptance/group-a?question=essay');
  await page.getByRole('button', {name: 'Show Answer', exact: true}).click();
  await page.locator('.trainingRatings').waitFor();
  assert.equal(await page.locator('.trainingRating').count(), 4);
  assert.equal(await page.locator('.trainingRating').evaluateAll(items => new Set(items.map(i => getComputedStyle(i).backgroundColor)).size), 4);
  const revealed = await readCurrent(); assert.equal(revealed.due_at, null);
  await shot('advanced-desktop');
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await shot('advanced-mobile');
  await page.locator('[data-rating="4"]').click(); await done();
  assert.equal((await readCurrent()).result.rating, 4);
  await page.setViewportSize({width: 1365, height: 950});
  await select('choice'); assert.equal(await page.getByRole('button', {name: 'Show Answer', exact: true}).count(), 0);
  pass('Advanced empty text reveals explanation and four colored ratings; fixed questions remain ordinary; mobile layout fits');
  await go('/settings'); await page.locator('#trainingSelfRating').waitFor(); assert.equal(await page.locator('#trainingSelfRating').isChecked(), true);
  await page.locator('#trainingSelfRating').uncheck(); await page.locator('.profileForm button[type=submit]').click();
  await page.getByText('设置已保存。', {exact: true}).waitFor();
  await go('/subjects/math/banks/training-acceptance/group-a?question=essay'); await page.locator('.trainingRedo').click();
  assert.equal(await page.locator('.trainingAnswerForm button[type=submit]').textContent(), 'Submit');
  assert.equal(await page.locator('.trainingAnswerForm button[type=submit]').isDisabled(), true);
  pass('Default-on advanced setting can be disabled and is honored after navigation');
  await login('admin'); await go('/subjects/math/banks/training-acceptance/group-a?question=essay');
  await page.locator('#trainingAnswer').fill('ADMIN_TRANSIENT_DRAFT');
  assert.equal(await page.evaluate(() => [...Array(localStorage.length)].map((_, i) => localStorage.getItem(localStorage.key(i))).some(v => v?.includes('ADMIN_TRANSIENT_DRAFT'))), false);
  await page.locator('#trainingAnswer').fill(''); await page.getByRole('button', {name: 'Show Answer', exact: true}).click();
  await page.locator('[data-rating="3"]').click(); await done();
  pass('Administrator answering works without persisting the answer draft in Web Storage');
  assert.deepEqual(report.errors, []); report.passed = true;
} catch (error) {
  report.failure = error.message; await shot('failure').catch(() => {}); throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close();
}
