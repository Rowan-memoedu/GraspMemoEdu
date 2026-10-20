/** Enrich an immutable local source export before importing it. No answer submissions. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {publicDataset,publicCodeOptions} from './hyperskill_presentation.mjs';
import {presentation} from './hyperskill_protocol.mjs';
const [configPath,source,output]=process.argv.slice(2);
if(!output)throw Error('Usage: capture_hyperskill_presentations config source output');
const config=JSON.parse(await fs.readFile(configPath));
const {LiveCookieSnapshot}=await import(pathToFileURL(config.cookieModule));
const auth=new LiveCookieSnapshot({workflowRoot:config.cookieWorkflowRoot,dataRoot:config.cookieDataRoot});
async function call(method,route,body) {
  auth.refresh();
  const url='https://hyperskill.org/api'+route,cookie=auth.headerForUrl(url);
  const csrf=cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith('csrftoken='));
  const response=await fetch(url,{method,redirect:'error',signal:AbortSignal.timeout(45000),
    headers:{Cookie:cookie,Accept:'application/json',...(body?{'Content-Type':'application/json',
      'X-CSRFToken':decodeURIComponent(csrf?.slice(10)||''),Origin:'https://hyperskill.org',Referer:'https://hyperskill.org/learn'}:{})},
    ...(body?{body:JSON.stringify(body)}:{})});
  if(!response.ok)throw Error(`Hyperskill ${method} ${route.split('?')[0]}: ${response.status}`);
  return response.json();
}
async function identity() {
  if((await call('GET','/profiles/current')).profiles?.[0]?.id!==config.expectedUserId)throw Error('Account changed');
}
await identity();
await fs.mkdir(output,{recursive:true});
const inventory=JSON.parse(await fs.readFile(path.join(source,'inventory.json')));
await fs.writeFile(path.join(output,'inventory.json'),JSON.stringify(inventory,null,2));
let total=0,created=0;
for(const topic of inventory.topics) {
  const name=`topic-${topic.id}.json`,target=path.join(output,name);
  const raw=JSON.parse(await fs.readFile(path.join(source,name)));
  let saved;try{saved=JSON.parse(await fs.readFile(target));}catch{}
  for(const problem of raw.practices) {
    const prior=saved?.practices.find(p=>p.id===problem.id);
    if(prior?.presentation_source&&JSON.stringify(prior.block)===JSON.stringify(problem.block)) {
      problem.presentation_source=prior.presentation_source;total++;continue;
    }
    let dataset={};
    if(!['code','number','string'].includes(problem.type)) {
      const data=await call('GET',`/attempts?step=${problem.id}&user=${config.expectedUserId}&page_size=100`);
      let attempt=data.attempts?.find(a=>a.step===problem.id&&a.status==='active')||data.attempts?.find(a=>a.step===problem.id);
      if(!attempt) {
        await identity();
        attempt=(await call('POST','/attempts',{step:problem.id})).attempts?.[0];created++;
      }
      if(attempt?.step!==problem.id)throw Error('Attempt identity mismatch');
      dataset=publicDataset(problem.type,attempt.dataset);
    }
    const code_options=problem.type==='code'?publicCodeOptions(problem.block.options):{};
    presentation(problem.type,dataset,s=>({html:s}),code_options);
    problem.presentation_source={dataset,code_options};
    await fs.writeFile(target,JSON.stringify(raw,null,2));
    total++;
    if(total%20===0)console.log(JSON.stringify({captured:total,created_attempts:created,submitted_answers:0}));
  }
  await fs.writeFile(target,JSON.stringify(raw,null,2));
  console.log(JSON.stringify({topic:topic.id,captured:total,created_attempts:created,submitted_answers:0}));
}
