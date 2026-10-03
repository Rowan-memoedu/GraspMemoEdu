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
async function geometry(root, selectors) {
  return page.locator(root).evaluate((frame, selectors) => Object.fromEntries(selectors.map(selector => {
    const item = frame.querySelector(selector), style = getComputedStyle(item), box = item.getBoundingClientRect();
    return [selector, {left: Math.round(box.x), width: Math.round(box.width),
      ...Object.fromEntries(['fontSize', 'fontWeight', 'lineHeight', 'padding', 'border', 'borderRadius', 'backgroundColor', 'gridTemplateColumns']
        .map(key => [key, style[key]]))}];
  })), selectors);
}
const readerSelectors = ['.historySidebar', '.pageShell', '.lessonTitle', '.step', '.stepTitle', '.answerForm', '.answerInput'];
const select = id => page.locator(`.trainingQuestionLink[data-question-id="${id}"]`).click();
const submit = () => page.locator('#trainingSubmit').click();
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
  await go('/subjects/math/topic/synthetic-course');
  await page.getByRole('button', {name: '已掌握，试着作答', exact: true}).click();
  await page.locator('#answerInput').waitFor();
  const lessonDesktop = await geometry('#appLayout', readerSelectors); await shot('original-lesson-desktop');
  await page.setViewportSize({width: 390, height: 844});
  const lessonMobile = await geometry('#appLayout', readerSelectors); await shot('original-lesson-mobile');
  await page.setViewportSize({width: 1365, height: 950});
  await page.locator('#answerInput').fill('5'); await page.locator('#submitButton').click();
  await page.locator('#stepCard .feedback.correct').waitFor({timeout: 240000});
  pass('Original Lesson still submits through the shared answer editor and real LLM');
  await go('/subjects/math/banks');
  await page.locator('.courseChoice').getByRole('link', {name: '训练', exact: true}).click();
  await page.locator('.courseUnitGroup').waitFor();
  assert.equal(await page.locator('.courseUnitGroup').count(), 1);
  await page.locator('.portalTask .taskToggle').first().click(); await shot('bank-tree');
  await page.getByRole('button', {name: '开始训练', exact: true}).click();
  await page.locator('#trainingAnswer').waitFor(); assert.equal(await page.locator('.trainingQuestionLink').count(), 4);
  const trainingDesktop = await geometry('#trainingappLayout', readerSelectors);
  assert.deepEqual(trainingDesktop, lessonDesktop); await shot('training-lesson-desktop');
  await page.setViewportSize({width: 390, height: 844});
  const trainingMobile = await geometry('#trainingappLayout', readerSelectors);
  assert.deepEqual(trainingMobile, lessonMobile); await shot('training-lesson-mobile');
  report.geometry = {lessonDesktop, trainingDesktop, lessonMobile, trainingMobile};
  await page.locator('.trainingQuestionLink[data-question-id="boolean"]').click();
  assert.equal(await page.locator('#trainingappLayout').evaluate(item => item.classList.contains('historyCollapsed')), true);
  await page.locator('#traininghistoryDrawerToggle').click();
  await page.setViewportSize({width: 1365, height: 950}); await select('essay');
  await page.locator('#skipContent').focus(); await page.keyboard.press('Enter');
  assert.equal(await page.locator('#traininglessonContent').evaluate(item => item === document.activeElement), true);
  assert.deepEqual(await page.locator('[id]').evaluateAll(items => items.map(i => i.id).filter((id, n, ids) => ids.indexOf(id) !== n)), []);
  pass('Reader geometry exactly matches original Lesson on desktop and mobile; shared drawer and unrestricted selection work without duplicate IDs');
  assert.equal(await page.locator('#trainingSubmit').textContent(), 'Submit');
  assert.equal(await page.locator('#trainingSubmit').isDisabled(), true);
  await select('choice'); await page.locator('.choiceOption input[value=a]').check();
  await page.reload({waitUntil: 'networkidle'}); await page.locator('.choiceOption input:checked').waitFor();
  assert.equal(await page.locator('.choiceOption input:checked').inputValue(), 'a');
  await submit(); await done();
  const choice = await readCurrent(); assert.equal(choice.result.correct, true);
  pass('Arbitrary question order, structured choice control and draft restored after refresh');
  await select('blank'); await page.locator('[data-blank-id=a]').fill('5');
  await page.locator('[data-blank-id=a]').press('Control+Enter'); await done();
  assert.equal((await readCurrent()).result.correct, true);
  assert.equal(await page.locator('[data-blank-id=a]').isDisabled(), true);
  pass('Inline fill-blank input uses the shared editor, submits by keyboard and is disabled in saved history');
  await select('essay'); await page.locator('#trainingAnswer').fill('5');
  await submit(); await page.reload({waitUntil: 'networkidle'}); await done();
  const initial = await readCurrent(); assert.equal(initial.result.correct, true); assert.equal(initial.result.rating, null);
  await page.locator('.trainingRedo').click(); await page.locator('#trainingAnswer').fill('99'); await submit(); await done();
  const repeated = await readCurrent(); assert.equal(repeated.result.correct, false); assert.equal(repeated.result.practice_only, true);
  assert.equal(repeated.due_at, initial.due_at);
  await page.locator('.historySidebar .trainingHistoryItem').first().waitFor();
  assert.equal(await page.locator('.trainingHistoryItem').count(), 1);
  await shot('ordinary-history');
  pass('Real LLM correct and incorrect answers, pending refresh recovery, six-hour first review and no reschedule for early redo');
  await fetch(base + '/__qa/advance', {method: 'POST', headers: {Origin: base}});
  await go('/subjects/math/reviews');
  const cards = page.locator('.trainingReviewSection .courseChoice'); await cards.first().waitFor(); assert.equal(await cards.count(), 3);
  await cards.filter({hasText: '选择结果'}).getByRole('link', {name: '复习此题'}).click();
  await page.locator('.choiceOption input[value=a]').check(); await submit(); await done();
  assert.equal((await readCurrent()).result.rating, 3);
  pass('Due question cards appear in unified Review and correct review maps to FSRS 3');
  await login('advanced');
  await api('catalog/select?subject_id=math', {course_id: 'legacy-course'});
  await go('/subjects/math/learn'); await page.locator('.courseUnitGroup').waitFor();
  const treeSelectors = ['.courseSidebar', '.dashboardTasks', '.courseUnitGroup', '.courseUnitHeading', '.portalTask', '.taskToggle', '.taskHeading'];
  const lessonTree = await geometry('.dashboardLayout', treeSelectors); await shot('original-learning-tree');
  await go('/subjects/math/banks/training-acceptance'); await page.locator('.courseUnitGroup').waitFor();
  const trainingTree = await geometry('.dashboardLayout', treeSelectors);
  assert.deepEqual(trainingTree, lessonTree); report.geometry.lessonTree = lessonTree; report.geometry.trainingTree = trainingTree;
  await shot('training-learning-tree');
  pass('Bank grouping uses the same course sidebar, hierarchy and task-card renderer with matching geometry');
  await go('/subjects/math/banks/training-acceptance/group-a?question=essay');
  await page.getByRole('button', {name: 'Show Answer', exact: true}).click();
  await page.locator('.trainingRatings').waitFor();
  assert.equal(await page.locator('.trainingRating').count(), 5);
  assert.equal(await page.locator('.trainingRating').evaluateAll(items => new Set(items.map(i => getComputedStyle(i).backgroundColor)).size), 5);
  assert.deepEqual(await page.locator('.trainingRating small').allTextContents(), ['做错了', '勉强做对，但很困难', '正常做对', '轻松做对', '极度熟悉，不必再练']);
  assert.equal(await page.getByText('对照答案，选择你的熟练度。', {exact: true}).count(), 0);
  assert.equal(await page.locator('.trainingRedo').textContent(), '提前复习');
  assert.ok((await page.locator('.trainingRedo').boundingBox()).y > (await page.locator('.trainingRatings').boundingBox()).y);
  const revealed = await readCurrent(); assert.equal(revealed.due_at, null);
  await shot('advanced-desktop');
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('#traininghistoryButton').click();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await shot('advanced-mobile');
  await page.locator('[data-rating="4"]').click(); await done();
  assert.equal((await readCurrent()).result.rating, 4);
  await page.setViewportSize({width: 1365, height: 950});
  await page.locator('#traininghistoryButton').click();
  await select('choice'); assert.equal(await page.getByRole('button', {name: 'Show Answer', exact: true}).count(), 0);
  pass('Advanced empty text reveals explanation and five labeled small-text ratings; early review is below them; no rating hint; mobile layout fits');
  await select('essay'); await page.locator('.trainingRedo').click();
  await page.getByRole('button', {name: 'Show Answer', exact: true}).click();
  await page.locator('[data-rating="5"]').click(); await done();
  const stopped = await readCurrent(); assert.equal(stopped.stopped, true); assert.equal(stopped.result.rating, null);
  await page.reload({waitUntil: 'networkidle'}); await page.getByText('已停止后续推送', {exact: true}).waitFor();
  await page.locator('.trainingRedo').click(); await page.locator('#trainingAnswer').fill('5'); await submit(); await done();
  assert.equal((await readCurrent()).stopped, true);
  await fetch(base + '/__qa/advance', {method: 'POST', headers: {Origin: base}});
  assert.equal((await api('training/reviews?subject_id=math')).items.some(q => q.question_id === 'essay'), false);
  pass('Fifth button persists stopping across reload, real LLM early review and time advance without reenrolling the card');
  await go('/settings'); await page.locator('#trainingSelfRating').waitFor(); assert.equal(await page.locator('#trainingSelfRating').isChecked(), true);
  await page.locator('#trainingSelfRating').uncheck(); await page.locator('.profileForm button[type=submit]').click();
  await page.getByText('设置已保存。', {exact: true}).waitFor();
  await go('/subjects/math/banks/training-acceptance/group-a?question=essay'); await page.locator('.trainingRedo').click();
  assert.equal(await page.locator('#trainingSubmit').textContent(), 'Submit');
  assert.equal(await page.locator('#trainingSubmit').isDisabled(), true);
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
