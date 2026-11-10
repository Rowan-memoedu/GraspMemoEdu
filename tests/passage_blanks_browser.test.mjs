import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {before, after, beforeEach, afterEach, test} from 'node:test';

// A real DOM is necessary for text-node replacement, native selection and event
// bubbling. All requests are fulfilled from this checkout; no API is contacted.
let playwright;
try { playwright = createRequire(import.meta.url)('playwright'); }
catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND') throw error;
  const dependencies = process.env.PLAYWRIGHT_NODE_MODULES || path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules');
  playwright = createRequire(path.join(dependencies, '_entry.cjs'))('playwright');
}
const {chromium} = playwright;
let browser, page;
before(async () => {
  browser = await chromium.launch({headless: true, executablePath: process.env.CHROME_EXECUTABLE
    || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
});
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage();
  await page.route('http://passage.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/') return route.fulfill({contentType: 'text/html', body: '<!doctype html><body data-subject="english"><div id="stem"></div><form id="answer-form"></form></body>'});
    if (!/^\/web\/(?:[a-z0-9_.-]+\/)*[a-z0-9_.-]+\.m?js$/i.test(pathname)) return route.abort();
    return route.fulfill({contentType: 'text/javascript', body: await readFile(new URL('..' + pathname, import.meta.url), 'utf8')});
  });
  await page.goto('http://passage.test/');
});
afterEach(async () => { await page?.close(); });

const options = [{id: 'a', text: 'the first expression'}, {id: 'b', text: 'the second expression'}];
const control = (id, label) => ({id, label, type: 'select', options});
const composite = controls => ({version: 2, type: 'composite', grading: 'exact', controls,
  sections: [{id: 'one', control_ids: controls.map(c => c.id), complete: true, html: '<p>Choose the appropriate option.</p>'}]});
async function mount(spec, html, extra = {}) {
  await page.evaluate(async ({spec, html, extra}) => {
    const {questionInput} = await import('/web/question-input.js');
    const stem = document.getElementById('stem'), form = document.getElementById('answer-form');
    stem.innerHTML = html; window.submissions = 0; window.updates = 0;
    form.addEventListener('submit', event => { event.preventDefault(); window.submissions++; });
    window.widget = questionInput(spec, {id: 'answer', stem, formId: form.id, ...extra});
    form.append(widget.element); widget.input.addEventListener('input', () => window.updates++);
  }, {spec, html, extra});
}
const draft = () => page.evaluate(() => JSON.parse(widget.input.value));
const inline = id => page.locator(`#stem .passageBlank[data-passage-id="${id}"] select`);

test('legacy bracket gaps bind unique prose occurrences and leave repeated instructions untouched', async () => {
  const spec = composite([control('first', '[1]'), control('second', '[2]'), control('meaning', 'What does the sentence mean?')]);
  await mount(spec, '<p>The guide [1] the visitors while the driver [2] the door.</p><p>为 [1] 和 [2] 各选一项，使句意完整。</p>');
  assert.equal(await page.locator('#stem select').count(), 2);
  assert.equal(await page.locator('#answer-form fieldset').count(), 3);
  assert.equal(await page.locator('#stem > p').last().textContent(), '为 [1] 和 [2] 各选一项，使句意完整。');
  await inline('first').selectOption('b');
  assert.equal(await page.locator('fieldset[data-control-id="first"] input:checked').inputValue(), 'b');
  assert.equal((await draft()).values.first, 'b');
  assert.equal(await page.evaluate(() => updates), 1);
  await page.locator('fieldset[data-control-id="first"] input[value="a"]').check();
  assert.equal(await inline('first').inputValue(), 'a');
  assert.equal(await page.evaluate(() => updates), 2);
  assert.equal(await page.evaluate(() => new FormData(document.getElementById('answer-form')).getAll('answer').length), 1);
});

