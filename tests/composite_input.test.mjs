import assert from 'node:assert/strict';
import test from 'node:test';
import {compositeInput} from '../web/composite-input.js';

// Contract tests use a small DOM; option wrapping and native radio behaviour are
// also checked in Chrome against the actual prepared content package.
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = {};
    this.events = {}; this.className = ''; this.value = ''; this.disabled = false; this.checked = false;
  }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) {
    this.html = value; this.children = []; this.text = value.replace(/<[^>]*>/g, '');
    for (const match of value.matchAll(/<span data-control="([^"]+)"[^>]*><\/span>/g)) {
      const marker = new Element('span'); marker.dataset.control = match[1]; this.append(marker);
    }
  }
  get innerHTML() { return this.html; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
  dispatchEvent(event) { for (const fn of this.events[event.type] || []) fn(event); return true; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); }
  replaceWith(other) {
    const parent = this.parentElement, index = parent.children.indexOf(this);
    parent.children[index] = other; other.parentElement = parent;
  }
  matches(selector) {
    if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
    if (selector === '[data-control]') return 'control' in this.dataset;
    if (selector === 'input:checked') return this.tagName === 'INPUT' && this.checked;
    return this.tagName.toLowerCase() === selector;
  }
  querySelectorAll(selector) {
    const found = [], selectors = selector.split(',');
    for (const child of this.children) {
      if (selectors.some(value => child.matches(value))) found.push(child);
      found.push(...child.querySelectorAll(selector));
    }
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  focus() { document.activeElement = this; }
  scrollIntoView() { this.scrolled = true; }
}
globalThis.document = {createElement: tag => new Element(tag), getElementById: () => null};
const select = {id: 'reading', type: 'select', label: '1. Choose the supported explanation.', options: [
  {id: 'a', text: 'A sentence with enough detail to wrap onto several lines on a small screen.'},
  {id: 'b', text: 'A different explanation.'},
]};
const shortText = {id: 'number', type: 'text', label: '2. Number', input_type: 'number'};
const spec = {type: 'composite', grading: 'exact', controls: [select, shortText], sections: [
  {id: 'first', html: '<p>Read the complete account before choosing.</p>', control_ids: ['reading'], complete: true},
  {id: 'second', html: '<p>Record the number.</p>', control_ids: ['number'], complete: true},
]};
const event = type => new Event(type, {bubbles: true});
const choose = (widget, value) => {
  const radio = widget.element.querySelectorAll('input').find(el => el.type === 'radio' && el.value === value);
  for (const other of widget.element.querySelectorAll('input')) if (other.name === radio.name) other.checked = false;
  radio.checked = true; radio.dispatchEvent(event('change')); return radio;
};
const state = widget => JSON.parse(widget.input.value);

test('select controls show every option without preselecting or changing serialized keys', () => {
  const widget = compositeInput(spec, {id: 'answer', formId: 'answer-form'}), root = widget.element;
  assert.equal(root.querySelectorAll('select').length, 0);
  assert.equal(root.querySelectorAll('.choiceOption').length, 2);
  assert.equal(root.querySelector('.compositeLabel').textContent, select.label);
  assert.equal(root.querySelector('fieldset').dataset.controlId, 'reading');
  assert(root.querySelectorAll('input').every(radio => !radio.checked && radio.getAttribute('form') === 'answer-form'));
  assert.deepEqual(state(widget), {section: 0, values: {reading: '', number: ''}});
  assert(widget.input.questionControl.empty()); assert(!widget.complete());
  assert(root.querySelector('.compositeNext').disabled);
  let updates = 0; widget.input.addEventListener('input', () => updates++);
  choose(widget, 'b');
  assert.deepEqual(state(widget), {section: 0, values: {reading: 'b', number: ''}});
  assert.equal(updates, 1); assert(!root.querySelector('.compositeNext').disabled);
  assert(root.querySelector('.choiceBody').textContent.includes('several lines'));
});

test('selection advances through the existing section gate and short text keeps its payload', () => {
  const widget = compositeInput(spec, {id: 'answer'});
  widget.element.querySelector('.compositeNext').dispatchEvent(event('click'));
  assert.equal(state(widget).section, 0);
  choose(widget, 'a'); widget.element.querySelector('.compositeNext').dispatchEvent(event('click'));
  assert.equal(state(widget).section, 1); assert(!widget.complete());
  const field = widget.element.querySelector('.compositeField');
  assert.equal(field.type, 'text'); assert.equal(field.inputMode, 'decimal'); assert.equal(field.dataset.controlId, 'number');
  field.value = '27'; field.dispatchEvent(event('input'));
  assert.deepEqual(state(widget), {section: 1, values: {reading: 'a', number: '27'}});
  assert(widget.complete()); assert(!widget.input.questionControl.empty());
});

