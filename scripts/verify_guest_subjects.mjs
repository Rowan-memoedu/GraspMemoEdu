/** Guest-only read/navigation acceptance against the real deployed frontend. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [url, out] = process.argv.slice(2);
if (!url || !out) throw new Error('Expected page URL and evidence directory');
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
await fs.mkdir(out, {recursive:true});
const browser = await chromium.launch({headless:true, executablePath:path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const page = await browser.newPage({viewport:{width:1365,height:1000}});
const report = {url, subjects:[], errors:[], learningWrites:[], loginRequests:0};
page.on('pageerror', e => report.errors.push(String(e)));
page.on('request', r => {
  const pathname = new URL(r.url()).pathname;
  if (pathname === '/api/access/login') report.loginRequests++;
  if (r.method() === 'POST' && /^\/api\/(choose|continue|submit|pause|select|guest\/reset|review\/begin)/.test(pathname)) report.learningWrites.push(pathname);
});
try {
  await page.goto(url, {waitUntil:'networkidle'});
  await page.locator('.subjectCard').first().waitFor();
  assert.equal(await page.locator('.subjectCard').count(), 7);
  for (const subject of ['math','physics','english','chinese','biology','chemistry','cs']) {
    for (const section of ['courses','guide']) {
      const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === (section === 'guide' ? '/api/guide' : '/api/catalog') && new URL(r.url()).searchParams.get('subject_id') === subject);
      await page.evaluate(hash => {location.hash=hash;}, `#/subjects/${subject}/${section}`);
      const response = await responsePromise;
      assert.equal(response.status(), 200, `${subject}/${section}`);
      await page.waitForFunction(() => !document.querySelector('#portalContent .portalLoading') && document.querySelector('#portalContent')?.textContent.trim());
      assert.equal(await page.locator('.subjectAccessNotice').count(), 0);
      assert.equal(await page.locator('#identityDialog[open]').count(), 0);
      report.subjects.push({subject,section,status:response.status()});
    }
  }
  await page.screenshot({path:path.join(out,'guest-chemistry.png'),fullPage:true});
  assert.deepEqual(report.learningWrites, []);
  assert.equal(report.loginRequests, 0);
  assert.deepEqual(report.errors, []);
  report.status='passed';
} catch(e) {
  report.status='failed'; report.failure=String(e); process.exitCode=1;
  await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
} finally {
  await browser.close();
  await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));
}
console.log(JSON.stringify(report,null,2));
