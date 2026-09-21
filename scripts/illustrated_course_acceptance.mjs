/** Real Chrome images + first Topic real judgement; isolated fixture only. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [fixtureFile, output, mode] = process.argv.slice(2);
assert.ok(mode === undefined || mode === '--images-only');
const judgeFirst = mode !== '--images-only';
const fixture = JSON.parse(await fs.readFile(fixtureFile, 'utf8'));
assert.equal(fixture.formal_data_opened, false); assert.equal(fixture.real_judge, true);
const base = fixture.base;
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.ok(!['', '8765', '8767'].includes(new URL(base).port));
const plan = JSON.parse(await fs.readFile(fixture.plan, 'utf8'));
const credentials = JSON.parse(await fs.readFile(fixture.credentials_file, 'utf8'));
const account = credentials.accounts.find(a => a.purpose === 'test');
assert.ok(account?.invitation_code);
await fs.mkdir(output);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const page = await browser.newPage({viewport:{width:1365,height:960}, locale:'zh-CN'});
page.setDefaultTimeout(30000);
const report = {status:'running', isolated:true, real_judge:judgeFirst, mocked:false, topics:[], errors:[]};
page.on('pageerror', error => report.errors.push(error.message));
try {
  await page.goto(base + '#/', {waitUntil:'networkidle'});
  await page.locator('#userMenuButton').click(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(account.invitation_code);
  const [login] = await Promise.all([
    page.waitForResponse(r => new URL(r.url()).pathname === '/api/access/login' && r.request().method() === 'POST'),
    page.locator('#identitySubmitButton').click(),
  ]);
  assert.equal(login.status(), 200); assert.equal((await login.json()).access.purpose, 'test');
  await page.locator('#identityDialog').waitFor({state:'hidden'});
  await page.goto(`${base}#/subjects/${plan.subject_id}/courses`, {waitUntil:'networkidle'});
  await page.locator('.courseChoice').filter({hasText:plan.title}).waitFor();
  await page.screenshot({path:path.join(output, 'course.png'), fullPage:true});
  for (const [index, entry] of plan.topics.entries()) {
    await page.setViewportSize({width:1365,height:960});
    await page.goto(`${base}#/subjects/${plan.subject_id}/topic/${entry.topic_id}`, {waitUntil:'networkidle'});
    await page.getByRole('button', {name:'已掌握，试着作答', exact:true}).waitFor();
    const images = page.locator('#stepCard img');
    assert.ok(await images.count() > 0, `${entry.topic_id}: first Example must include images`);
    await page.waitForFunction(() => [...document.querySelectorAll('#stepCard img')].every(img => img.complete && img.naturalWidth > 0 && img.naturalHeight > 0));
    const sizes = await images.evaluateAll(nodes => nodes.map(n => ({width:n.naturalWidth,height:n.naturalHeight,png:n.src.startsWith('data:image/png;base64,')})));
    assert.ok(sizes.every(s => s.png));
    if (index === 0) await page.screenshot({path:path.join(output, 'example-desktop.png'), fullPage:true});
    await page.setViewportSize({width:390,height:844});
    if (await page.locator('body').evaluate(body => body.classList.contains('historyDrawerOpen'))) {
      await page.locator('#historyButton').click();
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, entry.topic_id + ': mobile overflow');
    if (index === 0) await page.screenshot({path:path.join(output, 'example-mobile.png'), fullPage:true});
    report.topics.push({topic_id:entry.topic_id, images:sizes, mobile_no_overflow:true});
    if (index === 0 && judgeFirst) {
      await page.setViewportSize({width:1365,height:960});
      const topic = JSON.parse(await fs.readFile(path.join(fixture.data_dir, 'imported-topics', entry.topic_id, 'topic.json'), 'utf8'));
      await page.getByRole('button', {name:'已掌握，试着作答', exact:true}).click();
      await page.locator('#answerInput').fill(topic.modules[0].example.reference_answer);
      const [submission] = await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === '/api/submit' && r.request().method() === 'POST'),
        page.locator('#submitButton').click(),
      ]);
      assert.equal(submission.status(), 200);
      await page.waitForFunction(() => document.querySelector('.feedbackTitle') && !document.querySelector('.waiting'), null, {timeout:240000});
      assert.equal(await page.locator('.feedbackTitle').innerText(), '回答正确');
      report.real_judgement = {topic_id:entry.topic_id, question_id:topic.modules[0].example.id, correct:true};
      await page.screenshot({path:path.join(output, 'real-feedback.png'), fullPage:true});
    }
    console.log('PASS ' + entry.topic_id);
  }
  assert.deepEqual(report.errors, []); report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = String(error);
  await page.screenshot({path:path.join(output, 'failure.png'), fullPage:true}).catch(() => {});
  throw error;
} finally {
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