test('plain numbered cloze controls require a gap cue and numbered underlines are replaced as one blank', async () => {
  await mount(composite([control('opaque', '1')]), '<p>Read the passage and choose the option for the blank.</p><p>The children (1) ______ before the bus arrived.</p>');
  assert.equal(await inline('opaque').count(), 1);
  assert(!await page.locator('#stem').textContent().then(text => text.includes('______')));
  assert.equal(await page.locator('.passageBlankNumber').textContent(), '1');
});

test('prefixed gap instructions bind prose blanks without turning numbered reading questions into gaps', async () => {
  const spec = composite([
    control('phrase', '（1）选择补全空白的表达。'), control('verb', '(2) Fill in the blank.'),
    control('word', '3. Choose the expression for the missing word.'),
    control('reading', '（4）这段阅读的主要内容是什么？'), control('meaning', "(5) What does 'blank' mean in the report?"),
    control('reason', '(6) Why did the artist choose to leave a blank space?'), control('cause', '（7）文中为何选择留出空白？'),
  ]);
  await mount(spec, '<p>The driver asked us (1) ___ before the train left. We [2] the luggage and (3) ___ the seats.</p><p>为（1）选择补全空白的表达。</p><p>The appendix contains notes (4), (5), (6) and (7) about this journey.</p>');
  assert.deepEqual(await page.locator('#stem select').evaluateAll(fields => fields.map(field => field.dataset.controlId)), ['phrase', 'verb', 'word']);
  assert.equal(await page.locator('fieldset').count(), 7);
  assert.equal(await page.locator('#stem p').nth(1).textContent(), '为（1）选择补全空白的表达。');
  await inline('phrase').selectOption('b');
  assert.equal(await page.locator('fieldset[data-control-id="phrase"] input:checked').inputValue(), 'b');
  assert.equal((await draft()).values.phrase, 'b');
});

test('external draft restoration reuses stem nodes, restores both views and never selects an unknown option', async () => {
  await mount(composite([control('c', '[1]')]), '<p>The engineer [1] before the inspection.</p>', {value: JSON.stringify({section: 0, values: {c: 'b'}})});
  assert.equal(await inline('c').inputValue(), 'b');
  await page.evaluate(() => { window.originalSelect = document.querySelector('#stem select'); });
  for (const value of ['a', 'missing', 'b']) {
    await page.evaluate(value => { widget.input.value = JSON.stringify({section: 0, values: {c: value}}); widget.input.dispatchEvent(new Event('input', {bubbles: true})); }, value);
    assert.equal(await page.locator('#stem select').count(), 1);
    assert(await page.evaluate(() => originalSelect === document.querySelector('#stem select')));
    assert.equal(await inline('c').inputValue(), value === 'missing' ? '' : value);
    assert.equal(await page.locator('fieldset input:checked').count(), value === 'missing' ? 0 : 1);
  }
});

test('external passage controls obey section gates and busy/disabled state without losing draft values', async () => {
  const spec = composite([control('a', '[1]'), control('b', '[2]')]);
  spec.sections = [{id: 'one', control_ids: ['a'], complete: true, html: '<p>First part.</p>'},
    {id: 'two', control_ids: ['b'], complete: true, html: '<p>Second part.</p>'}];
  await mount(spec, '<p>The first visitor [1] the route. The next visitor [2] the route.</p>');
  assert(await inline('b').isDisabled());
  await inline('a').selectOption('a'); await page.locator('.compositeNext').click();
  assert(await inline('a').isDisabled()); assert(!await inline('b').isDisabled());
  await inline('b').selectOption('b'); const saved = await draft();
  await page.evaluate(() => widget.input.questionControl.setDisabled(true));
  assert(await inline('b').isDisabled());
  await page.evaluate(() => { const field = document.querySelector('#stem [data-passage-id="b"] select'); field.value = 'a'; field.dispatchEvent(new Event('change', {bubbles: true})); });
  assert.deepEqual(await draft(), saved);
  await page.evaluate(() => widget.input.questionControl.setDisabled(false));
  assert.equal(await inline('b').inputValue(), 'b'); assert(await inline('a').isDisabled());
  assert(await page.evaluate(() => widget.input.questionControl.complete()));
});

