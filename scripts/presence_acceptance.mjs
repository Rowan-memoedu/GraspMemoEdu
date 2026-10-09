// Real Chrome, isolated synthetic identities and records; no production learning writes.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const [base, artifactRoot, publicBase] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const auth = JSON.parse(await fs.readFile(path.join(artifactRoot, 'auth.private.json'), 'utf8'));
const publicApi = publicBase ? JSON.parse(await fs.readFile(path.join(artifactRoot, 'pages/deployment.json'), 'utf8')).api_origin : null;
const nonce = await (await fetch(base + '/api/session', {headers: {Origin: base}})).json();
const out = path.join(artifactRoot, publicBase ? 'published-presence-browser' : 'presence-browser'); await fs.mkdir(out, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const errors = [], checks = [], pulses = [];
const pass = name => {checks.push(name); console.log('PASS ' + name);};
const snapshot = async () => (await (await fetch(base + '/qa/snapshot')).json()).hash;
async function readPresence() {
  return (await (await fetch(base + '/api/admin/students/' + auth.target, {
    headers: {Origin: base, 'X-Learning-Session': nonce.session_token, 'X-Learning-Identity': auth.admin.identity_token}
  })).json()).presence;
}
async function pageFor(role, mobile = false) {
  const context = await browser.newContext({viewport: mobile ? {width: 390, height: 844} : {width: 1440, height: 1000}, locale: 'zh-CN'});
  await context.addInitScript(({origin, identity}) => {
    if (!['http:', 'https:'].includes(location.protocol)) return;
    localStorage.setItem('graspmemoedu:identity:' + origin, identity.identity_token);
    localStorage.setItem('graspmemoedu:identity-role:' + origin, identity.access.role);
  }, {origin: publicApi || base, identity: auth[role]});
  const page = await context.newPage(); page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {if (new URL(request.url()).pathname === '/api/presence') pulses.push({role, body: request.postDataJSON()});});
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    assert.ok(!url.pathname.startsWith('/api/materials/content'), 'Q&A must not fetch the entire Topic');
    if (!publicBase) return route.continue();
    const headers = {...request.headers(), origin: base, 'x-learning-session': nonce.session_token,
      'x-learning-identity': auth[role].identity_token}; delete headers.host; delete headers.referer;
    const response = await fetch(base + url.pathname + url.search, {method: request.method(), headers,
      body: ['GET', 'HEAD'].includes(request.method()) ? undefined : request.postData()});
    await route.fulfill({status: response.status, contentType: response.headers.get('content-type') || 'application/json',
      headers: {'Access-Control-Allow-Origin': new URL(publicBase).origin, 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, X-Learning-Identity'}, body: Buffer.from(await response.arrayBuffer())});
  });
  return page;
}
const url = hash => (publicBase || base + '/') + hash;
try {
  const admin = await pageFor('admin'), before = await snapshot();
  await admin.goto(url('#/qa/' + auth.report_id)); await admin.locator('.helpContentBlock').waitFor();
  assert.equal(await admin.locator('.helpContentBlock').getAttribute('data-content-block'), 'introduction');
  await admin.getByRole('link', {name: '答疑', exact: true}).first().click();
  await admin.getByRole('link', {name: '打开对应内容', exact: true}).first().click();
  await admin.locator('.helpContentBlock').waitFor();
  assert.equal(new URL(admin.url()).hash, '#/qa/' + auth.report_id);
  await admin.screenshot({path: path.join(out, 'help-content-desktop.png'), fullPage: true});
  assert.equal(await snapshot(), before);
  pass('Q&A click opens the exact submission snapshot with no whole-Topic request or learning writes');

  await admin.goto(url('#/admin/students?learner=' + auth.target));
  await admin.locator('.studentToday').waitFor();
  assert.match(await admin.locator('.studentToday').innerText(), /今日在线时长|北京时间|首次访问|最近访问/);
  assert.equal(await admin.locator('.studentVisitList li').count(), (await readPresence()).topics.length);
  assert.match(await admin.locator('.studentVisitList').innerText(), /Synthetic acceptance course/);
  await admin.screenshot({path: path.join(out, 'today-desktop.png'), fullPage: true});
  const mobile = await pageFor('admin', true);
  await mobile.goto(url('#/admin/students?learner=' + auth.target)); await mobile.locator('.studentToday').waitFor();
  assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await mobile.screenshot({path: path.join(out, 'today-mobile.png'), fullPage: true});
  await admin.goto(url('#/admin/students?learner=' + auth.empty_target)); await admin.locator('.studentToday').waitFor();
  assert.match(await admin.locator('.studentToday').innerText(), /0 分 0 秒|暂无已记录/);
  assert.equal(pulses.filter(pulse => pulse.role === 'admin').length, 0);
  pass('Today summary, first/latest Topic visits, empty state and mobile layout; admins never report presence');

  const student = await pageFor('student');
  await student.goto(url('#/subjects/english/topic/atoms'));
  await student.locator('[data-help-block]').first().waitFor();
  await student.waitForFunction(() => document.querySelector('[data-help-block]'));
  for (let n = 0; n < 20 && !(await readPresence()).topics.some(topic => topic.topic_id === 'atoms'); n++) await student.waitForTimeout(250);
  assert.ok((await readPresence()).topics.some(topic => topic.topic_id === 'atoms'));
  const started = (await readPresence()).online_ms;
  await student.waitForTimeout(31000);
  assert.ok((await readPresence()).online_ms > started + 20000);
  assert.ok(pulses.some(pulse => pulse.role === 'student' && pulse.body.topic_ids.includes('atoms')));
  const frozen = await snapshot();
  assert.ok((await (await fetch(base + '/qa/snapshot')).json()).presence_active > 0);
  await student.goto('about:blank');
  await student.waitForTimeout(800);
  assert.equal((await (await fetch(base + '/qa/snapshot')).json()).presence_active, 0, 'Server accepted the page-exit heartbeat');
  const stopped = (await readPresence()).online_ms;
  await student.waitForTimeout(1200);
  assert.equal((await readPresence()).online_ms, stopped);
  assert.equal(await snapshot(), frozen);
  pass('Student reads a Topic, receives timed heartbeats, flushes on exit, and telemetry preserves learning records');
  assert.deepEqual(errors, []);
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify({checks, errors, pulse_count: pulses.length}, null, 2));
  await browser.close();
}
