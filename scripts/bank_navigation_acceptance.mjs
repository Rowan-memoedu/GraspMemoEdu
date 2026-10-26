/** Real SPA navigation and API state in a disposable local fixture. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, output, accountPath] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert(!['8765', '18768', ''].includes(new URL(base).port));
await fs.mkdir(output, {recursive: true});
const accounts = JSON.parse(await fs.readFile(accountPath, 'utf8'));
const session = (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
let token;
async function api(route, body) {
  const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'Content-Type': 'application/json',
      ...(token ? {'X-Learning-Identity': token} : {})}, ...(body ? {body: JSON.stringify(body)} : {})});
  const data = await response.json();
  assert.equal(response.status, 200, route + ': ' + JSON.stringify(data)); return data;
}
token = (await api('access/login', {invitation_code: accounts[0].invitation_code})).identity_token;
const subjects = (await api('subjects')).subjects.map(s => s.id);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {subjects, checks: [], errors: [], synthetic_content: true, native_launches: 0, formal_data_opened: false};
const pass = message => { report.checks.push(message); console.log('PASS ' + message); };
let page;
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 900}, locale: 'zh-CN'});
  await context.addInitScript(({token, base}) => {
    if (location.origin !== base) return;
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
    localStorage.setItem('graspmemoedu:ui-language', 'zh-CN');
  }, {token, base});
  page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  await context.route(url => ['127.0.0.1', 'localhost'].includes(url.hostname) && url.origin !== base,
    route => route.abort());
  const hash = () => page.evaluate(() => location.hash);
  const nav = name => page.locator('.mainNavigation [data-navigation="' + name + '"]');
  async function go(route, selector) {
    await page.evaluate(route => new Promise(resolve => {
      if (location.hash === route) { resolve(); return; }
      addEventListener('hashchange', () => resolve(), {once: true}); location.hash = route;
    }), route);
    await page.waitForFunction(() => !document.getElementById('portalContent').inert);
    await page.locator(selector).first().waitFor({state: 'visible'});
    await page.waitForFunction(() => !document.querySelector('#portalContent:not([hidden]) > .portalLoading'));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  async function returnToBanks(expected, selector) {
    assert.equal(await nav('banks').getAttribute('href'), expected);
    await nav('banks').click();
    await page.waitForFunction(() => !document.getElementById('portalContent').inert);
    await page.locator(selector).first().waitFor({state: 'visible'});
    await page.waitForFunction(expected => location.hash === expected, expected);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  const lastBank = new Map();
  await page.goto(base, {waitUntil: 'networkidle'});
  for (const subject of subjects) {
    await api('catalog/select?subject_id=' + subject, {course_id: 'course-' + subject});
    for (const active of (await api('review-papers/inbox?subject_id=' + subject)).active)
      await api('review-papers/cancel?subject_id=' + subject, {paper_id: active.id});
    const prefix = '#/subjects/' + subject;
    await go(prefix + '/banks/bank-' + subject + '/group-a?question=exact', '#trainingAnswer');
    await page.locator('#trainingAnswer').fill('saved draft ' + subject);
    await page.evaluate(() => {
      window.scrollTo(0, 480); document.getElementById('traininghistoryPanel').scrollTop = 110;
    });
    const position = await page.evaluate(() => ({y: scrollY, sidebar: document.getElementById('traininghistoryPanel').scrollTop}));
    assert(position.y > 300 && position.sidebar > 50);
    const exactHash = await hash();
    await nav('guide').click(); await page.locator('.guideBody h1').waitFor();
    await returnToBanks(exactHash, '#trainingAnswer');
    assert.equal(await page.locator('#trainingAnswer').inputValue(), 'saved draft ' + subject);
    const restored = await page.evaluate(() => ({y: scrollY, sidebar: document.getElementById('traininghistoryPanel').scrollTop}));
    assert(Math.abs(position.y - restored.y) < 3, 'Body position ' + subject + JSON.stringify(restored));
    assert(Math.abs(position.sidebar - restored.sidebar) < 3, 'Sidebar position ' + subject);
    await page.locator('.trainingQuestionLink[data-question-id="essay"]').click();
    await page.waitForFunction(() => location.hash.endsWith('?question=essay'));
    await page.locator('#trainingAnswer').fill('second draft ' + subject);
    const selectedHash = await hash();
    await nav('courses').click(); await page.locator('.courseGrid .courseChoice').first().waitFor();
    await returnToBanks(selectedHash, '#trainingAnswer');
    assert.equal(await page.locator('#trainingAnswer').inputValue(), 'second draft ' + subject);
    await nav('banks').click();
    assert.equal(await hash(), selectedHash, 'Active tab keeps its reader');
    await page.locator('.trainingIntroductionLink').click();
    await page.waitForFunction(() => location.hash.includes('introduction=reading'));
    const introHash = await hash();
    await nav('reviews').click(); await page.locator('[data-review-kind="atomic"]').waitFor();
    await returnToBanks(introHash, '#trainingStepTitle');
    assert.equal(await page.locator('#trainingStepTitle').innerText(), 'Reading');
    await page.locator('#trainingtopicHomeLink').click();
    await page.locator('#portalContent .dashboardLayout').waitFor();
    const bankHash = await hash(); lastBank.set(subject, bankHash);
    await nav('guide').click(); await page.locator('.guideBody').waitFor();
    await returnToBanks(bankHash, '#portalContent .dashboardLayout');
    pass(subject + ': question, replaced selection, introduction, bank, drafts and scroll survive tab switches');

    for (const provider of ['exact', 'academy', 'hyperskill']) {
      const opened = await api('training/open?subject_id=' + subject, {
        request_id: crypto.randomUUID(), bank_id: 'bank-' + subject, group_id: 'group-a', question_id: provider});
      await api('training/notebook?subject_id=' + subject, {attempt_id: opened.attempt_id});
    }
    const inbox = await api('review-papers/inbox?subject_id=' + subject);
    assert.equal(inbox.due_count, 3);
    const entries = inbox.trees.flatMap(tree => tree.tasks);
    assert.equal(entries.length, 3);
    assert(entries.every(item => item.kind === 'single' && item.count === 1));
    for (const provider of ['exact', 'academy', 'hyperskill']) {
      await go(prefix + '/reviews', '[data-review-kind="atomic"]');
      assert.equal(await page.locator('[data-review-kind="training"]').count(), 0);
      assert.equal(await page.getByRole('heading', {name: '原子卡片', exact: true}).count(), 1);
      const title = provider === 'exact' ? '隔离习题 exact' : provider === 'academy' ? 'IDE card' : 'Hyperskill card';
      const card = page.locator('[data-review-kind="atomic"] .portalTask').filter({has: page.locator('.taskTitle', {hasText: title})});
      assert.equal(await card.count(), 1);
      await card.locator('.taskToggle').click();
      await card.getByRole('button', {name: '开始复习', exact: true}).click();
      const reader = provider === 'exact' ? 'paper' : 'training';
      await page.locator('#' + reader + 'stepCard').waitFor();
      await page.waitForFunction(() => document.querySelector('[data-question-index]'));
      if (reader === 'training') await page.waitForFunction(() =>
        new URLSearchParams(location.hash.split('?')[1]).has('attempt') && document.getElementById('trainingStepTitle'));
      assert.equal(await page.locator('#' + reader + 'historyPanel [data-question-index]').count(), 1);
      assert.equal(await page.locator('.trainingQuestionLink').count(), 0);
      assert.equal(await nav('reviews').getAttribute('aria-current'), 'page');
      assert.equal(await page.locator('#' + reader + 'topicHomeLink').getAttribute('href'), prefix + '/reviews');
      const openedHash = await hash();
      await page.reload({waitUntil: 'networkidle'});
      await page.locator('#' + reader + 'stepCard').waitFor();
      if (reader === 'training') await page.locator('#trainingStepTitle').waitFor();
      assert.equal(await hash(), openedHash, 'Reload reuses the same attempt');
      assert.equal(await page.locator('#' + reader + 'historyPanel [data-question-index]').count(), 1);
      // Reload intentionally starts a new in-memory navigation history.
      await go(bankHash, '#portalContent .dashboardLayout');
      await go(openedHash, '#' + reader + 'stepCard');
      await returnToBanks(bankHash, '#portalContent .dashboardLayout');
      pass(subject + '/' + provider + ': one review card, shared navigation, repeat load without a new attempt');
      if (subject === 'cs' && provider === 'academy') {
        await go(openedHash, '#trainingstepCard');
        await page.screenshot({path: path.join(output, 'cs-single-review.png'), fullPage: false});
        for (const width of [320, 375, 414, 768]) {
          await page.setViewportSize({width, height: 844});
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2), 'Overflow at ' + width);
          await page.screenshot({path: path.join(output, 'cs-single-review-' + width + '.png'), fullPage: false});
        }
        await page.setViewportSize({width: 1365, height: 900});
      }
    }
  }
  await go('#/subjects/cs/banks/bank-cs/group-a?question=extra-0&card=1', '#trainingAnswer');
  await page.locator('.trainingNotebookButton').click();
  await page.waitForFunction(() => document.querySelector('.trainingNotebookButton').disabled);
  await page.locator('#trainingAnswer').fill('2');
  await page.locator('#trainingSubmit').click();
  await page.locator('.trainingVerdict').waitFor();
  assert.equal(await page.locator('#traininghistoryPanel [data-question-index]').count(), 1);
  const finished = await hash();
  await page.reload({waitUntil: 'networkidle'});
  await page.locator('.trainingVerdict').waitFor();
  assert.equal(await hash(), finished);
  assert.equal(await page.locator('#traininghistoryPanel [data-question-index]').count(), 1);
  pass('Completed card refresh keeps one card and the same graded attempt');
  await go('#/subjects/math/banks/bank-math/group-a?question=essay', '#trainingAnswer');
  const mathHash = await hash();
  await go('#/subjects/cs/banks/bank-cs/group-b?question=essay-b', '#trainingAnswer');
  const csHash = await hash();
  await go('#/subjects/math/guide', '.guideBody h1');
  await returnToBanks(mathHash, '#trainingAnswer');
  await go('#/subjects/cs/guide', '.guideBody h1');
  await returnToBanks(csHash, '#trainingAnswer');
  pass('Subject positions are independent');
  await go('#/subjects/cs/guide', '.guideBody h1');
  await page.locator('#userMenuButton').hover();
  await page.locator('#logoutButton').click();
  await page.waitForFunction(() => document.getElementById('logoutButton').hidden);
  await go('#/subjects/cs/guide', '.guideBody h1');
  assert.equal(await nav('banks').getAttribute('href'), '#/subjects/cs/banks');
  pass('Identity switch clears prior navigation positions');
  assert.deepEqual(report.errors, []);
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({...report, status: 'passed'}, null, 2));
} catch (error) {
  if (page) {
    await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
    await fs.writeFile(path.join(output, 'failure.json'), JSON.stringify({message: error.message, url: page.url(),
      text: await page.locator('body').innerText(), ...report}, null, 2));
  }
  throw error;
} finally {
  await browser.close();
  await api('access/logout', {}).catch(() => {});
}
