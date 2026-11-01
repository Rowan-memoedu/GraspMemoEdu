/** Read-only chunk inspection and rating geometry in an isolated Chrome profile. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const [base, out] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
await fs.mkdir(out);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, ignoreDefaultArgs: ['--hide-scrollbars'],
  executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context = await browser.newContext({viewport: {width: 1440, height: 1050}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], formal_data_opened: false};
let token, session, navigation = 0, mutations = 0;
page.on('pageerror', error => report.errors.push(error.message));
page.on('request', request => {
  token = request.headers()['x-learning-identity'] || token;
  if (request.method() === 'POST' && /\/api\/(?:submit|continue|choose|reveal)$/.test(new URL(request.url()).pathname)) mutations++;
});
const go = () => page.goto(`${base}/?visual=${++navigation}#/subjects/math/topic/synthetic-course`, {waitUntil: 'networkidle'});
const screenshot = name => page.screenshot({path: path.join(out, name + '.png'), fullPage: true});
const pass = label => { report.checks.push(label); console.log('PASS ' + label); };
const api = async (route, body) => {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'X-Learning-Identity': token, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})});
  assert.equal(response.status, 200); return response.json();
};
const state = () => api('/api/state?topic_id=synthetic-course&subject_id=math');
const next = () => page.locator('#stepCard .stepNavigation .continueRow .primaryButton').click();
const start = () => page.locator('.atomicMain').getByRole('button', {name: '开始原子卡训练', exact: true}).click();
try {
  await go();
  assert.deepEqual(await page.locator('#topicAtomicProgress .topicProgressCaption strong').allTextContents(), ['0 / 2', '0 / 2', '0 / 3']);
  await next(); await page.locator('.atomicMnemonic').waitFor();
  assert.deepEqual(await page.locator('.atomicMain .topicProgressCaption strong').allTextContents(), ['1 / 2', '0 / 2', '0 / 3']);
  assert.equal(await page.evaluate(() => scrollY), 0);
  assert.ok((await page.locator('.atomicSourceDocument').innerText()).includes('补充讲解 15'));
  assert.equal(await page.locator('.atomicSourceMark').allTextContents().then(parts => parts.join('')), '甲标签是红色，乙标签是蓝色。');
  assert.ok((await page.locator('.atomicChunkCore').innerText()).includes('名称与属性'));
  assert.ok((await page.locator('.atomicMnemonic').innerText()).includes('甲红，乙蓝'));
  await page.waitForFunction(() => document.querySelector('.atomicSourceViewport').scrollTop > 0);
  const layout = await page.evaluate(() => {
    const rect = selector => document.querySelector(selector).getBoundingClientRect();
    const source = rect('.atomicSourceViewport'), note = rect('.atomicChunkNote'), history = rect('.atomicHistory');
    const mark = rect('.atomicSourceMark');
    return {source: source.width, note: note.width, historyX: history.x, noteRight: note.right,
      centerError: Math.abs((mark.top + mark.bottom) / 2 - source.top - document.querySelector('.atomicSourceViewport').clientHeight / 2),
      horizontalError: Math.abs((mark.left + mark.right) / 2 - source.left - document.querySelector('.atomicSourceViewport').clientWidth / 2),
      prose: Boolean(document.querySelector('.atomicSourceProse')),
      scrollLeft: document.querySelector('.atomicSourceViewport').scrollLeft,
      scrollWidth: document.querySelector('.atomicSourceViewport').scrollWidth,
      font: parseFloat(getComputedStyle(document.querySelector('.atomicSourceDocument')).fontSize)};
  });
  assert.ok(layout.source > layout.note && layout.historyX > layout.noteRight && layout.font < 15);
  assert.ok(layout.centerError < 3);
  if (layout.prose) { assert.equal(layout.scrollLeft, 0); assert.ok(layout.scrollWidth <= layout.source + 1); }
  else assert.ok(layout.horizontalError < 3);
  const scrolling = await page.locator('.atomicSourceViewport').evaluate(el => {
    const originalX = el.scrollLeft, originalY = el.scrollTop;
    el.scrollLeft = 140; el.scrollTop = 200;
    const result = {x: el.scrollLeft, y: el.scrollTop, width: el.clientWidth};
    el.scrollLeft = originalX; el.scrollTop = originalY; return result;
  });
  assert.ok((layout.prose ? scrolling.x === 0 : scrolling.x > 0) && scrolling.y > 0 && scrolling.width > 400);
  await screenshot('chunk-desktop');
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({width, height: 900});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    if (width === 375) await screenshot('chunk-mobile');
  }
  pass('Whole Topic counters, wider source, vertical highlight centering, prose reflow or canvas scroll, right history and separate mnemonic; four responsive widths');
  await page.setViewportSize({width: 1440, height: 1050});
  await start(); await page.locator('#atomicAnswer').fill('尚未提交的答案草稿');
  assert.equal(await page.locator('.atomicPeekButton, .memoryChunkDialog').count(), 0);
  await page.locator('#atomicAnswerForm button[type="submit"]').click();
  await page.locator('.atomicFeedback').waitFor({timeout: 120000});
  const before = await state(), writes = mutations;
  await page.getByRole('button', {name: '查看记忆组块', exact: true}).click();
  await page.locator('dialog[open] .atomicMnemonic').waitFor();
  await screenshot('chunk-dialog');
  await page.getByRole('button', {name: '返回作答', exact: true}).click();
  assert.equal(await page.locator('#atomicAnswer').inputValue(), '尚未提交的答案草稿');
  assert.deepEqual(await state(), before);
  assert.equal(mutations, writes);
  await page.getByRole('button', {name: '查看记忆组块', exact: true}).click();
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('dialog[open]').count(), 0);
  assert.equal(await page.getByRole('button', {name: '查看记忆组块', exact: true}).evaluate(el => el === document.activeElement), true);
  pass('Chunk access stays absent before submission; feedback dialog preserves answer, revision and active step; Escape restores focus');
  await page.locator('.atomicQuestion > .primaryButton').click();
  await page.locator('.atomicFeedback').waitFor({state: 'hidden'});
  assert.equal(await page.locator('.atomicPeekButton:visible, .memoryChunkDialog:visible').count(), 0);
  pass('Continuing past feedback leaves no accessible chunk controls');
  const advanced = await api('/__qa/advanced', {});
  token = advanced.identity_token;
  await page.evaluate(({base, token}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
  }, {base, token});
  await go(); await next(); await start();
  assert.equal(await page.locator('.atomicPeekButton, .memoryChunkDialog').count(), 0);
  await page.locator('.showAnswer').click(); await page.locator('.trainingRating[data-rating="5"]').waitFor();
  await page.locator('.atomicPeekButton').click();
  await page.locator('.memoryChunkDialog[open]').waitFor();
  await page.keyboard.press('Escape');
  pass('Show Answer unlocks chunk access before self-rating');
  assert.equal(await page.locator('.trainingRating').count(), 5);
  for (const width of [1440, 320, 375, 414, 768]) {
    await page.setViewportSize({width, height: 1050});
    const boxes = await page.locator('.trainingRating').evaluateAll(buttons => buttons.map(el => {
      const r = el.getBoundingClientRect(); return {top: r.top, left: r.left, right: r.right, height: r.height};
    }));
    assert.ok(boxes.every(box => Math.abs(box.top - boxes[0].top) < 1 && box.left >= 0 && box.right <= width && box.height >= 44));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    if (width === 1440) await screenshot('five-options-desktop');
    if (width === 375) await screenshot('five-options-mobile');
  }
  pass('All five rating choices share one row at desktop, tablet and 320/375/414px; full accessible labels retained');
  await page.setViewportSize({width: 1440, height: 1050});
  const projection = await page.evaluate(async base => {
    const {underlineAnchors, memoryChunk, chunkPeek} = await import(base + '/memory-chunk.js');
    const root = document.createElement('div');
    root.innerHTML = '<p>🧠A <em>label</em>. A label.</p><p>Outside.</p>';
    underlineAnchors(root, [{start: 1, end: 9, quote: 'A label.'}]);
    const chunk = {id: 'legacy', title: 'Legacy', has_mnemonic: true,
      html: '<div class="outlineNode"><p>Core <strong>text</strong>.</p></div><div class="outlineNode"><p>Existing mnemonic.</p></div>',
      anchors: [], introduction: {html: '<p>Owner A.</p>'}};
    const rendered = memoryChunk(chunk);
    const host = chunkPeek([chunk, {...chunk, id: 'second', title: 'Second chunk', introduction: {html: '<p>Owner B.</p>'}}]);
    host.id = 'bundle-probe'; document.body.append(host);
    return {plain: root.textContent, marks: [...root.querySelectorAll('mark')].map(el => el.textContent).join(''),
      emphasis: root.querySelector('em').textContent, core: rendered.querySelector('.atomicChunkCore').textContent,
      mnemonic: rendered.querySelector('.atomicMnemonicBody').textContent};
  }, base);
  assert.equal(projection.plain, '🧠A label. A label.Outside.');
  assert.equal(projection.marks, 'A label.'); assert.equal(projection.emphasis, 'label');
  assert.equal(projection.core, 'Core text.'); assert.equal(projection.mnemonic, 'Existing mnemonic.');
  await page.locator('#bundle-probe > button').click();
  await page.getByRole('tab', {name: 'Legacy', exact: true}).focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('dialog[open] .atomicSourceDocument').innerText(), 'Owner B.');
  await page.keyboard.press('Escape');
  pass('Unicode offsets, inline formatting, repeated quotes, legacy mnemonic separation and bundled-card keyboard tabs');
  assert.deepEqual(report.errors, []);
} catch (error) {
  report.failure = error.stack; await screenshot('failure').catch(() => {}); throw error;
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); await browser.close();
}
