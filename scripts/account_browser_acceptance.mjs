/** Real Chrome + real API account canary. Only accounts permanently marked test.
 * Usage: node scripts/account_browser_acceptance.mjs PAGE API DATA PRIVATE_JSON OUTPUT
 * Uses local management only for these test accounts; never prints credentials.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';

const [base, api, dataDirectory, invitationsFile, destination] = process.argv.slice(2);
if (![base, api, dataDirectory, invitationsFile, destination].every(Boolean)) throw new Error('Missing required arguments');
const manifest = JSON.parse(await fs.readFile(invitationsFile, 'utf8'));
const testAccounts = manifest.accounts.filter(account => account.purpose === 'test');
assert.equal(testAccounts.length, 5, 'Canary requires five explicitly reserved test accounts');
assert.equal(path.resolve(manifest.database), path.resolve(dataDirectory, 'learning.sqlite3'));
const secretValues = manifest.accounts.map(account => account.invitation_code);
const report = {started_at:new Date().toISOString(), base, api, real_browser:'Chrome', mocked:false, real_judge:true, checks:[], errors:[]};
const clean = value => secretValues.reduce((text, secret) => text.split(secret).join('[credential redacted]'), String(value));
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({executablePath:process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe'), headless:true});
const course = JSON.parse(await fs.readFile(path.join(dataDirectory, 'courses/digits-to-letters.json'), 'utf8'));
const topic = course.id;
await fs.mkdir(destination, {recursive:true});
const passed = name => {report.checks.push(name); process.stdout.write(`PASS ${name}\n`);};
const origin = new URL(base).origin;
const publicTransport = new URL(api).protocol === 'https:';
const headers = async token => {
  const result = {Origin:origin, 'X-Learning-Identity':token};
  if (!publicTransport) {
    const response = await fetch(`${api}/api/session`, {headers:{Origin:origin}});
    const data = await response.json(); assert.equal(response.status, 200);
    result['X-Learning-Session'] = data.session_token; secretValues.push(data.session_token);
  }
  return result;
};
async function get(token, route = `state?topic_id=${topic}`) {
  const response = await fetch(`${api}/api/${route}`, {headers:await headers(token)});
  return {status:response.status, body:await response.json()};
}
async function post(token, route, body) {
  const response = await fetch(`${api}/api/${route}`, {method:'POST', headers:{...await headers(token), 'Content-Type':'application/json'}, body:JSON.stringify(body)});
  return {status:response.status, body:await response.json()};
}
const active = state => state.steps.find(step => step.id === state.active_step_id);
async function createDevice(mobile = false) {
  const context = await browser.newContext({viewport:mobile ? {width:390,height:844} : {width:1360,height:1000}, deviceScaleFactor:1});
  const page = await context.newPage();
  const device = {context, page, token:null};
  page.setDefaultTimeout(25000);
  page.on('pageerror', error => report.errors.push(clean(error.message)));
  page.on('response', async response => {
    if (response.url().startsWith(`${api}/api/access/`) && response.request().method() === 'POST' && response.status() === 200) {
      try { const body = await response.json(); if (body.identity_token) {device.token = body.identity_token; secretValues.push(body.identity_token);} } catch {}
    }
  });
  page.on('request', request => {
    if (request.url().startsWith(`${api}/api/`)) {
      const token = request.headers()['x-learning-identity'];
      if (token) {device.token = token; if (!secretValues.includes(token)) secretValues.push(token);}
    }
  });
  await page.goto(base.replace(/#.*$/, '')+'#/subjects/math/courses', {waitUntil:'networkidle'});
  await page.locator('.courseChoice').first().waitFor();
  assert.equal((await get(device.token, 'access')).body.role, 'guest');
  return device;
}
async function openLogin(device) {
  await device.page.locator('#userMenuButton').click();
  await device.page.locator('#identityButton').click();
  await device.page.locator('#identityDialog').waitFor({state:'visible'});
}
async function login(device, account) {
  await openLogin(device);
  await device.page.locator('#invitationCode').fill(account.invitation_code);
  const responsePromise = device.page.waitForResponse(response => response.url() === `${api}/api/access/login` && response.request().method() === 'POST');
  await device.page.locator('#identitySubmitButton').click();
  const response = await responsePromise;
  assert.equal(response.status(), 200, 'Login must succeed');
  const result = await response.json();
  secretValues.push(result.identity_token); device.token = result.identity_token;
  assert.equal(result.access.learner_id, account.learner_id);
  assert.equal(result.access.purpose, 'test');
  await device.page.locator('#identityDialog').waitFor({state:'hidden'});
  assert.equal(await device.page.locator('#menuAccountPurpose').getAttribute('hidden'),null);
  assert.equal(await device.page.locator('#menuAccountPurpose').textContent(),'测试账号');
  return result;
}
async function startTopic(device) {
  if (await device.page.locator('.courseChoice').count()) {
    await device.page.locator('.courseChoice').filter({hasText:'再探小学算术'}).getByRole('button', {name:'Learn',exact:true}).click();
  }
  await device.page.goto(base.replace(/#.*$/, '') + `#/topic/${topic}`, {waitUntil:'networkidle'});
  await device.page.locator('#stepCard [data-step-content], #stepTitle').first().waitFor();
  const result = await get(device.token); assert.equal(result.status, 200);
  assert.equal(result.body.access.role, 'account');
  return result.body;
}
function manage(account, command, extra = []) {
  assert.equal(account.purpose, 'test');
  // Check the authoritative DB purpose before invoking any local mutation.
  const checker = "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); r=c.execute('SELECT purpose FROM access_principals WHERE learner_id=?',(sys.argv[2],)).fetchone(); assert r and r[0]=='test'";
  const python = 'D:/AppData/MathLearningWeb/venv/Scripts/python.exe';
  execFileSync(python, ['-c', checker, path.join(dataDirectory, 'learning.sqlite3'), account.learner_id], {stdio:'pipe'});
  const result = execFileSync(python, ['-m','math_learning','--data-dir',dataDirectory,command,'--learner',account.learner_id,...extra], {cwd:path.resolve(import.meta.dirname,'..'),encoding:'utf8',stdio:'pipe'});
  return JSON.parse(result);
}
async function logout(device) {
  await device.page.locator('#userMenuButton').click();
  const responsePromise = device.page.waitForResponse(response => response.url() === `${api}/api/access/logout` && response.request().method() === 'POST');
  await device.page.locator('#logoutButton').click();
  assert.equal((await responsePromise).status(), 200);
  await device.page.waitForFunction(() => document.querySelector('#identityAction')?.textContent === '邀请码登录');
}
try {
  const a = await createDevice();
  const guest = a.token;
  await openLogin(a);
  await a.page.locator('#invitationCode').fill('INVALID-ACCOUNT-CANARY');
  await a.page.locator('#identitySubmitButton').click();
  await a.page.locator('#identityError:not([hidden])').waitFor();
  assert.equal((await get(guest,'access')).status,200);
  await a.page.locator('#cancelIdentityButton').click();
  passed('Invalid invitation leaves guest access intact');

  await login(a,testAccounts[0]); const accountAToken = a.token;
  let state = await startTopic(a);
  assert.equal(state.status,'in_progress');
  assert.equal(state.modules.filter(module => module.mastery !== null).length,0,'Test account must start unused');
  assert.notEqual(await a.page.locator('#guestDemoOption').getAttribute('hidden'),null);
  await a.page.screenshot({path:path.join(destination,'account-desktop.png'),fullPage:true});
  await a.page.getByRole('button',{name:'已掌握，试着作答',exact:true}).click();
  const answer = course.modules[0].example.reference_answer;
  await a.page.locator('#answerInput').fill(answer);
  const submitted = a.page.waitForResponse(response => new URL(response.url()).pathname === '/api/submit' && response.request().method() === 'POST');
  await a.page.locator('#submitButton').click();
  const receipt = await submitted; assert.equal(receipt.status(),200);
  const submission = await receipt.json();
  await a.page.locator('.feedbackTitle').waitFor({timeout:240000});
  state = (await get(a.token)).body;
  assert.equal(active(state).feedback.correct,true);
  assert.equal(active(state).feedback.submission_id,submission.submission_id);
  passed('Reserved test account completes a real judgement and records mastery');

  const b = await createDevice(); await login(b,testAccounts[0]);
  const otherDeviceToken = b.token;
  const shared = await startTopic(b);
  assert.equal(shared.learner_id,state.learner_id); assert.equal(shared.revision,state.revision);
  assert.equal(active(shared).feedback.submission_id,submission.submission_id);
  assert.notEqual(otherDeviceToken,accountAToken);
  passed('Independent second device resumes the same account and exact saved progress');

  const c = await createDevice(true); await login(c,testAccounts[1]);
  const isolated = await startTopic(c);
  assert.equal(isolated.steps.length,1); assert.equal(active(isolated).feedback,null);
  assert.notEqual(isolated.learner_id,state.learner_id);
  const foreign = await get(c.token,`submissions/${submission.submission_id}?topic_id=${topic}`);
  assert.ok([403,404].includes(foreign.status));
  assert.ok(await c.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  if (await c.page.locator('#historyButton').getAttribute('aria-expanded') === 'true') await c.page.locator('#historyButton').click();
  await c.page.locator('#userMenuButton').click();
  await c.page.screenshot({path:path.join(destination,'account-mobile.png'),fullPage:true});
  passed('Different account has isolated progress, rejects foreign submissions and fits mobile');

  await a.page.getByRole('button',{name:'Continue，继续学习',exact:true}).click();
  manage(testAccounts[0],'account-permissions',['--features','learn,submit_answer,review_history']);
  await a.page.reload({waitUntil:'networkidle'});
  assert.equal(await a.page.locator('#pauseButton').isDisabled(),true);
  assert.equal((await post(a.token,`pause?topic_id=${topic}`,{request_id:crypto.randomUUID()})).status,403);
  manage(testAccounts[0],'account-permissions',['--features','learn,submit_answer,review_history,pause_topic']);
  await a.page.reload({waitUntil:'networkidle'});
  const pausedPromise = a.page.waitForResponse(response => new URL(response.url()).pathname === '/api/pause' && response.request().method() === 'POST');
  await a.page.locator('#pauseButton').click();
  const pausedResponse = await pausedPromise; assert.equal(pausedResponse.status(),200);
  const paused = await pausedResponse.json(); assert.equal(paused.status,'paused');
  await b.page.reload({waitUntil:'networkidle'});
  assert.equal((await get(b.token)).body.status,'paused');
  const unlocked = manage(testAccounts[0],'unlock',['--topic',topic,'--module',paused.pause.module_id,'--pause-event',paused.pause.event_id]);
  assert.equal(unlocked.status,'in_progress');
  await a.page.reload({waitUntil:'networkidle'});
  assert.equal((await get(a.token)).body.status,'in_progress');
  passed('Local permission revocation, account pause and precise local unlock work across devices');

  await logout(a);
  assert.equal((await get(accountAToken,'access')).status,401);
  assert.equal((await get(otherDeviceToken,'access')).status,200);
  const restoredGuest = (await get(a.token,'access')).body;
  assert.equal(restoredGuest.learner_id,(await get(guest,'access')).body.learner_id);
  assert.equal((await get(a.token)).body.steps.length,1);
  await logout(b); await logout(c);
  passed('Logout invalidates server session, preserves another device, and restores original guest');

  // The other reserved codes are tested without creating learning progress.
  for (const account of testAccounts.slice(2)) {
    const d = await createDevice(); await login(d,account);
    const access = await get(d.token,'access'); assert.equal(access.status,200);
    assert.equal(access.body.purpose,'test'); await logout(d); await d.context.close();
  }
  passed('All five reserved invitations authenticate; no normal account is used');
  assert.deepEqual(report.errors,[]);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = clean(error.stack || error);
  process.exitCode = 1; process.stderr.write(report.failure+'\n');
} finally {
  // Leave test permissions usable even if the browser test fails mid-step.
  try {manage(testAccounts[0],'account-permissions',['--features','learn,submit_answer,review_history,pause_topic']);} catch {report.cleanup_warning='Restore the reserved test account permissions locally';}
  await browser.close(); report.finished_at = new Date().toISOString();
  await fs.writeFile(path.join(destination,'report.json'),JSON.stringify(report,null,2));
}
