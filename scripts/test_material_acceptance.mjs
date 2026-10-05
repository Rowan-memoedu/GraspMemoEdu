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
const page = await browser.newPage({viewport: {width: 1365, height: 950}, locale: 'zh-CN'});
page.setDefaultTimeout(20000);
const report = {checks: [], errors: [], formal_data_opened: false}; let token, session;
page.on('pageerror', error => report.errors.push(error.message));
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
const go = route => page.goto(base + '#' + route, {waitUntil: 'networkidle'});
async function login(who) {
  await page.locator('#userMenuButton').hover(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities[who]); await page.locator('#identitySubmitButton').click();
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
}
async function read(route) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + route, {headers: {'X-Learning-Identity': token, 'X-Learning-Session': session, Origin: base}});
  return {status: response.status, data: await response.json()};
}
function pass(text) { report.checks.push(text); console.log('PASS ' + text); }
try {
  await go('/subjects/math/courses'); await login('standard'); await go('/subjects/math/courses');
  assert.equal((await read('/api/catalog?subject_id=math')).data.courses.length, 1);
  assert.equal(await page.getByText('管理员测试课程', {exact: true}).count(), 0);
  assert.equal((await read('/api/training/bank?bank_id=isolated-bank')).status, 403);
  assert.equal((await read('/api/guide?subject_id=math')).status, 404);
  await page.screenshot({path: path.join(output, 'standard-catalog.png'), fullPage: true});
  pass('Standard test-purpose account cannot see or directly open test materials');
  await login('admin'); await go('/subjects/math/courses');
  assert.equal((await read('/api/catalog?subject_id=math')).data.courses.length, 2);
  await page.getByText('管理员测试课程', {exact: true}).first().waitFor();
  assert.equal((await read('/api/training/bank?bank_id=isolated-bank')).status, 200);
  await page.screenshot({path: path.join(output, 'admin-catalog.png'), fullPage: true});
  pass('Administrator sees retained test courses and banks');
  await go('/subjects/math/guide');
  await page.getByRole('heading', {name: '管理员测试指南', exact: true}).waitFor();
  const attachment = page.getByRole('link', {name: '测试附件', exact: true});
  await attachment.waitFor(); assert((await attachment.getAttribute('href')).startsWith('blob:'));
  await page.screenshot({path: path.join(output, 'admin-test-guide.png'), fullPage: true});
  pass('Protected mathematics guide and attachment load through authenticated transport');
  assert.deepEqual(report.errors, []);
} catch (error) { report.failure = String(error.stack || error); throw error; }
finally { await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close(); }
