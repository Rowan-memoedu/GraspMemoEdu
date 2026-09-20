import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base,accountFile,out]=process.argv.slice(2);
assert.equal(new URL(base).hostname,'127.0.0.1');assert.ok(!['8765','8767'].includes(new URL(base).port));
assert.ok(path.resolve(accountFile).toLowerCase().startsWith(path.resolve('D:/Artifacts/MathLearningWeb/platform').toLowerCase()+path.sep));
const account=JSON.parse(await fs.readFile(accountFile,'utf8'));assert.equal(account.account.purpose,'test');
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,executablePath:path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
await fs.mkdir(out,{recursive:true});
const report={checks:[],errors:[],subjects:[],http_mocked:false,isolated_fixture:true};
const ids=['math','physics','biology','english','chinese','chemistry'];
const lum=rgb=>rgb.match(/[\d.]+/g).slice(0,3).reduce((s,n,i)=>{const c=Number(n)/255;return s+(c<=.04045?c/12.92:((c+.055)/1.055)**2.4)*[.2126,.7152,.0722][i]},0);
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(25000);
 page.on('pageerror',e=>report.errors.push(e.message));
 const go=async route=>{await page.goto(base+'#'+route,{waitUntil:'networkidle'});};
 await go('/');await page.locator('.subjectCard').first().waitFor();
 assert.equal(await page.locator('.subjectSecondaryName').count(),6,'Keep homepage bilingual subtitles');
 for(const selector of ['.mainNavigation a.active','.userAvatar'])assert.equal(await page.locator(selector).evaluate(node=>getComputedStyle(node).color),'rgb(0, 0, 0)','Homepage navbar uses neutral black');
 assert.equal(await page.locator('.mainNavigation a.active').evaluate(node=>getComputedStyle(node).borderBottomColor),'rgb(0, 0, 0)');
 const homeLogos=await page.locator('.subjectCard').evaluateAll(cards=>Object.fromEntries(cards.map(card=>[card.dataset.subjectId,card.querySelector('.subjectEmblem').src])));
 await page.locator('#userMenuButton').click();await page.locator('#identityButton').click();
 await page.locator('#invitationCode').fill(account.invitation_code);await page.locator('#identitySubmitButton').click();
 await page.locator('#identityDialog').waitFor({state:'hidden'});
 const snapshots=new Map();let mathStyles;
 for(const lang of ['zh-CN','en']){
   if(lang==='en'){
     await go('/settings');await Promise.all([page.waitForEvent('load'),page.locator('#profileLanguage').selectOption('en')]);
     await page.waitForFunction(()=>document.documentElement.lang==='en-US');
   }
   for(const id of ids){
     await go(`/subjects/${id}/courses`);await page.locator('.courseChoice').first().waitFor();
     assert.equal(await page.locator('.portalLogo img').getAttribute('src'),homeLogos[id],`${id} same homepage and navbar logo`);
     assert.equal(await page.locator('.portalLogo').evaluate(node=>getComputedStyle(node).color),await page.locator('.courseLearn').first().evaluate(node=>getComputedStyle(node).backgroundColor),`${id} navbar label uses accent`);
     const styles=await page.evaluate(()=>{
       const title=getComputedStyle(document.querySelector('.courseChoice h2')),button=getComputedStyle(document.querySelector('.courseLearn')),card=getComputedStyle(document.querySelector('.courseChoice'));
       return {heading:title.color,button:button.backgroundColor,buttonText:button.color,font:title.fontFamily,size:title.fontSize,weight:title.fontWeight,padding:card.padding,border:card.borderRadius};
     });
     if(!mathStyles)mathStyles=styles;
     for(const key of ['font','size','weight','padding','border'])assert.equal(styles[key],mathStyles[key],`${id} common ${key}`);
     assert.ok(lum(styles.heading)<=lum(mathStyles.heading)+.006,`${id} heading contrast`);
     if(id==='physics'){
       assert.equal(styles.button,'rgb(255, 140, 0)');
       assert.equal(styles.buttonText,'rgb(0, 0, 0)');
       assert.equal(await page.locator('.courseLearn').first().evaluate(node=>getComputedStyle(node).fontWeight),'700');
     }
     else assert.ok(lum(styles.button)<=lum(mathStyles.button)+.006,`${id} button contrast`);
     if(id==='english')assert.ok(Math.abs(lum(styles.button)-lum(mathStyles.button))<.006,'Approved violet brightness');
     if(id==='chemistry')assert.equal(styles.button,'rgb(17, 17, 17)','Black primary button');
     assert.ok((Math.max(lum(styles.button),lum(styles.buttonText))+.05)/(Math.min(lum(styles.button),lum(styles.buttonText))+.05)>=4.5,`${id} button text contrast`);
     assert.equal(await page.locator('.mainNavigation').innerText(),lang==='en'?'Learn\nCourses\nGuide':'学习\n课程\n指南');
     await page.screenshot({path:path.join(out,`${lang}-${id}-courses.png`),fullPage:true});
     await page.locator('.courseGraphButton').first().click();
     await page.locator('.graphDialog[open] .graphViewport rect').first().waitFor();
     const graphLabel=await page.locator('.graphDialog[open] .graphViewport text').first().evaluate(node=>getComputedStyle(node).fill);
     assert.ok(lum(graphLabel)<=lum(mathStyles.heading)+.006,`${id} graph label contrast`);
     assert.doesNotMatch(await page.locator('.graphLegend').innerText(),/深蓝|浅蓝|蓝色|Dark blue|Light blue/);
     await page.locator('.graphDialog .iconButton').click();
     const topic=id==='math'?'digits-to-letters':`test-${id}-topic`;
     await go(`/subjects/${id}/topic/${topic}`);await page.locator('#courseShell:not([hidden])').waitFor();
     if(lang==='zh-CN'){
       const choice=page.getByRole('button',{name:'未掌握，先学习',exact:true});
       if(await choice.isVisible())await choice.click();
       if(await page.locator('#stepTitle').innerText()!=='课程介绍')await page.getByRole('button',{name:'继续学习',exact:true}).click();
       await page.waitForFunction(()=>document.querySelector('#stepTitle')?.textContent==='课程介绍');
       snapshots.set(id,await page.locator('#stepCard .courseContent').innerHTML());
     }else{
       assert.equal(await page.locator('#stepTitle').innerText(),'Introduction');
       assert.equal(await page.locator('#stepCard .courseContent').innerHTML(),snapshots.get(id),'Course body must stay identical');
     }
     const ui=await page.locator('#stepCard .stepNavigation').innerText();
     if(lang==='zh-CN')assert.doesNotMatch(ui,/Continue|Submit|Next|Previous/);
     else assert.doesNotMatch(ui,/[\u3400-\u9fff]/);
     await page.screenshot({path:path.join(out,`${lang}-${id}-reader.png`),fullPage:true});
     await page.setViewportSize({width:390,height:844});
     if(await page.locator('#historyScrim').isVisible())await page.locator('#historyScrim').click({position:{x:370,y:50}});
     assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${id}/${lang} mobile overflow`);
     await page.screenshot({path:path.join(out,`${lang}-${id}-mobile.png`),fullPage:true});
     await page.setViewportSize({width:1440,height:1000});
     report.subjects.push({id,lang,...styles});
     console.log('PASS',lang,id);
   }
 }
 assert.deepEqual(report.errors,[]);
 report.checks=['Homepage bilingual subtitles retained','Six subjects share component geometry/fonts/contrast','Requested theme hues and monochrome chemistry','Chinese/English UI coherent including Introduction','Content HTML unchanged across languages','All six subject readers fit mobile'];
 report.status='passed';
}finally{await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2));await browser.close();}
