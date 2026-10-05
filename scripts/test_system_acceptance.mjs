/** Retained synthetic test-system acceptance in a fresh headless Chrome profile. */
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
const context = await browser.newContext({viewport: {width: 1365, height: 960}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], screenshots: [], real_judge: false, production_data: false};
let token, session;
page.on('pageerror', error => report.errors.push(error.message));
const pass = label => { report.checks.push(label); console.log('PASS ' + label); };
const go = route => page.goto(base + '/#' + route, {waitUntil: 'networkidle'});
async function api(route, body, expected = 200, qa = false) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + (qa ? '/__qa/' : '/api/') + route, {method: body === undefined ? 'GET' : 'POST',
    headers: {Origin: base, 'Content-Type': 'application/json', 'X-Learning-Session': session, ...(token ? {'X-Learning-Identity': token} : {})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const result = await response.json(); assert.equal(response.status, expected, JSON.stringify(result)); return result;
}
async function login(who) {
  await page.locator('#userMenuButton').hover(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities[who].code);
  const response = page.waitForResponse(r => r.url().includes('/api/access/login') && r.request().method() === 'POST');
  await page.locator('#identitySubmitButton').click();
  const logged = await (await response).json(); token = logged.identity_token;
  assert.equal(logged.access.learner_id, identities[who].id);
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
}
async function shot(name) {
  const filename = path.join(output, name + '.png'); await page.screenshot({path: filename, fullPage: true}); report.screenshots.push(filename);
}
async function paperState(id) { return api('review-papers/state?paper_id=' + encodeURIComponent(id)); }
async function settle(id) {
  for (let i = 0; i < 100; i++) {
    const state = await paperState(id);
    if (state.phase !== 'grading' && !['pending', 'running'].includes(state.job_status)) return state;
    await new Promise(r => setTimeout(r, 150));
  }
  throw new Error('Paper did not settle');
}
async function openCandidate(candidate, subject = 'math') {
  const opened = await api('review-papers/open?subject_id=' + subject, {candidate_id: candidate.id, request_id: crypto.randomUUID()});
  await go(`/subjects/${subject}/reviews/${opened.id}`); await page.locator('#paperstepCard').waitFor();
  return opened;
}
async function inputAnswer(item, answer) {
  const value = item.interaction.type === 'text' ? answer : JSON.parse(answer);
  if (item.interaction.type === 'choice') {
    for (const id of Array.isArray(value.selected) ? value.selected : [value.selected]) await page.locator(`#paperstepCard .choiceOption input[value="${id}"]`).check();
  } else if (item.interaction.type === 'fill_blank') {
    for (const [id, text] of Object.entries(value)) await page.locator(`#paperstepCard [data-blank-id="${id}"]`).fill(text);
  } else await page.locator('#paperAnswer').fill(answer);
}
async function pick(index) {
  const title = page.locator('#paperstepCard .stepTitle').filter({hasText: new RegExp(`^习题 ${index + 1}$`)});
  if (await title.isVisible()) return;
  await page.locator(`[data-question-index="${index}"]`).click();
  await title.waitFor();
}
try {
  await go('/subjects/math/courses');
  const guest = await api('access/guest', {}); token = guest.identity_token;
  await api('training/bank?bank_id=test-review-math', undefined, 403);
  for (const who of ['standard', 'advanced_nonstudent', 'ordinary_student']) {
    await login(who); await go('/subjects/english/courses');
    assert.equal((await api('catalog?subject_id=english')).courses.length, 0);
    await api('training/bank?bank_id=test-review-math', undefined, 403);
    await api('state?topic_id=atomic-english&subject_id=english', undefined, 403);
  }
  pass('Guests, ordinary accounts, ordinary students and advanced nonstudents cannot see test materials');
  await login('student1'); await go('/subjects/math/reviews');
  let inbox = await api('review-papers/inbox?subject_id=math');
  assert.equal(inbox.due_count, 28);
  assert.equal(inbox.items.filter(i => i.kind === 'paper').length, 10);
  assert.equal(new Set(inbox.items.filter(i => i.kind === 'paper').map(i => i.paper_type_id)).size, 5);
  await page.locator('.portalTask').first().waitFor({state: 'attached'});
  for (let i = 0; i < 30 && await page.locator('details.courseUnitGroup:not([open]) > summary').count(); i++) {
    await page.locator('details.courseUnitGroup:not([open]) > summary').first().click();
  }
  await shot('student-review-ready');
  pass('Five paper types each provide two papers, with overlapping source points and single-card access');
  const selected = inbox.items.filter(i => i.kind === 'paper' && i.count === 14).at(-1);
  let opened = await openCandidate(selected), paper = opened;
  assert.equal(paper.mode, 'paper');
  const answers = (await api('answers?paper=' + paper.id, undefined, 200, true)).items;
  assert(answers.some(i => i.card_id.startsWith('variant-')));
  const variantIndex = answers.findIndex(i => i.card_id.startsWith('variant-'));
  const variant = paper.items[variantIndex];
  await pick(variantIndex);
  await page.locator('#paperstepCard .requestHelpButton').waitFor();
  const reportId = (await api('help-requests', {...variant.content_context, content_version: variant.content_version,
    content_block_id: 'question:' + variant.content_context.question_id, request_id: crypto.randomUUID(), message: '【浏览器验收】请解释这张变式卡。'})).id;
  assert.equal((await api('help-center?report_id=' + reportId)).items[0].content_html, variant.html);
  pass('Pre-generated Practice is shown in review and Q&A preserves the exact variant');
  for (let i = 0; i < paper.items.length; i++) {
    await pick(i);
    await inputAnswer(paper.items[i], answers[i].answer);
    if (i === 0) {
      for (let tries = 0; tries < 40 && !(await paperState(paper.id)).items[0].answer; tries++) await new Promise(r => setTimeout(r, 100));
      await page.reload({waitUntil: 'networkidle'}); await page.locator('#paperstepCard').waitFor();
      assert.equal((await paperState(paper.id)).items[0].answer, answers[0].answer);
    }
    if (i < paper.items.length - 1) {
      await page.locator('#paperSubmit').click();
      await page.locator('#paperstepCard .stepTitle').filter({hasText: new RegExp(`^习题 ${i + 2}$`)}).waitFor();
    }
  }
  await shot('paper-before-submit'); await page.locator('#paperSubmit').click();
  await page.locator('.completionBanner').waitFor({timeout: 120000}); paper = await settle(paper.id);
  assert.equal(paper.phase, 'done'); assert(paper.items.every(i => i.result.correct));
  assert.equal((await api('review-papers/inbox?subject_id=math')).due_count, 14);
  await api('review-papers/open?subject_id=math', {candidate_id: selected.id, request_id: crypto.randomUUID()}, 409);
  pass('Whole-paper submission, real typed controls, refresh recovery, overlap invalidation and rescheduling');
  await login('student2');
  await api('review-papers/preferences', {mode: 'question'});
  inbox = await api('review-papers/inbox?subject_id=math');
  paper = await openCandidate(inbox.items.find(i => i.kind === 'paper' && i.count === 8));
  const first = (await api('answers?paper=' + paper.id, undefined, 200, true)).items[0];
  await inputAnswer(paper.items[0], first.answer); await page.locator('#paperSubmit').click();
  for (let tries = 0; tries < 50; tries++) { paper = await settle(paper.id); if (paper.items[0].result) break; await new Promise(r => setTimeout(r, 100)); }
  assert.equal(paper.phase, 'answer'); assert.equal(paper.items[0].result.rating, 3);
  assert(paper.items.slice(1).every(i => !i.result));
  await api('review-papers/cancel', {paper_id: paper.id});
  await api('review-papers/preferences', {mode: 'paper'});
  pass('Advanced per-question mode releases only the completed point; cancelled remainder stays due');
  inbox = await api('review-papers/inbox?subject_id=english'); assert.equal(inbox.due_count, 3);
  paper = await openCandidate(inbox.items.find(i => i.point_count === 2), 'english');
  const bundleAnswer = (await api('answers?paper=' + paper.id, undefined, 200, true)).items[0];
  await inputAnswer(paper.items[0], bundleAnswer.answer); await page.locator('#paperSubmit').click();
  await page.locator('.completionBanner').waitFor(); paper = await settle(paper.id);
  assert.deepEqual(Object.values(paper.items[0].result.ratings), [3, 3]);
  pass('Atomic bundle grades one card and updates precisely its two memory points');
  await go('/subjects/chinese/topic/atomic-chinese');
  await page.getByRole('button', {name: '已理解，开始训练', exact: true}).waitFor(); await shot('atomic-initial');
  assert.equal((await api('review-papers/inbox?subject_id=biology')).due_count, 0);
  assert((await api('review-papers/inbox?subject_id=chemistry')).legacy.some(i => i.paused));
  pass('Initial atomic learning, future scheduling and paused-review presets remain distinct');
  await go('/subjects/math/qa'); await page.locator('.helpRequestList').waitFor();
  assert.equal((await api('help-center')).total, 12); await shot('student-qa');
  const ownReport = (await api('help-center')).items.find(i => !i.reply);
  await login('admin1');
  await api('help-center?report_id=' + ownReport.id, undefined, 404);
  await go('/subjects/math/qa'); await page.locator('.helpReplyForm').first().waitFor();
  assert.equal((await api('help-center')).total, 13);
  const field = page.locator('.helpReplyForm textarea').first(); await field.fill('【浏览器验收回复】已核对这张卡的条件和设问。');
  const replyResponse = page.waitForResponse(r => r.url().includes('/api/help-replies') && r.request().method() === 'POST');
  await page.locator('.helpReplyForm').first().locator('button[type="submit"]').click(); assert.equal((await replyResponse).status(), 200);
  await shot('admin-qa');
  await go('/admin/students'); await page.getByText('student1', {exact: true}).first().waitFor();
  await go('/admin/pauses'); const pauses = await api('admin/pauses');
  assert(pauses.items.every(i => i.learner_id === identities.student1.id));
  await shot('admin-pauses');
  await go('/subjects/english/reviews?materials=1');
  assert.equal((await api('review-papers/inbox?subject_id=english&materials=1')).due_count, 7);
  pass('Administrator can browse all atomic cards, inspect students, see owned pauses and reply; cross-pair access is denied');
  await login('student1'); const updated = await api('help-center'); assert(updated.unread_count > 0);
  const unread = updated.items.find(i => i.unread); assert(unread);
  const readResponse = page.waitForResponse(r => r.url().includes('/api/help-replies/read') && r.request().method() === 'POST');
  await go('/subjects/' + unread.subject_id + '/qa/' + unread.id);
  assert.equal((await readResponse).status(), 200);
  assert.equal((await api('help-center?report_id=' + unread.id)).items[0].unread, false);
  await page.setViewportSize({width: 390, height: 844}); await go('/subjects/math/qa');
  await page.locator('.helpRequestList').waitFor(); await shot('student-qa-mobile');
  pass('Student receives replies, marks the exact revision read, and can use the mobile Q&A page');
  await page.setViewportSize({width: 1365, height: 960});
  await login('student2');
  const semantic = await api('training/open?subject_id=math', {bank_id: 'test-review-math', group_id: 'paths',
    question_id: 'semantic', request_id: crypto.randomUUID()});
  const submission = await api('training/submit', {attempt_id: semantic.attempt_id, request_id: crypto.randomUUID(),
    answer: '正方形的四个角都是直角，符合矩形的定义，所以正方形是一种特殊的矩形。', elapsed_ms: 1500});
  let judged = submission;
  for (let i = 0; i < 240 && ['pending', 'running'].includes(judged.submission_status); i++) {
    await new Promise(r => setTimeout(r, 1000));
    judged = await api('training/state?attempt_id=' + semantic.attempt_id);
  }
  assert.equal(judged.result?.correct, true); assert.equal(judged.result.rating, 5);
  assert.equal(judged.stopped, true); assert.equal(judged.due_at, null); report.real_judge = true;
  pass('Real semantic judging gives initial correct answer knowledge five and stops further push');
  inbox = await api('review-papers/inbox?subject_id=math');
  paper = await openCandidate(inbox.items.find(i => i.kind === 'paper' && i.count === 3 && i.requirements.multiple));
  await api('advance', {seconds: 13 * 60}, 200, true);
  paper = await settle(paper.id); assert.equal(paper.phase, 'done'); assert(paper.items.every(i => i.result.correct === false));
  pass('Expired whole paper submits missing answers through the real review transaction');
  assert.equal(report.errors.length, 0, JSON.stringify(report.errors));
} catch (error) {
  report.failure = String(error.stack || error); console.error(report.failure); await shot('failure').catch(() => {}); process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close();
}
