/**
 * Loopback-only relay. Cookies stay in the existing local cookie snapshot.
 * An upstream POST is recorded before sending and is never blindly replayed.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import {pathToFileURL} from 'node:url';
import {randomBytes,randomUUID} from 'node:crypto';
import {answer,presentation,hash,supportedTypes} from './hyperskill_protocol.mjs';
import {embedSourceImages} from './hyperskill_media.mjs';
import {renderSourceHtml} from './render_mathacademy.mjs';
import {publicDataset,remapAnswer} from './hyperskill_presentation.mjs';
import {officialIDE} from './hyperskill_ide.mjs';

export class Bridge {
  constructor({directory,hyperskill,cloud,openIDE,expectedUserId}) {
    Object.assign(this,{directory,hyperskill,cloud,openIDE,expectedUserId});
    this.queue=Promise.resolve();
  }
  serial(fn) { const next=this.queue.then(fn); this.queue=next.catch(()=>{}); return next; }
  file(id) {
    if (!/^[a-zA-Z0-9_-]{32,80}$/.test(id)) throw Error('本机连接已失效，请重新连接。');
    return path.join(this.directory,id+'.json');
  }
  async save(run) {
    await fs.mkdir(this.directory,{recursive:true});
    const file=this.file(run.id),temp=file+'.tmp';
    await fs.writeFile(temp,JSON.stringify(run),{mode:0o600});
    await fs.rename(temp,file);
  }
  async load(id) { return JSON.parse(await fs.readFile(this.file(id),'utf8')); }
  async open(ticket) {
    if (!/^[A-Za-z0-9_-]{40,100}$/.test(ticket)) throw Error('连接授权无效。');
    const binding=await this.cloud('claim',ticket,{});
    if (binding.provider!=='hyperskill' || !Number.isSafeInteger(binding.step_id) ||
        !supportedTypes.includes(binding.type)) throw Error('此连接未绑定受支持的 Hyperskill 题目。');
    await this.cloud('event',ticket,{kind:'opened'});
    await fs.mkdir(this.directory,{recursive:true});
    for (const name of await fs.readdir(this.directory)) {
      if (!/^[A-Za-z0-9_-]{32,80}\.json$/.test(name)) continue;
      const old=JSON.parse(await fs.readFile(path.join(this.directory,name),'utf8'));
      if (old.binding.round_id===binding.round_id) {
        old.ticket=ticket;
        old.prepared ||= binding.presentation||null;
        old.codeOptions ||= binding.presentation?.code_options;
        await this.save(old); return this.status(old);
      }
    }
    const run={id:randomBytes(32).toString('base64url'),ticket,binding,attempt:null,
      prepared:binding.presentation||null,codeOptions:binding.presentation?.code_options,
      sequence:binding.next_sequence,pending:null,last:null};
    // Persist the round before creating an upstream attempt.
    await this.save(run);
    return this.status(run);
  }
  async status(run) {
    if (run.pending) await this.reconcile(run);
    if(run.ide?.active)await this.syncIDE(run);
    if (run.binding.type!=='code' && !run.attempt && !run.pending && run.last?.status!=='correct') {
      await this.cloud('claim',run.ticket,{}); // Fresh authorization before any upstream write.
      const result=await this.hyperskill('POST','/attempts',{step:run.binding.step_id});
      const attempt=result.attempts?.[0];
      if (!attempt || attempt.step!==run.binding.step_id || !Number.isSafeInteger(attempt.id)) throw Error('Hyperskill 未返回当前题目的练习实例。');
      run.attempt=attempt; await this.save(run);
    }
    const pending=run.pending;
    if(run.binding.type==='code'&&!run.codeOptions) {
      const source=(await this.hyperskill('GET',`/steps/${run.binding.step_id}`)).steps?.[0];
      if(source?.id!==run.binding.step_id||source.block?.name!=='code')throw Error('代码题身份不一致。');
      run.codeOptions=source.block.options;await this.save(run);
    }
    const interaction=run.prepared?structuredClone(run.prepared.interaction):
      run.attempt||run.binding.type==='code'?presentation(run.binding.type,run.attempt?.dataset||{},source=>({html:source}),run.codeOptions):null;
    const items=interaction?[...(interaction.options||[]),...(interaction.left||[]),...(interaction.right||[]),
      ...(interaction.items||[]),...(interaction.rows||[]),...(interaction.columns||[])]:[];
    for(const key of ['template','description'])
      if(interaction?.[key]!==undefined)items.push({html:interaction[key],key});
    if(!run.prepared) {
      const media=await embedSourceImages(items.map(item=>item.html),{directory:path.join(this.directory,'media')});
      items.forEach((item,index)=>item.html=renderSourceHtml(media.html[index]).html);
    }
    for(const item of items)if(item.key)interaction[item.key]=item.html;
    return {session:run.id,step_id:run.binding.step_id,attempt_id:run.attempt?.id,
      interaction,ide:run.ide?{watching:!!run.ide.active,opened:!!run.ide.opened,submitting:!!run.ide.submitting}:null,
      last:run.last,pending:Boolean(pending),
      message:pending?'提交正在确认中。请点击刷新结果，不要重复提交。':null};
  }
  async submit(id,attemptId,raw,requestId) {
    const run=await this.load(id);
    if(run.binding.type==='code')throw Error('代码题请在 Hyperskill 官方 IDEA 插件中点击 Check，网站会自动同步结果。');
    if (!/^[a-zA-Z0-9-]{16,64}$/.test(requestId)) throw Error('请求标识无效。');
    if (run.last?.request_id===requestId || run.pending) {
      if (run.pending && run.pending.request_id!==requestId) throw Error('上一笔提交仍在确认，请先刷新结果。');
      if (run.pending && (attemptId!==run.pending.attempt_id || hash(raw)!==run.pending.raw_hash))
        throw Error('正在确认原答案；请先刷新结果，再开始新的尝试。');
      return this.status(run);
    }
    if (!run.attempt || run.attempt.id!==attemptId || run.last?.status==='correct') throw Error('练习实例已变化，请重新加载。');
    const value=run.prepared?remapAnswer(run.binding.type,run.prepared.dataset,run.attempt.dataset,raw,run.codeOptions):
      answer(run.binding.type,run.attempt.dataset,raw,run.codeOptions);
    await this.cloud('claim',run.ticket,{});
    const before=await this.hyperskill('GET',`/submissions?step=${run.binding.step_id}&page_size=100&order=desc`);
    run.pending={request_id:requestId,attempt_id:attemptId,value,raw_hash:hash(raw),started_at:new Date().toISOString(),
      before:(before.submissions||[]).map(s=>s.id),submission_id:null,posted:false,event:null};
    await this.save(run);
    // Persist uncertainty before the network call. A crash cannot turn into an automatic second POST.
    run.pending.posted=true; await this.save(run);
    try {
      const result=await this.hyperskill('POST','/submissions',{
        attempt:attemptId,reply:JSON.stringify(value.reply),solving_context:run.binding.mode==='review'?'repetition':'default'});
      const submission=result.submissions?.[0];
      if (submission?.id) { run.pending.submission_id=submission.id; await this.save(run); }
    } catch (error) {
      // Explicit client rejection is safe to edit; network and 5xx results remain uncertain.
      if (error.status && error.status>=400 && error.status<500 && ![408,429].includes(error.status)) {
        run.pending=null; await this.save(run); throw error;
      }
    }
    return this.status(run);
  }
  async reconcile(run) {
    const pending=run.pending;
    if (!pending.submission_id) {
      const data=await this.hyperskill('GET',`/submissions?step=${run.binding.step_id}&page_size=100&order=desc`);
      const rows=(data.submissions||[]).filter(s=>{
        let reply=s.reply; if(typeof reply==='string') {try{reply=JSON.parse(reply);}catch{return false;}}
        return s.attempt===pending.attempt_id && !pending.before.includes(s.id) &&
          hash(reply)===hash(pending.value.reply) && Date.parse(s.time)>=Date.parse(pending.started_at)-5000;
      });
      if (rows.length!==1) return;
      pending.submission_id=rows[0].id; await this.save(run);
    }
    if (!pending.event) {
      const data=await this.hyperskill('GET',`/submissions/${pending.submission_id}`);
      const row=data.submissions?.[0];
      if (!row || row.id!==pending.submission_id || row.attempt!==pending.attempt_id) throw Error('判题回执与当前提交不匹配。');
      if (!['correct','wrong'].includes(row.status)) return;
      const receipt={submission_id:row.id,attempt_id:row.attempt,step_id:run.binding.step_id,
        status:row.status,time:row.time,reply:pending.value.reply,answer_display:pending.value.display,
        ...(run.binding.type!=='code'?{dataset:publicDataset(run.binding.type,run.attempt.dataset)}:{}),
        feedback:typeof row.feedback==='string'?row.feedback:JSON.stringify(row.feedback||'')};
      pending.event={id:randomUUID(),sequence:run.sequence,category:row.status==='correct'?'passed':'wrong',
        code_hash:hash(pending.value.semantic),occurred_at:row.time,receipt};
      await this.save(run);
    }
    await this.cloud('event',run.ticket,{kind:'check',event:pending.event});
    run.last={request_id:pending.request_id,...pending.event.receipt};
    run.sequence++; run.pending=null; await this.save(run);
  }
  async retry(id) {
    const run=await this.load(id);
    if (run.pending || run.last?.status!=='wrong') throw Error('当前无法开始下一次尝试。');
    if(run.binding.type==='code')return this.status(run);
    run.attempt=null; run.last=null; await this.save(run); return this.status(run);
  }
  async flush() {
    await fs.mkdir(this.directory,{recursive:true});
    for(const name of await fs.readdir(this.directory)) {
      if(!/^[A-Za-z0-9_-]{32,80}\.json$/.test(name))continue;
      const run=await this.load(name.slice(0,-5));
      if(run.pending) {try{await this.reconcile(run);}catch{/* Keep the original receipt for a later retry. */}}
      if(run.ide?.active||run.ide?.event) {try{await this.syncIDE(run);}catch{/* Retry the same source receipt, never resubmit code. */}}
    }
  }
  async editor(id) {
    const run=await this.load(id);
    await this.cloud('claim',run.ticket,{});
    if(run.binding.type!=='code'||run.pending||run.last?.status==='correct')
      throw Error('当前题目不能在 IDEA 中开始新的检查。');
    if(!this.openIDE||!this.expectedUserId)throw Error('本机尚未配置 Hyperskill 官方 IDEA 入口。');
    await this.status(run);
    if(!Object.keys(run.codeOptions?.code_templates||{}).some(k=>/^java\d*$/.test(k)))
      throw Error('当前 IDEA 入口仅支持 Java。');
    if(!run.ide?.active) {
      // One active round owns a source step on this device. Reopening another round
      // cannot make one official Check score two website attempts.
      for(const name of await fs.readdir(this.directory)) {
        if(!/^[A-Za-z0-9_-]{32,80}\.json$/.test(name)||name===run.id+'.json')continue;
        const old=await this.load(name.slice(0,-5));
        if(old.ide?.active&&old.binding.step_id===run.binding.step_id) {
          old.ide.active=false;await this.save(old);
        }
      }
      const before=await this.hyperskill('GET',`/submissions?step=${run.binding.step_id}&page_size=100&order=desc`);
      run.ide={active:true,opened:false,user:this.expectedUserId,started_at:new Date().toISOString(),
        before:(before.submissions||[]).map(s=>s.id),seen:[],event:null,polled:0};
      await this.save(run); // Watch before handing control to the official plugin.
    }
    await this.openIDE({step:run.binding.step_id,user:this.expectedUserId,language:'java'});
    run.ide.opened=true;await this.save(run);
    return this.status(run);
  }
  async syncIDE(run) {
    const watch=run.ide;
    if(!watch||!watch.active&&!watch.event||Date.now()-(watch.polled||0)<2000)return;
    // Persist each event before sending. Lost acknowledgements replay that same ID.
    const deliver=async()=>{
      const ack=await this.cloud('event',run.ticket,{kind:'check',event:watch.event});
      run.last={request_id:'ide-'+watch.event.receipt.submission_id,...watch.event.receipt};
      watch.seen.push(watch.event.receipt.submission_id);run.sequence++;
      if(run.last.status==='correct'||ack.closed)watch.active=false;
      watch.event=null;await this.save(run);
    };
    if(watch.event) {await deliver();if(!watch.active)return;}
    if(Date.now()-Date.parse(watch.started_at)>24*60*60*1000) {
      watch.active=false;await this.save(run);return;
    }
    let rows=[];
    for(let page=1;page<=100;page++) {
      const data=await this.hyperskill('GET',`/submissions?step=${run.binding.step_id}&page_size=100&order=desc&page=${page}`);
      const batch=data.submissions||[];
      rows.push(...batch.filter(s=>s.step===run.binding.step_id&&s.user_id===watch.user&&
        !watch.before.includes(s.id)&&!watch.seen.includes(s.id)&&
        Date.parse(s.time)>=Date.parse(watch.started_at)));
      if(!data.meta?.has_next||batch.some(s=>watch.before.includes(s.id)||Date.parse(s.time)<Date.parse(watch.started_at)))break;
      if(page===100)throw Error('待同步记录过多，本轮尚未继续计分。');
    }
    rows.sort((a,b)=>Date.parse(a.time)-Date.parse(b.time)||a.id-b.id);
    watch.polled=Date.now();watch.submitting=false;
    for(const row of rows) {
      if(!['correct','wrong'].includes(row.status)) {watch.submitting=true;break;}
      let reply=row.reply;
      if(typeof reply==='string') {try{reply=JSON.parse(reply);}catch{continue;}}
      if(!reply||typeof reply.code!=='string'||!reply.code.trim()||typeof reply.language!=='string')continue;
      await this.cloud('claim',run.ticket,{});
      const receipt={submission_id:row.id,attempt_id:row.attempt,step_id:run.binding.step_id,
        status:row.status,time:row.time,reply,answer_display:reply.code,
        feedback:typeof row.feedback==='string'?row.feedback:JSON.stringify(row.feedback||'')};
      watch.event={id:randomUUID(),sequence:run.sequence,category:row.status==='correct'?'passed':'wrong',
        code_hash:hash({language:reply.language,code:reply.code}),occurred_at:row.time,receipt};
      await this.save(run);await deliver();
      if(!watch.active)break;
    }
    await this.save(run);
  }
}

