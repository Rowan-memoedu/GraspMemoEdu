import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
const [base='http://127.0.0.1:8767',out='D:/Artifacts/GraspMemoEdu/formal-preview']=process.argv.slice(2);
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE||path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
const report={browser:browser.version(),url:base,learning_actions:false,may_initialize_guest:true,errors:[],views:[]};
try {
  const page=await browser.newPage();
  page.on('pageerror',e=>report.errors.push(String(e)));
  for(const [name,width,height] of [['desktop',1100,1000],['mobile',390,844]]){
    await page.setViewportSize({width,height});
    await page.goto(base.replace(/#.*$/,'')+'#/topic/digits-to-letters',{waitUntil:'networkidle'});
    await page.locator('#courseShell:not([hidden])').waitFor();
    report.views.push({name,...await page.evaluate(()=>({title:document.title,math:document.querySelectorAll('mjx-container svg').length,
      mathErrors:document.querySelectorAll('[data-mml-node="merror"]').length,overflow:document.documentElement.scrollWidth>innerWidth+1,
      controls:[...document.querySelectorAll('#stepCard button')].map(x=>x.textContent),heading:document.querySelector('#stepTitle').textContent}))});
    await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
  }
  report.status=report.errors.length===0&&report.views.every(v=>!v.overflow&&!v.mathErrors&&v.math>0)?'passed':'failed';
  if(report.status!=='passed')process.exitCode=1;
}finally{
  await fs.writeFile(path.join(out,'check.json'),JSON.stringify(report,null,2));
  await browser.close();
}
console.log(JSON.stringify(report,null,2));
