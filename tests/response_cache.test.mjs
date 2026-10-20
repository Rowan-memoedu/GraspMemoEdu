import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash} from 'node:crypto';
import {createReadCache, createContentCache, contentPath, contentKey, readLifetime} from '../web/response-cache.js';

test('reads coalesce, callers cannot mutate shared data, writes invalidate pending reads', async () => {
  let time = 0, count = 0, release;
  const cache = createReadCache({now: () => time});
  const fetch = () => { count++; return new Promise(resolve => { release = resolve; }); };
  const one = cache.get('a', 10, fetch), two = cache.get('a', 10, fetch);
  await Promise.resolve(); assert.equal(count, 1);
  release({items: ['original']});
  const [a, b] = await Promise.all([one, two]); a.items.push('changed');
  assert.deepEqual(b.items, ['original']);
  assert.deepEqual((await cache.get('a', 10, fetch)).items, ['original']);
  time = 11;
  const old = cache.get('a', 10, fetch);
  await Promise.resolve(); cache.clear(); release({items: ['stale']}); await old;
  assert.deepEqual(await cache.get('a', 10, async () => ({items: ['fresh']})), {items: ['fresh']});
});

function storage() {
  const records = new Map();
  return {records, read: async key => records.get(key), write: async row => records.set(row.key, structuredClone(row)),
    clear: async () => records.clear()};
}
const envelope = (texts, state = {html: {$gme_text: 0}, answer: 'private draft', revision: 2}) =>
  ({$gme_content: 1, revision: createHash('sha256').update(JSON.stringify(texts ?? ['<p>content</p>'])).digest('hex'), texts, state});

test('persistent cache contains presentation text only and survives page reload', async () => {
  const store = storage(), first = createContentCache({store});
  const ticket = await first.prepare('learner-a', 'state?topic_id=one');
  assert.equal(first.decode(envelope(['<p>content</p>']), ticket).answer, 'private draft');
  const written = JSON.stringify([...store.records.values()]);
  assert(!written.includes('private draft'));
  const reopened = createContentCache({store}), next = await reopened.prepare('learner-a', 'state?topic_id=one');
  assert.equal(reopened.decode(envelope(null), next).html, '<p>content</p>');
  assert.equal((await reopened.prepare('learner-b', 'state?topic_id=one')).record, undefined);
  assert.equal((await reopened.prepare('learner-a/new-permissions', 'state?topic_id=one')).record, undefined);
  for (const record of store.records.values()) record.texts[0] = 'corrupted disk content';
  assert.equal((await createContentCache({store}).prepare('learner-a', 'state?topic_id=one')).record, null);
});

test('authorization removal and content updates cannot reveal old text by position', async () => {
  const cache = createContentCache({store: storage()});
  const first = await cache.prepare('a', 'state');
  cache.decode(envelope(['question', 'revealed answer']), first);
  const next = await cache.prepare('a', 'state');
  const hidden = cache.decode({...envelope(['new question'], {html: {$gme_text: 0}, explanation_html: null}), revision: 'changed'}, next);
  assert.equal(hidden.html, 'new question'); assert.equal(hidden.explanation_html, null);
  assert.throws(() => cache.decode({...envelope(null), revision: 'not-cached'}, next), /content_cache_miss/);
  assert.throws(() => cache.decode(envelope(['x'], {html: {$gme_text: 99}}), next), /content_cache_miss/);
});

test('new learning steps reuse old blocks while carrying new content and fresh progress', async () => {
  const cache = createContentCache({store: storage()});
  cache.decode(envelope(['existing question', 'shared formulas']), await cache.prepare('a', 'state'));
  const ticket = await cache.prepare('a', 'state');
  const result = cache.decode({...envelope([{$gme_previous: 1}, 'new question', {$gme_previous: 0}],
    {math_css: {$gme_text: 0}, current: {$gme_text: 1}, history: {$gme_text: 2}, revision: 3}), revision: 'new'}, ticket);
  assert.deepEqual(result, {math_css: 'shared formulas', current: 'new question', history: 'existing question', revision: 3});
  assert.deepEqual((await cache.prepare('a', 'state')).record.texts, ['shared formulas', 'new question', 'existing question']);
});

test('logout fences delayed responses; administrator content stays in memory', async () => {
  const store = storage(), cache = createContentCache({store});
  const ticket = await cache.prepare('a', 'state');
  cache.clear(); cache.decode(envelope(['obsolete']), ticket);
  assert.equal(store.records.size, 0);
  const admin = await cache.prepare('admin', 'state', false);
  cache.decode(envelope(['admin']), admin);
  assert.equal(store.records.size, 0);
  assert.equal((await cache.prepare('admin', 'state', false)).record.texts[0], 'admin');
});

test('blocked storage degrades to memory; actions and live learning state have no TTL', async () => {
  const store = {read: async () => {throw Error('blocked');}, write: async () => {throw Error('full');}, clear: async () => {}};
  const cache = createContentCache({store}), ticket = await cache.prepare('a', 'guide');
  assert.equal(cache.decode(envelope(['text']), ticket).html, 'text');
  for (const path of ['state', 'training/state', 'review/sessions/one', 'submit', 'review-papers/inbox'])
    assert.equal(readLifetime(path), 0);
  assert(contentPath('review/sessions/one?topic_id=two'));
  assert(!contentPath('help-images?image_id=one'));
  assert(contentPath('continue?topic_id=one', 'POST'));
  assert.equal(contentKey('state?topic_id=one&subject_id=math'), contentKey('continue?subject_id=math&topic_id=one'));
  assert.notEqual(contentKey('state?topic_id=one'), contentKey('state?topic_id=two'));
  assert(!contentPath('access/login', 'POST'));
});