async function main() {
  const config=JSON.parse(await fs.readFile(process.argv[2],'utf8'));
  const backend=new URL(config.backend);
  if (backend.protocol!=='https:' || backend.pathname!=='/') throw Error('HTTPS backend origin required');
  const {LiveCookieSnapshot}=await import(pathToFileURL(config.cookieModule));
  const auth=new LiveCookieSnapshot({workflowRoot:config.cookieWorkflowRoot,dataRoot:config.cookieDataRoot});
  async function json(url,options={}) {
    const response=await fetch(url,{...options,redirect:'error',signal:AbortSignal.timeout(45000)});
    if (!response.ok) { const e=Error(`接口返回 ${response.status}，尚未记录新的评分。`);e.status=response.status;throw e; }
    return response.json();
  }
  const hyperskill=async(method,route,body)=>{
    auth.refresh();
    if(method==='POST') {
      const current=await hyperskill('GET','/profiles/current');
      if(current.profiles?.[0]?.id!==config.expectedUserId)throw Error('Hyperskill 登录账号已变化，请重新连接。');
    }
    if(method==='GET'&&route.startsWith('/submissions?'))route+=`&user=${config.expectedUserId}`;
    const url='https://hyperskill.org/api'+route, cookie=auth.headerForUrl(url);
    if (!cookie) throw Error('本机 Hyperskill 登录态不可用。');
    const csrf=cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('csrftoken='));
    return json(url,{method,headers:{Cookie:cookie,Accept:'application/json',
      ...(method==='POST'?{'Content-Type':'application/json','X-CSRFToken':decodeURIComponent(csrf?.slice(10)||''),
        Origin:'https://hyperskill.org',Referer:'https://hyperskill.org/learn'}:{})},
      ...(body?{body:JSON.stringify(body)}:{})});
  };
  const profile=await hyperskill('GET','/profiles/current');
  if (profile.profiles?.[0]?.id!==config.expectedUserId) throw Error('Hyperskill 登录账号不一致。');
  let warmup=null,warmedAt=0;
  const warmConnection=()=>{
    if(!warmup||Date.now()-warmedAt>30000) {
      warmedAt=Date.now();
      warmup=hyperskill('GET','/profiles/current').then(value=>{
        if(value.profiles?.[0]?.id!==config.expectedUserId)throw Error('Hyperskill 登录账号已变化，请重新连接。');
        return {ready:true};
      }).catch(error=>{warmup=null;throw error;});
    }
    return warmup;
  };
  const bridge=new Bridge({directory:config.directory,hyperskill,expectedUserId:config.expectedUserId,
    openIDE:officialIDE({executable:config.ideaExecutable||'D:/Applications/IntelliJIDEA/bin/idea64.exe',
      properties:config.ideaProperties}),
    cloud:(route,ticket,body)=>json(new URL('/api/academy/'+route,backend),{
      method:'POST',headers:{Authorization:'Bearer '+ticket,'Content-Type':'application/json',Origin:backend.origin},
      body:JSON.stringify(body)})});
  const server=http.createServer(async(req,res)=>{
    const origin=req.headers.origin;
    const allowed=config.origins.includes(origin);
    if (req.headers.host!==`127.0.0.1:${config.port}` || !allowed) {res.writeHead(403);res.end();return;}
    res.setHeader('Access-Control-Allow-Origin',origin);
    res.setHeader('Vary','Origin');
    res.setHeader('Cache-Control','no-store');
    res.setHeader('Access-Control-Allow-Private-Network','true');
    if(req.method==='OPTIONS') {
      res.setHeader('Access-Control-Allow-Methods','POST');
      res.setHeader('Access-Control-Allow-Headers','Content-Type,X-Grasp-Local');
      res.writeHead(204);res.end();return;
    }
    res.setHeader('Content-Type','application/json');
    try {
      if(req.method!=='POST'||req.headers['x-grasp-local']!=='1'||
        !req.headers['content-type']?.startsWith('application/json')) throw Error('请求格式不受支持。');
      let raw='';for await(const part of req){raw+=part;if(raw.length>40000)throw Error('请求过长。');}
      const body=JSON.parse(raw),url=req.url;
      if(url==='/health') {res.end(JSON.stringify({ready:true,provider:'hyperskill'}));return;}
      if(url==='/warmup') {res.end(JSON.stringify(await warmConnection()));return;}
      const result=await bridge.serial(async()=>{
        if(url==='/open')return bridge.open(body.ticket);
        if(url==='/status')return bridge.status(await bridge.load(body.session));
        if(url==='/submit')return bridge.submit(body.session,body.attempt_id,body.answer,body.request_id);
        if(url==='/retry')return bridge.retry(body.session);
        if(url==='/ide')return bridge.editor(body.session);
        throw Error('未知操作。');
      });
      res.end(JSON.stringify(result));
    } catch(error) {
      // No credential, raw upstream body or local path in logs or browser errors.
      res.statusCode=400;res.end(JSON.stringify({error:error.message?.includes('。')?error.message:'本机连接暂不可用，请重试。'}));
    }
  });
  const retryTimer=setInterval(()=>{void bridge.serial(()=>bridge.flush()).catch(()=>{});},30000);
  retryTimer.unref();
  server.listen(config.port,'127.0.0.1',()=>console.log('Hyperskill bridge ready'));
}
if(process.argv[1] && import.meta.url===pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(()=>{console.error('Hyperskill bridge startup failed');process.exitCode=1;});
}
