import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

// Execute the real app bindings and shared drawer renderer. No API, learning
// state, browser storage or artificial lesson content is involved.
const app = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const shared = readFileSync(new URL('../web/learning-ui.js', import.meta.url), 'utf8');
const setter = app.slice(app.indexOf('function setHistoryOpen('), app.indexOf('\nfunction renderReview('));
const bindings = app.slice(app.indexOf('$("historyButton").addEventListener'), app.indexOf('$("pauseButton").addEventListener'));
const renderer = shared.slice(shared.indexOf('export function readerDrawerState('), shared.indexOf('// Stateless presentation')).replace('export ', '');

function fixture(width = 1365, hidden = false) {
  const events = new Map(), nodes = new Map();
  const classes = () => {
    const values = new Set();
    return {toggle(name, enabled) { enabled ? values.add(name) : values.delete(name); }, contains: name => values.has(name)};
  };
  const document = {body: {classList: classes()}, activeElement: null, dialog: false,
    addEventListener(name, callback) { events.set(name, callback); }, querySelector() { return this.dialog ? {} : null; }};
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, {hidden: id === 'appLayout' ? hidden : false,
      classList: classes(), attrs: {}, events: {}, setAttribute(name, value) { this.attrs[name] = value; },
      addEventListener(name, callback) { this.events[name] = callback; }, focus() { document.activeElement = id; }});
    return nodes.get(id);
  };
  const narrowLayout = {matches: width <= 1000, addEventListener(name, callback) { this.change = callback; }};
  const context = vm.createContext({document, narrowLayout, $: get, t: value => value});
  vm.runInContext('let historyOpen=true;\n' + renderer + setter + bindings, context);
  return {
    get, document,
    click(id) { get(id).events.click(); },
    escape() { events.get('keydown')({key: 'Escape'}); },
    resize(nextWidth) {
      const next = nextWidth <= 1000;
      if (next !== narrowLayout.matches) { narrowLayout.matches = next; narrowLayout.change(); }
    },
    snapshot() { return {panelHidden: get('historyPanel').hidden, scrimHidden: get('historyScrim').hidden,
      overlay: document.body.classList.contains('historyDrawerOpen'), expanded: get('historyDrawerToggle').attrs['aria-expanded']}; },
  };
}

for (const width of [320, 375, 414, 768]) test(`first mobile load at ${width}px leaves the lesson unobscured`, () => {
  const ui = fixture(width);
  assert.deepEqual(ui.snapshot(), {panelHidden: true, scrimHidden: true, overlay: false, expanded: 'false'});
});

test('desktop completed reader closes its sidebar when crossing to mobile and reopens on desktop', () => {
  const ui = fixture();
  assert.deepEqual(ui.snapshot(), {panelHidden: false, scrimHidden: true, overlay: false, expanded: 'true'});
  for (const width of [320, 375]) {
    ui.resize(width);
    assert.deepEqual(ui.snapshot(), {panelHidden: true, scrimHidden: true, overlay: false, expanded: 'false'});
    ui.resize(1365);
    assert.deepEqual(ui.snapshot(), {panelHidden: false, scrimHidden: true, overlay: false, expanded: 'true'});
  }
});

test('manual mobile opening survives resizing inside the same breakpoint and closes by scrim or button', () => {
  const ui = fixture(375);
  ui.click('historyDrawerToggle');
  assert.deepEqual(ui.snapshot(), {panelHidden: false, scrimHidden: false, overlay: true, expanded: 'true'});
  assert.equal(ui.document.activeElement, 'historyButton');
  ui.resize(414);
  assert.equal(ui.snapshot().overlay, true);
  ui.click('historyScrim');
  assert.equal(ui.snapshot().overlay, false);
  assert.equal(ui.document.activeElement, 'historyDrawerToggle');
  ui.click('historyDrawerToggle'); ui.click('historyButton');
  assert.equal(ui.snapshot().panelHidden, true);
});

test('Escape closes the mobile drawer unless a dialog owns Escape', () => {
  const ui = fixture(375);
  ui.click('historyDrawerToggle'); ui.document.dialog = true; ui.escape();
  assert.equal(ui.snapshot().overlay, true);
  ui.document.dialog = false; ui.escape();
  assert.equal(ui.snapshot().overlay, false);
  assert.equal(ui.document.activeElement, 'historyDrawerToggle');
});

test('an inactive legacy reader never puts a page-wide overlay on the active route', () => {
  const ui = fixture(375, true);
  ui.click('historyDrawerToggle');
  assert.equal(ui.snapshot().overlay, false);
});
