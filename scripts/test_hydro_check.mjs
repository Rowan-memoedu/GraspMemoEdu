import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {HydroClient, resultOf, validateConfig} from './hydro_check.mjs';
const root=process.argv[2];assert(root && /^[dD]:[\\/]/.test(root));
await fs.mkdir(root,{recursive:true});
const uid=901, pid='P234', docId=234, rid='abcdef012345abcdef012345';
const auth={headerForUrl:()=> 'synthetic=fixture'};
let posts=0, gets=0, postedCode='', disconnected=false, contest=null;
function transport(url, options) {
  const u=new URL(url);
  if(options.method==='POST') {
    posts++;postedCode=new URLSearchParams(options.body).get('code');
    if(contest)assert.equal(u.searchParams.get('tid'),contest);
    if(disconnected) throw Error('synthetic connection lost after POST');
    return Promise.resolve(new Response(JSON.stringify({rid}),{status:200}));
  }
  if(u.pathname.endsWith('/submit') && options.headers.Accept==='text/html')
    return Promise.resolve(new Response(`UserContext = '{"_id":${uid}}';`));
  if(u.pathname.endsWith('/submit')) return Promise.resolve(Response.json({pdoc:{docId,config:{langs:['java']}}}));
  if(u.pathname.endsWith('/record')) return Promise.resolve(Response.json({rdocs:[{_id:rid,uid,pid:docId,lang:'java',code:postedCode,contest}]}));
  gets++;
  return Promise.resolve(Response.json({rdoc:{_id:rid,uid,pid:docId,lang:'java',code:postedCode,contest,status:gets===1?20:1,score:100,testCases:[{id:1,status:1},{id:2,status:1}]}}));
}
const base={origin:'http://127.0.0.1:12345',domain:'fixture',expected_user_id:uid,poll_ms:1,poll_timeout_ms:1000};
const binding={pid,lang:'java'};
const cfg={...base,data_dir:root+'/normal'};validateConfig(cfg);
const client=new HydroClient(cfg,auth,transport);
const accepted=await client.check(binding,'public class Main {}');assert.equal(accepted.status,'Solved');assert.equal(accepted.cases.length,2);
assert.equal(posts,1);assert.equal(gets,2);
assert.equal((await client.check(binding,'public class Main {}')).status,'Solved');assert.equal(posts,1);
disconnected=true;
const uncertain=new HydroClient({...base,data_dir:root+'/uncertain'},auth,transport);
await assert.rejects(()=>uncertain.check(binding,'class Second {}'));
assert.equal(posts,2);
assert.equal((await uncertain.check(binding,'class Second {}')).status,'Solved');assert.equal(posts,2);
const badIdentity=new HydroClient({...base,expected_user_id:uid+1,data_dir:root+'/wrong-account'},auth,transport);
await assert.rejects(()=>badIdentity.check(binding,'class Third {}'),/账号/);assert.equal(posts,2);
const ambiguous=new HydroClient({...base,data_dir:root+'/ambiguous'},auth,async(url,opt)=>{
  if(new URL(url).pathname.endsWith('/record')) return Response.json({rdocs:[]});
  return transport(url,opt);
});
await assert.rejects(()=>ambiguous.check(binding,'class Fourth {}'));
assert.equal(posts,3);
await assert.rejects(()=>ambiguous.check(binding,'class Fourth {}'),/未重复 POST/);assert.equal(posts,3);
assert.equal(resultOf({_id:rid,status:7,compilerTexts:['Compile Error']},'fixture').category,'compile');
assert.equal(resultOf({_id:rid,status:2},'fixture').status,'Failed');
assert.equal(resultOf({_id:rid,status:8},'fixture').status,'Unchecked');
assert.equal(resultOf({_id:rid,status:20},'fixture').status,'Unchecked');
disconnected=false;contest='abcdef012345abcdef012346';
const scoped=new HydroClient({...base,data_dir:root+'/contest'},auth,transport);
assert.equal((await scoped.check({...binding,tid:contest},'class ContestEntry {}')).status,'Solved');
assert.equal(posts,4);
console.log('PASS remote verdicts, pending polling, duplicate Check, uncertain POST recovery, ambiguous recovery and identity guard');
