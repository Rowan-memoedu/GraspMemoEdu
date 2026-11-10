/** Real browser + HTTP + judge acceptance in the disposable training fixture. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base,fixture,release] = process.argv.slice(2);
const paperOnly=process.argv.includes('--paper-only');
const choicesOnly=process.argv.includes('--choices-only');
assert.equal(new URL(base).hostname,'127.0.0.1');
const require = createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const output=path.join(fixture,choicesOnly?'choice-colour-browser':paperOnly?'multi-step-paper-browser':'multi-step-browser');await fs.mkdir(output,{recursive:true});
const report={passed:false,checks:[],errors:[],real_judge:true,production_requests:0};
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({viewport:{width:1280,height:950},locale:'zh-CN'});
page.setDefaultTimeout(25000);
if(release)await page.route('**/*',async route=>{
  const url=new URL(route.request().url());
  assert.equal(url.origin,base,'Fixture must not contact production');
  const relative=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname.slice(1));
  if(!relative.startsWith('api/')&&relative!=='deployment.json'&&!relative.includes('..')){
    const file=path.join(release,relative);
    try{if((await fs.stat(file)).isFile())return route.fulfill({path:file});}catch{}
  }
  return route.continue();
});
page.on('pageerror',e=>report.errors.push(e.message));
let token,session;
page.on('request',r=>{token=r.headers()['x-learning-identity']||token;});
async function api(route,body){
  if(!route.includes('subject_id='))route+=(route.includes('?')?'&':'?')+'subject_id=math';
  session||=(await(await fetch(base+'/api/session',{headers:{Origin:base}})).json()).session_token;
  const r=await fetch(base+'/api/'+route,{method:body?'POST':'GET',headers:{Origin:base,'Content-Type':'application/json','X-Learning-Session':session,'X-Learning-Identity':token},...(body?{body:JSON.stringify(body)}:{})});
  const data=await r.json();assert.equal(r.status,200,JSON.stringify(data));return data;
}
let latest;
page.on('response',async r=>{if(/\/api\/training\/(?:submit|submission|state|open)/.test(r.url())&&r.status()===200){try{latest=await r.json();}catch{}}});
const go=id=>page.goto(base+'#/subjects/math/banks/training-acceptance/group-a?question='+id,{waitUntil:'networkidle'});
const pass=s=>{report.checks.push(s);console.log('PASS '+s);};
const shot=async name=>page.screenshot({path:path.join(output,name+'.png'),fullPage:true});
const submit=()=>page.locator('#trainingSubmit').click();
async function step(number,answer){
  const current=page.locator('.multiStepCurrent');await current.waitFor();
  assert.equal(await current.getAttribute('data-step-id'),'step-'+number);
  const blank=current.locator('input:not([type=hidden])');
  if(await blank.count())await blank.fill(answer);else await page.locator('#trainingAnswer').fill(answer);
  await submit();
}
async function colours(expected){
  await page.locator('.trainingVerdict').waitFor();
  const values=await page.locator('.choiceOption[data-answer-truth]').evaluateAll(nodes=>Object.fromEntries(nodes.map(n=>[n.dataset.optionId,{truth:n.dataset.answerTruth,color:getComputedStyle(n).borderColor}])));
  assert.deepEqual(Object.fromEntries(Object.entries(values).map(([k,v])=>[k,v.truth])),expected);
  for(const value of Object.values(values))assert.equal(value.color,value.truth==='false'?'rgb(180, 35, 40)':'rgb(38, 118, 77)');
}
try{
  await go('multi');
  const identities=JSON.parse(await fs.readFile(path.join(fixture,'identities.json'),'utf8'));
  await page.locator('#userMenuButton').hover();await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities.standard);await page.locator('#identitySubmitButton').click();
  await page.locator('#identityDialog').waitFor({state:'hidden'});
  if(!paperOnly && !choicesOnly){
  await go('multi');await page.locator('#trainingAnswer').waitFor({state:'attached'});
  pass('Multi-step question mounts the current fill input');
  assert.equal(await page.locator('.multiStepPart').count(),1);
  await step(1,'5');await page.locator('.multiStepCurrent[data-step-id=step-2]').waitFor({timeout:240000});
  assert.equal(await page.locator('.multiStepPart').count(),2);assert.equal(await page.locator('.multiStepPart[data-step-id=step-3]').count(),0);
  await page.reload({waitUntil:'networkidle'});await page.locator('.multiStepCurrent[data-step-id=step-2]').waitFor();
  await step(2,'会改变，交换后和变成 7。');await page.locator('.trainingVerdict.incorrect').waitFor({timeout:240000});
  assert.equal(await page.locator('.multiStepPart').count(),3);assert.equal(await page.locator('#trainingSubmit').count(),0);
  assert.equal(latest.result.multi_step.first_failed_step_id,'step-2');assert.equal(latest.result.multi_step.mode,'whole');
  assert.equal((await page.locator('#trainingstepCard').innerText()).includes('{{blank:'),false);
  await shot('wrong-step-reveals-all');pass('Real judge unlocks one step, resumes after reload, first wrong step reveals full question and solution');
  await page.locator('.trainingRedo').click();await page.locator('.multiStepCurrent[data-step-id=step-1]').waitFor();
  await step(1,'5');await page.locator('.multiStepCurrent[data-step-id=step-2]').waitFor({timeout:240000});
  await step(2,'和不变，因为加法满足交换律。');await page.locator('.multiStepCurrent[data-step-id=step-3]').waitFor({timeout:240000});
  await step(3,'6');await page.locator('.trainingVerdict.correct').waitFor({timeout:240000});
  await shot('all-steps-correct');pass('Whole-question retry starts at step 1; fill/proof/fill all pass using the real judge');
  for(const width of [320,375,414,768,1280]){await page.setViewportSize({width,height:950});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
  await shot('multi-step-mobile');pass('Completed multi-step layout fits 320/375/414/768/1280px');
  await page.setViewportSize({width:1280,height:950});
  }
  if(!paperOnly){
  await go('choice');await page.locator('.choiceOption input[value=b]').check();await submit();await colours({a:'true',b:'false'});await shot('positive-wrong');
  await page.locator('.trainingRedo').click();await page.locator('.choiceOption input[value=a]:enabled').check();await submit();await colours({a:'true'});
  await go('negative');await page.locator('.choiceOption input[value=a]').check();await submit();await colours({a:'false',b:'true'});await shot('negative-wrong');
  await page.locator('.trainingRedo').click();await page.locator('.choiceOption input[value=b]:enabled').check();await submit();await colours({b:'true'});await shot('negative-correct');
  pass('Correct options are green and wrong selections are red for both prompt directions');
  if(!choicesOnly)await fetch(base+'/__qa/advance',{method:'POST',headers:{Origin:base}});
  }
  if(!choicesOnly){
  const inbox=await api('review-papers/inbox?subject_id=math');
  const candidate=[...inbox.active,...inbox.items].find(i=>i.paper_type_id==='multi-step-pair'||i.title==='分步题隔离卷');assert(candidate,'Missing mixed review paper');
  let paper=candidate.active?await api('review-papers/state?paper_id='+candidate.id):await api('review-papers/open',{candidate_id:candidate.id,request_id:crypto.randomUUID()});
  assert.equal(paper.mode,'paper');const index=paper.items.findIndex(i=>i.multi_step);assert(index>=0);
  await page.goto(base+'#/subjects/math/reviews/'+paper.id,{waitUntil:'networkidle'});
  if(index!==paper.items.findIndex(i=>!i.completed))await page.locator(`[data-question-index="${index}"]`).click();
  await page.locator('.multiStepCurrent[data-step-id=step-1] input').fill('5');
  const checked=page.waitForResponse(r=>r.url().includes('/review-papers/submit')&&r.request().method()==='POST');
  await page.locator('#paperSubmit').click();const checkedResponse=await checked;assert.equal(checkedResponse.status(),200,await checkedResponse.text());
  await page.locator('.multiStepCurrent[data-step-id=step-2]').waitFor({timeout:240000});
  await page.reload({waitUntil:'networkidle'});
  await page.locator('.multiStepCurrent[data-step-id=step-2]').waitFor();
  await page.locator('#paperAnswer').fill('会改变，交换后和变成 7。');await page.locator('#paperSubmit').click();
  await page.locator('.multiStepPart[data-step-id=step-3]').waitFor({timeout:240000});
  paper=await api('review-papers/state?paper_id='+paper.id);
  assert.equal(paper.items[index].result.correct,false);assert.equal(paper.items[index].result.multi_step.first_failed_step_id,'step-2');
  const other=paper.items.find(i=>i.index!==index);assert.equal(other.explanation_html,null);
  await shot('paper-wrong-step-reveals-all');
  await page.locator(`[data-question-index="${other.index}"]`).click();
  await page.locator('#paperstepCard .stepTitle').filter({hasText:new RegExp(`^习题 ${other.index+1}$`)}).waitFor();
  await page.locator('.choiceOption input[value=a]').check();await page.locator('#paperSubmit').click();
  await page.getByText('本次复习已完成',{exact:true}).waitFor({timeout:240000});
  paper=await api('review-papers/state?paper_id='+paper.id);assert(paper.items.every(i=>i.result));
  pass('Whole-paper mode supports step checks, reload, first wrong reveal, and completing the remaining question');
  }
  assert.deepEqual(report.errors,[]);report.passed=true;
}finally{if(!report.passed)await shot('failure');await fs.writeFile(path.join(output,'acceptance.json'),JSON.stringify(report,null,2));await browser.close();}
