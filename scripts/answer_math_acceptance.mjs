/** Verify repeated imported SVG glyph caches, including a hidden reader clone. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import {renderer} from './import_course.mjs';
const [base, output] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const render = await renderer(new Map(), {cacheGlyphs: true});
const html = render.nodes([{head: '解析：$x^2+1=2$，所以 $x=1$。', lines: [], children: [], line: 1}]);
assert.ok(html.includes('<use'), 'Must exercise the production importer glyph cache');
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
try {
  const page = await browser.newPage(); await page.goto(base, {waitUntil: 'networkidle'});
  const result = await page.evaluate(async ({html, css, base}) => {
    const {prepareAnswerContent} = await import(new URL('self-assessment.js', base).href);
    const style = document.createElement('style'); style.textContent = css; document.head.append(style);
    const fragments = [true, false, false].map(hidden => {
      const root = document.createElement('article'); root.innerHTML = html; root.hidden = hidden;
      prepareAnswerContent(root); document.body.append(root); return root;
    });
    const checks = fragments.map(root => ({hidden: root.hidden,
      local: [...root.querySelectorAll('use')].every(use => {
        const href = use.getAttribute('href') || use.getAttribute('xlink:href');
        const target = document.getElementById(href.slice(1)); return target && root.contains(target);
      }),
      boxes: root.hidden ? [] : [...root.querySelectorAll('mjx-container svg')].map(svg => ({
        width: svg.getBBox().width, height: svg.getBBox().height,
        ink: [...svg.querySelectorAll('use')].every(use => use.getBBox().width > 0),
      })),
    }));
    const ids = fragments.flatMap(root => [...root.querySelectorAll('defs [id]')].map(node => node.id));
    const unique = new Set(ids).size === ids.length;
    fragments.forEach(root => root.remove()); style.remove(); return {checks, unique};
  }, {html, css: render.css, base: base + '/'});
  assert.equal(result.unique, true);
  assert.ok(result.checks.every(row => row.local));
  assert.ok(result.checks.filter(row => !row.hidden).every(row => row.boxes.every(box => box.width > 0 && box.height > 0 && box.ink)));
  await fs.writeFile(output, JSON.stringify(result, null, 2));
  console.log('PASS production importer SVG caches resolve inside each repeated fragment; visible glyphs have ink');
} finally { await browser.close(); }
