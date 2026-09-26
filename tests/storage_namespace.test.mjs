import assert from 'node:assert/strict';
import test from 'node:test';
import {migrateLegacyStorage} from '../web/i18n.js';

function storage(entries, failOnWrite = false) {
  const values = new Map(Object.entries(entries));
  return {
    get length() { return values.size; },
    key: index => [...values.keys()][index] ?? null,
    getItem: key => values.get(key) ?? null,
    setItem(key, value) {
      if (failOnWrite) throw new Error('Storage quota exceeded');
      values.set(key, value);
    },
    removeItem: key => values.delete(key),
  };
}

test('namespace upgrade preserves identity, preferences and drafts without touching unrelated keys', () => {
  const entries = {
    'math-learning-web:identity:https://example.test': 'fixture-token',
    'math-learning-web:ui-language': 'en',
    'math-learning-web:review:learner:topic:session:draft:1': '{"answer":"2x"}',
    'another-app:preference': 'untouched',
  };
  const data = storage(entries);
  migrateLegacyStorage(data);
  migrateLegacyStorage(data);
  for (const [key, value] of Object.entries(entries)) {
    const target = key.replace('math-learning-web:', 'graspmemoedu:');
    assert.equal(data.getItem(target), value);
    if (target !== key) assert.equal(data.getItem(key), null);
  }
});

test('newer state wins and a removed identity cannot reappear after logout', () => {
  const data = storage({'math-learning-web:identity:api': 'old', 'graspmemoedu:identity:api': 'new'});
  migrateLegacyStorage(data);
  assert.equal(data.getItem('graspmemoedu:identity:api'), 'new');
  data.removeItem('graspmemoedu:identity:api');
  migrateLegacyStorage(data);
  assert.equal(data.length, 0);
});

test('failed writes retain original values for recovery', () => {
  const data = storage({'math-learning-web:ui-language': 'en'}, true);
  migrateLegacyStorage(data);
  assert.equal(data.getItem('math-learning-web:ui-language'), 'en');
  assert.equal(data.getItem('graspmemoedu:ui-language'), null);
});
