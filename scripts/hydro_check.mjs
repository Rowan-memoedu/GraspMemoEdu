// Local-only Hydro adapter. Cookies never leave this process or the configured OJ.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash, randomUUID} from 'node:crypto';

const digest = value => createHash('sha256').update(value).digest('hex');
const pending = new Set([0, 20, 21, 22]);
const verdicts = {1:['Solved','Accepted','passed'],2:['Failed','Wrong Answer','wrong'],
  3:['Failed','Time limit exceeded','limit'],4:['Failed','Memory limit exceeded','limit'],
  5:['Failed','Output limit exceeded','limit'],6:['Failed','Runtime Error','runtime'],
  7:['Failed','Compile Error','compile'],11:['Failed','Wrong Answer (Hacked)','wrong']};
export function resultOf(record, url) {
  const [status, verdict, category] = verdicts[record.status] || ['Unchecked','OJ 未给出可评分的最终结果','environment'];
  const messages = [...(record.compilerTexts || []), ...(record.judgeTexts || [])]
    .map(x => typeof x === 'string' ? x : JSON.stringify(x)).join('\n').slice(0, 12000);
  return {status, category, message:`OJ: ${verdict}`, rid:record._id, oj_status:record.status,
    score:record.score, time:record.time, memory:record.memory, test_cases:record.testCases?.length || 0,
    cases:(record.testCases||[]).map((c,i)=>({name:`OJ test ${c.subtaskId ?? 0}.${c.id ?? i+1}`,status:c.status})),
    details:`${url}\nScore: ${record.score ?? '-'}; Time: ${record.time ?? '-'} ms; Memory: ${record.memory ?? '-'} KiB${messages ? '\n'+messages : ''}`};
}
export async function writeJournal(file, data) {
  await fs.mkdir(path.dirname(file), {recursive:true});
  await fs.writeFile(file+'.pending', JSON.stringify(data,null,2));
  await fs.rename(file+'.pending',file);
}
function sameSnapshot(record, journal) {
  const prefix='// '+journal.marker+'\n';
  return typeof record.code==='string' && record.code.startsWith(prefix)
    && digest(record.code.slice(prefix.length).replace(/\r\n/g,'\n'))===journal.code_hash;
}
export class HydroClient {
  constructor(config, auth, transport=fetch) { this.cfg=config;this.auth=auth;this.fetch=transport; }
  url(route) { return this.cfg.origin + '/d/' + encodeURIComponent(this.cfg.domain) + route; }
  async request(route, options={}) {
    const url=this.url(route), cookie=this.auth.headerForUrl(url);
    if (!cookie) throw Error('OJ 登录态不可用，请在 Chrome 登录后重试。');
    return this.fetch(url,{...options,redirect:'manual',signal:AbortSignal.timeout(25000),
      headers:{Cookie:cookie,Accept:'application/json',Origin:this.cfg.origin,Referer:this.url('/p'),...options.headers}});
  }
  async json(route, options) {
    const response=await this.request(route,options);
    if(response.status!==200) throw Error(`OJ HTTP ${response.status}；请检查登录态、权限或提交频率。`);
    return response.json();
  }
  async identity(pid, tid) {
    const query=tid?'?tid='+encodeURIComponent(tid):'';
    const response=await this.request('/p/'+encodeURIComponent(pid)+'/submit'+query,{headers:{Accept:'text/html'}});
    if(response.status!==200) throw Error(`OJ 提交页面 HTTP ${response.status}；尚未提交代码。`);
    const html=await response.text();
    const id=Number(html.match(/\bUserContext\s*=\s*'\{\s*"_id"\s*:\s*(\d+)/)?.[1]);
    if(!id || id!==this.cfg.expected_user_id) throw Error('OJ 登录账号与绑定账号不一致；尚未提交代码。');
    return id;
  }
  async recover(journal, binding, uid) {
    const params=new URLSearchParams({uidOrName:String(uid),pid:binding.pid,full:'true'});
    if(binding.tid) params.set('tid',binding.tid);
    const list=await this.json('/record?'+params);
    const matches=(list.rdocs||[]).filter(r=>r.uid===uid && sameSnapshot(r,journal)
      && r.lang===binding.lang && (r.contest||null)===(binding.tid||null));
    if(matches.length===1) return matches[0]._id;
    throw Error('上次提交结果不确定；未重复 POST。请先在 OJ 核对该次提交。');
  }
  async check(binding, code) {
    if(typeof code!=='string' || !code.trim() || Buffer.byteLength(code)>128*1024) throw Error('代码为空或超过 OJ 大小限制。');
    const uid=await this.identity(binding.pid,binding.tid);
    const problem=await this.json('/p/'+encodeURIComponent(binding.pid)+'/submit'+(binding.tid?'?tid='+encodeURIComponent(binding.tid):''));
    if(!problem.pdoc?.config?.langs?.includes(binding.lang)) throw Error('该题不允许绑定的语言；尚未提交代码。');
    code=code.replace(/\r\n/g,'\n');
    const hash=digest(code), key=digest(JSON.stringify([this.cfg.origin,this.cfg.domain,uid,binding.pid,binding.tid||null,binding.lang,hash]));
    const file=path.join(this.cfg.data_dir,key+'.json'), lock=file+'.lock';
    await fs.mkdir(this.cfg.data_dir,{recursive:true});
    try { await fs.writeFile(lock,String(process.pid),{flag:'wx'}); }
    catch(error) {
      if(error.code!=='EEXIST') throw error;
      const owner=Number(await fs.readFile(lock,'utf8'));
      let alive=true;try {process.kill(owner,0);}catch(e){if(e.code==='ESRCH')alive=false;}
      if(alive) throw Error('相同代码仍在检查，请等该次检查完成。');
      await fs.unlink(lock);await fs.writeFile(lock,String(process.pid),{flag:'wx'});
    }
    try {
      let journal;try {journal=JSON.parse(await fs.readFile(file,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
      // A completed record is reusable for unchanged code; no extra OJ submission is needed.
      if(!journal) {
        journal={code_hash:hash,marker:'GraspMemoEdu-Check '+randomUUID(),started_at:new Date().toISOString(),phase:'posting'};
        await writeJournal(file,journal);
        const body=new URLSearchParams({lang:binding.lang,code:'// '+journal.marker+'\n'+code});
        if(binding.tid) body.set('tid',binding.tid);
        // Exactly one POST. An uncertain response remains recoverable in the journal.
        const response=await this.request('/p/'+encodeURIComponent(binding.pid)+'/submit'+(binding.tid?'?tid='+encodeURIComponent(binding.tid):''),{
          method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body.toString()});
        if(response.status===200) {
          const data=await response.json();journal.rid=data.rid;
        } else if([302,303].includes(response.status)) {
          journal.rid=response.headers.get('location')?.match(/\/record\/([a-f0-9]{24})(?:[?#]|$)/)?.[1];
        }
        if(!/^[a-f0-9]{24}$/.test(journal.rid||'')) {
          journal.phase='uncertain';await writeJournal(file,journal);
          throw Error(`OJ 未确认提交编号（HTTP ${response.status}）；不会自动重复提交。`);
        }
        journal.phase='judging';await writeJournal(file,journal);
      }
      if(!journal.rid) {journal.rid=await this.recover(journal,binding,uid);journal.phase='judging';await writeJournal(file,journal);}
      const deadline=Date.now()+(this.cfg.poll_timeout_ms||120000);
      while(Date.now()<deadline) {
        const data=await this.json('/record/'+journal.rid), record=data.rdoc;
        if(!record || record.uid!==uid || record.pid!==problem.pdoc.docId || record.lang!==binding.lang
          || (record.contest||null)!==(binding.tid||null) || !sameSnapshot(record,journal))
          throw Error('OJ 记录归属或代码快照不匹配，未采用判题结果。');
        if(!pending.has(record.status)) {
          const result=resultOf(record,this.url('/record/'+journal.rid));
          journal.phase='complete';journal.result=result;journal.finished_at=new Date().toISOString();
          await writeJournal(file,journal);return result;
        }
        await new Promise(resolve=>setTimeout(resolve,this.cfg.poll_ms||1500));
      }
      return {status:'Unchecked',category:'environment',message:'OJ 仍在判题；再次 Check 将继续查询同一提交。',rid:journal.rid};
    } finally {await fs.unlink(lock);}
  }
}
export function validateConfig(cfg) {
  const origin=new URL(cfg.origin);
  if(!['http:','https:'].includes(origin.protocol) || origin.origin!==cfg.origin || origin.username || origin.password
    || !cfg.domain || !Number.isInteger(cfg.expected_user_id) || !path.isAbsolute(cfg.data_dir)) throw Error('OJ 本机配置无效。');
}
async function main() {
  let text='';for await(const chunk of process.stdin) text+=chunk;
  const input=JSON.parse(text), cfg=JSON.parse(await fs.readFile(process.argv[2],'utf8'));validateConfig(cfg);
  const binding=cfg.bindings.find(b=>path.resolve(b.project_root)===path.resolve(input.project_root) && b.task_path===input.task_path);
  if(!binding) throw Error('当前题目没有 OJ 绑定。');
  const {LiveCookieSnapshot}=await import(pathToFileURL(cfg.cookie_module));
  const auth=new LiveCookieSnapshot({workflowRoot:cfg.cookie_workflow_root,dataRoot:cfg.cookie_data_root});
  try {return await new HydroClient(cfg,auth).check(binding,input.code);}finally{auth.clear();}
}
if(process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url) {
  main().then(result=>process.stdout.write(JSON.stringify(result)+'\n')).catch(error=>{
    // Never echo an upstream response, command, Cookie or request body.
    const safe=/^(OJ |代码|当前题目|相同代码|上次提交|该题)/.test(error.message)?error.message:'OJ 连接或本机适配器失败；已保留提交状态，请重试查询。';
    process.stdout.write(JSON.stringify({status:'Unchecked',category:'environment',message:safe})+'\n');
  });
}
