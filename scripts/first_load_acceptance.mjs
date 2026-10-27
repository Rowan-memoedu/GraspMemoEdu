// Isolated real API checks: delayed scripts/profile/catalog must not add serial waits.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const [base, output, accountPath, previousExport] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert(!['8765', '18768'].includes(new URL(base).port));
await fs.mkdir(output, {recursive: true});
const account = JSON.parse(await fs.readFile(accountPath, 'utf8'));
const session = await fetch(base + '/api/session', {headers: {Origin: base}}).then(r => r.json());
const login = await fetch(base + '/api/access/login', {method: 'POST', headers: {'Content-Type': 'application/json', Origin: base, 'X-Learning-Session': session.session_token},
  body: JSON.stringify({invitation_code: account.invitation_code})}).then(r => r.json());
assert(login.identity_token);
const browser = await chromium.launch({channel: 'chrome', headless: true});
const report = {checks: {}, timings: [], errors: []};
const gate = () => { let release; const promise = new Promise(resolve => {release = resolve;}); return {promise, release}; };
const ready = (page, selector) => page.waitForFunction(selector => {
  const element = document.querySelector(selector);
  return element?.getClientRects().length && !element.closest('[hidden], [inert]') && !document.querySelector('#portalContent[aria-busy="true"]');
}, selector, {timeout: 20000});
async function context() {
  const result = await browser.newContext({viewport: {width: 1365, height: 900}});
  await result.addInitScript(({base, token}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
  }, {base, token: login.identity_token});
  return result;
}
try {
  // Holding app.js proves the HTML discovers its shared dependencies before execution.
  const first = await context(), appGate = gate(), catalogGate = gate();
  const page = await first.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  const requests = [];
  page.on('request', r => requests.push({url: new URL(r.url()).pathname, method: r.method()}));
  await first.route('**/app.js?*', async route => {await appGate.promise; await route.continue();});
  await first.route('**/api/catalog?*', async route => {await catalogGate.promise; await route.continue();});
  const trainingLoaded = page.waitForResponse(r => new URL(r.url()).pathname === '/training.js');
  const subjectLoaded = page.waitForResponse(r => new URL(r.url()).pathname === '/subjects.js');
  await page.goto(base + '/#/subjects/math/courses', {waitUntil: 'commit'});
  try {
    await Promise.all([trainingLoaded, subjectLoaded]);
    report.checks.shared_modules_load_before_app_execution = true;
    const savedTimezone = page.waitForResponse(r => new URL(r.url()).pathname === '/api/profile' && r.request().method() === 'POST');
    appGate.release();
    assert.equal((await savedTimezone).status(), 200);
    assert(requests.some(r => r.url === '/api/catalog'));
    report.checks.timezone_save_overlaps_catalog_load = true;
    catalogGate.release(); await ready(page, '.courseChoice');
    const scripts = requests.filter(r => /\.(?:m?js)$/.test(r.url)).map(r => r.url);
    assert.equal(new Set(scripts).size, scripts.length);
    report.checks.preload_does_not_duplicate_scripts = true;
  } finally {appGate.release(); catalogGate.release(); await first.close();}

  const second = await context(), bank = await second.newPage();
  bank.on('pageerror', error => report.errors.push(error.message));
  let courseCatalogReads = 0;
  await second.route('**/api/catalog?*', route => {courseCatalogReads++; return route.abort('failed');});
  await bank.goto(base + '/#/subjects/math/banks/bank-math', {waitUntil: 'domcontentloaded'});
  await ready(bank, '.dashboardLayout');
  assert.equal(courseCatalogReads, 0);
  await bank.locator('.workspaceReselect').click(); await ready(bank, '.courseGrid');
  assert.equal(courseCatalogReads, 0);
  report.checks.bank_and_reselect_work_without_course_catalog = true;
  await second.close();

  if (previousExport) {
    // Same account, response bodies, 150 ms per request and empty browser caches.
    // Previous static assets run against the same isolated backend as the candidate.
    for (let round = 0; round < 2; round++) for (const version of ['before', 'after']) for (const kind of ['courses', 'bank']) {
      const c = await context(), p = await c.newPage();
      p.on('pageerror', error => report.errors.push(error.message));
      await c.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== base) return route.continue();
        await new Promise(resolve => setTimeout(resolve, 150));
        if (version === 'before' && !url.pathname.startsWith('/api/') && url.pathname !== '/deployment.json') {
          const file = path.resolve(previousExport, url.pathname.slice(1) || 'index.html');
          assert(file.startsWith(path.resolve(previousExport) + path.sep));
          try {return await route.fulfill({path: file});} catch {}
        }
        return route.continue();
      });
      const start = performance.now();
      await p.goto(base + '/#/subjects/math/' + (kind === 'bank' ? 'banks/bank-math' : 'courses'), {waitUntil: 'domcontentloaded'});
      await ready(p, kind === 'bank' ? '.dashboardLayout' : '.courseChoice');
      const row = {version, round, kind, ms: Math.round(performance.now() - start)};
      report.timings.push(row); console.log(JSON.stringify(row));
      await c.close();
    }
    for (const kind of ['courses', 'bank']) {
      const average = version => { const rows = report.timings.filter(r => r.kind === kind && r.version === version); return rows.reduce((sum, r) => sum + r.ms, 0) / rows.length; };
      assert(average('after') < average('before') * .85, `${kind} must reduce first load by at least 15% under equal latency`);
    }
    report.checks.faster_first_entry_under_equal_latency = true;
  }
  assert.deepEqual(report.errors, []); report.status = 'passed';
} catch (error) {report.failure = error.message; throw error;}
finally {await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close();}
console.log(JSON.stringify(report.checks));
