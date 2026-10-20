// Real browser, real isolated API, deterministic judge. No formal learner data.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, output, accountPath] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert(!['8765', '18768'].includes(new URL(base).port));
await fs.mkdir(output, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const account = JSON.parse(await fs.readFile(accountPath, 'utf8'));
const session = (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
const headers = {Origin: base, 'X-Learning-Session': session, 'Content-Type': 'application/json'};
const login = await (await fetch(base + '/api/access/login', {method: 'POST', headers,
  body: JSON.stringify({invitation_code: account.invitation_code})})).json();
headers['X-Learning-Identity'] = login.identity_token;
const subjects = (await (await fetch(base + '/api/subjects', {headers})).json()).subjects.map(s => s.id);
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {checks: {}, errors: [], subjects: []};
let page;
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 960}});
  await context.addInitScript(({base, token}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
  }, {base, token: login.identity_token});
  page = await context.newPage();
  const requests = [];
  page.on('pageerror', e => report.errors.push(e.message));
  page.on('request', r => { if (r.url().includes('/api/')) requests.push({url: r.url(), method: r.method()}); });
  await page.goto(base + '/#/subjects/math/topic/lesson-math');
  await page.getByRole('button', {name: '未掌握，先学习', exact: true}).waitFor();
  const choiceResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/choose');
  await page.getByRole('button', {name: '未掌握，先学习', exact: true}).click();
  const choice = await (await choiceResponse).json();
  assert.equal(choice.$gme_content, 1);
  assert.equal(choice.texts, null);
  await page.getByText('已选择未掌握。点击“继续学习”，阅读相关指导。', {exact: true}).waitFor();
  report.checks.mutation_returns_fresh_state_without_repeating_text = true;
  const continueResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/continue');
  await page.getByRole('button', {name: '继续学习', exact: true}).click();
  const continued = await (await continueResponse).json();
  assert.equal(continued.$gme_content, 1);
  assert(continued.texts.some(text => text?.$gme_previous !== undefined));
  assert(continued.texts.some(text => typeof text === 'string'));
  assert.equal(requests.filter(r => r.method === 'POST' && new URL(r.url).pathname === '/api/choose').length, 1);
  assert.equal(requests.filter(r => r.method === 'POST' && new URL(r.url).pathname === '/api/continue').length, 1);
  report.checks.next_page_transfers_only_new_blocks_and_no_write_replay = true;
  for (const subject of subjects) {
    await page.evaluate(async hash => {location.hash = hash; await new Promise(requestAnimationFrame);},
      '#/subjects/' + subject + '/topic/lesson-' + subject);
    await page.waitForFunction(subject => location.hash.includes('/' + subject + '/topic/lesson-' + subject)
      && document.querySelector('#courseShell:not([hidden])')
      && !document.querySelector('#portalContent[aria-busy="true"]'), subject);
    const before = requests.length;
    await page.reload({waitUntil: 'domcontentloaded'});
    await page.locator('#courseShell:not([hidden])').waitFor();
    const reads = requests.slice(before).filter(r => new URL(r.url).pathname === '/api/state');
    assert.equal(reads.length, 1, subject + ' must not duplicate its initial state request');
    assert.notEqual(new URL(reads[0].url).searchParams.get('content_revision'), '1', subject + ' disk cache survives reload');
    report.subjects.push(subject);
  }
  report.checks.all_subjects_share_persistent_content_and_single_state_read = true;
  await page.evaluate(() => new Promise((resolve, reject) => {
    const request = indexedDB.open('graspmemoedu-content-v1', 1);
    request.onerror = reject;
    request.onsuccess = () => {
      const tx = request.result.transaction('content', 'readwrite'), store = tx.objectStore('content');
      const all = store.getAll();
      all.onsuccess = () => { for (const row of all.result) store.put({...row, texts: []}); };
      tx.oncomplete = resolve; tx.onerror = reject;
    };
  }));
  const beforeRepair = requests.length;
  await page.reload({waitUntil: 'domcontentloaded'});
  await page.locator('#courseShell:not([hidden])').waitFor();
  const repaired = requests.slice(beforeRepair).filter(r => new URL(r.url).pathname === '/api/state');
  assert.equal(repaired.length, 1);
  assert.equal(new URL(repaired[0].url).searchParams.get('content_revision'), '1');
  report.checks.corrupt_disk_cache_recovers_with_one_full_read = true;
  await page.locator('#userMenuButton').click();
  await page.locator('#logoutButton').click();
  await page.waitForFunction(() => document.querySelector('#menuRole')?.textContent.includes('游客'));
  const retained = await page.evaluate(learner => new Promise(resolve => {
    const request = indexedDB.open('graspmemoedu-content-v1', 1);
    request.onsuccess = () => {
      const all = request.result.transaction('content').objectStore('content').getAll();
      all.onsuccess = () => resolve(all.result.some(row => row.key.includes(learner)));
    };
  }), login.access.learner_id);
  assert.equal(retained, false);
  report.checks.logout_clears_previous_learner_content = true;
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.failure = error.message;
  if (page) {
    await page.screenshot({path: path.join(output, 'failure.png')}).catch(() => {});
    await fs.writeFile(path.join(output, 'failure.txt'), await page.locator('body').innerText()).catch(() => {});
  }
  throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify(report, null, 2));
