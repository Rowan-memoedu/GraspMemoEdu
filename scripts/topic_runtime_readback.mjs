// API mutations on synthetic data; Chrome is only used to read and screenshot.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
const [base, output] = process.argv.slice(2);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
let token, session;
async function api(endpoint, body) {
  const response = await fetch(base + '/api/' + endpoint, {method: body === undefined ? 'GET' : 'POST',
    headers: {Origin:base, 'Content-Type':'application/json', ...(token ? {'X-Learning-Identity':token} : {}), ...(session ? {'X-Learning-Session':session} : {})},
    ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  assert.equal(response.status, 200, `${endpoint}: ${await response.clone().text()}`);
  return response.json();
}
session = (await api('session')).session_token;
token = (await api('access/guest', {})).identity_token;
await api('catalog/select', {course_id:'canary-course'});
const action = async (topic, endpoint, extra={}) => {
  const state = await api(`state?topic_id=${topic}`);
  return api(`${endpoint}?topic_id=${topic}`, {request_id:crypto.randomUUID(), expected_revision:state.revision,
    attempt_id:state.attempt_id, step_id:state.active_step_id, ...extra});
};
const browser = await chromium.launch({headless:true, executablePath:path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
const errors = [];
try {
  const context = await browser.newContext({viewport:{width:1365,height:1000},locale:'zh-CN'});
  await context.addInitScript(({base,token}) => localStorage.setItem('graspmemoedu:identity:'+base,token), {base,token});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const visit = async hash => {
    await page.goto(base + '/?readback=' + crypto.randomUUID() + hash, {waitUntil:'networkidle'});
    await page.locator('#stepTitle').waitFor();
  };
  await api('state?topic_id=format-lesson');
  await visit('#/subjects/math/topic/format-introduction');
  await page.locator('.local-mermaid[data-mermaid-state="rendered"]').waitFor();
  assert.equal(await page.locator('#stepCard .callout').count(), 4);
  assert.equal(await page.locator('#stepCard .topicBacklinks a').count(), 1);
  const colors = await page.locator('#stepCard .callout').evaluateAll(items => items.map(item => ({type:item.dataset.callout,color:getComputedStyle(item).backgroundColor})));
  assert.equal(new Set(colors.map(item=>item.color)).size, 4);
  assert.ok(colors.every(item=>item.color !== 'rgba(0, 0, 0, 0)'));
  await page.screenshot({path:path.join(output,'introduction-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(output,'introduction-mobile.png'),fullPage:true});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth + 1), 'Mobile page must not overflow');
  await page.setViewportSize({width:1365,height:1000});
  await action('format-introduction', 'continue');
  await action('format-introduction', 'continue');
  await visit('#/subjects/math/topic/format-lesson');
  assert.equal(await page.locator('a.topicReference[href]').count(), 1);
  await action('format-lesson', 'continue');
  await action('format-lesson', 'choose', {choice:'unmastered'});
  await action('format-lesson', 'continue');
  await visit('#/subjects/math/topic/format-lesson');
  assert.ok((await page.locator('#stepCard').textContent()).includes('idea'));
  const href = await page.locator('#stepCard a.topicReference[href]').getAttribute('href');
  assert.ok(href.includes('node=definition'));
  await page.screenshot({path:path.join(output,'lesson-explanation.png'),fullPage:true});
  await visit(href);
  assert.equal(await page.evaluate(()=>document.activeElement.id), 'node-definition');
  await action('format-lesson', 'select', {step_id:(await api('state?topic_id=format-lesson')).active_step_id});
  await action('format-lesson', 'continue');
  for (let i=0;i<2;i++) {
    const result = await action('format-lesson','submit',{answer:'correct'});
    let judged = result;
    for (let tries=0; judged.status === 'pending' && tries<50; tries++) {
      await new Promise(resolve=>setTimeout(resolve,100));
      judged = await api(`submissions/${result.submission_id}?topic_id=format-lesson`);
    }
    assert.equal(judged.status,'judged');
    await action('format-lesson','continue');
  }
  await visit('#/subjects/math/topic/format-lesson');
  const historyTitles = await page.locator('#historyPanel .moduleName').allTextContents();
  assert.equal(historyTitles.length, 3);
  assert.match(historyTitles[0], /第一节$/);
  assert.match(historyTitles[1], /例题\s*1[:：] Synthetic$/);
  assert.match(historyTitles[2], /第二节$/);
  await page.screenshot({path:path.join(output,'source-order.png'),fullPage:true});
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(output,'frontend-readback.json'), JSON.stringify({passed:true,colors,browser_errors:errors,
    checks:['four Callouts','local Mermaid SVG','body backlinks','reference node focus','source order','mobile overflow'],
    isolated:true,synthetic:true},null,2));
  console.log('PASS Topic v2 frontend: Callouts, Mermaid, backlinks, node focus, ordering and mobile layout.');
} finally { await browser.close(); }
