/** Course/bank navigation in an isolated synthetic service; never production. */
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
  assert.equal(response.status, 200, route); return response.json();
}
token = (await api('access/login', {invitation_code: accounts[1].invitation_code})).identity_token;
const subjects = (await api('subjects')).subjects.map(subject => subject.id);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {subjects, checks: [], errors: [], synthetic_content: true, formal_data_opened: false};
const pass = message => { report.checks.push(message); console.log('PASS ' + message); };
let page;
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 900}, locale: 'zh-CN'});
  await context.addInitScript(({token, base}) => {
    if (location.origin !== base) return;
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
    if (!localStorage.getItem('graspmemoedu:ui-language')) localStorage.setItem('graspmemoedu:ui-language', 'zh-CN');
  }, {token, base});
  page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  const nav = section => page.locator('.mainNavigation [data-navigation="' + section + '"]');
  const hash = () => page.evaluate(() => location.hash);
  async function ready(selector) {
    await page.locator(selector).first().waitFor({state: 'visible'});
    await page.waitForFunction(() => !document.getElementById('portalContent').inert);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  }
  async function go(route, selector) {
    await page.goto(base + '/' + route, {waitUntil: 'networkidle'}); await ready(selector);
  }
  async function inspectReselect(kind, prefix) {
    const reselect = page.getByRole('link', {name: '重新选择主题', exact: true});
    assert.equal(await reselect.getAttribute('href'), prefix + '/' + kind);
    const circleBox = await page.locator('.coursePercent').boundingBox(), linkBox = await reselect.boundingBox();
    assert(linkBox.x >= circleBox.x + circleBox.width, 'Reselect sits right of percentage');
    assert(Math.abs(linkBox.y + linkBox.height / 2 - circleBox.y - circleBox.height / 2) < 2);
    assert.equal(await nav(kind).getAttribute('aria-current'), 'page');
  }
  const guest = await browser.newPage();
  await guest.goto(base + '/#/subjects/math/learn', {waitUntil: 'networkidle'});
  await guest.locator('.courseGrid .courseChoice').first().waitFor();
  assert.equal(await guest.evaluate(() => location.hash), '#/subjects/math/courses');
  await guest.close();
  pass('Legacy learning link without a selection opens the course directory');
  for (const subject of subjects) {
    const prefix = '#/subjects/' + subject;
    await go(prefix, '.courseGrid .courseChoice');
    assert.equal(await hash(), prefix + '/courses');
    assert.equal(await nav('learn').count(), 0);
    const rootCard = page.locator('.catalogOutlineRow[data-root-id="chapter"]');
    await rootCard.locator('.courseLearn').click();
    await ready('.dashboardLayout .portalTask');
    const selected = prefix + '/courses/course-' + subject + '?root=chapter';
    assert.equal(await hash(), selected);
    await inspectReselect('courses', prefix);
    await page.reload({waitUntil: 'networkidle'}); await ready('.dashboardLayout .portalTask');
    assert.equal(await hash(), selected);
    await nav('guide').click(); await ready('.guideBody h1');
    assert.equal(await nav('courses').getAttribute('href'), selected);
    await nav('courses').click(); await ready('.dashboardLayout .portalTask');
    const task = page.locator('.portalTask').filter({has: page.locator('.taskTitle', {hasText: 'Matrix lesson'})}).first();
    await task.locator('.taskToggle').click();
    await task.locator('.portalStart:not([disabled])').first().click();
    await ready('#lessonTitle');
    assert.equal(await nav('courses').getAttribute('aria-current'), 'page');
    assert.equal(await page.locator('#topicHomeLink').getAttribute('href'), selected);
    assert(new URLSearchParams((await hash()).split('?')[1]).get('course') === 'course-' + subject);
    await page.reload({waitUntil: 'networkidle'}); await ready('#lessonTitle');
    assert.equal(await page.locator('#topicHomeLink').getAttribute('href'), selected);
    await nav('guide').click(); await ready('.guideBody h1');
    await nav('courses').click(); await ready('#lessonTitle');
    await page.locator('#topicHomeLink').click(); await ready('.dashboardLayout .portalTask');
    await page.locator('.workspaceReselect').click(); await ready('.courseGrid .courseChoice');
    assert.equal(await hash(), prefix + '/courses');
    assert.equal((await api('catalog?subject_id=' + subject)).selected_course_id, 'course-' + subject);
    await page.goBack(); await ready('.dashboardLayout .portalTask');
    assert.equal(await hash(), selected);
    await page.goForward(); await ready('.courseGrid .courseChoice');
    await page.reload({waitUntil: 'networkidle'}); await ready('.courseGrid .courseChoice');
    await go(prefix + '/courses/course-' + subject, '.dashboardLayout .portalTask');
    assert.equal(await page.locator('.courseNameLink').textContent(), 'Matrix course');
    await go(prefix + '/learn?root=chapter&source=bookmark', '.dashboardLayout .portalTask');
    assert.equal(await hash(), selected + '&source=bookmark');
    pass(subject + ': directory, scoped course, reader return, tab restore, history and legacy compatibility');

    await go(prefix + '/banks', '.courseGrid .courseChoice');
    const bankScope = page.locator('.catalogOutlineRow .courseLearn').first();
    const bankHash = await bankScope.getAttribute('href');
    await bankScope.click(); await ready('.dashboardLayout .portalTask');
    assert.equal(await hash(), bankHash);
    await inspectReselect('banks', prefix);
    await nav('guide').click(); await ready('.guideBody h1');
    assert.equal(await nav('banks').getAttribute('href'), bankHash);
    await nav('banks').click(); await ready('.dashboardLayout .portalTask');
    await page.locator('.workspaceReselect').click(); await ready('.courseGrid .courseChoice');
    assert.equal(await hash(), prefix + '/banks');
    await nav('guide').click(); await ready('.guideBody h1');
    await nav('banks').click(); await ready('.courseGrid .courseChoice');
    assert.equal(await hash(), prefix + '/banks');
    pass(subject + ': bank scope uses the same workspace and explicit reselect clears its old destination');
  }
  for (const kind of ['courses', 'banks']) {
    const route = '#/subjects/english/' + kind + '/' + (kind === 'courses' ? 'course-english?root=chapter' : 'bank-english');
    await go(route, '.dashboardLayout .portalTask');
    await page.screenshot({path: path.join(output, kind + '-desktop.png'), fullPage: false});
    for (const language of ['zh-CN', 'en']) {
      await page.evaluate(language => localStorage.setItem('graspmemoedu:ui-language', language), language);
      await page.reload({waitUntil: 'networkidle'}); await ready('.dashboardLayout .portalTask');
      for (const width of [320, 375, 414, 768]) {
        await page.setViewportSize({width, height: 844});
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2), kind + ': overflow at ' + width);
        const link = page.locator('.workspaceReselect'), box = await link.boundingBox();
        assert(box.x >= 0 && box.x + box.width <= width);
        assert.equal(await link.innerText(), language === 'en' ? 'Choose another topic' : '重新选择主题');
        assert.equal(await link.evaluate(element => {
          const range = document.createRange(); range.selectNodeContents(element); return range.getClientRects().length;
        }), 1, 'Link text stays on one line');
        await page.screenshot({path: path.join(output, kind + '-' + language + '-' + width + '.png'), fullPage: false});
      }
    }
    await page.setViewportSize({width: 1365, height: 900});
    await page.evaluate(() => localStorage.setItem('graspmemoedu:ui-language', 'zh-CN'));
  }
  pass('Both workspaces fit desktop and 320/375/414/768 widths in Chinese and English');
  await go('#/subjects/math/courses', '.courseGrid .courseChoice');
  await page.route('**/api/catalog/select?*', route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({detail: 'Selection unavailable'})}));
  await page.locator('.courseLearn').first().click(); await ready('.courseChoice .fieldError');
  assert.equal(await hash(), '#/subjects/math/courses');
  assert.equal(await page.locator('.courseLearn').first().isEnabled(), true);
  await page.unroute('**/api/catalog/select?*');
  await page.locator('.courseLearn').first().click(); await ready('.dashboardLayout .portalTask');
  pass('Failed selection stays in the directory with an error and supports retry');
  assert.deepEqual(report.errors, []);
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({...report, status: 'passed'}, null, 2));
} catch (error) {
  if (page) {
    await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
    await fs.writeFile(path.join(output, 'failure.json'), JSON.stringify({message: error.message,
      url: page.url(), text: await page.locator('body').innerText(), ...report}, null, 2));
  }
  throw error;
} finally {
  await browser.close();
  await api('access/logout', {}).catch(() => {});
}
