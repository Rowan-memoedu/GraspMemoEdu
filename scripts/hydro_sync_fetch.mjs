// Read-only OJ synchronization. Never submit code and never export Cookie/source answers.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {HydroClient,validateConfig} from './hydro_check.mjs';
export function summarize(problems,statuses,records,uid){
 const out={};
 for(const p of problems){
  const best=statuses[String(p.docId)];
  if(best&&best.uid!==uid)throw Error('OJ status identity mismatch');
  const own=records.filter(r=>r.pid===p.docId).sort((a,b)=>b._id.localeCompare(a._id));
  const accepted=own.find(r=>r.status===1),latest=own[0];
  out[p.pid]={submitted:!!(latest||best?.rid),accepted:!!(accepted||best?.status===1),
   submission_count:own.length,latest_status:latest?.status??best?.status??null,
   latest_rid:latest?._id??best?.rid??null,accepted_rid:accepted?._id??(best?.status===1?best.rid:null),
   accepted_at:accepted?new Date(parseInt(accepted._id.slice(0,8),16)*1000).toISOString():best?.status===1?new Date(parseInt(best.rid.slice(0,8),16)*1000).toISOString():null};
 }
 return out;
}
async function main(){
 const [configFile,output]=process.argv.slice(2),cfg=JSON.parse(await fs.readFile(configFile,'utf8'));validateConfig(cfg);
 const {LiveCookieSnapshot}=await import(pathToFileURL(cfg.cookie_module));
 const auth=new LiveCookieSnapshot({workflowRoot:cfg.cookie_workflow_root,dataRoot:cfg.cookie_data_root});
 try{
  const client=new HydroClient(cfg,auth),first=await client.json('/p');
  if(!first.pdocs?.length)throw Error('OJ empty inventory; keep previous data');
  await client.identity(first.pdocs[0].pid||String(first.pdocs[0].docId));
  const docs=[...first.pdocs],statuses={...first.psdict};
  for(let page=2;docs.length<first.pcount;page++){
   if(page>200)throw Error('OJ inventory pagination exceeded');
   const data=await client.json('/p?page='+page);
   if(!data.pdocs?.length||data.pdocs.some(p=>docs.some(v=>v.docId===p.docId)))throw Error('OJ incomplete inventory');
   docs.push(...data.pdocs);Object.assign(statuses,data.psdict);
  }
  if(docs.length!==first.pcount)throw Error('OJ count mismatch');
  const records=[],seen=new Set();
  for(let page=1;page<=200;page++){
   const data=await client.json('/record?uidOrName='+cfg.expected_user_id+'&page='+page);
   if(!data.rdocs?.length)break;
   if(data.rdocs.some(r=>r.uid!==cfg.expected_user_id))throw Error('OJ record identity mismatch');
   if(data.rdocs.some(r=>seen.has(r._id)))throw Error('OJ repeated record page');
   for(const r of data.rdocs){seen.add(r._id);records.push({_id:r._id,pid:r.pid,status:r.status,uid:r.uid});}
   if(page===200)throw Error('OJ record history incomplete');
  }
  const problems=[];await fs.mkdir(path.dirname(output),{recursive:true});
  const assets=path.join(path.dirname(output),'assets');await fs.mkdir(assets,{recursive:true});
  for(const p of docs){
   const data=await client.json('/p/'+encodeURIComponent(p.pid||p.docId));
   const v=data.pdoc;if(v.docId!==p.docId||v.domainId!==cfg.domain)throw Error('OJ problem identity mismatch');
   problems.push({pid:v.pid||String(v.docId),docId:v.docId,title:v.title,content:v.content,config:v.config,
    additional_file:v.additional_file||[]});
   for(const name of new Set([...v.content.matchAll(/file:\/\/([^\s)"\\]+)/g)].map(m=>m[1]))){
    if(!/^[A-Za-z0-9_.-]+\.(png|jpe?g)$/i.test(name))continue;
    const destination=path.join(assets,name);try{await fs.access(destination);continue;}catch{}
    let r=await client.request('/p/'+encodeURIComponent(v.pid||v.docId)+'/file/'+encodeURIComponent(name));
    if(r.headers.get('content-type')?.includes('application/json')){
     const data=await r.json(),url=new URL(data.url,cfg.origin);if(url.origin!==cfg.origin)throw Error('OJ unexpected image host');
     r=await fetch(url,{headers:{Cookie:auth.headerForUrl(url.href)},signal:AbortSignal.timeout(25000)});
    }
    if(r.status!==200)throw Error('OJ image download failed');
    const bytes=Buffer.from(await r.arrayBuffer());
    if(bytes.length>10*1024*1024||!(bytes[0]===137&&bytes[1]===80||bytes[0]===255&&bytes[1]===216))throw Error('OJ invalid image');
    await fs.writeFile(destination,bytes);
   }
  }
  const state={schema:1,origin:cfg.origin,domain:cfg.domain,user_id:cfg.expected_user_id,fetched_at:new Date().toISOString(),
   complete:true,problems,statuses:summarize(problems,statuses,records,cfg.expected_user_id)};
  await fs.writeFile(output+'.pending',JSON.stringify(state));await fs.rename(output+'.pending',output);
  console.log(JSON.stringify({problems:problems.length,accepted:Object.values(state.statuses).filter(s=>s.accepted).length,records:records.length}));
 }finally{auth.clear();}
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url)main().catch(e=>{console.error('OJ synchronization read failed: '+e.message);process.exitCode=3;});
