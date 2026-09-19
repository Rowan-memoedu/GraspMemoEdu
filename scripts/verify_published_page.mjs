/** Verify the real HTTPS frontend against an isolated local service, without network mocks. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const [url, api, credentialFile, out, courseFile, mode = 'exercise'] = process.argv.slice(2);
if (!url || !api || !credentialFile || !out || !courseFile) throw new Error('Expected HTTPS URL, loopback API URL, private credential file, evidence directory, course JSON, optional read-only mode.');
assert.equal(new URL(url).protocol, 'https:');
assert.equal(new URL(api).hostname, '127.0.0.1');
const credential = (await fs.readFile(credentialFile, 'utf8')).replace(/^\uFEFF/, '').trim();
const course = JSON.parse(await fs.readFile(courseFile, 'utf8'));
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const { chromium } = require('playwright');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe') });
const context = await browser.newContext({ viewport: { width: 1365, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(20000);
const report = { url, api, browser: browser.version(), mode, network_mocked: false, security_disabled: false, started_at: new Date().toISOString(), checks: [], errors: [], mutations: [] };
let identityToken = null;
page.on('pageerror', error => report.errors.push(String(error)));
page.on('request', request => {
  if (request.url().startsWith(api)) {
    identityToken = request.headers()['x-learning-identity'] || identityToken;
    if (request.method() === 'POST' && !new URL(request.url()).pathname.startsWith('/api/access/')) report.mutations.push(new URL(request.url()).pathname);
  }
});
async function state() {
  assert.ok(identityToken, 'Page must establish its own browser identity');
  return page.evaluate(async ({ api, credential, identityToken }) => {
    const response = await fetch(`${api}/api/state`, { headers: { Authorization: `Bearer ${credential}`, 'X-Learning-Identity': identityToken } });
    if (!response.ok) throw new Error(`State request failed: ${response.status}`);
    return response.json();
  }, { api, credential, identityToken });
}
async function connect(address = api) {
  if (!await page.locator('#settingsDialog').isVisible()) await page.locator('#settingsButton').click();
  await page.locator('#apiAddress').fill(address);
  await page.locator('#pairingCredential').fill(credential);
  await page.getByRole('button', { name: '连接并保存', exact: true }).click();
}
async function action(selector, endpoint) {
  const response = page.waitForResponse(r => r.url() === `${api}/api/${endpoint}` && r.request().method() === 'POST');
  await page.locator(selector).click();
  const result = await response;
  assert.equal(result.status(), 200);
  return result.json();
}
async function screenshot(name) {
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true });
}
try {
  const response = await page.goto(url, { waitUntil: 'networkidle' });
  assert.equal(response.status(), 200);
  assert.equal(page.url(), url);
  report.checks.push('Actual GitHub HTTPS document and relative assets loaded');
  // This grants the normal browser permission only; Chrome security checks remain enabled.
  report.permission_before = await page.evaluate(async () => {
    try { return (await navigator.permissions.query({ name: 'local-network-access' })).state; }
    catch { return 'query unavailable'; }
  });
  await context.grantPermissions(['local-network-access'], { origin: new URL(url).origin });
  report.permission_after = await page.evaluate(async () => {
    try { return (await navigator.permissions.query({ name: 'local-network-access' })).state; }
    catch { return 'granted through Chrome DevTools'; }
  });
  await connect();
  await page.locator('#courseShell:not([hidden])').waitFor();
  await page.locator('#settingsDialog').waitFor({ state: 'hidden' });
  let initial = await state();
  report.initial = { status: initial.status, revision: initial.revision, current_step_id: initial.current_step_id, active_step_id: initial.active_step_id, course_version: initial.course_version };
  assert.equal(initial.course_version, course.version);
  assert.equal(initial.access.role, 'guest');
  assert.equal(initial.access.features.includes('pause_topic'), false);
  assert.equal(['learn', 'submit_answer', 'review_history'].every(feature => initial.access.features.includes(feature)), true);
  report.access = { role: initial.access.role, features: initial.access.features };
  assert.equal(await page.locator('#historyButton').getAttribute('aria-expanded'), 'true');
  assert.equal(await page.locator('mjx-container svg').count() > 0, true);
  assert.equal(await page.locator('[data-mml-node="merror"]').count(), 0);
  report.checks.push('Paired cross-origin request loads real local course and saved progress');
  await screenshot('01-connected');
  if (mode !== 'read-only') {
    assert.equal(initial.steps.length, 1, 'Exercise mode requires pristine QA data');
    assert.equal(await page.locator('#previousPageButton').isDisabled(), true);
    assert.equal(await page.locator('#nextPageButton').isDisabled(), true);
    await page.locator('#pauseButton').click();
    await page.getByText('该功能需注册账号才能使用', { exact: true }).first().waitFor();
    assert.equal((await state()).revision, initial.revision);
    assert.equal(report.mutations.includes('/api/pause'), false);
    report.checks.push('Guest pause click displays registration hint and sends no pause request');
    await page.getByRole('button', { name: '已掌握，试着作答', exact: true }).click();
    await page.locator('#answerInput').fill(course.modules[0].example.reference_answer);
    const receipt = await action('#submitButton', 'submit');
    assert.equal(receipt.status, 'pending');
    await page.reload({ waitUntil: 'networkidle' });
    await page.locator('.feedbackTitle').waitFor({ timeout: 240000 });
    const judged = await state();
    const step = judged.steps.find(s => s.id === judged.active_step_id);
    assert.equal(step.feedback?.correct, true);
    assert.equal(step.feedback?.submission_id, receipt.submission_id);
    assert.equal(judged.modules[0].mastery, 4);
    assert.equal(judged.active_step_id, initial.active_step_id);
    report.judgement = { submission_id: receipt.submission_id, correct: step.feedback.correct, mastery: judged.modules[0].mastery };
    report.checks.push('Real judge result survives HTTPS page reload and requires Continue to advance');
    console.log('Real HTTPS-to-loopback judgement passed.');
    const next = await action('button[aria-label="Continue，继续学习"]', 'continue');
    assert.notEqual(next.active_step_id, initial.active_step_id);
    await page.locator('#previousPageButton:not([disabled])').waitFor();
    assert.equal(await page.locator('#nextPageButton').isDisabled(), true);
    const previous = await action('#previousPageButton', 'select');
    assert.equal(previous.current_step_id, initial.active_step_id);
    await page.locator('#nextPageButton:not([disabled])').waitFor();
    const returned = await action('#nextPageButton', 'select');
    assert.equal(returned.current_step_id, next.active_step_id);
    assert.equal(returned.active_step_id, next.active_step_id);
    report.checks.push('Previous/next reviews unlocked pages and cannot open future pages');
    await screenshot('02-navigation');
    // An unreachable loopback endpoint exercises connection failure without stopping any service.
    await connect('http://127.0.0.1:1');
    await page.locator('#connectionNotice:not([hidden])').waitFor();
    await connect();
    await page.locator('#settingsDialog').waitFor({ state: 'hidden' });
    await page.locator('#connectionNotice').waitFor({ state: 'hidden' });
    assert.equal((await state()).active_step_id, next.active_step_id);
    report.checks.push('Connection failure is visible and reconnect restores saved progress');
  }
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('#courseShell:not([hidden])').waitFor();
  report.final = await page.evaluate(() => ({ heading: document.querySelector('#stepTitle').textContent, overflow: document.documentElement.scrollWidth > innerWidth + 1 }));
  assert.equal(report.final.overflow, false);
  if (mode === 'read-only') {
    const after = await state();
    assert.equal(after.revision, initial.revision);
    assert.equal(after.current_step_id, initial.current_step_id);
    assert.deepEqual(report.mutations, []);
    report.checks.push('Progress inspected without learning writes; revision and selected page unchanged');
  }
  await screenshot('03-final');
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed';
  report.failure = String(error).replaceAll(credential, '[redacted]');
  await screenshot('failure').catch(() => {});
  process.exitCode = 1;
} finally {
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify(report, null, 2));