test('old drafts restore selected option IDs and stale drafts cannot skip an unfinished section', () => {
  const saved = {section: 0, values: {reading: 'b', number: ''}};
  const widget = compositeInput(spec, {id: 'answer', value: JSON.stringify(saved)});
  assert.equal(widget.element.querySelector('input:checked').value, 'b'); assert.deepEqual(state(widget), saved);
  widget.input.value = JSON.stringify({section: 1, values: {reading: 'unknown-option', number: '8'}});
  widget.input.dispatchEvent(event('input'));
  assert.equal(state(widget).section, 0); assert.equal(widget.element.querySelector('input:checked'), null);
  assert(widget.element.querySelector('.compositeNext').disabled); assert(!widget.complete());
  widget.input.value = JSON.stringify({section: 1, values: {reading: 'a', number: '8'}});
  widget.input.dispatchEvent(event('input'));
  assert.equal(widget.element.querySelector('.compositeField').value, '8'); assert(widget.complete());
});

test('disabled state applies to groups and radios, prevents changes, and resumes the existing gate', () => {
  const widget = compositeInput(spec, {id: 'answer'}), before = widget.input.value;
  widget.input.questionControl.setDisabled(true);
  assert(widget.input.disabled); assert(widget.element.querySelector('fieldset').disabled);
  assert(widget.element.querySelectorAll('input').every(field => field.disabled));
  choose(widget, 'a'); assert.equal(widget.input.value, before); assert(!widget.complete());
  widget.input.questionControl.setDisabled(false);
  assert(!widget.element.querySelector('fieldset').disabled);
  assert(widget.element.querySelectorAll('input').every(field => !field.disabled));
  assert(widget.element.querySelector('.compositeNext').disabled);
  choose(widget, 'b'); assert(!widget.element.querySelector('.compositeNext').disabled);
});

test('submitted read-only answers show all sections and their stored values', () => {
  const widget = compositeInput(spec, {id: 'history', disabled: true,
    value: JSON.stringify({section: 1, values: {reading: 'b', number: '27'}})});
  assert.equal(widget.element.querySelectorAll('.compositeSection').length, 2);
  assert.equal(widget.element.querySelector('input:checked').value, 'b');
  assert.equal(widget.element.querySelector('.compositeField').value, '27');
  assert(widget.element.querySelectorAll('input').every(field => field.disabled));
  assert.equal(widget.element.querySelector('.compositeNext'), null); assert(!widget.complete());
});

test('explicit passage choices retain lower radios and synchronize with the inline select', () => {
  const inline = {...spec, sections: [{id: 'inline', complete: true, control_ids: ['reading', 'number'],
    html: '<p>Start <span data-control="reading"></span> middle <span data-control="number"></span> end.</p>'}]};
  const widget = compositeInput(inline, {id: 'inline'}), text = widget.element.querySelector('.courseContent');
  assert.equal(text.querySelector('fieldset'), null);
  const dropdown = text.querySelector('.passageBlankSelect');
  assert.equal(dropdown.dataset.controlId, 'reading');
  assert.equal(text.querySelector('.passageBlankNumber'), null);
  assert.equal(text.querySelector('.compositeField').dataset.controlId, 'number');
  assert.equal(widget.element.querySelector('fieldset').parentElement.className, 'compositeSection');
  dropdown.value = 'b'; dropdown.dispatchEvent(event('change'));
  assert.equal(widget.element.querySelector('input:checked').value, 'b');
  assert.equal(state(widget).values.reading, 'b');
  choose(widget, 'a'); assert.equal(dropdown.value, 'a');
});

test('compiled rich options are rendered in the selectable option rather than a hidden duplicate', () => {
  const rich = {...select, options: [{id: 'a', text: 'x squared', html: '<p><strong>x</strong> squared</p>'}, select.options[1]]};
  const widget = compositeInput({...spec, controls: [rich, shortText]}, {id: 'rich'});
  assert.equal(widget.element.querySelector('.choiceBody').innerHTML, rich.options[0].html);
  assert.equal(widget.element.querySelectorAll('details').length, 0);
  assert.equal(widget.element.querySelectorAll('.choiceBody').length, 2);
});
