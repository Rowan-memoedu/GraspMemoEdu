/** Read-only DOM regression for original book order; no learning API or user browser session. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';

const output = path.resolve(process.argv[2]);
await fs.mkdir(output, {recursive: true});
const repository = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const source = await fs.readFile(path.join(repository, 'web/portal.js'), 'utf8');
const start = source.indexOf('  function taskTree(');
const end = source.indexOf('  function renderLearn(', start);
assert.ok(start >= 0 && end > start);
const actualFunction = source.slice(start, end);
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
try {
  const page = await browser.newPage({viewport: {width: 1000, height: 780}, locale: 'zh-CN'});
  await page.route('**/*', route => route.abort());
  await page.setContent('<html lang="zh-CN"><meta charset="utf-8"><style>body{font:18px system-ui;padding:28px}details,article{border:1px solid #bbb;margin:10px;padding:12px}.courseUnitBody{padding-left:25px}</style><h1>原目录顺序：独立合成样本</h1><main></main></html>');
  const result = await page.evaluate(actualFunction => {
    const node = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = text; return n; };
    const incompleteTask = task => { const n = node('article', '', task.title); n.dataset.taskId = task.id; return n; };
    const taskTree = new Function('node', 'incompleteTask', 'bridge', 'expandedUnits', actualFunction + '; return taskTree;')(
      node, incompleteTask, {getAccess: () => ({learner_id: 'isolated'})}, new Map());
    const ids = ['top-before', 'first', 'inside', 'last', 'top-after'];
    const titles = ['前置练习', '第一节', '第二节·第一课时', '第三节', '章后练习'];
    const data = {course: {id: 'isolated'}, tasks: ids.map((id, index) => ({id, topic_id: id, type: 'Lesson', title: titles[index]})),
      pending_hierarchy: [{id: 'chapter', title: '第一章', parent_id: null, topic_ids: ['first', 'last']},
        {id: 'middle', title: '第二节', parent_id: 'chapter', topic_ids: ['inside']}]};
    const order = parent => [...parent.children].map(child => child.dataset.unitId || child.dataset.taskId);
    const pending = document.querySelector('main');
    taskTree(pending, data, {training: true, sourceOrder: ['top-before', 'chapter', 'first', 'middle', 'inside', 'last', 'top-after']});
    const book = {root: order(pending), chapter: order(pending.querySelector('[data-unit-id="chapter"] > .courseUnitBody')),
      nested: order(pending.querySelector('[data-unit-id="middle"] > .courseUnitBody'))};
    for (const details of pending.querySelectorAll('details')) details.open = true;
    const baseline = document.createElement('div');
    taskTree(baseline, data, {});
    return {book, baseline: order(baseline)};
  }, actualFunction);
  assert.deepEqual(result.book, {root: ['top-before', 'chapter', 'top-after'], chapter: ['first', 'middle', 'last'], nested: ['inside']});
  assert.deepEqual(result.baseline, ['chapter', 'top-before', 'top-after']);
  await page.screenshot({path: path.join(output, 'original-directory-order.png'), fullPage: true});
  await fs.writeFile(path.join(output, 'directory-order-validation.json'), JSON.stringify({passed: true, actual_component: 'taskTree', ...result}, null, 2));
  console.log('PASS mixed book siblings, nested sections, root ordering, unchanged course ordering');
} finally { await browser.close(); }
