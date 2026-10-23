import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {createLearningCache} from '../web/learning-cache.js';

const source = readFileSync(new URL('../web/atomic.js', import.meta.url), 'utf8');
const renderStart = source.indexOf('  function render() {');
const openStart = source.indexOf('  async function open(');
const renderSource = source.slice(renderStart, openStart);
const testSource = (source.slice(0, renderStart) + '  function render() {}\n' + source.slice(openStart))
  .replace(/^import .*;\r?\n/gm, '').replace('export function createAtomicView', 'function createAtomicView')
  .replace('return {open, stop, reset()', 'return {open, stop, submit, refresh, savedSubmission, current: () => state, reset()');
const access = {learner_id: 'isolated-learner', features: ['learn', 'submit_answer']};
const baseState = () => ({training_mode: 'atomic_retrieval', topic_id: 'topic', learner_id: access.learner_id,
  course_version: 'version', revision: 1, attempt_id: 'attempt', active_step_id: 'step', phase: 'answer', mode: 'learn',
  current: {card_id: 'question', phase: 'answer'}, access, can_learn: true, can_submit: true});
const prefix = 'graspmemoedu:atomic:https://api.invalid:isolated-learner:topic:version';

async function fixture({error, oldPending = false} = {}) {
  const values = new Map(), calls = [], notice = {};
  const prior = {request_id: 'previous-request', expected_revision: 1, attempt_id: 'attempt', step_id: 'step', answer: 'kept answer'};
  values.set(prefix + ':draft:step', 'kept answer');
  if (oldPending) values.set(prefix + ':submission', JSON.stringify(prior));
  let failure = error, count = 0;
  const bridge = {root: {querySelector: () => notice}, getAccess: () => access, setAccess() {}, getApiOrigin: () => 'https://api.invalid',
    progressChanged() {}, request: async (route, options = {}) => {
      calls.push({route, method: options.method || 'GET', body: options.body});
      if (route.startsWith('submit?')) {
        if (failure) throw failure;
        return {state: {...baseState(), revision: 2, phase: 'feedback', current: {card_id: 'question', phase: 'feedback', correct: true}}};
      }
      if (route.startsWith('submissions/')) throw Object.assign(new Error('Unknown submission'), {status: 404, code: 'unknown_submission'});
      return baseState();
    }};
  const context = vm.createContext({createLearningCache, bridge, document: {hidden: false}, window: {scrollTo() {}},
    sessionStorage: {getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)},
    crypto: {randomUUID: () => 'new-request-' + (++count)}, t: value => value, translateMessage: value => value,
    answerReady: input => Boolean(input.value), setTimeout: () => 1, clearTimeout() {}});
  vm.runInContext(testSource + '\nglobalThis.driver = createAtomicView(bridge);', context);
  const driver = context.driver;
  await driver.open('topic', 'learn', baseState());
  return {driver, calls, values, notice, prior, allowSubmission() { failure = null; }};
}

for (const code of ['rate_limited', 'judge_capacity', 'daily_judge_quota']) test(`${code}: a known unaccepted answer keeps its draft and admission message without polling a nonexistent submission`, async () => {
  const message = 'Admission denied; this answer was not accepted';
  const ui = await fixture({error: Object.assign(new Error(message), {status: 429, code})});
  await ui.driver.submit({value: 'kept answer'});
  assert.equal(ui.driver.savedSubmission(), null);
  assert.equal(ui.values.get(prefix + ':draft:step'), 'kept answer');
  assert.equal(ui.notice.textContent, message);
  await ui.driver.refresh();
  assert.equal(ui.calls.at(-1).route, 'state?topic_id=topic');
  assert.equal(ui.notice.textContent, message);
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 1, 'No automatic resend');
  ui.allowSubmission(); await ui.driver.submit({value: 'kept answer'});
  const posts = ui.calls.filter(x => x.method === 'POST');
  assert.equal(posts.length, 2); assert.notEqual(posts[0].body.request_id, posts[1].body.request_id);
  assert.equal(ui.driver.current().current.correct, true);
});

for (const failure of [{status: 429, code: 'unknown_gateway_limit'}, {status: 0, code: 'timeout'}, {status: 408, code: 'timeout'}, {status: 500, code: 'server_error'}])
  test(`${failure.status}/${failure.code}: uncertain acceptance retains the same request and never resends during refresh`, async () => {
    const ui = await fixture({error: Object.assign(new Error('Uncertain'), failure)});
    await ui.driver.submit({value: 'kept answer'});const saved = ui.driver.savedSubmission();assert(saved);
    await ui.driver.refresh();
    assert(ui.calls.at(-1).route.startsWith('submissions/' + saved.request_id));
    assert.equal(ui.driver.savedSubmission().request_id, saved.request_id);
    assert.equal(ui.calls.filter(x => x.method === 'POST').length, 1);
  });

test('a saved pre-upgrade request with unknown 404 stays safe and can continue through an explicit same-question retry', async () => {
  const ui = await fixture({oldPending: true});
  assert.equal(ui.calls.filter(x => x.method === 'POST').length, 0);
  assert.equal(ui.driver.savedSubmission().request_id, ui.prior.request_id);
  await ui.driver.submit({value: 'kept answer'});
  const posts = ui.calls.filter(x => x.method === 'POST');assert.equal(posts.length, 1);
  assert.equal(JSON.stringify(posts[0].body), JSON.stringify(ui.prior));
  assert.equal(ui.driver.current().current.correct, true);assert.equal(ui.driver.savedSubmission(), null);
});

class Element {
  constructor(tag, className = '', text = '') { this.tag = tag; this.className = className; this.text = text; this.children = []; this.dataset = {}; this.classList = {add() {}}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  querySelectorAll(tag) { return this.children.flatMap(child => [...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag)]); }
}
function pageHeader(phase, mode = 'learn') {
  const root = new Element('div'), state = {...baseState(), title: 'Answer-bearing topic grammar rule', phase, mode,
    current: ['answer', 'feedback'].includes(phase) ? {phase} : null, history: [], learning_sections: [],
    chunk: phase === 'chunk' ? {} : null, introduction: {html: 'teaching'}, topic_progress: {}};
  const node = (tag, className, text) => new Element(tag, className, text);
  const context = vm.createContext({state, root, node, selected: null, failure: null, busy: false, audioControl: null,
    bridge: {homeLabel: () => 'Home', homeHref: () => '/', reviewHref: () => '/'},
    document: {getElementById: () => ({isConnected: true}), head: {append() {}}},
    t: key => ({'atomic.title': 'Atomic retrieval training', 'atomic.reviewTitle': 'Review'}[key] || key),
    button: text => node('button', '', text), topicHistory: () => [], topicProgress: () => node('div', 'progress', '1 / 3'),
    introductionContent: () => [node('div', '', 'teaching')], renderChunk: () => node('div', '', 'chunk'),
    renderQuestion: () => node('section', '', 'Actual question body')});
  vm.runInContext(renderSource + '\nrender();', context);
  return root.querySelectorAll('h1').map(x => x.text);
}
test('an active initial question has a neutral page header, without the authored topic rule', () => {
  assert.deepEqual(pageHeader('answer'), ['Atomic retrieval training']);
});
test('an active review question also has a neutral page header', () => {
  assert.deepEqual(pageHeader('answer', 'review'), ['Review']);
});
for (const phase of ['feedback', 'introduction', 'chunk']) test(`${phase}: the authored Topic heading remains available`, () => {
  assert.deepEqual(pageHeader(phase), ['Answer-bearing topic grammar rule']);
});