test('matching passage gaps are static, exclude ranges and keep the existing pairing answer', async () => {
  const spec = {version: 1, type: 'matching', fixed_side: 'left', row_count: 2, fixed: ['x', 'y'], direction: 'right',
    left: [{id: 'x', text: '(1)'}, {id: 'y', text: '(2)'}], right: [{id: 'r', text: 'A new sentence.'}, {id: 's', text: 'Another sentence.'}, {id: 't', text: 'Unused sentence.'}]};
  await mount(spec, '<p>Choose a sentence for each gap (1)-(2).</p><p>We opened the gate. (1) Everyone came inside.</p><p>The meeting finished. (2) We walked home.</p>');
  assert.equal(await page.locator('#stem .passageBlankStatic').count(), 2);
  assert.deepEqual(await page.locator('#stem .passageBlankValue').allTextContents(), ['', '']);
  assert.equal(await page.locator('#stem input,#stem select').count(), 0);
  assert.equal(await page.locator('#stem .passageNumberLink').count(), 2);
  assert.equal(await page.locator('#stem p').first().textContent(), 'Choose a sentence for each gap (1)-(2).');
  await page.locator('.matchingOption[data-side="right"][data-option-id="r"]').click();
  await page.locator('.matchingSlot[data-side="right"][data-row="0"]').click();
  assert.deepEqual(await draft(), [{left: 'x', right: 'r'}, {left: 'y', right: null}]);
});

test('explicit grammar blanks keep opaque IDs, shared values and read-only history', async () => {
  const spec = {version: 1, type: 'fill_blank', blanks: [{id: 'opaque9999', label: '61'}]};
  await mount(spec, '<p>First: {{blank:opaque9999}}. Again: {{blank:opaque9999}}.</p><pre>{{blank:opaque9999}}</pre>');
  assert.equal(await page.locator('#stem textarea').count(), 2);
  assert.deepEqual(await page.locator('.passageBlankNumber').allTextContents(), ['61', '61']);
  await page.locator('#stem textarea').first().fill('saved wording');
  assert.deepEqual(await draft(), {opaque9999: 'saved wording'});
  assert.equal(await page.locator('#stem textarea').last().inputValue(), 'saved wording');
  assert.equal(await page.locator('pre').textContent(), '{{blank:opaque9999}}');
  await page.evaluate(() => widget.input.questionControl.setDisabled(true));
  await page.evaluate(() => { const field = document.querySelector('#stem textarea'); field.value = 'changed'; field.dispatchEvent(new Event('input', {bubbles: true})); });
  assert.deepEqual(await draft(), {opaque9999: 'saved wording'});
  await page.evaluate(() => widget.input.value = JSON.stringify({opaque9999: 'restored'}));
  await page.evaluate(() => widget.input.dispatchEvent(new Event('input', {bubbles: true})));
  assert.deepEqual(await page.locator('#stem textarea').evaluateAll(fields => fields.map(field => [field.value, field.disabled])), [['restored', true], ['restored', true]]);
});

test('single-choice unique underlines synchronize both ways, including an instruction and prose in one paragraph', async () => {
  await mount({version: 1, type: 'choice', multiple: false, options}, '<p>阅读语境，选择一项补全空白：The visitor asked us ___ the window.</p>');
  assert.equal(await inline('selected').count(), 1);
  await inline('selected').selectOption('b'); assert.deepEqual(await draft(), {selected: 'b'});
  assert.equal(await page.locator('#answer-form input:checked').inputValue(), 'b');
  await page.locator('#answer-form input[value="a"]').check(); assert.equal(await inline('selected').inputValue(), 'a');
  await page.evaluate(() => { widget.input.value = JSON.stringify({selected: 'b'}); widget.input.dispatchEvent(new Event('input', {bubbles: true})); });
  assert.equal(await inline('selected').inputValue(), 'b');
  await page.evaluate(() => widget.input.questionControl.setDisabled(true)); assert(await inline('selected').isDisabled());
});

