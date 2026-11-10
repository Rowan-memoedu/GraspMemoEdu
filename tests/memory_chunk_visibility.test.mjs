import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {before, after, beforeEach, afterEach, test} from 'node:test';

let playwright;
try { playwright = createRequire(import.meta.url)('playwright'); }
catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  const dependencies = process.env.PLAYWRIGHT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
  playwright = createRequire(path.join(dependencies, '_entry.cjs'))('playwright');
}
const web = process.env.GRASPMEMOEDU_TEST_WEB
  ? pathToFileURL(path.resolve(process.env.GRASPMEMOEDU_TEST_WEB) + path.sep) : new URL('../web/', import.meta.url);
let browser, page, errors;
before(async () => {
  browser = await playwright.chromium.launch({headless: true, executablePath: process.env.CHROME_EXECUTABLE
    || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
});
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage(); page.setDefaultTimeout(5000); errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://chunk.test') return route.abort();
    const name = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
    if (!/^[a-z0-9_./-]+$/i.test(name) || name.includes('..')) return route.abort();
    try {
      let body = await readFile(new URL(name, web));
      if (name === 'index.html') body = Buffer.from(body.toString().replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ''));
      await route.fulfill({body, contentType: name.endsWith('.html') ? 'text/html' : /\.m?js$/.test(name)
        ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'application/octet-stream'});
    } catch { await route.abort(); }
  });
  await page.goto('https://chunk.test/');
});
afterEach(async () => { await page?.close(); assert.deepEqual(errors, []); });

async function mount(kind, {mode = 'learn', correct = true, reject = false} = {}) {
  await page.evaluate(async ({kind, mode, correct, reject}) => {
    const root = document.createElement('div'); document.body.append(root);
    const access = {learner_id: 'chunk-test', features: ['learn', 'submit_answer'], is_guest: true};
    const chunks = [{id: 'chunk', title: 'Synthetic chunk', content_html: '<p>Chunk content.</p>',
      introduction: {html: '<p>Synthetic source.</p>'}, anchors: []}];
    const card = index => ({index, card_id: `card-${index}`, title: `Card ${index}`, phase: 'answer',
      html: '<p>Recall the label.</p>', answer: '', elapsed_ms: 0, can_self_rate: true, revealed: false,
      source_kind: 'atomic', interaction: {type: 'text'}, memory_chunks: chunks});
    let state = kind === 'atomic' ? {training_mode: 'atomic_retrieval', topic_id: 'topic', learner_id: access.learner_id,
      course_version: 'version', revision: 1, attempt_id: 'attempt', active_step_id: 'step-0', mode,
      phase: 'answer', current: card(0), history: [], learning_sections: [], access, can_learn: true, can_submit: true}
      : {id: 'paper', title: 'Synthetic review', revision: 1, mode: 'question', phase: 'answer',
        can_submit: true, can_edit: true, items: [card(0), card(1)], math_css: ''};
    const request = async (route, {body} = {}) => {
      const action = route.split('?')[0].split('/').at(-1);
      const item = kind === 'atomic' ? state.current : state.items[body?.index ?? 0];
      if (action === 'draft') for (const answer of body.answers) Object.assign(state.items[answer.index], answer);
      if (action === 'reveal' || action === 'submit') {
        if (reject) throw Object.assign(new Error('Request rejected'), {status: 429, code: 'judge_capacity'});
        item.explanation_html = '<p>Reference answer.</p>';
        if (action === 'reveal') item.revealed = true;
        else {
          if (kind === 'atomic') { item.answer = body.answer; item.phase = state.phase = 'feedback'; item.correct = correct; }
          else { item.completed = true; item.result = {correct, reason: 'Recorded'}; }
        }
      }
      if (action === 'continue') { state.current = card(1); state.phase = 'answer'; state.active_step_id = 'step-1'; }
      if (body) state.revision++;
      return structuredClone(kind === 'atomic' && action === 'submit' ? {state} : state);
    };
    const shared = {root, request, getAccess: () => access, progressChanged() {}, formatDate: value => value};
    if (kind === 'atomic') {
      const {createAtomicView} = await import('/atomic.js');
      window.view = createAtomicView({...shared, setAccess() {}, getApiOrigin: () => 'https://chunk.test',
        homeHref: () => '#/', reviewHref: () => '#/review'});
      await view.open('topic', mode, structuredClone(state));
    } else {
      const {createPaperView} = await import('/review-papers.js');
      window.view = createPaperView({...shared, href: route => '#' + route});
      await view.open('paper');
    }
  }, {kind, mode, correct, reject});
}
const absent = async () => assert.equal(await page.locator('.atomicPeekButton, .memoryChunkDialog').count(), 0);
async function inspect() {
  await page.locator('.atomicPeekButton').click();
  await page.locator('.memoryChunkDialog[open]').waitFor();
  assert.equal(await page.locator('.memoryChunkDialog .atomicChunkCore').innerText(), 'Chunk content.');
  await page.keyboard.press('Escape');
  await page.locator('.memoryChunkDialog').waitFor({state: 'detached'});
}
async function next(kind) {
  if (kind === 'atomic') await page.locator('.atomicQuestion > .primaryButton').click();
  else await page.locator('[data-question-index="1"]').click();
  await page.waitForFunction(kind => kind === 'atomic' ? document.querySelector('[data-card-id="card-1"]')
    : document.querySelector('[data-question-index="1"][aria-current="step"]'), kind);
  await absent();
}

for (const [kind, mode] of [['atomic', 'learn'], ['atomic', 'review'], ['paper', 'review']]) {
  for (const correct of [true, false]) test(`${kind}/${mode}: only submitted feedback unlocks chunks (${correct ? 'correct' : 'incorrect'})`, async () => {
    await mount(kind, {mode, correct}); await absent();
    const input = page.locator(kind === 'atomic' ? '#atomicAnswer' : '#paperAnswer');
    await input.fill('A draft'); await absent();
    await page.locator(kind === 'atomic' ? '#atomicAnswerForm button[type="submit"]' : '#paperSubmit').click();
    await page.locator('.atomicPeekButton').waitFor(); await inspect();
    assert.equal(await page.locator('textarea.answerInput:visible').inputValue(), 'A draft');
    await next(kind);
  });
  test(`${kind}/${mode}: Show Answer unlocks chunks before rating`, async () => {
    await mount(kind, {mode}); await absent();
    await page.locator('.showAnswer').click(); await page.locator('.trainingRatings').waitFor();
    await inspect();
    if (kind === 'paper') await next(kind);
  });
  test(`${kind}/${mode}: a rejected submission leaves chunks inaccessible`, async () => {
    await mount(kind, {mode, reject: true});
    await page.locator(kind === 'atomic' ? '#atomicAnswer' : '#paperAnswer').fill('A draft');
    await page.locator(kind === 'atomic' ? '#atomicAnswerForm button[type="submit"]' : '#paperSubmit').click();
    await page.locator(kind === 'atomic' ? '#atomicNotice' : '.paperError').waitFor(); await absent();
  });
}
