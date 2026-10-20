/** Inspect actual native-bank and v2-reader pages in a disposable local app. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base,output,accountPath]=process.argv.slice(2);
assert.equal(new URL(base).hostname,'127.0.0.1');
assert(!['8765','18768',''].includes(new URL(base).port));
await fs.mkdir(output,{recursive:true});
const headers={Origin:base,'Content-Type':'application/json'};
headers['X-Learning-Session']=(await (await fetch(base+'/api/session',{headers})).json()).session_token;
const account=JSON.parse(await fs.readFile(accountPath,'utf8'));
const login=await (await fetch(base+'/api/access/login',{method:'POST',headers,
  body:JSON.stringify({invitation_code:account.invitation_code})})).json();
assert(login.access?.purpose==='test'&&login.access.is_student);
headers['X-Learning-Identity']=login.identity_token;
async function api(route,body) {
  const response=await fetch(base+'/api/'+route,{headers,method:body?'POST':'GET',...(body?{body:JSON.stringify(body)}:{})});
  assert.equal(response.status,200,route);return response.json();
}
// Advance only the isolated test reader, using the ordinary public API. Screenshots
// exercise actual code/image sections, rather than just brand icons/preambles.
for(const [tid,pattern] of [[308,/<pre\b/],[25,/<img\b/]]) {
  const query=`?subject_id=cs&topic_id=hyperskill-topic-${tid}`;
  let state=await api('state'+query), found=false;
  for(let i=0;i<30;i++) {
    const step=state.steps.find(s=>s.current);
    if(pattern.test(step?.html||'')){found=true;break;}
    assert(step?.actions.includes('continue'),'Missing source section');
    state=await api('continue'+query,{request_id:crypto.randomUUID(),expected_revision:state.revision,
      attempt_id:state.attempt_id,step_id:state.active_step_id});
  }
  assert(found,'Source section was not reachable');
}
const req=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=req('playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
const report={pages:[],errors:[],user_browser_touched:false};
try {
  const context=await browser.newContext({locale:'zh-CN'});
  await context.addInitScript(({base,token})=>{
    localStorage.setItem('graspmemoedu:identity:'+base,token);
    localStorage.setItem('graspmemoedu:identity-role:'+base,'account');
    localStorage.setItem('graspmemoedu:language','zh-CN');
  },{base,token:login.identity_token});
  const page=await context.newPage();
  page.on('pageerror',e=>report.errors.push(e.message));
  const cases=[
    ['numeric-theory','topic/hyperskill-topic-308'],
    ['image-theory','topic/hyperskill-topic-25'],
    ['bank','banks/hyperskill-practice'],
    ['fill','banks/hyperskill-practice/hyperskill-topic-113?question=hyperskill-step-38412'],
    ['code','banks/hyperskill-practice/hyperskill-topic-152?question=hyperskill-step-43304'],
  ];
  for(const width of [320,375,414,768,1365])for(const [name,route] of cases) {
    await page.setViewportSize({width,height:960});
    await page.goto(base+'/#/subjects/cs/'+route,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>![...document.querySelectorAll('.portalLoading')]
      .some(e=>e.getClientRects().length&&!e.closest('[hidden]'))).catch(async error=>{
        await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
        await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({
          name,width,text:await page.locator('body').innerText(),errors:report.errors},null,2));
        throw error;
      });
    await page.waitForTimeout(150);
    const state=await page.evaluate(()=>{
      const visible=e=>e.getClientRects().length&&!e.closest('[hidden]');
      return {errors:[...document.querySelectorAll('.portalError,.trainingError')].filter(visible).map(e=>e.textContent),
        overflow:document.documentElement.scrollWidth>innerWidth,
        title:document.title,
        codes:[...document.querySelectorAll('pre')].filter(visible).length,
        sourceImages:[...document.querySelectorAll('.courseContent img')].filter(visible).map(i=>i.complete&&i.naturalWidth>0),
        images:[...document.querySelectorAll('img')].filter(visible).map(i=>({loaded:i.complete&&i.naturalWidth>0})),
        connection:[...document.querySelectorAll('button')].filter(visible).some(e=>e.textContent.includes('连接 Hyperskill')),
        officialIDE:[...document.querySelectorAll('button')].filter(visible).some(e=>e.textContent.includes('在 IDEA 中解题')),
        blanks:[...document.querySelectorAll('input.inlineBlank,select.inlineBlank')].filter(visible).length,
        webCodeEditor:[...document.querySelectorAll('.hyperskillCodeEditor')].filter(visible).length,
        text:document.body.innerText.slice(0,2000)};
    });
    await page.screenshot({path:path.join(output,`${name}-${width}.png`),fullPage:true});
    await fs.writeFile(path.join(output,`${name}-${width}.json`),JSON.stringify({...state,pageErrors:report.errors},null,2));
    assert.deepEqual(state.errors,[],name);
    assert(!state.overflow,`${name} ${width} overflow`);
    assert(state.images.every(i=>i.loaded),`${name} images`);
    if(['fill','code'].includes(name))assert(!state.connection,`${name} still waits for source loading`);
    if(name==='fill')assert(state.blanks>0,'Prepared blank fields not visible');
    if(name==='code')assert(state.officialIDE&&!state.webCodeEditor,'Missing official IDEA-only flow');
    // The chapter drawer is intentionally closed on mobile; assert the current
    // source section, while the package validator checks every Introduction.
    if(name==='numeric-theory')assert(state.text.includes('Integer literals'),state.text);
    if(name==='numeric-theory')assert(state.codes>0,'Missing source code block');
    if(name==='image-theory')assert(state.sourceImages.length>0&&state.sourceImages.every(Boolean),'Missing source image');
    report.pages.push({name,width,...state});
  }
  assert.deepEqual(report.errors,[]);
  await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({pages:report.pages.length,errors:report.errors.length,status:'passed'}));
} finally {
  await browser.close();
  await fetch(base+'/api/access/logout',{method:'POST',headers,body:'{}'});
}
