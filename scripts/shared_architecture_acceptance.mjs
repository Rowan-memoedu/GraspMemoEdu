/** Read-only page inspection plus API setup in a disposable synthetic fixture. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, output, accountPath] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert(!['8765', '18768', ''].includes(new URL(base).port));
await fs.mkdir(output, {recursive: true});
const session = (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
let token;
async function api(route, body) {
  const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'Content-Type': 'application/json',
      ...(token ? {'X-Learning-Identity': token} : {})}, ...(body ? {body: JSON.stringify(body)} : {})});
  assert.equal(response.status, 200, route);
  return response.json();
}
const account = JSON.parse(await fs.readFile(accountPath, 'utf8'));
token = (await api('access/login', {invitation_code: account.invitation_code})).identity_token;
const subjects = (await api('subjects')).subjects.map(s => s.id);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {subjects, pages: [], errors: [], synthetic_content: true, native_launches: 0, formal_data_opened: false};
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 960}, locale: 'zh-CN'});
  await context.addInitScript(({token, base}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
    localStorage.setItem('graspmemoedu:language', 'zh-CN');
  }, {token, base});
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  const baseline = new Map();
  for (const subject of subjects) {
    await api('catalog/select?subject_id=' + subject, {course_id: 'course-' + subject});
    const pages = [
      ['courses', 'courses'], ['learn', 'learn'], ['banks', 'banks'],
      ['bank', 'banks/bank-' + subject],
      ['question', 'banks/bank-' + subject + '/group-a?question=exact'],
      ['reviews', 'reviews'], ['guide', 'guide'],
      ...['lesson', 'atomic', 'composed', 'academy', 'hyperskill'].map(kind => [kind, 'topic/' + kind + '-' + subject]),
      ['qa', 'qa'], ['settings', 'settings'], ['progress', 'progress'],
    ];
    for (const [kind, route] of pages) {
      console.log('Inspect ' + subject + '/' + kind);
      await page.goto(base + '/#/subjects/' + subject + '/' + route, {waitUntil: 'networkidle'});
      try {
        const ready = {
          courses: '.courseGrid .courseChoice', learn: '.dashboardLayout .portalTask',
          banks: '.courseGrid .courseChoice', bank: '.dashboardLayout .portalTask',
          question: '#trainingAnswer', reviews: '[data-review-kind]',
          guide: '.guideBody h1', qa: '.helpRequestList', settings: '.profileForm',
          progress: '.studentOverview',
        }[kind];
        if (ready) await page.locator(ready).first().waitFor({state: 'visible'});
        else await page.getByRole('heading', {name: 'Matrix ' + kind, exact: true}).waitFor({state: 'visible'});
        await page.waitForFunction(() => ![...document.querySelectorAll('.portalLoading, .spinner')]
          .some(e => e.getClientRects().length && !e.closest('[hidden]')));
      } catch (error) {
        await page.screenshot({path: path.join(output, 'failed-' + subject + '-' + kind + '.png'), fullPage: true});
        await fs.writeFile(path.join(output, 'failure.json'), JSON.stringify({
          subject, kind, url: page.url(), text: await page.locator('body').innerText(), errors: report.errors,
        }, null, 2));
        throw error;
      }
      assert.equal(await page.locator('.portalError, .trainingError').count(), 0, `${subject}/${kind}`);
      const shape = await page.evaluate(() => {
        const root = [...document.querySelectorAll('#portalContent, main.lesson')]
          .find(e => e.getClientRects().length && !e.closest('[hidden]'));
        return [...root.querySelectorAll('*')].filter(e => e.getClientRects().length && !e.closest('[hidden]'))
          // CodeMirror allocates a fresh generated style ID for each editor instance.
          .map(e => [e.tagName, [...e.classList].filter(c => !c.startsWith('ͼ')).join(' ')].join(':'));
      });
      assert(shape.length, `${subject}/${kind} rendered`);
      // Progress grows as this one fixture identity visits additional subjects.
      if (kind !== 'progress') {
        if (!baseline.has(kind)) baseline.set(kind, shape);
        else assert.deepEqual(shape, baseline.get(kind), `${subject}/${kind} uses another page structure`);
      }
      if (['math', 'cs'].includes(subject))
        await page.screenshot({path: path.join(output, subject + '-' + kind + '.png'), fullPage: true});
      report.pages.push({subject, page: kind, nodes: shape.length, same_component: kind !== 'progress'});
    }
    console.log('PASS shared pages for ' + subject);
  }
  assert.deepEqual(report.errors, []);
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
} finally {
  await browser.close();
  await api('access/logout', {});
}
