/** Dedicated answer-cache producer. Never emits website learner events. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {randomUUID, createHash} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';
import {Bridge} from './hyperskill_bridge.mjs';
import {answer} from './hyperskill_protocol.mjs';
import {publicDataset, remapAnswer} from './hyperskill_presentation.mjs';

const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const hash=v=>createHash('sha256').update(v).digest('hex');
export async function read(file, fallback) {
  try {return JSON.parse((await fs.readFile(file,'utf8')).replace(/^\uFEFF/,''));}
  catch(e) {if(e.code==='ENOENT')return fallback;throw e;}
}
export async function save(file,value) {
  await fs.mkdir(path.dirname(file),{recursive:true});
  await fs.writeFile(file+'.tmp',JSON.stringify(value));
  for(let n=0;;n++) {
    try {await fs.rename(file+'.tmp',file);return;}
    catch(e) {if(!['EPERM','EBUSY','EACCES'].includes(e.code)||n>=6)throw e;await sleep(100*(n+1));}
  }
}
class CacheBridge extends Bridge {
  async save(run) {await save(this.file(run.id),run);}
}
export function rawFromReply(type,data,reply) {
  if(type==='number')return String(reply.number);
  if(type==='string')return reply.text;
  let value;
  if(type==='choice') {
    const selected=reply.choices.flatMap((v,i)=>v?[String(i)]:[]);
    value={selected:data.is_multiple_choice?selected:selected[0]};
  } else if(type==='matching')value=reply.ordering.map((v,i)=>({left:String(i),right:String(v)}));
  else if(type==='sorting')value=reply.ordering;
  else if(type==='parsons')value=reply.lines;
  else if(type==='fill-blanks')value=reply.blanks;
  else if(type==='table')value=data.rows.map(r=>data.columns.map(c=>
    reply.choices.find(x=>x.name_row===r)?.columns.find(x=>x.name===c)?.answer));
  else throw Error('Unsupported non-code type');
  const raw=JSON.stringify(value);answer(type,data,raw);return raw;
}
export async function verify(job, id, api, expectedUserId, expected=null) {
  const q=job.question,kind=q.hyperskill.type;
  const s=(await api('GET',`/submissions/${id}`)).submissions?.[0];
  if(s?.id!==id||s.user_id!==expectedUserId||s.step!==q.hyperskill.step_id||s.status!=='correct')
    throw Error('Official correct submission identity mismatch');
  const a=(await api('GET',`/attempts/${s.attempt}`)).attempts?.[0];
  if(a?.id!==s.attempt||a.step!==s.step)throw Error('Official attempt identity mismatch');
  const dataset=publicDataset(kind,a.dataset),reply=typeof s.reply==='string'?JSON.parse(s.reply):s.reply;
  const sourceRaw=rawFromReply(kind,dataset,reply);
  const value=answer(kind,dataset,sourceRaw);
  if(expected&&(expected.attempt_id!==a.id||expected.time!==s.time||
     !isDeepStrictEqual(expected.dataset,dataset)||
     Object.keys(expected.reply).some(k=>!isDeepStrictEqual(expected.reply[k],reply[k]))))
    throw Error('Independent source readback differs from submitted answer');
  const current=q.hyperskill_presentation.dataset;
  const mapped=remapAnswer(kind,dataset,current,sourceRaw);
  return {problem_key:job.problem_key,source_verified:true,verified_at:new Date().toISOString(),
    raw_answer:rawFromReply(kind,current,mapped.reply),receipt:{submission_id:s.id,attempt_id:a.id,
      step_id:s.step,status:s.status,time:s.time,reply:value.reply,dataset,answer_display:value.display,
      feedback:typeof s.feedback==='string'?s.feedback:JSON.stringify(s.feedback||'')}};
}

export function createApi(config,auth,{interval=250,log=async()=>{}}={}) {
  let gate=Promise.resolve(),last=0,stopped=null;
  const slot=()=>{const next=gate.then(async()=>{await sleep(Math.max(0,interval-(Date.now()-last)));last=Date.now();});gate=next;return next;};
  const api=async(method,route,body)=>{
    if(stopped)throw Error(stopped);
    if(!/^\/(profiles\/current|steps\/\d+$|attempts(?:[/?]|$)|submissions(?:[/?]|$))/.test(route))throw Error('Unexpected endpoint');
    if(method==='POST')await identity();
    if(method==='GET'&&route.startsWith('/submissions?'))route+=`&user=${config.expectedUserId}`;
    await slot();if(stopped)throw Error(stopped);
    try {auth.refresh();} catch {stopped='Authenticated snapshot unavailable or stale';throw Error(stopped);}
    const url='https://hyperskill.org/api'+route,cookie=auth.headerForUrl(url);
    if(!cookie){stopped='Authenticated snapshot unavailable';throw Error(stopped);}
    const csrf=cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith('csrftoken='));
    const response=await fetch(url,{method,redirect:'error',signal:AbortSignal.timeout(45000),headers:{
      Cookie:cookie,Accept:'application/json',...(method==='POST'?{'Content-Type':'application/json',
        'X-CSRFToken':decodeURIComponent(csrf?.slice(10)||''),Origin:'https://hyperskill.org',Referer:'https://hyperskill.org/learn'}:{})},
      ...(body?{body:JSON.stringify(body)}:{})});
    await log({at:new Date().toISOString(),method,endpoint:route.split('?')[0],status:response.status});
    if(!response.ok) {
      if([401,403,429].includes(response.status))stopped=`Stopped on upstream HTTP ${response.status}`;
      const e=Error(`Upstream HTTP ${response.status}`);e.status=response.status;throw e;
    }
    return response.json();
  };
  async function identity() {
    if((await api('GET','/profiles/current')).profiles?.[0]?.id!==config.expectedUserId) {
      stopped='Authenticated account mismatch';throw Error(stopped);
    }
  }
  return {api,identity,isStopped:()=>Boolean(stopped)};
}

export async function processJob(job,{directory,api,expectedUserId,polls=30}) {
  const q=job.question,kind=q.hyperskill.type;
  if(kind==='code'||!q.hyperskill_presentation)throw Error('Only prepared non-code problems are allowed');
  const folder=path.join(directory,job.problem_key),file=path.join(folder,'state.json');
  const state=await read(file,{generation:0,attempts:[]});
  if(state.verified)return {status:'correct',reference:state.verified};
  const raw=job.raw_answer;
  if(typeof raw!=='string')throw Error('Missing candidate');
  answer(kind,q.hyperskill_presentation.dataset,raw);
  const candidateHash=hash(raw);
  for(let rejected=0;rejected<3;rejected++) {
    const ticket=hash(job.problem_key),binding={...q.hyperskill,provider:'hyperskill',kind:'automated',
      task_key:q.id,version:job.problem_key,round_id:job.problem_key+'-'+state.generation,next_sequence:1,
      mode:'learn',presentation:q.hyperskill_presentation};
    const bridge=new CacheBridge({directory:path.join(folder,'rounds'),expectedUserId,hyperskill:api,
      cloud:async(route,t,body)=>{
        if(t!==ticket)throw Error('Binding mismatch');
        if(route==='claim')return binding;
        if(route!=='event'||!['opened','check'].includes(body.kind))throw Error('Unexpected local sink event');
        return {closed:body.event?.receipt?.status==='correct'};
      }});
    let current=state.session?await bridge.status(await bridge.load(state.session)):await bridge.open(ticket);
    state.session=current.session;await save(file,state);
    const remember=async receipt=>{
      if(!receipt||state.attempts.some(r=>r.submission_id===receipt.submission_id))return;
      if(receipt.request_id!==state.current_request?.request_id)throw Error('Recovered receipt request mismatch');
      state.attempts.push({submission_id:receipt.submission_id,status:receipt.status,feedback:receipt.feedback,
        candidate_hash:state.current_request.candidate_hash,raw_answer:state.current_request.raw});
      await save(file,state);
    };
    await remember(current.last);
    if(!current.pending&&current.last?.status!=='correct') {
      if(current.last?.status==='wrong') {
        if(state.attempts.find(r=>r.submission_id===current.last.submission_id)?.candidate_hash===candidateHash)
          return {status:'wrong',feedback:current.last.feedback,attempts:state.attempts};
        current=await bridge.retry(state.session);
      }
      // Some steps sample distractors from a pool as well as shuffling them.
      // Obtain the exact installed variant; never transplant a different key.
      let sourceChecked=false;
      while(true) {
        const run=await bridge.load(state.session);
        try {remapAnswer(kind,q.hyperskill_presentation.dataset,run.attempt.dataset,raw);break;}
        catch(error) {
          if(!sourceChecked) {
            const source=(await api('GET',`/steps/${binding.step_id}`)).steps?.[0];
            if(source?.id!==binding.step_id||source.block?.name!==kind||
              source.block.text.replace(/\r\n/g,'\n').trim()!==q.question.replace(/\r\n/g,'\n').trim())
              throw Error('Official question stem changed; installed question was preserved');
            sourceChecked=true;
          }
          state.variant_attempts??=[];
          if(state.variant_attempts.length>=60)throw Error('No matching installed variant after 60 unused attempts');
          if(run.pending||run.last)throw Error('Cannot replace an attempt with a submission');
          state.variant_attempts.push({attempt_id:run.attempt.id,dataset:publicDataset(kind,run.attempt.dataset)});
          await save(file,state);
          run.attempt=null;await bridge.save(run);current=await bridge.status(run);
        }
      }
      state.current_request={request_id:randomUUID(),candidate_hash:candidateHash,raw};await save(file,state);
      current=await bridge.submit(state.session,current.attempt_id,raw,state.current_request.request_id);
    }
    let terminalRejection=null;
    for(let poll=0;current.pending&&poll<polls;poll++) {
      const run=await bridge.load(state.session);
      if(run.pending?.submission_id) {
        const s=(await api('GET',`/submissions/${run.pending.submission_id}`)).submissions?.[0];
        const reply=typeof s?.reply==='string'?JSON.parse(s.reply):s?.reply;
        if(s?.id!==run.pending.submission_id||s.attempt!==run.pending.attempt_id||
          s.user_id!==expectedUserId||s.step!==binding.step_id||
          Object.keys(run.pending.value.reply).some(k=>!isDeepStrictEqual(reply?.[k],run.pending.value.reply[k])))
          throw Error('Pending receipt identity mismatch');
        if(s.status==='rejected') {terminalRejection=s;break;}
      }
      await sleep(1500);current=await bridge.status(await bridge.load(state.session));
    }
    if(terminalRejection) {
      state.attempts.push({submission_id:terminalRejection.id,status:'rejected',feedback:terminalRejection.feedback,
        retained_session:state.session,verified_at:new Date().toISOString()});
      state.session=null;state.generation++;await save(file,state);continue;
    }
    if(current.pending)return {status:'pending',reason:'Original uncertain submission retained; no second POST'};
    const receipt=current.last;
    if(!receipt)throw Error('Missing terminal receipt');
    await remember(receipt);
    if(receipt.status==='wrong')return {status:'wrong',feedback:receipt.feedback,attempts:state.attempts};
    state.verified=await verify(job,receipt.submission_id,api,expectedUserId,receipt);
    await save(file,state);return {status:'correct',reference:state.verified};
  }
  return {status:'rejected',reason:'Three independently confirmed source rejections; checkpoint retained'};
}

async function main() {
  const input=await read(process.argv[2]),config=await read(input.config);
  const {LiveCookieSnapshot}=await import(pathToFileURL(config.cookieModule));
  const auth=new LiveCookieSnapshot({workflowRoot:config.cookieWorkflowRoot,dataRoot:config.cookieDataRoot});
  await fs.mkdir(input.output,{recursive:true});
  const client=createApi(config,auth,{log:row=>fs.appendFile(path.join(input.output,'requests.jsonl'),JSON.stringify(row)+'\n')});
  await client.identity();
  if(input.action==='history') {
    const wanted=new Set(input.jobs.map(j=>j.question.hyperskill.step_id)),found={};
    for(let page=1;page<=100;page++) {
      const data=await client.api('GET',`/submissions?status=correct&page_size=100&order=desc&page=${page}`);
      for(const s of data.submissions||[])if(s.user_id===config.expectedUserId&&s.status==='correct'&&wanted.has(s.step))
        found[s.step]??=s.id;
      if(!data.meta?.has_next)break;
    }
    let reused=0;
    for(const job of input.jobs) {
      const id=found[job.question.hyperskill.step_id];if(!id)continue;
      try {
        const reference=await verify(job,id,client.api,config.expectedUserId);
        await save(path.join(input.output,job.problem_key+'.json'),{status:'correct',reference});reused++;
      } catch(e) {if(client.isStopped())throw e;}
    }
    console.log(JSON.stringify({historical_correct_reused:reused}));return;
  }
  const queue=[...input.jobs];let done=0;
  await Promise.all(Array.from({length:Math.min(8,queue.length)},async()=>{
    while(queue.length) {
      if(client.isStopped())return;
      const job=queue.shift();let result;
      try {result=await processJob(job,{directory:input.state,api:client.api,expectedUserId:config.expectedUserId});}
      catch(e) {result={status:'error',reason:e.message};}
      await save(path.join(input.output,job.problem_key+'.json'),result);
      console.log(JSON.stringify({done:++done,total:input.jobs.length,step:job.question.hyperskill.step_id,status:result.status}));
    }
  }));
  if(client.isStopped())throw Error('Upstream stopped; remaining work preserved');
}
if(process.argv[1]&&import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href)
  main().catch(e=>{console.error(e.message);process.exitCode=1;});
