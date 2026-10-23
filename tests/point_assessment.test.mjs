import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import {pointAssessment, assessmentItemLabels} from '../web/point-assessment.js';

// A small text-only DOM for result semantics; real browser layout is accepted separately.
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ''; this.value = ''; }
  append(...children) { this.children.push(...children); }
  set textContent(value) { this.children = []; this.value = String(value); }
  get textContent() { return this.value + this.children.map(child => typeof child === 'string' ? child : child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Feedback labels must be inserted as text'); }
}
globalThis.document = {createElement: tag => new Element(tag), createTextNode: text => String(text), documentElement: {}};
const source = await fs.readFile(new URL('../web/point-assessment.js', import.meta.url), 'utf8');
const {setLanguage} = await import(new URL(source.match(/from '([^']*i18n[^']*)'/)[1], new URL('../web/point-assessment.js', import.meta.url)));
const find = (node, cls) => node?.className?.split(' ').includes(cls) ? node : node?.children?.map(child => find(child, cls)).find(Boolean);
const itemRows = root => find(root, 'itemAssessmentList')?.children.map(row => row.textContent) || [];
const points = [{id: 'private-point-key', text: 'Past tense', correct: false, basis: 'items', item_ids: ['b57', 'b56']}];
test.beforeEach(() => setLanguage('zh-CN'));

test('fill blanks use authored question numbers in question order, not evidence key order', () => {
  const spec = {type: 'fill_blank', blanks: [{id: 'b56', label: '56'}, {id: 'b57', label: '57'}]};
  const root = pointAssessment({items: [{id: 'b57', correct: false}, {id: 'b56', correct: true}], points}, spec);
  assert.deepEqual(itemRows(root), ['第 56 题正确', '第 57 题错误']);
  assert.match(root.textContent, /需复习 · Past tense/);
  assert.match(root.textContent, /依据：第 56 题, 第 57 题/);
  assert.doesNotMatch(root.textContent, /b56|b57|private-point-key/);
});

test('composite feedback preserves the whole-answer fallback without inventing an unmapped item result', () => {
  const spec = {type: 'composite', controls: [{id: 'q1', label: '1'}, {id: 'q2', label: '2'}, {id: 'q3', label: '3'}]};
  const root = pointAssessment({items: [{id: 'q1', correct: true}, {id: 'q2', correct: false}], points: [
    {id: 'evidence', text: 'Use evidence', correct: true, basis: 'items', item_ids: ['q1']},
    {id: 'cohesion', text: 'Cohesion', correct: false, basis: 'whole', item_ids: []},
  ]}, spec);
  assert.deepEqual(itemRows(root), ['第 1 题正确', '第 2 题错误']);
  assert.match(root.textContent, /依据整题结果/);
  assert.doesNotMatch(root.textContent, /第 3 题|q1|q2|q3/);
  assert.equal(find(root, 'pointAssessmentList').children.length, 2);
});

test('one item can support several points without duplicating the item result', () => {
  const root = pointAssessment({items: [{id: 'shared', correct: false, point_ids: ['p1', 'p2']}], points: [
    {id: 'p1', text: 'Evidence', correct: false, basis: 'items', item_ids: ['shared']},
    {id: 'p2', text: 'Meaning', correct: false, basis: 'items', item_ids: ['shared']},
  ]}, {type: 'composite', controls: [{id: 'shared', label: 'Question 6(a)'}]});
  assert.deepEqual(itemRows(root), ['Question 6(a)错误']);
  assert.equal(find(root, 'pointAssessmentList').children.length, 2);
});

test('unknown keys and non-boolean outcomes never appear as learner results', () => {
  const spec = {type: 'composite', controls: [{id: 'known', label: '4'}]};
  assert.equal(pointAssessment({items: [{id: 'secret-key', correct: true}]}, spec), null);
  assert.equal(pointAssessment({items: [{id: 'known', correct: 'false'}]}, spec), null);
  const root = pointAssessment({points: [{id: 'secret-point', correct: true, basis: 'items', item_ids: ['secret-key']}]}, spec);
  assert.match(root.textContent, /知识点 1/);
  assert.match(root.textContent, /依据小题结果/);
  assert.doesNotMatch(root.textContent, /secret-|第 4 题/);
});

test('before submission and unassessed legacy questions have no result component', () => {
  const spec = {type: 'fill_blank', blanks: [{id: 'b1'}]};
  for (const result of [undefined, null, {}, {items: [], points: []}]) assert.equal(pointAssessment(result, spec), null);
  assert.equal(pointAssessment({items: [{id: 'b1', correct: true}]}, undefined), null);
});

test('every supported deterministic interaction has public labels without backend ID fallback', () => {
  assert.deepEqual([...assessmentItemLabels({type: 'fill_blank', blanks: [{id: 'key'}]})], [['key', '第 1 空']]);
  assert.deepEqual([...assessmentItemLabels({type: 'true_false', statements: [{id: 'statement-key'}]})], [['statement-key', '第 1 题']]);
  assert.deepEqual([...assessmentItemLabels({type: 'matching', left: [{id: 'left-key', text: 'reuse'}], fixed_side: 'right'})], [['left-key', 'reuse']]);
  for (const type of ['text', 'choice']) assert.deepEqual([...assessmentItemLabels({type})], [['answer', '你的答案']]);
});

test('English localizes structural feedback but preserves authored labels and point content', () => {
  setLanguage('en');
  const root = pointAssessment({items: [{id: 'x', correct: false}, {id: 'y', correct: true}], points},
    {type: 'composite', controls: [{id: 'x', label: '8'}, {id: 'y', label: '作者给定的标签'}]});
  assert.deepEqual(itemRows(root), ['Question 8Incorrect', '作者给定的标签Correct']);
  assert.match(root.textContent, /Item results/);
  assert.match(root.textContent, /Memory-point feedback/);
  assert.match(root.textContent, /Review again · Past tense/);
});

test('authored labels and point descriptions are plain text, including markup-looking content', () => {
  const label = '<img src=x onerror=alert(1)>', description = '<script>unsafe()</script>';
  const root = pointAssessment({items: [{id: 'safe', correct: true}], points: [
    {id: 'p', text: description, correct: true, basis: 'items', item_ids: ['safe']},
  ]}, {type: 'composite', controls: [{id: 'safe', label}]});
  assert.equal(find(root, 'assessmentItemLabel').textContent, label);
  assert.match(root.textContent, /<script>unsafe\(\)<\/script>/);
  assert.deepEqual(itemRows(root), [label + '正确']);
});
