/** Exercise the actual Lesson and bank controllers with isolated source fixtures. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';

const [output, sourceBank] = process.argv.slice(2);
if (!output) throw new Error('Expected evidence directory');
const site=path.dirname(path.dirname(fileURLToPath(import.meta.url))), web=path.join(site,'web');
const original=sourceBank ? JSON.parse(await fs.readFile(sourceBank,'utf8')) : null;
const source=original?.groups.flatMap(g=>g.introductions||[]).find(i=>i.html.includes('<table'));
const intro={id:'guide-a',title:'Introduction',text:'原始正文',html:source?.html||'<p>保留原书正文。</p><table><tr><td>原表格</td></tr></table>'};
const second={id:'guide-b',title:'Introduction: 第二段',text:'原始后续正文',html:'<p>保留后续正文。</p>'};
const choice={id:'question-a',title:'原题 1',html:'<p>请选择原题的选项。</p>',interaction:{type:'choice',multiple:false,options:[
  {id:'oA',text:'第一项',html:'<p>第一项</p>'},{id:'oB',text:'第二项',html:'<p>第二项</p>'}]}};
const bank={id:'isolated-reader',title:'共用阅读器验收',subject_id:'math',version:'fixture-v1',math_css:original?.math_css||'',groups:[
  {id:'only',title:'纯正文',parent_id:null,introductions:[intro,second],questions:[],content_order:[{type:'introduction',id:intro.id},{type:'introduction',id:second.id}]},
  {id:'mixed',title:'混合内容',parent_id:null,introductions:[intro,second],questions:[choice],content_order:[{type:'introduction',id:intro.id},{type:'question',id:choice.id},{type:'introduction',id:second.id}]},
  {id:'questions',title:'无正文',parent_id:null,questions:[choice]}]};
const access={learner_id:'isolated-reader-test',role:'account',purpose:'test',features:['learn','submit_answer','review_history'],topics:[],topics_mode:'all',display_name:'隔离验收'};
const introStep={...intro,kind:'introduction',phase:'reading',visited:true,unlocked:true,current:true,actions:['continue'],answer:null};
const example={...choice,kind:'example',module_id:'module-a',phase:'answer',visited:true,unlocked:true,actions:['submit'],answer:null,attempt_id:'isolated-attempt'};
let trainingState;
let state={learner_id:access.learner_id,access,topic_id:'isolated-topic',title:bank.title,topic_format_version:2,topic_kind:'lesson',
  course_version:'fixture-v1',revision:1,status:'in_progress',attempt_id:'isolated-attempt',current_step_id:intro.id,active_step_id:intro.id,
  introduction_read:false,steps:[introStep,example,{...second,kind:'introduction',visited:true,unlocked:true,phase:'reading',actions:['continue']}],
  modules:[{id:'module-a',title:'原题模块',status:'not_started',attempt_id:'isolated-attempt',practice_answered_count:0,practice_target_count:2}],
  math_css:bank.math_css,dependency_ready:true,pending_submission_id:null};
const origin='http://127.0.0.1:18866',report={status:'pending',fixture_network:true,production_requests:0,checks:[],errors:[],requests:[]};
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({headless:true,executablePath:path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
await fs.mkdir(output,{recursive:true});
const context=await browser.newContext({viewport:{width:1365,height:1000},locale:'zh-CN'});
await context.addInitScript(origin=>{
  localStorage.setItem('graspmemoedu:identity:'+origin,'isolated-fixture-identity');
  localStorage.setItem('graspmemoedu:identity-role:'+origin,'account');
},origin);
await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());
  if(url.origin!==origin){report.production_requests++;return route.abort();}
  if(url.pathname==='/deployment.json')return route.fulfill({json:{api_origin:''}});
  if(url.pathname.startsWith('/api/')){
    const endpoint=url.pathname.slice(5);report.requests.push({endpoint,method:req.method()});
    let data;
    if(endpoint==='session')data={session_token:'isolated-fixture-session'};
    else if(endpoint==='access')data=access;
    else if(endpoint==='subjects')data={subjects:[{id:'math',name:'数学',title:'数学',available:true}]};
    else if(endpoint==='profile')data={display_name:'隔离验收',timezone:'Asia/Shanghai'};
    else if(endpoint==='catalog')data={courses:[],selected_course_id:null};
    else if(endpoint==='state')data=state;
    else if(endpoint==='select'||endpoint==='continue'){
      const body=req.postDataJSON();const pages=state.steps,index=pages.findIndex(s=>s.id===state.current_step_id);
      const id=endpoint==='select'?body.step_id:pages[index+1]?.id;
      state={...state,current_step_id:id,active_step_id:id,revision:state.revision+1};data=state;
    } else if(endpoint==='training/bank')data={...bank,groups:bank.groups.map(g=>({...g,questions:undefined,question_count:g.questions.length,introduction_count:g.introductions?.length||0,completed_count:0}))};
    else if(endpoint==='training/group'){
      const group=bank.groups.find(g=>g.id===url.searchParams.get('group_id'));
      data={...group,bank_id:bank.id,bank_title:bank.title,version:bank.version,math_css:bank.math_css,
        questions:group.questions.map(q=>({id:q.id,title:q.title,completed_count:0,latest_attempt_id:null}))};
    } else if(endpoint==='training/history')data={items:[],next_offset:null};
    else if(endpoint==='training/open'){
      const body=req.postDataJSON();data={attempt_id:body.request_id,bank_id:bank.id,group_id:body.group_id,question_id:choice.id,
        version:bank.version,phase:'answer',question:choice,can_self_rate:false,can_submit:true,can_learn:true,can_review_early:false};
      trainingState=data;
    } else if(endpoint==='training/submit'){
      const body=req.postDataJSON();data={...trainingState,phase:'done',answer:body.answer,answer_display:'第一项',
        result:{correct:true,rating:5,practice_only:true},reference_answer_html:'<p>第一项</p>',explanation_html:'<p>保留原解析。</p>'};
    } else {report.errors.push('Unexpected fixture endpoint '+endpoint);return route.fulfill({status:500,json:{message:'Unexpected fixture endpoint'}});}
    return route.fulfill({json:data});
  }
  const file=path.resolve(web,'.'+(url.pathname==='/'?'/index.html':url.pathname));
  if(!file.startsWith(web+path.sep))return route.abort();
  try{return route.fulfill({body:await fs.readFile(file),contentType:file.endsWith('.js')||file.endsWith('.mjs')?'text/javascript':file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':undefined});}
  catch{return route.abort();}
});
const page=await context.newPage();page.setDefaultTimeout(20000);page.on('pageerror',e=>report.errors.push(e.message));
const check=name=>report.checks.push(name);
const shape=selector=>page.locator(selector).evaluate(el=>({header:el.querySelector('.stepHeader').textContent,
  body:el.querySelector('.courseContent').innerHTML,classes:[...el.children].map(n=>n.className)}));
try{
  await page.goto(origin+'/#/subjects/math/topic/isolated-topic',{waitUntil:'networkidle'});
  await page.locator('#stepCard .stepHeader').waitFor();const lessonIntro=await shape('#stepCard');
  assert.equal(await page.locator('#stepCard .stepCounter').textContent(),'阅读');
  await page.locator('#stepCard .primaryButton').click();await page.locator('#answerInput').waitFor({state:'attached'});
  assert.equal(await page.locator('#stepCard .questionTypeField').count(),1);
  assert.equal(await page.locator('#previousPageButton').count(),1);check('actual Lesson guidance, choice field and shared navigation');
  await page.goto(origin+'/#/subjects/math/banks/'+bank.id,{waitUntil:'networkidle'});
  const only=page.locator('[data-task-id="only"]');await only.waitFor();
  assert.equal(await only.locator('.taskHeading strong').textContent(),'引论');
  assert.equal(await only.locator('.taskPrerequisites').textContent(),'');
  assert.equal(await page.locator('[data-task-id="mixed"] .taskHeading strong').textContent(),'题组');check('Introduction-only task uses canonical 引论 label without a zero-question count');
  await page.goto(origin+'/#/subjects/math/banks/'+bank.id+'/only',{waitUntil:'networkidle'});
  await page.locator('#trainingStepTitle').waitFor();const bankIntro=await shape('#trainingstepCard');
  assert.deepEqual(bankIntro,lessonIntro);check('actual bank and Lesson Introduction content/header DOM is identical');
  assert.equal(await page.locator('#traininghistoryPanel .historyGroupHeader').count(),2);
  assert.equal(await page.locator('#trainingstepCard .answerForm').count(),0);
  await page.locator('#trainingnextPageButton').click();await page.getByRole('heading',{name:'指导: 第二段',exact:true}).waitFor();
  await page.locator('#trainingpreviousPageButton').click();await page.getByRole('heading',{name:'指导',exact:true}).waitFor();
  assert.equal(report.requests.filter(r=>r.endpoint.startsWith('training/')&&r.method==='POST').length,0);check('guidance-only previous/next and sidebar cause zero bank writes');
  for(const width of [1365,390]){
    await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(output,'guidance-'+width+'.png'),fullPage:true});
  }
  await page.setViewportSize({width:1365,height:1000});
  await page.goto(origin+'/#/subjects/math/banks/'+bank.id+'/mixed',{waitUntil:'networkidle'});
  await page.locator('#trainingnextPageButton').click();await page.locator('#trainingAnswer').waitFor({state:'attached'});
  assert.equal(await page.locator('#trainingstepCard .questionTypeField').count(),1);
  assert.equal(await page.locator('.trainingRedo').count(),0);
  await page.locator('#trainingpreviousPageButton').click();await page.getByRole('heading',{name:'指导',exact:true}).waitFor();
  check('mixed source-order navigation preserves choice controls and unlearned review policy');
  await page.goto(origin+'/#/subjects/math/banks/'+bank.id+'/questions',{waitUntil:'networkidle'});
  await page.locator('#trainingAnswerForm input[type="radio"]').first().check();
  await page.locator('#trainingSubmit').click();await page.locator('#trainingstepCard .submittedAnswer').waitFor({state:'attached'});
  assert.equal(await page.locator('#trainingstepCard .choiceAnswer').textContent(),'第一项');
  assert.equal(await page.locator('#trainingstepCard .referenceAnswerBody').textContent(),'第一项');
  assert.equal(await page.locator('#trainingstepCard .feedbackTitle').textContent(),'回答正确');
  assert.equal(await page.locator('#trainingstepCard input[type="radio"]:checked').count(),1);
  check('no-Introduction group preserves shared submitted choice, reference answer, explanation and feedback');
  assert.deepEqual(report.errors,[]);assert.equal(report.production_requests,0);report.status='passed';
}catch(error){report.status='failed';report.failure=String(error);await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});process.exitCode=1;}
finally{await fs.writeFile(path.join(output,'shared-reader-validation.json'),JSON.stringify(report,null,2));await browser.close();}
console.log(JSON.stringify({status:report.status,checks:report.checks,failure:report.failure}));