test('ambiguous numbers, ordinary reading, multiple-choice and multiple bare blanks retain lower controls', async () => {
  await mount(composite([control('n', '[1]'), control('m', '[2]')]), '<p>The first report says [1].</p><p>The other report says [1].</p><p>The final report says [2].</p><p>为 [2] 选择一项。</p><code>[2]</code><p>$[2]$</p>');
  assert.equal(await inline('n').count(), 0); assert.equal(await inline('m').count(), 1);
  assert.equal(await page.locator('fieldset').count(), 2);
  await page.reload();
  await mount(composite([control('n', '1')]), '<p>The record has a numbered note (1), which is a reference rather than a missing expression.</p>');
  assert.equal(await page.locator('#stem select').count(), 0);
  await page.reload();
  await mount({version: 1, type: 'choice', multiple: true, options}, '<p>The visitor ___ before we left.</p>');
  assert.equal(await page.locator('#stem select').count(), 0);
  await page.reload();
  await mount({version: 1, type: 'choice', multiple: false, options}, '<p>We ___ while they ___.</p><code>___</code><p>$___$</p>');
  assert.equal(await page.locator('#stem select').count(), 0);
});

test('keyboard submission is once for external and nested blanks; native keys and composition remain untouched', async () => {
  const spec = composite([control('outer', '[1]'), control('inner', '2')]);
  spec.sections[0].html = '<p>The second expression goes <span data-control="inner"></span>.</p>';
  await mount(spec, '<p>The first expression goes [1].</p>');
  const result = await page.evaluate(() => {
    const fields = [document.querySelector('select[data-control-id="outer"]'), document.querySelector('select[data-control-id="inner"]')];
    const handled = [];
    for (const field of fields) {
      for (const options of [{key: 'ArrowDown'}, {key: 'Enter'}, {key: 'Enter', ctrlKey: true, isComposing: true}, {key: 'Enter', ctrlKey: true}]) {
        const event = new KeyboardEvent('keydown', {bubbles: true, cancelable: true, ...options}); field.dispatchEvent(event); handled.push(event.defaultPrevented);
      }
    }
    return {handled, submissions};
  });
  assert.deepEqual(result, {handled: [false, false, false, true, false, false, false, true], submissions: 2});
});

test('an explicit marker hidden by an outer replacement keeps its ordinary text input fallback', async () => {
  const controls = [{id: 'outer', type: 'text', label: 'First response'}, {id: 'inner', type: 'text', label: 'Second response'}];
  const spec = composite(controls);
  spec.sections[0].html = '<p>The first response is <span data-control="outer"><span data-control="inner"></span></span>.</p>';
  await mount(spec, '<p>Provide both responses.</p>');
  assert.equal(await page.locator('textarea[data-control-id="outer"]').count(), 1);
  assert.equal(await page.locator('input[data-control-id="inner"]').count(), 1);
  await page.locator('textarea[data-control-id="outer"]').fill('first');
  await page.locator('input[data-control-id="inner"]').fill('second');
  assert(await page.evaluate(() => widget.input.questionControl.complete()));
  assert.deepEqual((await draft()).values, {outer: 'first', inner: 'second'});
});

test('explicit heading markers remain answerable while heading numbers are not inferred as blanks', async () => {
  await mount({version: 1, type: 'fill_blank', blanks: [{id: 'heading'}]}, '<h2>Complete {{blank:heading}}</h2>');
  assert.equal(await page.locator('h2 textarea[data-blank-id="heading"]').count(), 1);
  await page.locator('h2 textarea').fill('complete');
  assert.deepEqual(await draft(), {heading: 'complete'});
  assert(await page.evaluate(() => widget.input.questionControl.complete()));
  await page.reload();
  await mount(composite([control('number', '[1]')]), '<h2>Practice blank [1]</h2><p>There is no missing phrase in this paragraph.</p>');
  assert.equal(await page.locator('#stem select').count(), 0);
  assert.equal(await page.locator('fieldset[data-control-id="number"]').count(), 1);
});
