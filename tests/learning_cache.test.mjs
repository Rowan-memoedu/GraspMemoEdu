import assert from 'node:assert/strict';
import test from 'node:test';
import {createLearningCache} from '../web/learning-cache.js';

test('administrator answers and elapsed times never enter browser storage', () => {
  const disk = new Map(), storage = {getItem: k => disk.get(k), setItem: (k,v) => disk.set(k,v), removeItem: k => disk.delete(k)};
  let access = {learning_policy: {persistence: 'memory'}};
  const cache = createLearningCache(() => access);
  for (const key of ['graspmemoedu:draft:topic', 'graspmemoedu:atomic:id:submission', 'graspmemoedu:review:id:elapsed']) {
    cache.write(storage, key, 'transient'); assert.equal(cache.read(storage, key), 'transient');
  }
  assert.equal(disk.size, 0); cache.clear();
  assert.equal(cache.read(storage, 'graspmemoedu:draft:topic'), null);
  access = {};
  cache.write(storage, 'graspmemoedu:draft:topic', 'normal draft');
  assert.equal(disk.size, 1); assert.equal(cache.read(storage, 'graspmemoedu:draft:topic'), 'normal draft');
});
