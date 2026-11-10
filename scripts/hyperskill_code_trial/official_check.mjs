import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
if (!process.env.HYPERSKILL_TRIAL_ROOT) throw Error('HYPERSKILL_TRIAL_ROOT is required');
const root=path.resolve(process.env.HYPERSKILL_TRIAL_ROOT);
const config=JSON.parse(await fs.readFile('D:/AppData/GraspMemoEduHyperskillBridge/config.json','utf8'));
const {LiveCookieSnapshot}=await import(pathToFileURL(config.cookieModule));
const auth=new LiveCookieSnapshot({workflowRoot:config.cookieWorkflowRoot,dataRoot:config.cookieDataRoot});
const read=async(name,fallback)=>{try{return JSON.parse(await fs.readFile(path.join(root,name),'utf8'));}catch(e){if(e.code==='ENOENT')return fallback;throw e;}};
const saveChains=new Map();
const save=(name,data)=>{const body=JSON.stringify(data,null,2),p=path.join(root,name);const next=(saveChains.get(name)||Promise.resolve()).then(async()=>{await fs.writeFile(p+'.tmp',body);await fs.rename(p+'.tmp',p);});saveChains.set(name,next.catch(()=>{}));return next;};
const hash=code=>createHash('sha256').update(code).digest('hex');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
let last=0,stop=false,networkChain=Promise.resolve();
const counts=await read('official-request-counts.json',{get:0,attempt_posts:0,submission_posts:0});
async function rawRequest(method,route,body){
  if(stop)throw Error('Upstream request stop retained');
  if(!/^\/(profiles\/current|attempts(?:[/?]|$)|submissions(?:[/?]|$))/.test(route))throw Error('Endpoint outside trial scope');
  if(method==='GET'&&route.startsWith('/submissions?'))route+='&user='+config.expectedUserId;
  if(method==='POST'&&route==='/submissions'&&counts.submission_posts>=400)throw Error('400 submission ceiling reached');
  await wait(Math.max(0,650-(Date.now()-last)));
  auth.refresh();const url='https://hyperskill.org/api'+route,cookie=auth.headerForUrl(url);
  if(!cookie)throw Error('Authenticated snapshot unavailable');
  const csrf=cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('csrftoken='));
  const field=method==='GET'?'get':route==='/attempts'?'attempt_posts':'submission_posts';
  counts[field]++;await save('official-request-counts.json',counts);last=Date.now();
  let response;
  try{response=await fetch(url,{method,redirect:'error',signal:AbortSignal.timeout(45000),headers:{Cookie:cookie,Accept:'application/json',...(method==='POST'?{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(csrf?.slice(10)||''),Origin:'https://hyperskill.org',Referer:'https://hyperskill.org/learn'}:{})},...(body?{body:JSON.stringify(body)}:{})});}
  catch{throw Error('Network response uncertain; pending POST retained');}
  await fs.appendFile(path.join(root,'official-request-metrics.jsonl'),JSON.stringify({at:new Date().toISOString(),method,endpoint:route.split('?')[0],http_status:response.status,elapsed_ms:Date.now()-last})+'\n');
  if(!response.ok){
    const detail=(await response.text()).slice(0,3000);
    await fs.appendFile(path.join(root,'official-http-errors.jsonl'),JSON.stringify({at:new Date().toISOString(),method,endpoint:route.split('?')[0],http_status:response.status,detail})+'\n');
    if([401,403,429].includes(response.status))stop=true;
    const error=Error('Upstream HTTP '+response.status);error.httpStatus=response.status;throw error;
  }
  return response.json();
}
async function request(method,route,body){
  if(method==='POST')await identity();
  const next=networkChain.then(()=>rawRequest(method,route,body));networkChain=next.catch(()=>{});return next;
}
async function identity(){const p=(await request('GET','/profiles/current')).profiles?.[0];if(p?.id!==config.expectedUserId){stop=true;throw Error('Authenticated account mismatch');}return p;}
async function check(candidate,state){
  const key=candidate.step_id+':'+candidate.variant+':'+hash(candidate.code);
  let entry=state.entries[key];
  if(entry?.phase==='complete')return entry;
  if(!entry){entry=state.entries[key]={step_id:candidate.step_id,variant:candidate.variant,code_sha256:hash(candidate.code),language:candidate.language,phase:'new',started_at:new Date().toISOString()};await save('official-state.json',state);}
  if(entry.phase==='new'){
    const result=await request('POST','/attempts',{step:candidate.step_id});const a=result.attempts?.[0];
    if(!Number.isSafeInteger(a?.id)||a.step!==candidate.step_id)throw Error('Attempt identity mismatch');
    entry.attempt_id=a.id;entry.phase='attempt_ready';await save('official-state.json',state);
  }
  if(entry.phase==='attempt_ready'){
    const before=await request('GET','/submissions?step='+candidate.step_id+'&page_size=100&order=desc');
    entry.before=(before.submissions||[]).map(x=>x.id);entry.post_started_at=new Date().toISOString();entry.phase='posting';await save('official-state.json',state);
    const result=await request('POST','/submissions',{attempt:entry.attempt_id,reply:JSON.stringify({language:candidate.language,code:candidate.code}),solving_context:'default'});
    if(result.submissions?.[0]?.id){entry.submission_id=result.submissions[0].id;entry.phase='polling';await save('official-state.json',state);}
  }
  if(!entry.submission_id){
    const result=await request('GET','/submissions?step='+candidate.step_id+'&page_size=100&order=desc');
    const matches=(result.submissions||[]).filter(s=>{let reply=s.reply;if(typeof reply==='string'){try{reply=JSON.parse(reply);}catch{return false;}}return s.attempt===entry.attempt_id&&!(entry.before||[]).includes(s.id)&&reply?.language===candidate.language&&hash(reply?.code||'')===entry.code_sha256;});
    if(matches.length!==1)throw Error('Uncertain submission cannot be uniquely reconciled; no repeated POST');
    entry.submission_id=matches[0].id;entry.phase='polling';await save('official-state.json',state);
  }
  for(let poll=0;poll<60;poll++){
    const s=(await request('GET','/submissions/'+entry.submission_id)).submissions?.[0];
    if(s?.id!==entry.submission_id||s.attempt!==entry.attempt_id||s.step!==entry.step_id||s.user_id!==config.expectedUserId)throw Error('Submission ownership or question mismatch');
    let reply=s.reply;if(typeof reply==='string')reply=JSON.parse(reply);
    if(reply?.language!==candidate.language||hash(reply?.code||'')!==entry.code_sha256)throw Error('Submission code readback mismatch');
    if(['correct','wrong','rejected'].includes(s.status)){
      entry.phase='complete';entry.status=s.status;entry.feedback=s.feedback??null;entry.hint=s.hint??null;entry.time=s.time;entry.verified_at=new Date().toISOString();entry.account_matches_config=true;entry.code_readback_verified=true;delete entry.before;
      await save('official-state.json',state);return entry;
    }
    await wait(1200);
  }
  throw Error('Submission remains pending; exact ID retained for later readback');
}
const mode=process.argv[2]||'preflight';
try{
  await identity();
  if(mode==='preflight'){await save('official-preflight.json',{account_matches_config:true,credential_values_exposed:false,observed_at:new Date().toISOString()});console.log('Official account identity verified');}
  else if(mode==='recover-rejected'){
    const state=await read('official-state.json',{schema:1,entries:{}});
    const pending=Object.values(state.entries).filter(e=>e.phase==='posting'&&!e.submission_id);
    const metrics=(await fs.readFile(path.join(root,'official-request-metrics.jsonl'),'utf8')).trim().split('\n').map(JSON.parse);
    const rejected=metrics.filter(r=>r.method==='POST'&&r.endpoint==='/submissions'&&r.http_status===400);
    if(pending.length!==1||rejected.length!==1)throw Error('Historical rejection is not uniquely attributable');
    const entry=pending[0];if(entry.recovery_attempted)throw Error('One recovery already attempted');
    if(Date.parse(rejected[0].at)<Date.parse(entry.post_started_at))throw Error('Rejection predates the pending post');
    const remote=(await request('GET','/submissions?step='+entry.step_id+'&page_size=100&order=desc')).submissions||[];
    if(remote.some(s=>s.attempt===entry.attempt_id))throw Error('Old attempt has a submission; reconcile rather than retry');
    const succeededPosts=metrics.filter(r=>r.method==='POST'&&r.endpoint==='/submissions'&&r.http_status===201).length;
    const knownIds=new Set(Object.values(state.entries).map(e=>e.submission_id).filter(Boolean));
    if(knownIds.size!==succeededPosts)throw Error('Not all successful submission POSTs have retained exact IDs');
    await save('official-recovery.json',{reason:'One HTTP 400 rejection, all HTTP 201 submissions accounted for, old attempt has no submission on readback; retry once with a fresh attempt and per-question serialization',old_entry:entry,rejection:rejected[0],succeeded_posts:succeededPosts,retained_submission_ids:knownIds.size,checked_at:new Date().toISOString()});
    entry.phase='new';entry.recovery_attempted=true;delete entry.attempt_id;delete entry.before;delete entry.post_started_at;
    await save('official-state.json',state);console.log(JSON.stringify({step_id:entry.step_id,variant:entry.variant,recovery:'eligible for one fresh attempt after confirmed rejection'}));
  }else if(mode==='run'){
    const frozen=await read('evaluation-freeze.json',null);
    if(!frozen)throw Error('Independent cohort must be frozen before official submission');
    // Match Python read_text() universal-newline hashing used by the immutable freeze.
    for(const [file,expected] of Object.entries(frozen.file_sha256)){const text=(await fs.readFile(path.join(root,file),'utf8')).replace(/\r\n|\r/g,'\n');if(hash(text)!==expected)throw Error('Frozen evaluation input changed: '+file);}
    const inputName=process.argv[5]||'official-candidates.json';if(!/^[\w-]+\.json$/.test(inputName))throw Error('Invalid candidate input filename');
    const candidates=await read(inputName,[]),state=await read('official-state.json',{schema:1,entries:{}});
    const limit=Number(process.argv[3]||400),workers=Math.min(2,Math.max(1,Number(process.argv[4]||1)));const activeSteps=new Set(),failures=[];
    const tasks=candidates.slice(0,limit).filter(c=>state.entries[c.step_id+':'+c.variant+':'+hash(c.code)]?.phase!=='complete');
    const outcomes=await Promise.allSettled(Array.from({length:workers},async()=>{
      while(!stop&&tasks.length){
        const index=tasks.findIndex(c=>!activeSteps.has(c.step_id));
        if(index<0){await wait(200);continue;}
        const candidate=tasks.splice(index,1)[0];activeSteps.add(candidate.step_id);
        if(!Number.isSafeInteger(candidate.step_id)||!['reference','mutant','alternate'].includes(candidate.variant)||!candidate.code?.trim())throw Error('Invalid frozen candidate');
        try{
          const result=await check(candidate,state);console.log(JSON.stringify({step_id:result.step_id,variant:result.variant,status:result.status,readback_verified:result.code_readback_verified}));
        }catch(error){
          failures.push({step_id:candidate.step_id,variant:candidate.variant,error:error.message});
          console.log(JSON.stringify(failures.at(-1)));await save('official-run-failures.json',failures);
        }finally{activeSteps.delete(candidate.step_id);}
      }
    }));
    const failed=outcomes.find(x=>x.status==='rejected');if(failed)throw failed.reason;
    console.log(JSON.stringify({completed:Object.values(state.entries).filter(x=>x.phase==='complete').length,unresolved:failures,requests:counts}));
  }else throw Error('Unsupported command');
}finally{auth.clear();}
