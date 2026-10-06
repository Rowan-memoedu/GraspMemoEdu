import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const [base, artifactRoot, publicBase] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const auth = JSON.parse(await fs.readFile(path.join(artifactRoot, 'auth.private.json'), 'utf8'));
const out = path.join(artifactRoot, publicBase ? 'published-browser' : 'browser'); await fs.mkdir(out, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const errors = [], checks = [];
const pass = name => { checks.push(name); console.log('PASS ' + name); };
const snapshot = async () => (await (await fetch(base + '/qa/snapshot')).json()).hash;
let active;
async function pageFor(role, mobile = false) {
  const context = await browser.newContext({viewport: mobile ? {width: 390, height: 844} : {width: 1440, height: 1000}, locale: 'zh-CN', isMobile: mobile, hasTouch: mobile});
  await context.addInitScript(({base, token, role}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, role);
  }, {base, token: auth[role].identity_token, role: auth[role].access.role});
  const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => errors.push(error.message));
  if (publicBase) {
    // Published static UI is exercised against isolated records, never production learners.
    await page.route('**/deployment.json*', route => route.fulfill({json: {api_origin: ''}}));
    await page.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url());
      const headers = {...request.headers()}; delete headers.host; delete headers.origin; delete headers.referer;
      if (headers['x-learning-identity']) headers['x-learning-identity'] = auth[role].identity_token;
      else if (!url.pathname.endsWith('/session')) headers['x-learning-identity'] = auth[role].identity_token;
      const response = await fetch(base + url.pathname + url.search, {method: request.method(), headers,
        body: ['GET', 'HEAD'].includes(request.method()) ? undefined : request.postData()});
      await route.fulfill({status: response.status, contentType: response.headers.get('content-type') || 'application/json', body: Buffer.from(await response.arrayBuffer())});
    });
    await context.addInitScript(({origin, token, role}) => {
      localStorage.setItem('graspmemoedu:identity:' + origin, token); localStorage.setItem('graspmemoedu:identity-role:' + origin, role);
    }, {origin: new URL(publicBase).origin, token: auth[role].identity_token, role: auth[role].access.role});
  }
  return page;
}
const url = learner => (publicBase || base + '/') + '#/admin/students?learner=' + encodeURIComponent(learner);
try {
  const before = await snapshot();
  const page = active = await pageFor('admin');
  await page.goto(url(auth.target)); await page.locator('.studentDashboard').waitFor();
  await page.getByRole('tab', {name: '概览', exact: true}).waitFor();
  assert.equal(await page.getByRole('tab').count(), 5);
  assert.equal(await page.locator('.studentHeatCell').count(), 30);
  assert.equal(await page.locator('.studentMetric').count(), 8);
  await page.locator('.studentHeatCell').last().click();
  assert.match(await page.locator('.studentCharts').textContent(), /作答.*次/);
  await page.locator('.studentCalendarDay.hasReviews').first().click();
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({path: path.join(out, 'overview-desktop.png'), fullPage: true});
  pass('Overview metrics, activity heatmap, accuracy trend and date-specific review calendar');

  await page.getByRole('tab', {name: '全部 Topic', exact: true}).click();
  assert.equal(await page.locator('.studentTopic').count(), 2);
  const topic = page.locator('.studentTopic[data-topic-id=synthetic-course]');
  await topic.locator(':scope > summary').click();
  assert.match(await topic.innerText(), /1\s*\/\s*2/);
  assert.match(await topic.innerText(), /尚未学习/);
  await topic.locator('summary').filter({hasText: '题目与作答记录'}).click();
  await topic.locator('.studentQuestionRow').first().waitFor();
  assert.equal(await topic.locator('.studentQuestionRow').count(), 30);
  await topic.getByRole('button', {name: '加载更多', exact: true}).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-topic-id=synthetic-course] .studentQuestionRow').length === 32);
  await page.screenshot({path: path.join(out, 'topics-desktop.png'), fullPage: true});
  pass('Complete Topic tree, unstarted modules, spare Practice questions and paginated drilldown');

  await page.getByRole('tab', {name: '题库', exact: true}).click();
  await page.locator('.studentBank > summary').click();
  for (const summary of await page.locator('.studentBank .courseUnitGroup > summary').all()) {
    if (!(await summary.evaluate(node => node.parentElement.open))) await summary.click();
  }
  await page.locator('.studentBankGroup > summary').filter({hasText: '第一题组'}).click();
  const exact = page.locator('.studentBank .studentQuestionRow').filter({has: page.getByRole('heading', {name: '隔离习题 exact', exact: true})});
  await exact.waitFor(); await exact.locator('.studentAnswerHistory > summary').click();
  await exact.locator('.studentAnswerCard').first().waitFor();
  assert.equal(await exact.locator('.studentAnswerCard').count(), 3);
  assert.match(await exact.innerText(), /系统判错 2 次/);
  assert.match(await exact.innerText(), /学员提交的答案/);
  assert.match(await exact.innerText(), /一加一等于二/);
  await page.screenshot({path: path.join(out, 'bank-history-desktop.png'), fullPage: true});
  pass('Bank hierarchy and original answers, verdicts, explanations and error counts');

  await page.getByRole('tab', {name: '错题本', exact: true}).click();
  const notebook = page.getByRole('tabpanel', {name: '错题本', exact: true});
  await notebook.locator('.studentQuestionRow').first().waitFor();
  await notebook.getByLabel('题目状态筛选', {exact: true}).selectOption('unanswered');
  await notebook.getByText('共 1 道题', {exact: true}).waitFor();
  assert.match(await notebook.innerText(), /隔离习题 blank/);
  await notebook.getByLabel('题目状态筛选', {exact: true}).selectOption('all');
  await notebook.getByText('共 4 道题', {exact: true}).waitFor();
  const essay = notebook.locator('.studentQuestionRow').filter({has: page.getByRole('heading', {name: '隔离习题 essay', exact: true})});
  await essay.locator('.studentAnswerHistory > summary').click();
  await essay.getByText('自主评分：2', {exact: true}).waitFor();
  pass('Notebook includes manually enrolled untrained questions and separate self-assessment records');

  await page.getByRole('tab', {name: '全部复习排程', exact: true}).click();
  await page.locator('.studentSchedule .courseChoice').first().waitFor();
  assert.equal(await page.locator('.studentAtomicCard').count(), 7);
  await page.getByLabel('复习排程筛选', {exact: true}).selectOption('future');
  assert.ok(await page.locator('.studentSchedule .courseChoice').count() >= 1);
  await page.getByRole('tab', {name: '全部复习排程', exact: true}).press('Home');
  assert.equal(await page.getByRole('tab', {name: '概览', exact: true}).getAttribute('aria-selected'), 'true');
  await page.getByLabel('统计时间范围', {exact: true}).selectOption('7');
  await page.waitForFunction(() => document.querySelectorAll('.studentHeatCell').length === 7);
  pass('Existing schedules and atomic variants retained; tabs support keyboard; activity range refreshes');

  const mobile = active = await pageFor('admin', true); await mobile.goto(url(auth.target));
  await mobile.locator('.studentDashboard').waitFor();
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await mobile.screenshot({path: path.join(out, 'overview-mobile.png'), fullPage: true});
  await mobile.getByRole('tab', {name: '错题本', exact: true}).click();
  const mobileNotebook = mobile.getByRole('tabpanel', {name: '错题本', exact: true});
  await mobileNotebook.locator('.studentQuestionRow').first().waitFor();
  await mobileNotebook.locator('.studentAnswerHistory > summary').first().click();
  await mobileNotebook.locator('.studentAnswerCard').first().waitFor();
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await mobile.screenshot({path: path.join(out, 'notebook-mobile.png'), fullPage: true});
  pass('Mobile report and submitted-answer display fit 390px without page overflow');

  const ordinary = active = await pageFor('ordinary'); await ordinary.goto(url(auth.target));
  await ordinary.getByText('此页面仅供管理员使用。', {exact: true}).waitFor();
  assert.equal(await ordinary.locator('.studentDashboard').count(), 0);
  await page.goto(url(auth.empty_target)); await page.locator('.studentDashboard').waitFor();
  assert.match(await page.locator('.studentMetrics').first().innerText(), /0\s*\/\s*2/);
  assert.match(await page.locator('.studentCharts').innerText(), /暂无系统判题/);
  assert.equal(await snapshot(), before); assert.deepEqual(errors, []);
  pass('Disabled/unstarted student, ordinary-user denial, zero browser errors and unchanged learner database');
} catch (error) {
  if (active) await active.screenshot({path: path.join(out, 'failure.png'), fullPage: true}).catch(() => {});
  throw error;
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify({checks, errors}, null, 2)); await browser.close();
}
