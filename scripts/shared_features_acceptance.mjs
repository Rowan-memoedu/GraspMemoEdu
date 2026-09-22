/** Real Chrome + loopback HTTP. Fault injection is limited to explicit failure/race tests. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, out] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.ok(out); await fs.mkdir(out);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context = await browser.newContext({viewport: {width: 1365, height: 960}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], formal_data_opened: false, synthetic_course: true,
  fault_injection: ['one failed feedback request', 'one failed course selection', 'one delayed subject catalog']};
let token, submits = 0;
page.on('pageerror', error => report.errors.push(error.message));
page.on('request', request => {
  token = request.headers()['x-learning-identity'] || token;
  if (request.method() === 'POST' && request.url().includes('/api/submit?')) submits++;
});
const pass = label => { report.checks.push(label); console.log('PASS ' + label); };
const go = route => page.goto(base + '#' + route, {waitUntil: 'networkidle'});
const mathRoute = '/subjects/math/topic/shared-types';
const choose = async () => { await page.locator('#stepCard').getByRole('button', {name: '已掌握，试着作答', exact: true}).click(); };
const next = async () => { await page.locator('#stepCard .continueRow .primaryButton').click(); };
const submitLesson = async (correct = true) => {
  await page.locator('#submitButton:not(:disabled)').click();
  await page.locator(`#stepCard .feedback.${correct ? 'correct' : 'incorrect'}`).waitFor({timeout: 120000});
};
const pick = async (side, id, row, scope = '#stepCard') => {
  await page.locator(`${scope} .matchingOption[data-side="${side}"][data-option-id="${id}"]`).click();
  await page.locator(`${scope} .matchingSlot[data-side="${side}"][data-row="${row}"]`).click();
};
const noOverflow = async () => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
let session;
async function api(route, body, qa = false) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + (qa ? '/__qa/' : '/api/') + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'X-Learning-Identity': token, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result)); return result;
}
try {
  await go(mathRoute);
  await page.locator('#stepCard [data-report-block]').waitFor();
  await page.evaluate(() => {
    const block = document.querySelector('#stepCard [data-report-block]'), text = block.querySelector('p').firstChild;
    const range = document.createRange(); range.setStart(text, 2); range.setEnd(text, 5);
    getSelection().removeAllRanges(); getSelection().addRange(range);
  });
  await page.keyboard.press('Control+Enter');
  await page.locator('.feedbackDialog[open]').waitFor();
  assert.equal(await page.locator('.correctionPreview').first().locator('strong').innerText(), '六乘七');
  await page.locator('#correctionReplacement').fill('七乘六');
  assert.equal(await page.locator('.correctionPreview').last().locator('strong').innerText(), '七乘六');
  await page.screenshot({path: path.join(out, 'text-correction.png'), fullPage: true});
  await page.route('**/api/feedback*', route => route.abort('failed'), {times: 1});
  await page.locator('.feedbackDialog button[type=submit]').click();
  await page.waitForFunction(() => !document.querySelector('.feedbackDialog button[type=submit]').disabled);
  assert.equal(await page.locator('#correctionReplacement').inputValue(), '七乘六');
  assert.equal(await page.locator('.feedbackDialog').evaluate(el => el.open), true);
  await page.locator('#correctionReplacement').fill('');
  await page.locator('.feedbackDialog button[type=submit]').click();
  await page.getByText('反馈已保存，谢谢。', {exact: true}).waitFor();
  const saved = await api('audit', undefined, true);
  assert.equal(saved.corrections.length, 1); assert.equal(saved.corrections[0].operation, 'delete');
  assert.equal(saved.corrections[0].quote.exact, '六乘七');
  report.real_judge = saved.real_judge;
  await page.locator('.feedbackDialog .dialogActions').getByRole('button', {name: '关闭', exact: true}).click();
  assert.match(await page.locator('#stepCard').innerText(), /写出六乘七的结果/);
  await page.evaluate(() => getSelection().removeAllRanges()); await page.keyboard.press('Control+Enter');
  assert.equal(await page.locator('.feedbackDialog').evaluate(el => el.open), false);
  pass('Text selection, live replacement preview, failure draft, explicit delete, saved confirmation and unchanged content');

  await choose(); await page.locator('#answerInput').fill('42');
  let delayCatalog = true;
  await page.route('**/api/catalog?subject_id=math', async route => {
    if (!delayCatalog) return route.continue();
    delayCatalog = false; const response = await route.fetch();
    await new Promise(resolve => setTimeout(resolve, 700)); await route.fulfill({response});
  });
  await page.locator('#catalogToggle').click();
  await page.locator('.catalogSubject[data-subject-id=physics]').click();
  await page.locator('.catalogCourse[data-course-id=physics-course]').waitFor();
  await page.waitForResponse(response => response.url().endsWith('/api/catalog?subject_id=math'));
  assert.equal(await page.locator('.catalogSubject[aria-pressed=true]').getAttribute('data-subject-id'), 'physics');
  await page.unroute('**/api/catalog?subject_id=math');
  await page.locator('.catalogSubject[data-subject-id=english]').click();
  await page.getByText('该学科暂无已发布课程。', {exact: true}).waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#catalogPanel').isVisible(), false);
  assert.equal(await page.locator('#catalogToggle').evaluate(el => document.activeElement === el), true);
  await page.locator('#catalogToggle').click();
  await page.locator('.catalogSubject[data-subject-id=physics]').click();
  await page.locator('.catalogCourse[data-course-id=physics-course]').waitFor();
  await page.route('**/api/catalog/select*', route => route.abort('failed'), {times: 1});
  await page.locator('.catalogCourse').click(); await page.locator('.catalogRetry').waitFor();
  assert.ok(page.url().endsWith('#' + mathRoute));
  assert.equal(await page.locator('#answerInput').inputValue(), '42');
  await page.locator('.catalogRetry').click(); await page.locator('.catalogCourse').click();
  await page.waitForURL('**#/subjects/physics/learn');
  await page.locator('#catalogToggle').click(); await page.locator('.catalogSubject[data-subject-id=math]').click();
  await page.locator('.catalogCourse[data-course-id=math-course]').click(); await page.waitForURL('**#/subjects/math/learn');
  await go(mathRoute); await page.locator('#answerInput').waitFor();
  assert.equal(await page.locator('#answerInput').inputValue(), '42');
  await page.locator('#catalogToggle').click(); await page.locator('.catalogCourse').waitFor();
  await page.screenshot({path: path.join(out, 'catalog-desktop.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844}); await noOverflow();
  await page.screenshot({path: path.join(out, 'catalog-mobile.png'), fullPage: true});
  await page.keyboard.press('Escape'); await page.setViewportSize({width: 1365, height: 960});
  pass('Catalog ignores late responses, shows empty subjects, keeps failed selection and draft, switches same-title courses by ID, closes with Escape');

  await page.locator('#answerInput').press('Control+Enter');
  await page.locator('#stepCard .feedback.correct').waitFor(); await next();
  await choose();
  assert.equal(await page.locator('#submitButton').isDisabled(), true);
  await page.locator('.judgmentItem').nth(0).getByLabel('正确', {exact: true}).check();
  assert.equal(await page.locator('#submitButton').isDisabled(), true);
  await page.locator('.judgmentItem').nth(1).getByLabel('错误', {exact: true}).check();
  await page.reload({waitUntil: 'networkidle'});
  assert.equal(await page.locator('.judgmentItem input:checked').count(), 2);
  await submitLesson(); assert.match(await page.locator('#stepCard .feedbackReason').innerText(), /1 ✓ · 2 ✓/);
  await next(); await choose();
  await page.locator('[data-blank-id=a]').fill('东'); assert.equal(await page.locator('#submitButton').isDisabled(), true);
  await page.locator('[data-blank-id=b]').fill('西'); await page.reload({waitUntil: 'networkidle'});
  assert.equal(await page.locator('[data-blank-id=a]').inputValue(), '东');
  await page.locator('[data-blank-id=b]').press('Control+Enter');
  await page.locator('#stepCard .feedback.correct').waitFor(); await next();
  pass('Strict answer shortcut, multi-judgment completeness and draft reload, inline multi-blank draft and submission');

  await choose();
  await page.waitForFunction(() => { const option = document.querySelector('.matchingOption[data-option-id=l2]'); return option && !option.disabled; });
  await page.locator('.matchingOption[data-side=left][data-option-id=l2]').dragTo(page.locator('.matchingSlot[data-side=left][data-row="0"]'));
  await pick('left', 'l3', 1);
  await page.locator('.matchingRow').nth(0).locator('.matchingCell').first().getByRole('button', {name: /下移/}).click();
  await pick('right', 'r9', 0);
  await page.locator('.matchingOption[data-side=right][data-option-id=r4]').dragTo(page.locator('.matchingSlot[data-side=right][data-row="1"]'));
  const reversed = [{left:'l3',right:'r9'},{left:'l2',right:'r4'}];
  assert.deepEqual(JSON.parse(await page.locator('#answerInput').inputValue()), reversed);
  await page.reload({waitUntil: 'networkidle'});
  assert.deepEqual(JSON.parse(await page.locator('#answerInput').inputValue()), reversed);
  assert.equal(await page.locator('.matchingOption:not(:disabled)').count(), 2);
  assert.equal(await page.locator('.matchingRow').nth(0).locator('.matchingCell').first().getByRole('button', {name: /上移/}).isDisabled(), true);
  assert.ok(await page.locator('.matchingSlot svg').count());
  await page.screenshot({path:path.join(out,'matching-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844}); await page.keyboard.press('Escape'); await noOverflow();
  await page.screenshot({path:path.join(out,'matching-mobile.png'),fullPage:true});
  await submitLesson();
  assert.equal(await page.locator('.matchingSlot:not(:disabled)').count(),0);
  await page.setViewportSize({width:1365,height:960}); await next(); await choose();
  assert.equal(await page.locator('.matchingSlot.fixedSlot:disabled').count(),2);
  assert.equal(await page.locator('.matchingArrow').first().innerText(),'→');
  await pick('right','r4',0); await pick('right','r9',1); await submitLesson(); await next();
  pass('Matching supports drag, tap, directional move buttons, distractors, row-independent pairs, fixed side, math rendering, responsive layout and read-only history');

  await choose(); await page.locator('#answerInput').fill('水从液体变成了固体'); await submitLesson(); await next();
  await choose(); await page.locator('[data-blank-id=a]').fill('液体状态'); await page.locator('[data-blank-id=b]').fill('固体状态');
  await submitLesson(); await next();
  const completed = await api('state?topic_id=shared-types'); assert.equal(completed.status,'completed');
  assert.equal(submits,7);
  const history = await api('tasks/lesson%3Ashared-types/answers');
  assert.equal(history.groups.length,7);
  assert.match(history.groups[3].answers[0].answer_display,/↔/);
  pass('Two real LLM semantic variants pass, all seven examples complete, and completed-task history preserves typed answers');

  await api('seed-review',{module_id:'example-4'},true);
  await go('/subjects/math/review/shared-types/example-4');
  await page.locator('#reviewPractice .matchingBoard').waitFor();
  await pick('left','l2',0,'#reviewPractice'); await pick('left','l3',1,'#reviewPractice');
  await pick('right','r9',0,'#reviewPractice'); await pick('right','r4',1,'#reviewPractice');
  await page.locator('#reviewSubmitButton').click(); await page.locator('#reviewPractice .feedback.incorrect').waitFor();
  assert.match(await page.locator('.reviewProgress').innerText(),/1/);
  for(let i=0;i<2;i++) {
    await page.locator('#reviewContinueButton').click();
    await pick('left','l3',0,'#reviewPractice'); await pick('left','l2',1,'#reviewPractice');
    await pick('right','r9',0,'#reviewPractice'); await pick('right','r4',1,'#reviewPractice');
    await page.locator('#reviewSubmitButton').click(); await page.locator('#reviewPractice .feedback.correct').waitFor();
  }
  await page.locator('#reviewResult').waitFor();
  await page.screenshot({path:path.join(out,'review-completed.png'),fullPage:true});
  const reviewId=new URL(page.url()).hash.split('session=')[1];
  const reviewed=await api('review/sessions/'+reviewId+'?topic_id=shared-types');
  assert.equal(reviewed.practice_error_count,1); assert.equal(reviewed.practice_answered_count,3);
  assert.equal(reviewed.status,'completed');
  pass('Review counts two wrong pairs as one error, expands to three questions, restores correct matching answers and completes');
  const audit=await api('audit',undefined,true); assert.equal(audit.integrity,'ok');
  assert.deepEqual(report.errors,[]);
  report.status='passed';
} catch(error) {
  report.status='failed'; report.failure=error.stack; console.error(error);
  await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
  process.exitCode=1;
} finally {
  await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2)); await browser.close();
}
