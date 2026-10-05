/** Real shared controls and source media, in an offline Chrome page. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const [bankPath, formatPath, output] = process.argv.slice(2);
if (!output) throw new Error('Expected original bank, compiled format and new evidence directory');
const bank = JSON.parse(await fs.readFile(bankPath, 'utf8')), format = JSON.parse(await fs.readFile(formatPath, 'utf8'));
const web = path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), 'web');
const all = Object.entries(format.questions).map(([id, q]) => ({id, ...q}));
for (const q of all) for (const html of [q.html, ...(q.interaction.options || []).map(o => o.html)]) {
  const ids = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
  for (const m of html.matchAll(/(?:xlink:)?href="#([^"]+)"/g)) assert.ok(ids.has(m[1]), 'Unresolved source glyph');
}
const samples = [...new Map([
  all.find(q => q.interaction.type === 'choice' && !q.interaction.multiple),
  all.find(q => q.interaction.multiple && !q.interaction.options.some(o => o.html.includes('<img'))),
  all.find(q => q.interaction.options?.some(o => o.html.includes('<img'))),
  all.find(q => q.interaction.multiple && q.interaction.options.some(o => o.html.includes('<img'))),
  all.find(q => q.interaction.type === 'fill_blank' && q.interaction.blanks.length === 1),
  all.find(q => q.interaction.type === 'fill_blank' && q.interaction.blanks.length > 1),
].filter(Boolean).map(q => [q.id, q])).values()];
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
await fs.mkdir(output, {recursive: true});
const report = {passed: false, original_version: bank.version, checked_fragments: all.length, samples: [], browser_errors: [], external_requests: []};
try {
  const page = await browser.newPage({viewport: {width: 1150, height: 950}, locale: 'zh-CN'});
  page.on('pageerror', e => report.browser_errors.push(e.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:18859') { report.external_requests.push(url.origin); return route.abort(); }
    if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><link rel="stylesheet" href="/web/styles.css"><link rel="stylesheet" href="/web/portal.css"><style>body{margin:0;padding:24px}.pageShell{max-width:1000px;margin:auto}img{max-width:100%;height:auto}</style><main class="pageShell"><article class="step"><h2 id="title"></h2><div id="stem"></div><form id="answerForm" class="answerForm"></form></article></main></html>'});
    const file = path.resolve(web, '.' + url.pathname.slice(4));
    if (!file.startsWith(web + path.sep)) return route.abort();
    try { return route.fulfill({body: await fs.readFile(file), contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : undefined}); }
    catch { return route.abort(); }
  });
  for (const [index, q] of samples.entries()) {
    await page.goto('http://127.0.0.1:18859/', {waitUntil: 'load'});
    const publicSpec = structuredClone(q.interaction);
    delete publicSpec.correct_id; delete publicSpec.correct_ids;
    for (const b of publicSpec.blanks || []) delete b.answers;
    await page.evaluate(async ({q, spec}) => {
      const {questionInput, answerReady, choiceTypeField} = await import('/web/question-input.js');
      const stem = document.querySelector('#stem'); stem.innerHTML = q.html;
      document.querySelector('#title').textContent = spec.type === 'fill_blank' ? '填空题' : spec.multiple ? '多项选择题' : '单项选择题';
      const typeField = choiceTypeField(spec); if (typeField) document.querySelector('#title').after(typeField);
      window.control = questionInput(spec, {id: 'fixtureInput', stem, formId: 'answerForm'});
      document.querySelector('form').append(control.element);
      window.answerReady = answerReady;
    }, {q: {html: q.html}, spec: publicSpec});
    await page.locator('img').evaluateAll(images => Promise.all(images.map(img => { img.loading = 'eager'; return img.decode(); })));
    const file = `${index + 1}-${q.interaction.type}${q.interaction.multiple ? '-multiple' : ''}.png`;
    await page.screenshot({path: path.join(output, file), fullPage: true});
    assert.equal(await page.evaluate(() => answerReady(control.input)), false);
    if (q.interaction.type === 'choice') {
      const fields = page.locator('.choiceOption input');
      assert.equal(await fields.count(), q.interaction.options.length);
      await fields.nth(0).check();
      assert.equal(await page.locator('.questionTypeField').textContent(), q.interaction.multiple ? '多选' : '单选');
      assert.equal(await page.locator('#title + .questionTypeField + #stem').count(), 1);
      const beforeKeys = await page.evaluate(() => control.input.value);
      await fields.nth(0).focus();
      for (const key of ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']) await page.keyboard.press(key);
      assert.equal(await page.evaluate(() => control.input.value), beforeKeys);
      assert.equal(await fields.nth(0).isChecked(), true);
      await page.keyboard.press('Space');
      assert.equal(await fields.nth(0).isChecked(), !q.interaction.multiple);
      await fields.nth(0).check();
      if (q.interaction.multiple) {
        await fields.nth(2).check();
        assert.deepEqual(await page.evaluate(() => JSON.parse(control.input.value).selected), [q.interaction.options[0].id, q.interaction.options[2].id]);
        await fields.nth(0).uncheck();
        assert.deepEqual(await page.evaluate(() => JSON.parse(control.input.value).selected), [q.interaction.options[2].id]);
      } else assert.equal(await page.evaluate(() => JSON.parse(control.input.value).selected), q.interaction.options[0].id);
      // The existing draft restoration path re-renders the correct control.
      await page.evaluate(() => control.input.dispatchEvent(new Event('input', {bubbles: true})));
      assert.equal(await page.locator('.choiceOption input:checked').count(), 1);
    } else {
      assert.equal(await page.locator('.inlineBlank').count(), q.interaction.blanks.length);
      for (const input of await page.locator('.inlineBlank').all()) await input.fill('1');
      assert.equal(await page.locator('#stem').innerText().then(s => s.includes('{{blank:')), false);
    }
    assert.equal(await page.evaluate(() => answerReady(control.input)), true);
    await page.evaluate(() => control.input.questionControl.setDisabled(true));
    assert.equal(await page.evaluate(() => answerReady(control.input)), false);
    for (const width of [1150, 390]) {
      await page.setViewportSize({width, height: 950});
      const visual = await page.evaluate(() => ({overflow: document.documentElement.scrollWidth > innerWidth,
        badImages: [...document.images].filter(i => !i.complete || !i.naturalWidth).length,
        mathErrors: document.querySelectorAll('[data-mml-node="merror"]').length,
        missingGlyphs: [...document.querySelectorAll('use')].filter(u => !document.getElementById((u.getAttribute('href') || u.getAttribute('xlink:href') || '').slice(1)) || u.getBBox().width <= 0).length}));
      assert.deepEqual(visual, {overflow: false, badImages: 0, mathErrors: 0, missingGlyphs: 0});
      report.samples.push({id: q.id, type: q.interaction.type, multiple: Boolean(q.interaction.multiple), width, screenshot: file, ...visual});
    }
    await page.setViewportSize({width: 1150, height: 950});
  }
  assert.deepEqual(report.browser_errors, []); assert.deepEqual(report.external_requests, []);
  report.passed = true;
} finally {
  await fs.writeFile(path.join(output, 'format-control-validation.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log('PASS shared single/multiple/blank controls, draft restoration, mobile layout and original graphics');
