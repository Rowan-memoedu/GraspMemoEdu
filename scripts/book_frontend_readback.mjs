/** Verify exported public assets and read-only pages using an existing test account. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const [exportDirectory, output, compiledBankPath] = process.argv.slice(2);
if (!exportDirectory || !output) throw new Error('Expected static export and evidence directories');
const base = 'https://rowan-memoedu.github.io/GraspMemoEdu-Pages/';
const api = 'https://124.221.144.155';
const origin = new URL(base).origin;
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
async function listFiles(directory, prefix = '') {
  const result = [];
  for (const entry of await fs.readdir(directory, {withFileTypes: true})) {
    const name = prefix + entry.name;
    if (entry.isDirectory()) result.push(...await listFiles(path.join(directory, entry.name), name + '/'));
    else if (entry.isFile()) result.push(name);
  }
  return result.sort();
}
const files = await listFiles(exportDirectory);
const expected = new Map(await Promise.all(files.map(async name => [name, digest(await fs.readFile(path.join(exportDirectory, name)))])));
let verified = false;
for (let attempt = 1; attempt <= 8; attempt++) {
  const failures = [];
  for (let offset = 0; offset < files.length; offset += 4) {
    await Promise.all(files.slice(offset, offset + 4).map(async name => {
      try {
        const response = await fetch(base + name + '?book-readback=' + expected.get(name).slice(0, 16), {signal: AbortSignal.timeout(30000)});
        if (!response.ok || digest(Buffer.from(await response.arrayBuffer())) !== expected.get(name)) failures.push(name);
      } catch { failures.push(name); }
    }));
  }
  if (!failures.length) { verified = true; break; }
  console.log(`Waiting for public assets: attempt ${attempt}, remaining ${failures.length}`);
  if (attempt < 8) await new Promise(resolve => setTimeout(resolve, 30000));
}
assert.equal(verified, true, 'Public assets still differ from the static export');
await fs.mkdir(output, {recursive: true});
const manifest = JSON.parse(await fs.readFile('D:/AppData/GraspMemoEdu/admin/invitations-initial-20260920.json', 'utf8'));
const account = manifest.accounts.find(row => row.purpose === 'test');
assert.ok(account, 'An existing test account is required');
let token = '', browser;
async function request(endpoint, body) {
  const response = await fetch(api + endpoint, {method: body === undefined ? 'GET' : 'POST',
    headers: {Origin: origin, 'Content-Type': 'application/json', ...(token ? {'X-Learning-Identity': token} : {})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}), signal: AbortSignal.timeout(30000)});
  assert.equal(response.status, 200, 'Public API request failed: ' + endpoint);
  return response.json();
}
try {
  const login = await request('/api/access/login', {invitation_code: account.invitation_code});
  token = login.identity_token;
  assert.equal(login.access.purpose, 'test');
  assert.equal(login.access.learner_id, account.learner_id);
  const subjects = (await request('/api/subjects')).subjects.map(subject => subject.id);
  assert.ok(subjects.length > 0, 'The public subject registry is empty');
  const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
  const {chromium} = require('playwright');
  browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
  const context = await browser.newContext({viewport: {width: 1365, height: 1000}, locale: 'zh-CN'});
  await context.addInitScript(({api, token}) => {
    localStorage.setItem('graspmemoedu:identity:' + api, token);
    localStorage.setItem('graspmemoedu:identity-role:' + api, 'account');
  }, {api, token});
  const page = await context.newPage(), errors = [], mutations = [];
  page.on('pageerror', error => errors.push(error.message));
  // This observation cannot create guests, open questions, submit answers or change preferences.
  await page.route(api + '/**', async route => {
    if (route.request().method() === 'POST') {
      mutations.push(new URL(route.request().url()).pathname);
      await route.abort();
    } else await route.continue();
  });
  const version = expected.get('index.html').slice(0, 16);
  await page.goto(base + '?v=' + version + '#/', {waitUntil: 'networkidle'});
  await page.locator('.subjectCard').first().waitFor();
  assert.deepEqual(await page.locator('.subjectCard').evaluateAll(cards => cards.map(card => card.dataset.subjectId)), subjects);
  await page.screenshot({path: path.join(output, 'public-homepage.png'), fullPage: true});
  await page.goto(base + '?v=' + version + '#/subjects/math/banks', {waitUntil: 'networkidle'});
  await page.locator('.portalPageTitle').waitFor();
  assert.equal(await page.locator('.portalPageTitle').textContent(), '题库');
  await page.screenshot({path: path.join(output, 'public-question-banks.png'), fullPage: true});
  let book = null;
  if (compiledBankPath) {
    const expectedBank = JSON.parse(await fs.readFile(compiledBankPath, 'utf8'));
    await page.goto(base + '?v=' + version + '#/subjects/math/banks/' + encodeURIComponent(expectedBank.id), {waitUntil: 'networkidle'});
    await page.locator('.incompleteTasks [data-task-id]').first().waitFor();
    const actual = await page.locator('.incompleteTasks').evaluate(root => {
      const nodes = [...root.querySelectorAll('[data-unit-id], [data-task-id]')];
      return nodes.map(element => ({id: element.dataset.unitId || element.dataset.taskId,
        parent_id: element.parentElement.closest('[data-unit-id]')?.dataset.unitId || null,
        title: element.querySelector(':scope > .courseUnitHeading, .taskTitle')?.textContent}));
    });
    const expectedTree = expectedBank.groups.map(({id, parent_id, title}) => ({id, parent_id, title}));
    assert.deepEqual(actual, expectedTree, 'Rendered book directory differs from its compiled source');
    await page.locator('.incompleteTasks details').evaluateAll(nodes => nodes.forEach(node => { node.open = true; }));
    await page.screenshot({path: path.join(output, 'public-book-directory.png'), fullPage: true});
    book = {id: expectedBank.id, groups: actual.length,
      questions: expectedBank.groups.reduce((n, group) => n + group.questions.length, 0), rendered_tree: actual};
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(mutations, []);
  await fs.writeFile(path.join(output, 'public-frontend-validation.json'), JSON.stringify({passed: true,
    assets_verified: [...expected].map(([name, sha256]) => ({name, sha256})), browser_errors: errors,
    learning_mutations: mutations, existing_test_account: true, homepage_subjects: subjects.length, subject_ids: subjects, book}, null, 2));
  console.log('PASS all public asset hashes, homepage, question-bank page and zero learning writes');
} finally {
  if (browser) await browser.close();
  if (token) await request('/api/access/logout', {});
}
