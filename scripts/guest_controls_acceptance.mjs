/** Guest controls against a real isolated local service; never use production data. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const [base, out] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.notEqual(new URL(base).port, '8767');
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
await fs.mkdir(out, {recursive:true});
const browser = await chromium.launch({headless:true, executablePath:path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const report = {checks:[], errors:[], mocked:false};
try {
  const page = await browser.newPage({viewport:{width:1365,height:1000}});
  page.setDefaultTimeout(20000);
  page.on('pageerror', e => report.errors.push(String(e)));
  await page.goto(base, {waitUntil:'networkidle'});
  await page.locator('#guestDemoOption:not([hidden])').waitFor();
  assert.equal(await page.locator('#userMenu').isVisible(), false);
  for (const width of [1365, 760, 390, 320]) {
    await page.setViewportSize({width,height:1000});
    const geometry = await page.evaluate(() => {
      const avatar = document.querySelector('#userMenuButton').getBoundingClientRect();
      const demo = document.querySelector('#guestDemoOption').getBoundingClientRect();
      return {right:demo.left>=avatar.right, aligned:Math.abs((avatar.top+avatar.height/2)-(demo.top+demo.height/2))<3,
        overflow:document.documentElement.scrollWidth>innerWidth, font:parseFloat(getComputedStyle(document.querySelector('#guestDemoOption')).fontSize)};
    });
    assert.deepEqual(geometry, {right:true,aligned:true,overflow:false,font:14});
    await page.screenshot({path:path.join(out,`header-${width}.png`)});
  }
  await page.setViewportSize({width:1365,height:1000});
  await page.locator('#guestDemoCheckbox').check();
  assert.equal(await page.locator('#userMenu').isVisible(), false);
  await page.getByRole('button',{name:'Learn',exact:true}).click();
  await page.locator('.taskToggle').first().click();
  assert.equal(await page.locator('.guestReset').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(244, 199, 68)');
  await page.screenshot({path:path.join(out,'reset.png'),fullPage:true});
  await page.getByRole('button',{name:'Start',exact:true}).click();
  await page.getByRole('button',{name:'已掌握，试着作答',exact:true}).click();
  await page.waitForFunction(() => !!document.querySelector('#answerInput')?.value);
  await page.locator('#answerInput').fill('manually edited draft');
  await page.locator('#topicHomeLink').click();
  await page.locator('.taskToggle').first().click();
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/guest/reset' && r.request().method() === 'POST');
  await page.getByRole('button',{name:'reset',exact:true}).click();
  assert.equal((await response).status(),200);
  await page.locator('.taskToggle').first().click();
  await page.getByRole('button',{name:'Start',exact:true}).waitFor();
  await page.reload({waitUntil:'networkidle'});
  await page.locator('.taskToggle').first().click();
  await page.getByRole('button',{name:'Start',exact:true}).click();
  await page.getByRole('button',{name:'已掌握，试着作答',exact:true}).click();
  await page.waitForFunction(() => { const value=document.querySelector('#answerInput')?.value; return value && value !== 'manually edited draft'; });
  report.checks.push('Guest switch visible beside avatar, 14px, 320/390/760/1365px layouts', 'Checkbox works without opening menu', 'Reset restores Start, survives reload, drops old draft');
  await page.locator('#userMenuButton').click();
  await page.getByRole('link',{name:/Help/}).click();
  await page.getByRole('heading',{name:'Q&A',exact:true}).waitFor();
  assert.equal(await page.locator('.guideBody h2').count(),4);
  await page.reload({waitUntil:'networkidle'});
  await page.getByRole('heading',{name:'Q&A',exact:true}).waitFor();
  await page.screenshot({path:path.join(out,'qa.png'),fullPage:true});
  report.checks.push('Help opens Q&A, four useful answers, refresh supported');
  assert.deepEqual(report.errors,[]);
  report.status='passed';
} finally {
  await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));
  await browser.close();
}
