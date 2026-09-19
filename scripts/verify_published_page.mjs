/** Verify HTTPS-only visitor access. Exercise mode must use isolated QA data. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const [url, api, out, courseFile, mode = 'exercise'] = process.argv.slice(2);
if (!url || !api || !out || !courseFile) throw new Error('Expected HTTPS page URL, HTTPS API origin, evidence directory, course JSON, optional read-only mode.');
assert.equal(new URL(url).protocol, 'https:');
assert.equal(new URL(api).protocol, 'https:');
assert.equal(['127.0.0.1', 'localhost'].includes(new URL(api).hostname), false);
const course = JSON.parse(await fs.readFile(courseFile, 'utf8'));
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const { chromium } = require('playwright');
await fs.mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe') });
const context = await browser.newContext({ viewport: { width: 1365, height: 1000 } });
const page = await context.newPage();
page.setDefaultTimeout(20000);
const report = { url, api, browser: browser.version(), mode, network_mocked: false, security_disabled: false, permissions_granted: [], started_at: new Date().toISOString(), checks: [], errors: [], mutations: [], prohibited_requests: [] };
let identityToken = null;
page.on('pageerror', error => report.errors.push(String(error)));
page.on('request', request => {
  if (['127.0.0.1', 'localhost'].includes(new URL(request.url()).hostname) || ['/api/session', '/api/access/login'].includes(new URL(request.url()).pathname)) report.prohibited_requests.push(request.url());
  if (request.url().startsWith(api)) {
    identityToken = request.headers()['x-learning-identity'] || identityToken;
    if (request.method() === 'POST' && !new URL(request.url()).pathname.startsWith('/api/access/')) report.mutations.push(new URL(request.url()).pathname);
  }
});
async function state() {
  assert.ok(identityToken, 'Page must establish its own browser identity');
  return page.evaluate(async ({ api, identityToken }) => {
    const response = await fetch(`${api}/api/state`, { headers: { 'X-Learning-Identity': identityToken } });
    if (!response.ok) throw new Error(`State request failed: ${response.status}`);
    return response.json();
  }, { api, identityToken });
}
async function action(selector, endpoint) {
  const response = page.waitForResponse(r => new URL(r.url()).origin === api && new URL(r.url()).pathname === `/api/${endpoint}` && r.request().method() === 'POST');
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
  assert.equal(page.url().split('#')[0], url.split('#')[0]);
  report.checks.push('Actual HTTPS document and relative assets loaded');
  await page.locator('.courseChoice').filter({hasText:'再探小学算术'}).waitFor();
  assert.equal(await page.locator('.mainNavigation a').count(), 3);
  await screenshot('00-courses');
  await page.locator('.courseChoice').filter({hasText:'再探小学算术'}).getByRole('button',{name:'Learn',exact:true}).click();
  await page.locator('.taskToggle').filter({hasText:course.title}).click();
  await screenshot('00-learn');
  await page.getByRole('button',{name:'Start',exact:true}).click();
  await page.locator('#courseShell:not([hidden])').waitFor();
  assert.equal(await page.locator('#settingsDialog, #settingsButton, #apiAddress, #pairingCredential').count(), 0);
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
  report.checks.push('Guest enters automatically through the public HTTPS API without setup or local network permission');
  await screenshot('01-connected');
  if (mode !== 'read-only') {
    assert.equal(initial.steps.length, 1, 'Exercise mode requires pristine QA data');
    assert.equal(await page.locator('#previousPageButton').count(), 0);
    assert.equal(await page.locator('#nextPageButton').count(), 0);
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
    console.log('Real public HTTPS judgement passed.');
    const next = await action('button[aria-label="Continue，继续学习"]', 'continue');
    assert.notEqual(next.active_step_id, initial.active_step_id);
    await page.locator('#previousPageButton:not([disabled])').waitFor();
    assert.equal(await page.locator('#nextPageButton').count(), 0);
    const previous = await action('#previousPageButton', 'select');
    assert.equal(previous.current_step_id, initial.active_step_id);
    await page.locator('#nextPageButton:not([disabled])').waitFor();
    const returned = await action('#nextPageButton', 'select');
    assert.equal(returned.current_step_id, next.active_step_id);
    assert.equal(returned.active_step_id, next.active_step_id);
    report.checks.push('Previous/next reviews unlocked pages and cannot open future pages');
    await screenshot('02-navigation');
  }
  const savedIdentity = identityToken;
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('#courseShell:not([hidden])').waitFor();
  assert.equal(identityToken, savedIdentity);
  report.checks.push('Refresh retains the same guest identity and saved learning progress');
  report.final = await page.evaluate(() => ({ heading: document.querySelector('#stepTitle').textContent, overflow: document.documentElement.scrollWidth > innerWidth + 1 }));
  assert.equal(report.final.overflow, false);
  if (mode === 'read-only') {
    const after = await state();
    assert.equal(after.revision, initial.revision);
    assert.equal(after.current_step_id, initial.current_step_id);
    assert.deepEqual(report.mutations.filter(endpoint => !['/api/profile','/api/catalog/select'].includes(endpoint)), []);
    report.checks.push('Progress inspected without learning writes; revision and selected page unchanged');
  }
  await screenshot('03-final');
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await screenshot('04-mobile');
  report.checks.push('Desktop and mobile layouts fit the viewport');
  await page.locator('[data-navigation="guide"]').click();
  await page.locator('#guideBody h2').first().waitFor();
  assert.equal(await page.locator('#guideBody h2').count(), 2);
  assert.equal(await page.locator('#guideBody img').count(), 3);
  for (const picture of await page.locator('#guideBody img').all()) await picture.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => [...document.querySelectorAll('#guideBody img')].every(image => image.complete && image.naturalWidth > 0));
  await page.evaluate(() => window.scrollTo(0,0));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  await screenshot('05-mobile-guide');
  await page.setViewportSize({ width: 1365, height: 1000 });
  await screenshot('06-guide');
  report.checks.push('GUIDE renders two sections and three annotated screenshots from the actual public backend');
  assert.deepEqual(report.prohibited_requests, []);
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed';
  report.failure = String(error).replaceAll(identityToken || 'no-token-available', '[redacted]');
  await screenshot('failure').catch(() => {});
  process.exitCode = 1;
} finally {
  report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify(report, null, 2));
