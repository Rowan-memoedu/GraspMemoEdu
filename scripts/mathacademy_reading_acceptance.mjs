import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [data,output,base='http://127.0.0.1:18876']=process.argv.slice(2);
const identity=JSON.parse(await fs.readFile(path.join(data,'reading-identity.private.json'),'utf8'));
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
for(let retry=0;retry<120;retry++){
 try{if((await fetch(base+'/api/health')).ok)break;}catch{}
 if(retry===119)throw Error('Isolated server did not become ready');
 await new Promise(resolve=>setTimeout(resolve,1000));
}
const browser=await chromium.launch({headless:true,executablePath:path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
await fs.mkdir(output,{recursive:true}); const page=await browser.newPage({viewport:{width:1365,height:960}});
page.setDefaultTimeout(60000); const errors=[],states=new Map(),checks=[];
page.on('pageerror',e=>errors.push(e.message));
page.on('response',async r=>{if(new URL(r.url()).pathname==='/api/state' && r.status()===200) {const s=await r.json();states.set(s.topic_id,s);}});
try{
 await page.goto(base+'/#/',{waitUntil:'networkidle'});
 await page.locator('#userMenuButton').click();await page.locator('#identityButton').click();
 await page.locator('#invitationCode').fill(identity.invitation_code);await page.locator('#identitySubmitButton').click();
 await page.locator('#identityDialog').waitFor({state:'hidden'});
 const samples=[[2674,'ma-question-211995'],[2674,'ma-question-213022'],[2754,'ma-question-257643'],[2754,'ma-question-244615'],[2940,'ma-question-355212'],[2940,'code'],[894,'table']];
 for(const [id,blockId] of samples){
  const tid='ma-topic-'+id;
  await page.goto(`${base}/#/subjects/math/topic/${tid}`,{waitUntil:'networkidle'});
  await page.locator('.sourceReadingBlock').waitFor();await page.keyboard.press('Escape');
  const state=states.get(tid);assert.ok(state?.read_only);
  let index=state.blocks.findIndex(b=>blockId==='code'?b.html.includes('<pre'):blockId==='table'?b.html.includes('<table'):b.id===blockId);
  if(index<0 && blockId==='table') index=0;
  assert.ok(index>=0,`${tid}/${blockId} missing`);
  await page.evaluate(({key,index})=>sessionStorage.setItem(key,String(index)),{key:`graspmemoedu:source-reader:${tid}:${state.course_version}`,index});
  await page.reload({waitUntil:'networkidle'});await page.locator('.sourceReadingBlock').waitFor();await page.keyboard.press('Escape');
  if(blockId==='ma-question-355212'){assert.equal(await page.locator('.compositeSection').count(),10);assert.equal(await page.locator('.compositeSection select').count(),23);}
  if(blockId==='ma-question-257643') assert.match(await page.locator('.sourceReadingBlock').innerText(),/face\(s\)/);
  for(const width of [1365,320,375,414,768]){
   await page.setViewportSize({width,height:960});await page.keyboard.press('Escape');
   await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
   assert.equal(await page.locator('mjx-merror,[data-mjx-error]').count(),0);
   assert.equal(await page.locator('#submitButton').count(),0);
   assert.equal(await page.locator('.sourceReadingBlock img').evaluateAll(xs=>xs.filter(x=>x.src.startsWith('data:')&&!x.naturalWidth).length),0);
   const duplicates=await page.locator('.sourceReadingBlock [id]').evaluateAll(xs=>xs.map(x=>x.id).filter((id,i,all)=>all.indexOf(id)!==i));assert.deepEqual(duplicates,[]);
   if(width===1365||width===375) await page.screenshot({path:path.join(output,`${tid}-${blockId}-${width}.png`),fullPage:true});
  }
  checks.push({topic:tid,block:blockId,widths:[1365,320,375,414,768],read_only:true});
 }
 assert.deepEqual(errors,[]);await fs.writeFile(path.join(output,'reading-acceptance.json'),JSON.stringify({checks,errors},null,2));console.log(JSON.stringify({samples:checks.length,errors}));
}catch(error){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});console.log(JSON.stringify({error:String(error),errors}));throw error;}
finally{await browser.close();}
