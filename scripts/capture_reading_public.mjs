/** Observe published reading pages in an isolated, headless Google Chrome. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {randomUUID} from 'node:crypto';
const [root,output,...books]=process.argv.slice(2);
const api='https://124.221.144.155',base='https://rowan-memoedu.github.io/GraspMemoEdu-Pages/';
const assetVersion=(await fs.readFile(new URL('../web/index.html',import.meta.url),'utf8')).match(/app\.js\?v=([a-f0-9]+)/)[1];
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const manifest=JSON.parse(await fs.readFile('D:/AppData/GraspMemoEdu/admin/testing-20261005-admin-1.json','utf8'));
const account=manifest.accounts.find(a=>a.purpose==='test'&&a.is_admin);assert.ok(account);
await fs.mkdir(output,{recursive:true});
const profile=process.env.GRASPMEMOEDU_CAPTURE_PROFILE||path.join('D:/Caches/GraspMemoEduReadingCapture',randomUUID());await fs.mkdir(profile,{recursive:true});
const context=await chromium.launchPersistentContext(profile,{channel:'chrome',headless:true});
const report={passed:false,published:true,learning_answers_submitted:0,samples:[],errors:[]};
let token;
try{
 const response=await context.request.post(api+'/api/access/login',{headers:{Origin:'https://rowan-memoedu.github.io'},data:{invitation_code:account.invitation_code}});
 assert.equal(response.status(),200);
 const login=await response.json();token=login.identity_token;
 assert.equal(login.access.purpose,'test');assert.equal(login.access.is_admin,true);
 await context.addInitScript(({api,token})=>{
   localStorage.setItem('graspmemoedu:identity:'+api,token);
   localStorage.setItem('graspmemoedu:identity-role:'+api,'account');
 },{api,token});
 for(const name of books.length?books:['gaoxian','liwenwei']){
  const plan=JSON.parse(await fs.readFile(path.join(root,name,'course-install-plan.json'),'utf8'));
  const selections=[];
  for(const entry of plan.topics){if((await fs.readFile(entry.source,'utf8')).includes('assets/')){selections.push(entry);break;}}
  for(const entry of plan.topics){if((await fs.readFile(entry.source,'utf8')).includes('"run-in"')){selections.push(entry);break;}}
  if(plan.composition_revision){
   const topics=await Promise.all(plan.topics.map(async entry=>[entry,JSON.parse(await fs.readFile(path.join(entry.package,'topic.json'),'utf8'))]));
   for(const predicate of [t=>t.bank_questions?.length,t=>t.introductions.some(i=>i.example),t=>t.introductions.some(i=>i.footnotes?.length),t=>t.sections?.some(s=>s.title)]){
    const found=topics.find(([,topic])=>predicate(topic));if(found)selections.push(found[0]);
   }
  }
  for(const selected of [...new Map((selections.length?selections:[plan.topics[0]]).map(t=>[t.topic_id,t])).values()]){
  const topic=JSON.parse(await fs.readFile(path.join(selected.package,'topic.json'),'utf8'));
  for(const width of [375,1280]){
   const page=await context.newPage();page.setDefaultTimeout(90000);
   page.on('pageerror',e=>report.errors.push(e.message));
   await page.setViewportSize({width,height:1000});
   await page.goto(base+'?v='+assetVersion+'#/subjects/'+plan.subject_id+'/topic/'+selected.topic_id+'/content',{waitUntil:'domcontentloaded',timeout:90000});
   if(process.env.GRASPMEMOEDU_CAPTURE_PROFILE)await page.reload({waitUntil:'domcontentloaded',timeout:90000});
   await page.waitForFunction(title=>document.querySelector('.materialBrowser > .portalPageTitle')?.textContent===title && document.querySelector('.materialBlock .sourceReading'),topic.title);
   await page.evaluate(()=>Promise.all([...document.querySelectorAll('.materialBlock img')].map(i=>{i.loading='eager';return i.decode();})));
   await page.evaluate(()=>document.fonts.ready);
   const check=await page.evaluate(()=>({
    overflow:document.documentElement.scrollWidth>innerWidth,
    figures:document.querySelectorAll('.sourceFigure').length,
    images_centered:[...document.querySelectorAll('.sourceFigure img')].every(i=>{
      const a=i.getBoundingClientRect(),b=i.closest('figure').getBoundingClientRect();return Math.abs((a.left+a.right-b.left-b.right)/2)<2;
    }),
    missing_images:[...document.querySelectorAll('.materialBlock img')].filter(i=>!i.naturalWidth).length,
    math_errors:document.querySelectorAll('[data-mml-node="merror"]').length,
    book_reader:Boolean(document.querySelector('.sourceBook')),
    original_headings:document.querySelectorAll('[data-source-role="heading"]').length,
    original_leads:document.querySelectorAll('.sourceLead').length,
    sections:document.querySelectorAll('.readingSectionHeading').length,
    reading_examples:document.querySelectorAll('[data-field="example"]').length,
    footnote_fields:document.querySelectorAll('[data-field="footnotes"]').length,
    reader_font:getComputedStyle(document.querySelector('.sourceReading')).fontFamily,
    reader_font_size:getComputedStyle(document.querySelector('.sourceReading')).fontSize,
    reader_line_height:getComputedStyle(document.querySelector('.sourceReading')).lineHeight,
    overflow_elements:[...document.querySelectorAll('body *')].filter(n=>{
      const r=n.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+1||r.left< -1);
    }).slice(0,30).map(n=>({tag:n.tagName,classes:n.className?.baseVal??n.className,
      width:n.getBoundingClientRect().width,right:n.getBoundingClientRect().right,
      text:n.textContent?.slice(0,100)})),
   }));
   const screenshot=path.join(output,selected.topic_id+'-'+width+'.png');await page.screenshot({path:screenshot,fullPage:false});
   const detail=page.locator(check.original_leads?'.sourceLead':'.sourceFigure').first();
   if(await detail.count())await detail.scrollIntoViewIfNeeded();
   const figureScreenshot=path.join(output,selected.topic_id+'-'+width+'-detail.png');await page.screenshot({path:figureScreenshot,fullPage:false});
   report.samples.push({book:name,topic_id:selected.topic_id,title:topic.title,width,...check,screenshot,figure_screenshot:figureScreenshot});
   assert.equal(check.overflow,false);assert.equal(check.images_centered,true);assert.equal(check.missing_images,0);assert.equal(check.math_errors,0);
   if(plan.presentation_revision)assert.equal(check.book_reader,true);
   if(plan.composition_revision){
    assert.equal(check.reading_examples,topic.introductions.filter(i=>i.example).length);
    assert.equal(check.footnote_fields,topic.introductions.filter(i=>i.footnotes?.length).length);
    assert.ok(check.sections>=topic.sections.filter(s=>s.title).length);
   }
   await page.close();
   console.log('Verified published reading '+name+' at '+width+'px');
  }
  }
 }
 assert.equal(report.errors.length,0);report.passed=true;
}catch(error){
 report.failure=String(error);
 const page=context.pages().at(-1);
 if(page){report.failed_url=page.url();await page.screenshot({path:path.join(output,'failed-page.png')}).catch(()=>{});}
 throw error;
}finally{
 if(token)await context.request.post(api+'/api/access/logout',{headers:{Origin:'https://rowan-memoedu.github.io','X-Learning-Identity':token},data:{}});
 await fs.writeFile(path.join(output,'public-ui-verification.json'),JSON.stringify(report,null,2));await context.close();
}
console.log(JSON.stringify({passed:report.passed,samples:report.samples.length}));
