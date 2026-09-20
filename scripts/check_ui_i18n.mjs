import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {messages, t, getLanguage, setLanguage, LANGUAGE_KEY, translateMessage} from '../web/i18n.js';
assert.equal(getLanguage(), 'zh-CN', 'Fresh browsers default to Chinese regardless of browser locale');
globalThis.document = {documentElement:{}};
const stored = new Map(); globalThis.localStorage = {setItem:(key,value)=>stored.set(key,value)};
const placeholders = value => [...value.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map(match=>match[1]).sort();
for (const [key,pair] of Object.entries(messages)) {
  assert.equal(pair.length,2,key);
  assert.ok(pair.every(value=>typeof value === 'string' && value.trim()),key);
  assert.deepEqual(placeholders(pair[0]),placeholders(pair[1]),`Interpolation mismatch: ${key}`);
}
for (const file of ['app.js','portal.js']) {
  const source = await fs.readFile(new URL(`../web/${file}`,import.meta.url),'utf8');
  for (const match of source.matchAll(/\bt\("([^"\\]*(?:\\.[^"\\]*)*)"/g)) {
    const key=JSON.parse(`"${match[1]}"`); assert.ok(key in messages,`Missing UI key in ${file}: ${key}`);
  }
}
const html=await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8');
for(const match of html.matchAll(/data-i18n(?:-(?:aria-label|title|placeholder|content))?="([^"]+)"/g)) assert.ok(match[1] in messages,`Missing static key: ${match[1]}`);
setLanguage('en'); assert.equal(stored.get(LANGUAGE_KEY),'en'); assert.equal(document.documentElement.lang,'en-US');
assert.equal(translateMessage('邀请码无效，请联系管理者。'),'The invitation code is invalid. Please contact the administrator.');
assert.ok(!translateMessage('UnknownInternalError').includes('UnknownInternalError'));
for(const [key,pair] of Object.entries(messages)) assert.equal(t(key),pair[1],key);
setLanguage('zh-CN'); for(const [key,pair] of Object.entries(messages)) assert.equal(t(key),pair[0],key);
console.log(`PASS ${Object.keys(messages).length} bilingual UI keys, interpolation, static/dynamic key coverage, Chinese default and language persistence`);
