import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [fixture, output, base='http://127.0.0.1:18872'] = process.argv.slice(2);
const identity=JSON.parse(await fs.readFile(path.join(fixture,'identity.private.json'),'utf8'));
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,executablePath:path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
await fs.mkdir(output,{recursive:true});
const context=await browser.newContext({viewport:{width:1365,height:960},locale:'zh-CN'});
const page=await context.newPage(), errors=[], checks=[];
page.on('pageerror',e=>errors.push(e.message)); page.setDefaultTimeout(20000);
try {
  await page.goto(base+'/#/',{waitUntil:'networkidle'});
  await page.locator('#userMenuButton').click(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identity.invitation_code); await page.locator('#identitySubmitButton').click();
  await page.locator('#identityDialog').waitFor({state:'hidden'});
  await page.goto(base+'/#/subjects/math/topic/synthetic-composite',{waitUntil:'networkidle'});
  await page.locator('#stepCard').waitFor();
  const mastered = page.getByRole('button',{name:'已掌握，试着作答',exact:true});
  if (await mastered.count()) await mastered.click();
  await page.locator('.answerForm .compositeSection').waitFor();
  for(let i=0;i<10;i++) {
    await page.waitForFunction(i=>document.querySelector('.compositeSection .stepCounter')?.textContent === `${i+1} / 10`,i);
    const selects=page.locator('.compositeSection select');
    for(let j=0;j<await selects.count();j++) await selects.nth(j).selectOption('yes');
    if(i===2) {
      await page.reload({waitUntil:'networkidle'}); await page.locator('.compositeSection').waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('.compositeSection .stepCounter').textContent(),'3 / 10');
      assert.ok((await page.locator('.compositeSection select').evaluateAll(xs=>xs.map(x=>x.value))).every(v=>v==='yes'));
      checks.push('Refresh restores same section and all control values');
    }
    if(i===0) for(const width of [1365,320,375,414,768]) {
      await page.setViewportSize({width,height:960});
      if(await page.locator('#historyScrim').isVisible()) await page.locator('#historyScrim').click();
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`Overflow ${width}`);
      await page.screenshot({path:path.join(output,`composite-${width}.png`),fullPage:true});
    }
    if(i<9) { assert.equal(await page.locator('#submitButton').isDisabled(),true); await page.locator('.compositeNext').click(); }
  }
  assert.equal(await page.locator('#submitButton').isDisabled(),false);
  await page.locator('#submitButton').click(); await page.locator('.feedback.correct').waitFor();
  checks.push('Only final section submits one whole deterministic judgement');
  await page.locator('#stepCard .stepNavigation .continueRow button').click();
  await page.getByRole('heading',{name:'Synthetic final proof'}).waitFor();
  await page.reload({waitUntil:'networkidle'});
  await page.keyboard.press('Escape');
  await page.getByRole('heading',{name:'Synthetic final proof'}).waitFor();
  await page.locator('#stepCard .stepNavigation .continueRow button').click();
  await page.getByRole('heading',{name:'本主题已完成'}).waitFor();
  checks.push('Final proof remains pending across refresh and completes only after Continue');
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(output,'interaction-acceptance.json'),JSON.stringify({checks,errors,synthetic:true},null,2));
  console.log(JSON.stringify({checks,errors}));
} catch(error) {
  await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
  await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({error:String(error),errors,buttons:await page.locator('#stepCard button').allTextContents()},null,2));
  throw error;
} finally { await browser.close(); }
