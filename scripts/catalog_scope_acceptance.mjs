import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, output, accountPath] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert(!['8765', '18768', ''].includes(new URL(base).port));
await fs.mkdir(output, {recursive: true});
const account = JSON.parse(await fs.readFile(accountPath, 'utf8'))[0];
const session = (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
let token;
async function api(route, body) {
  const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'Content-Type': 'application/json',
      ...(token ? {'X-Learning-Identity': token} : {})}, ...(body ? {body: JSON.stringify(body)} : {})});
  assert.equal(response.status, 200, route); return response.json();
}
token = (await api('access/login', {invitation_code: account.invitation_code})).identity_token;
const subjects = (await api('subjects')).subjects.map(s => s.id);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {subjects, checks: [], errors: [], synthetic_content: true, formal_data_opened: false};
const pass = text => { report.checks.push(text); console.log('PASS ' + text); };
let page;
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 960}, locale: 'zh-CN'});
  await context.addInitScript(({token, base}) => {
    if (location.origin !== base) return;
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
    localStorage.setItem('graspmemoedu:language', 'zh-CN');
  }, {token, base});
  page = await context.newPage(); page.on('pageerror', e => report.errors.push(e.message));
  const expandAncestors = locator => locator.evaluate(el => {
    for (let p = el.parentElement; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true;
  });
  async function go(hash, selector) {
    await page.goto(base + '/' + hash);
    await page.locator(selector).first().waitFor();
    await page.waitForFunction(() => !document.getElementById('portalContent')?.inert);
  }
  async function selected(title, bank = false) {
    await page.waitForFunction(({title, bank}) => {
      const link = document.querySelector('.courseNameLink');
      return link?.textContent === title && link.getAttribute('href').includes(bank ? '/banks/' : '/progress')
        && link.getAttribute('href').includes('root=language');
    }, {title, bank});
  }
  for (const subject of subjects) {
    const prefix = '#/subjects/' + subject;
    await go(prefix + '/courses', `[data-course-id="course-${subject}"]`);
    const entry = page.locator(`.courseGrid > [data-course-id="course-${subject}"]`);
    assert.equal(await entry.locator(':scope > .courseTools .courseProgressLink').count(), 1);
    assert.equal(await entry.locator(':scope > .courseTools .courseGraphButton').count(), 1);
    const java = entry.locator('[data-root-id="language"]');
    await expandAncestors(java);
    assert.equal(await java.locator('.courseProgressLink').count(), 1);
    assert.equal(await java.locator('.courseGraphButton').count(), 1);
    await java.locator('.courseLearn').click(); await selected('Java');
    assert.deepEqual(await page.locator('.incompleteTasks .courseUnitGroup').evaluateAll(rows => rows.map(r => r.dataset.unitId)), ['basics']);
    assert.equal(await page.locator('.incompleteTasks [data-task-id*="hyperskill"]').count(), 0);
    await page.locator('.coursePercent').click();
    await page.locator('.courseGraph [data-topic-id="lesson-' + subject + '"]').waitFor();
    assert.equal(await page.locator('.courseGraph [data-topic-id="hyperskill-' + subject + '"]').count(), 0);
    await page.locator('.graphDialog .iconButton').click();
    await page.reload(); await selected('Java');
    if (subject === 'cs') await page.screenshot({path: path.join(output, 'java-learning.png'), fullPage: true});
    pass(subject + ': course outline, scope, progress controls, graph and refresh');
    await go(prefix + '/banks', `[data-bank-id="bank-${subject}"]`);
    const bankJava = page.locator(`.courseGrid > [data-bank-id="bank-${subject}"] [data-root-id="language"]`);
    await expandAncestors(bankJava); await bankJava.locator('.courseLearn').click(); await selected('Java', true);
    assert.deepEqual(await page.locator('.incompleteTasks .courseUnitGroup').evaluateAll(rows => rows.map(r => r.dataset.unitId)), ['chapter']);
    assert.equal(await page.locator('.incompleteTasks [data-task-id="group-b"]').count(), 0);
    assert.equal(await page.locator('.taskListOptions > .textButton').count(), 0);
    await page.locator('.coursePercent').click();
    await page.locator(`.courseGraph [data-topic-id="group:bank-${subject}:group-a"]`).waitFor();
    assert.equal(await page.locator(`.courseGraph [data-topic-id="group:bank-${subject}:group-b"]`).count(), 0);
    await page.locator('.graphDialog .iconButton').click();
    await page.locator('.mainNavigation [data-navigation="guide"]').click();
    await page.locator('.guideBody').waitFor();
    await page.locator('.mainNavigation [data-navigation="banks"]').click(); await selected('Java', true);
    if (subject === 'cs') await page.screenshot({path: path.join(output, 'java-bank.png'), fullPage: true});
    pass(subject + ': bank outline, scope, percent graph and tab return');
  }
  // The shared header picker must select the same subtree, not silently reset it.
  await page.locator('#catalogToggle').click();
  const pickerJava = page.locator('#catalogPanel [data-course-id="course-cs"][data-root-id="language"]');
  await pickerJava.waitFor({state: 'attached'}); await expandAncestors(pickerJava); await pickerJava.click();
  await selected('Java'); pass('Header catalog selects the same course subtree');
  await page.locator('#catalogToggle').click();
  await pickerJava.waitFor({state: 'attached'});
  assert.equal(await page.locator('#catalogPanel .catalogCourse[aria-pressed="true"]').count(), 1);
  assert.equal(await pickerJava.getAttribute('aria-pressed'), 'true');
  await page.keyboard.press('Escape');
  await go('#/subjects/cs/courses', '.courseGrid > [data-course-id="course-cs"]');
  const scope = page.locator('.courseGrid [data-root-id="language"]'); await expandAncestors(scope);
  await scope.locator('.courseProgressLink').click();
  await page.locator('.progressPageHeading').waitFor();
  assert.equal(await page.locator('.progressPageHeading h1').textContent(), 'Java');
  assert(!(await page.locator('#portalContent').innerText()).includes('Another branch'));
  pass('Selection progress link retains the chosen scope');
  await go('#/subjects/cs/banks/bank-cs/group-b?root=sibling', '#trainingAnswer,.trainingFinish');
  if (await page.locator('#trainingAnswer').count()) {
    await page.locator('#trainingAnswer').fill('2'); await page.locator('#trainingSubmit').click();
  }
  await page.locator('.trainingFinish').waitFor(); await page.locator('.trainingFinish').click();
  await page.waitForFunction(() => location.hash === '#/subjects/cs/banks/bank-cs?groupId=group-b&root=sibling');
  await page.locator('.completedTasks [data-task-id="group-b"]').waitFor();
  assert.equal(await page.locator('.courseNameLink').textContent(), 'Another branch');
  assert.equal(await page.locator('.incompleteTasks [data-task-id="group-a"]').count(), 0);
  pass('Completing a group returns to its selected subtree and completed history');
  const original = await api('training/bank?subject_id=cs&bank_id=bank-cs');
  for (const implicit of [false, true]) {
    const data = structuredClone(original);
    for (const group of data.groups) {
      if (group.id === 'chapter') { group.id = data.id; if (implicit) group.parent_id = null; }
      if (group.parent_id === 'chapter') group.parent_id = data.id;
    }
    const pattern = '**/api/training/bank?*';
    await page.route(pattern, route => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(data)}));
    // Each presentation fixture gets a new document, bypassing the app's
    // intentional GET cache for the otherwise identical bank endpoint.
    await page.goto(base + '/?catalog-case=' + implicit + '#/subjects/cs/banks/bank-cs', {waitUntil: 'networkidle'});
    await page.waitForFunction(implicit => document.querySelector('.courseNameLink')?.getAttribute('href') === '#/subjects/cs/banks/bank-cs'
      && document.querySelectorAll('.incompleteTasks [data-unit-id="bank-cs"]').length === (implicit ? 0 : 1), implicit);
    assert.equal(await page.locator('.incompleteTasks [data-unit-id="bank-cs"]').count(), implicit ? 0 : 1);
    assert.equal(await page.locator('.incompleteTasks [data-task-id="group-a"]').count(), 1);
    await page.unroute(pattern);
  }
  pass('Only an actual bank storage root is hidden; a same-ID nested chapter is retained');
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({width, height: 960});
    for (const area of ['courses', 'banks']) {
      await go(`#/subjects/cs/${area}`, area === 'courses' ? '.courseGrid > [data-course-id="course-cs"]' : '.courseGrid > [data-bank-id="bank-cs"]');
      await page.locator('.catalogOutline details').evaluateAll(rows => rows.forEach(r => r.open = true));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${area} overflow at ${width}`);
      const controls = await page.locator('.catalogOutline .textButton,.catalogOutline .primaryButton').evaluateAll(rows => rows.filter(r => r.offsetHeight).map(r => ({text:r.textContent,
        fits:r.scrollWidth <= r.clientWidth + 1})));
      assert(controls.every(c => c.fits), `${area} clipped control at ${width}`);
      await page.screenshot({path: path.join(output, `${area}-${width}.png`), fullPage: true});
      pass(`${area} outline at ${width}px`);
    }
  }
  assert.deepEqual(report.errors, []); report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = error.stack;
  if (page) await page.screenshot({path: path.join(output, 'failure.png'), fullPage: true});
  throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close();
}
