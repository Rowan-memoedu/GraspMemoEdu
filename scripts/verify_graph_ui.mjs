/** Isolated Chrome acceptance for the independent graph importer. No production identity. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const [base, output, credentials, subject, course] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.ok(!['8765', '8767', ''].includes(new URL(base).port));
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const session = await (await fetch(base + '/api/session', {headers: {Origin: base}})).json();
const headers = {Origin: base, 'X-Learning-Session': session.session_token, 'Content-Type': 'application/json'};
const privateFixture = JSON.parse(await fs.readFile(credentials, 'utf8'));
const login = await (await fetch(base + '/api/access/login', {method: 'POST', headers, body: JSON.stringify(privateFixture)})).json();
assert.ok(login.identity_token);
headers['X-Learning-Identity'] = login.identity_token;
const response = await fetch(`${base}/api/graphs/${encodeURIComponent(course)}?subject_id=${subject}`, {headers});
assert.equal(response.status, 200);
const graph = await response.json();
assert.ok(graph.nodes.length);
assert.ok(graph.nodes.every(node => node.content_status === 'not_imported'));
assert.ok(graph.nodes.every(node => !('source' in node)));
const browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_EXECUTABLE || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const report = {checks: [], errors: [], nodes: graph.nodes.length, edges: graph.edges.length, production_identity_used: false};
try {
  const context = await browser.newContext({viewport: {width: 1365, height: 960}});
  await context.addInitScript(({base, token}) => {
    localStorage.setItem('math-learning-web:identity:' + base, token);
    localStorage.setItem('math-learning-web:identity-role:' + base, 'account');
  }, {base, token: login.identity_token});
  const page = await context.newPage(); page.on('pageerror', error => report.errors.push(error.message));
  await page.goto(`${base}/#/subjects/${subject}/courses`, {waitUntil: 'networkidle'});
  await page.locator('.graphLibrary button').first().click();
  const nodes = page.locator('.graphDialog [data-topic-id]');
  await nodes.first().waitFor();
  assert.equal(await nodes.count(), graph.nodes.length);
  assert.equal(await page.locator('.graphDialog .cgViewport path[marker-end]').count(), graph.edges.length);
  assert.ok((await page.locator('.graphDialog').innerText()).includes('内容未导入'));
  const descendants = new Set(graph.edges.map(edge => edge.from));
  const upstream = id => {
    const result = new Set([id]);
    for (let changed = true; changed;) {
      changed = false;
      for (const edge of graph.edges) if (result.has(edge.to) && !result.has(edge.from)) { result.add(edge.from); changed = true; }
    }
    return result;
  };
  const terminal = graph.nodes.filter(node => !descendants.has(node.topic_id));
  for (const node of terminal) {
    await page.locator('.graphDialog select').selectOption(node.topic_id);
    assert.equal(await page.locator('.graphDialog [data-topic-id][opacity="1"]').count(), upstream(node.topic_id).size);
  }
  const capstone = terminal.sort((a, b) => upstream(b.topic_id).size - upstream(a.topic_id).size)[0];
  await page.locator('.graphDialog input[type=search]').fill(capstone.title);
  await page.locator('.graphDialog input[type=search]').press('Enter');
  const selected = page.locator(`.graphDialog [data-topic-id="${capstone.topic_id}"]`);
  assert.equal(await selected.getAttribute('aria-pressed'), 'true');
  assert.ok((await page.locator('.graphDialog .cgDetails').innerText()).includes(capstone.title));
  const ancestors = upstream(capstone.topic_id);
  assert.equal(await page.locator('.graphDialog [data-topic-id][opacity="1"]').count(), ancestors.size);
  await page.locator('.graphDialog').getByRole('button', {name: '+', exact: true}).click();
  assert.equal(await page.locator('.graphDialog .cgZoom').innerText(), '115%');
  await page.locator('.graphDialog').getByRole('button', {name: '适应宽度', exact: true}).click();
  assert.equal(await page.locator('.graphDialog .cgViewport').evaluate(view => {
    const boxes = [...view.querySelectorAll('g[data-topic-id] rect')].map(rect => rect.getBBox());
    return boxes.some((a, i) => boxes.slice(i + 1).some(b => a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y));
  }), false, 'Graph nodes must not overlap');
  await page.screenshot({path: path.join(output, 'website-desktop.png'), fullPage: true});
  report.checks.push('Graph-only course discovered; HTTP node/edge counts match rendered SVG; search and upstream path verified');
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('.graphDialog').getByRole('button', {name: '适应宽度', exact: true}).click();
  await page.screenshot({path: path.join(output, 'website-mobile.png'), fullPage: true});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  report.checks.push('Mobile page does not overflow; graph scroll stays inside viewport');
  const offline = await context.newPage(); offline.on('pageerror', error => report.errors.push(error.message));
  await offline.goto(pathToFileURL(path.join(output, 'preview.html')).href, {waitUntil: 'load'});
  assert.equal(await offline.locator('[data-topic-id]').count(), graph.nodes.length);
  await offline.locator('select').selectOption(capstone.topic_id);
  assert.equal(await offline.locator('[data-topic-id][opacity="1"]').count(), ancestors.size);
  await offline.screenshot({path: path.join(output, 'offline-preview.png'), fullPage: true});
  report.checks.push('Self-contained offline HTML renders identical nodes and upstream paths');
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} finally {
  await fs.writeFile(path.join(output, 'browser.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify(report));
