/** Actual training controller, source-bound passage, isolated API. Public mode reads deployed static assets only. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const [output, sourceFile, webOverride, publicBase, groupId] = process.argv.slice(2);
if (!output || !sourceFile) throw new Error('Expected evidence directory and compiled passage bank');
const web = webOverride || path.join(path.dirname(path.dirname(fileURLToPath(import.meta.url))), 'web');
const assetVersion = (await fs.readFile(path.join(web,'index.html'),'utf8')).match(/\?v=([a-f0-9]+)/)[1];
const bank = JSON.parse(await fs.readFile(sourceFile, 'utf8'));
const group = bank.groups.find(group => group.questions?.length && (!groupId || group.id === groupId));
const original = group.questions[0];
const backendProjection = process.env.PASSAGE_BACKEND_PROJECTION
  ? JSON.parse(await fs.readFile(process.env.PASSAGE_BACKEND_PROJECTION,'utf8')) : null;
const clean = value => Array.isArray(value) ? value.map(clean) : value && typeof value === 'object'
  ? Object.fromEntries(Object.entries(value).filter(([key]) => !['answers','answer','correct_id','correct_ids','explanation_html'].includes(key))
    .map(([key,value]) => [key,clean(value)])) : value;
const question = {id:original.id,title:original.title,html:original.html,interaction:clean(original.interaction)};
assert.equal(question.interaction.type,'composite');
const access = {learner_id:'isolated-passage-reader',role:'account',purpose:'test',is_student:true,
  features:['learn','submit_answer','review_history','request_help'],topics:[],topics_mode:'all',display_name:'隔离验收'};
const base = publicBase || 'http://127.0.0.1:18867/';
const apiOrigin = publicBase ? 'https://124.221.144.155' : new URL(base).origin;
const report = {passed:false,public_assets:Boolean(publicBase),api:'isolated fixture',production_api_requests:0,
  asset_version:assetVersion,errors:[],requests:[],checks:[],screenshots:[]};
let state;
const require = createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless:true,ignoreDefaultArgs:['--hide-scrollbars'],executablePath:process.env.CHROME_EXECUTABLE
  || path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
await fs.mkdir(output,{recursive:true});
const context = await browser.newContext({viewport:{width:1500,height:900},reducedMotion:'reduce',locale:'zh-CN'});
await context.addInitScript(origin => {
  localStorage.setItem('graspmemoedu:identity:'+origin,'isolated-passage-identity');
  localStorage.setItem('graspmemoedu:identity-role:'+origin,'account');
},apiOrigin);
await context.route('**/*',async route => {
  const request=route.request(), url=new URL(request.url());
  if (url.pathname.startsWith('/api/')) {
    const endpoint=url.pathname.slice(5);report.requests.push({endpoint,method:request.method()});
    let data;
    if(endpoint==='session') data={session_token:'isolated-passage-session'};
    else if(endpoint==='access')data=access;
    else if(endpoint==='subjects')data={subjects:[{id:'english',title:'英语',name:'英语',available:true}]};
    else if(endpoint==='profile')data={display_name:'隔离验收',timezone:'Asia/Shanghai'};
    else if(endpoint==='presence')data={status:'ok'};
    else if(endpoint==='help-center')data={items:[],unread_count:0,total:0};
    else if(endpoint==='catalog')data={courses:[],selected_course_id:null};
    else if(endpoint==='training/bank')data={id:bank.id,title:bank.title,subject_id:'english',version:bank.version,
      groups:[{id:group.id,title:group.title,parent_id:null,question_count:1,introduction_count:0,completed_count:0}]};
    else if(endpoint==='training/group')data={id:group.id,title:group.title,bank_id:bank.id,bank_title:bank.title,
      version:bank.version,math_css:bank.math_css,introductions:[],questions:[{id:question.id,title:question.title,completed_count:0,latest_attempt_id:null}]};
    else if(endpoint==='training/history')data={items:[],next_offset:null};
    else if(endpoint==='training/open') {
      state={attempt_id:request.postDataJSON().request_id,bank_id:bank.id,group_id:group.id,question_id:question.id,
        version:bank.version,phase:'answer',question,can_self_rate:false,can_submit:true,can_learn:true,can_review_early:false};data=state;
    } else if(endpoint==='training/submit') {
      const values=JSON.parse(request.postDataJSON().answer).values;
      const items=original.interaction.controls.map(control=>({id:control.id,correct:control.answers.includes(values[control.id])}));
      state={...state,phase:'done',answer:request.postDataJSON().answer,answer_display:'已保存',
        result:{correct:items.every(item=>item.correct),rating:2,practice_only:true,reason:items.map((item,i)=>`${i+1} ${item.correct?'✓':'✗'}`).join(' · ')},
        choice_feedback:{items:original.interaction.controls.map((control,i)=>({id:control.id,correct_ids:control.answers,answer:control.answers[0],correct:items[i].correct}))},
        reference_answer_html:'<p>隔离验收参考答案。</p>',explanation_html:'<p>隔离验收解析。</p>'};data=state;
      if(backendProjection) {
        assert.deepEqual(JSON.parse(state.answer),JSON.parse(backendProjection.answer));
        for(const key of ['result','choice_feedback','reference_answer','reference_answer_html','reference_math_css','explanation_html']) state[key]=backendProjection[key];
        report.grading='real isolated TrainingService';
      }
    } else if(endpoint==='training/state')data=state;
    else {report.errors.push('Unexpected fixture endpoint '+endpoint);return route.fulfill({status:500,json:{message:'Unexpected fixture endpoint'}});}
    return route.fulfill({json:data});
  }
  if(url.origin===apiOrigin && publicBase){report.production_api_requests++;return route.abort();}
  if(publicBase) {
    if(url.origin!==new URL(base).origin || request.method()!=='GET')return route.abort();
    return route.continue();
  }
  if(url.pathname==='/deployment.json')return route.fulfill({json:{api_origin:''}});
  const file=path.resolve(web,'.'+(url.pathname==='/'?'/index.html':url.pathname));
  if(!file.startsWith(path.resolve(web)+path.sep))return route.abort();
  try {return route.fulfill({body:await fs.readFile(file),contentType:/\.m?js$/.test(file)?'text/javascript':file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':undefined});}
  catch {return route.abort();}
});
const page=await context.newPage();page.setDefaultTimeout(20000);
page.on('pageerror',error=>report.errors.push(error.message));
try {
  await page.goto(base+(publicBase?'?v='+assetVersion:'')+'#/subjects/english/banks/'+bank.id+'/'+group.id,{waitUntil:'networkidle'});
  await page.locator('#trainingAnswerForm').waitFor();
  assert.equal(await page.locator('script[src*="/app.js"]').getAttribute('src'),'./app.js?v='+assetVersion);
  if (publicBase) {
    await page.reload({waitUntil:'networkidle'});await page.locator('#trainingAnswerForm').waitFor();
    assert.equal(await page.locator('script[src*="/app.js"]').getAttribute('src'),'./app.js?v='+assetVersion);
    report.checks.push('published asset version and same-browser reload');
  }
  const left=page.locator('#trainingstepCard > .passageQuestionPane'), right=page.locator('#trainingAnswerForm');
  assert.equal(await left.count(),1);
  assert.equal(await right.locator('.trainingNotebookActions,[data-content-actions]').count(),0);
  assert.equal(await left.locator('.trainingNotebookActions,[data-content-actions]').count(),0);
  assert.equal(await page.locator('#trainingstepCard > .stepHeader .trainingNotebookActions').count(),1);
  assert.equal(await page.locator('#trainingstepCard > .stepHeader [data-content-actions]').count(),1);
  const inlineSelects = await left.locator('select').count();
  if (inlineSelects) assert.equal(await left.locator('select').first().evaluate(select=>select.selectedIndex),-1);
  report.scrollbars = await page.locator('.passageQuestionPane,#trainingAnswerForm').evaluateAll(panes => panes.map(pane => ({
    overflow:getComputedStyle(pane).overflowY, gutter:pane.offsetWidth-pane.clientWidth,
    display:getComputedStyle(pane,'::-webkit-scrollbar').display,
    thumb:getComputedStyle(pane,'::-webkit-scrollbar-thumb').backgroundColor,
    width:getComputedStyle(pane,'::-webkit-scrollbar').width,
  })));
  assert(report.scrollbars.every(bar => bar.overflow==='scroll' && bar.gutter>2 && bar.display==='block'));
  const target=question.interaction.controls[Math.floor(question.interaction.controls.length/2)];
  const visibleGroups=()=>right.evaluate(pane=>{
    const box=pane.getBoundingClientRect();return [...pane.querySelectorAll('fieldset')].filter(el=>{const r=el.getBoundingClientRect();return r.top>=Math.max(box.top,60) && r.bottom<=Math.min(box.bottom,innerHeight);}).length;
  });
  report.visibleGroupsBefore=await visibleGroups();
  report.initialGeometry=await right.evaluate(pane=>({pane:pane.getBoundingClientRect().toJSON(),groups:[...pane.querySelectorAll('fieldset')].slice(0,5).map(el=>({rect:el.getBoundingClientRect().toJSON(),margin:getComputedStyle(el).margin,choice:getComputedStyle(el.querySelector('.choiceOption')).cssText,columns:getComputedStyle(el.querySelector('.choiceList')).gridTemplateColumns}))}));
  assert(report.visibleGroupsBefore>=5,'At least five cloze answer groups fit vertically on desktop');
  const columns=()=>right.locator('.choiceList').first().evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length);
  report.columnsBefore=await columns();
  const historyButton=page.locator('#traininghistoryButton');
  assert.equal(await historyButton.count(),1);
  await historyButton.click();report.columnsAfter=await columns();report.visibleGroupsAfter=await visibleGroups();assert(report.visibleGroupsAfter>=5);await historyButton.click();
  for (const width of [1500,768,390,320]) {
    await page.setViewportSize({width,height:1050});
    const link=left.locator(`[data-passage-target="${target.id}"]`);await link.scrollIntoViewIfNeeded();
    const before=await left.evaluate(el=>el.scrollTop);await link.click();
    assert.equal(await left.evaluate(el=>el.scrollTop),before);
    assert(await right.evaluate((pane,id)=>{
      const t=pane.querySelector(`[data-answer-control="${id}"]`).getBoundingClientRect(),p=pane.getBoundingClientRect();
      return Math.abs(t.y+t.height/2-p.y-p.height/2)<3;
    },target.id));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    const name='training-'+width+'.png';await page.screenshot({path:path.join(output,name),fullPage:true});report.screenshots.push(name);
  }
  report.checks.push('real controller: two panes, whole-question actions, five visible answer groups, responsive option columns, centered navigation');
  for(const control of question.interaction.controls) {
    const select=left.locator(`select[data-control-id="${control.id}"]`);
    if(await select.count()) await select.selectOption(control.options[0].id);
    else await right.locator(`input[data-control-id="${control.id}"][value="${control.options[0].id}"]`).check();
  }
  await page.locator('#trainingSubmit').click();await page.locator('#trainingstepCard .trainingVerdict').waitFor();
  assert.equal(report.requests.filter(r=>r.endpoint==='training/submit').length,1);
  assert.equal(await page.locator('#trainingstepCard select:not(:disabled)').count(),0);
  assert.equal(await page.locator('#trainingstepCard .choiceOption input:not(:disabled)').count(),0);
  assert(await page.locator('#trainingstepCard .choiceOption[data-answer-truth="true"]').count()>0);
  assert(await page.locator('#trainingstepCard .choiceOption[data-answer-truth="false"]').count()>0);
  assert(await page.locator('.referenceItemAnswer[data-answer-truth="true"]').count()>0);
  assert(await page.locator('.referenceItemAnswer[data-answer-truth="false"]').count()>0);
  await page.setViewportSize({width:1500,height:1050});
  await page.screenshot({path:path.join(output,'submitted-feedback.png'),fullPage:true});report.screenshots.push('submitted-feedback.png');
  await page.locator('#trainingstepCard').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'submitted-viewport.png')});report.screenshots.push('submitted-viewport.png');
  await page.locator('.referenceAnswer').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'reference-viewport.png')});report.screenshots.push('reference-viewport.png');
  report.checks.push('one passage / one submit and disabled submitted controls');
  assert.deepEqual(report.errors,[]);assert.equal(report.production_api_requests,0);report.passed=true;
} catch(error) {report.failure=String(error);await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});process.exitCode=1;}
finally {await fs.writeFile(path.join(output,'acceptance.json'),JSON.stringify(report,null,2));await browser.close();}
console.log(JSON.stringify({passed:report.passed,checks:report.checks,failure:report.failure}));
