import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {createLearningCache} from '../web/learning-cache.js';

// Run the actual request/identity code with isolated storage and fake transport.
// No public service, real credentials, learning records, or timers are touched.
const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '')
  .split('$("identityButton").addEventListener')[0]
  .replaceAll('import.meta.url', '"https://site.example/app.js"');
function setup({ nextOrigin = 'https://new.example', status = 502, role = 'guest', invalidConfig = false, newEndpointFails = false } = {}) {
  const store = () => {
    const values = new Map();
    return { get length() { return values.size; }, key: index => [...values.keys()][index], getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  };
  const localStorage = store(), sessionStorage = store(), calls = [];
  let clock = 100000, configs = 0;
  const identity = { learner_id: role + '-original', role, features: [], topics: [] };
  localStorage.setItem('graspmemoedu:identity:https://old.example', 'original-token');
  localStorage.setItem('graspmemoedu:identity-role:https://old.example', role);
  if (role === 'guest') localStorage.setItem('graspmemoedu:guest-identity:https://old.example', 'original-token');
  else localStorage.setItem('graspmemoedu:guest-identity:https://old.example', 'separate-guest-token');
  const context = vm.createContext({
    URL, AbortController, setTimeout, clearTimeout, console, localStorage, sessionStorage, createLearningCache,
    Date: { now: () => clock }, t: value => value, translateMessage: value => value,
    applyStaticTranslations() {}, document: { hasFocus: () => true },
    window: { location: { origin: 'https://site.example' }, matchMedia: () => ({ matches: false }) },
    fetch: async (url, options) => {
      url = String(url); calls.push({ url, method: options.method || 'GET', token: options.headers?.['X-Learning-Identity'] });
      if (url.endsWith('deployment.json')) {
        configs += 1;
        return { ok: true, json: async () => ({ api_origin: configs === 1 ? 'https://old.example' : invalidConfig ? 'http://unsafe.example' : nextOrigin }) };
      }
      if (url.startsWith('https://old.example')) {
        if (status === 0) throw new TypeError('network failed');
        return { ok: false, status, json: async () => {
          if (status === 502) throw new SyntaxError('gateway HTML');
          return { detail: { code: status === 403 ? 'topic_forbidden' : 'course_not_found', message: 'denied' } };
        } };
      }
      if (newEndpointFails) throw new TypeError('replacement endpoint offline');
      if (url.endsWith('/api/access')) return { ok: true, status: 200, json: async () => options.headers['X-Learning-Identity'] === 'separate-guest-token'
        ? { ...identity, role: 'guest', learner_id: 'separate-guest' } : identity };
      return { ok: true, status: 200, json: async () => ({ recovered: true }) };
    },
  });
  vm.runInContext(source + '\nfunction renderIdentity() {}\nfunction render() {}\nfunction renderBusy() {}\nfunction syncAnswerClock() {}\nfunction allowFeature() {return true;}\nfunction applyState() {}\nfunction renderConnectionNotice() {}\n' + `
    globalThis.driver = {
      init: async () => { await ensureDeployment(); access = ${JSON.stringify(identity)}; },
      read: () => request('state'),
      write: () => request('submit', {method:'POST', body:{request_id:'one-answer'}}),
      submission: () => {topicId='topic';state={status:'in_progress',learner_id:${JSON.stringify(identity.learner_id)}};return sendSubmission({request_id:'once',answer:'draft'});},
      stateRead: () => {topicId='topic';return refreshState();},
      snapshot: () => ({apiBase, identityToken, learner:access?.learner_id, generation:connectionGeneration, actionBusy, stateBusy})
    };`, context);
  return { driver: context.driver, calls, localStorage, sessionStorage, advance: () => { clock += 31000; } };
}

for (const role of ['guest', 'account']) test(`${role}: gateway failure reloads config, verifies identity and resumes GET`, async () => {
  const fixture = setup({ role }); await fixture.driver.init();
  assert.equal((await fixture.driver.read()).recovered, true);
  assert.equal(fixture.driver.snapshot().learner, `${role}-original`);
  assert.equal(fixture.driver.snapshot().identityToken, 'original-token');
  assert.equal(fixture.calls.filter(call => call.url.endsWith('/api/access')).length, role === 'guest' ? 1 : 2);
  assert.equal(fixture.calls.filter(call => call.url.endsWith('/api/access/guest')).length, 0);
  assert.equal(fixture.localStorage.getItem('graspmemoedu:identity:https://new.example'), 'original-token');
  assert.equal(fixture.localStorage.getItem('graspmemoedu:guest-identity:https://new.example'), role === 'guest' ? 'original-token' : 'separate-guest-token');
});

test('unchanged config preserves identity and rate-limits checks without read loops', async () => {
  const fixture = setup({ nextOrigin: 'https://old.example' }); await fixture.driver.init();
  const before = fixture.driver.snapshot();
  await assert.rejects(fixture.driver.read()); await assert.rejects(fixture.driver.read());
  assert.deepEqual(fixture.driver.snapshot(), before);
  assert.equal(fixture.calls.filter(call => call.url.endsWith('deployment.json')).length, 2);
  assert.equal(fixture.calls.filter(call => call.url.endsWith('/api/state')).length, 2);
  fixture.advance(); await assert.rejects(fixture.driver.read());
  assert.equal(fixture.calls.filter(call => call.url.endsWith('deployment.json')).length, 3);
});

test('uncertain answer is never replayed; following read reconnects', async () => {
  const fixture = setup({ status: 0 }); await fixture.driver.init();
  await assert.rejects(fixture.driver.write(), error => error.code === 'network_error');
  assert.equal((await fixture.driver.read()).recovered, true);
  assert.equal(fixture.calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(fixture.calls.find(call => call.method === 'POST').url, 'https://old.example/api/submit');
});

for (const status of [401, 403, 404, 409, 422]) test(`HTTP ${status} is returned unchanged without config refresh`, async () => {
  const fixture = setup({ status }); await fixture.driver.init();
  await assert.rejects(fixture.driver.read(), error => error.status === status);
  assert.equal(fixture.calls.filter(call => call.url.endsWith('deployment.json')).length, 1);
});

test('invalid refreshed configuration cannot replace the active endpoint', async () => {
  const fixture = setup({ invalidConfig: true }); await fixture.driver.init();
  const original = fixture.driver.snapshot();
  await assert.rejects(fixture.driver.read(), error => error.status === 502);
  assert.deepEqual(fixture.driver.snapshot(), original);
  assert.equal(fixture.calls.filter(call => call.url.endsWith('/api/state')).length, 1);
});

test('parallel failures share a single deployment refresh', async () => {
  const fixture = setup(); await fixture.driver.init();
  const results = await Promise.allSettled([fixture.driver.read(), fixture.driver.read(), fixture.driver.read()]);
  assert.ok(results.some(result => result.status === 'fulfilled'));
  assert.equal(fixture.calls.filter(call => call.url.endsWith('deployment.json')).length, 2);
  assert.equal(fixture.driver.snapshot().learner, 'guest-original');
});

test('offline replacement does not recurse or create another guest', async () => {
  const fixture = setup({ newEndpointFails: true }); await fixture.driver.init();
  await assert.rejects(fixture.driver.read(), error => error.code === 'network_error');
  assert.equal(fixture.calls.filter(call => call.url.endsWith('deployment.json')).length, 2);
  assert.equal(fixture.calls.filter(call => call.method === 'POST').length, 0);
  assert.ok(fixture.calls.length <= 4);
});

test('actual submission handler unlocks busy state after endpoint replacement without resending', async () => {
  const fixture = setup(); await fixture.driver.init();
  await fixture.driver.submission();
  assert.equal(fixture.driver.snapshot().actionBusy, false);
  assert.equal(fixture.driver.snapshot().stateBusy, false);
  assert.equal(fixture.calls.filter(call => call.method === 'POST').length, 1);
});

test('actual refreshState releases its busy flag after endpoint replacement', async () => {
  const fixture = setup(); await fixture.driver.init();
  await fixture.driver.stateRead();
  assert.equal(fixture.driver.snapshot().stateBusy, false);
});

test('only verified matching learner tab drafts and uncertain submissions migrate', async () => {
  const fixture = setup(); await fixture.driver.init();
  const prefix = 'graspmemoedu:';
  fixture.sessionStorage.setItem(prefix + 'draft:https://old.example:guest-original:topic:step', 'my draft');
  fixture.sessionStorage.setItem(prefix + 'draft:https://old.example:someone-else:topic:step', 'private other draft');
  fixture.sessionStorage.setItem(prefix + 'submission:https://old.example:guest-original:topic', '{"request_id":"same-id"}');
  await fixture.driver.read();
  assert.equal(fixture.sessionStorage.getItem(prefix + 'draft:https://new.example:guest-original:topic:step'), 'my draft');
  assert.equal(fixture.sessionStorage.getItem(prefix + 'submission:https://new.example:guest-original:topic'), '{"request_id":"same-id"}');
  assert.equal(fixture.sessionStorage.getItem(prefix + 'draft:https://new.example:someone-else:topic:step'), null);
});
