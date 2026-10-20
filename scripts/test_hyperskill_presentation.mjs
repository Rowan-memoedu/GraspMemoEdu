import assert from 'node:assert/strict';
import {preparePresentation,remapAnswer} from './hyperskill_presentation.mjs';
import {Bridge} from './hyperskill_bridge.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
const prepared=await preparePresentation('choice',{options:['<code>A</code>','B'],is_multiple_choice:false});
const live={options:['B','<code>A</code>'],is_multiple_choice:false};
assert.deepEqual(remapAnswer('choice',prepared.dataset,live,'{"selected":"0"}').reply,{choices:[false,true]});
assert.throws(()=>remapAnswer('choice',prepared.dataset,{options:['B','C']},'{"selected":"0"}'),/内容已更新/);
assert.deepEqual(remapAnswer('matching',{pairs:[{first:'a',second:'1'},{first:'b',second:'2'}]},
  {pairs:[{first:'b',second:'1'},{first:'a',second:'2'}]},'[{"left":"0","right":"1"},{"left":"1","right":"0"}]').reply,{ordering:[0,1]});
assert.deepEqual(remapAnswer('sorting',{options:['a','b']},{options:['b','a']},'[0,1]').reply,{ordering:[1,0]});
assert.deepEqual(remapAnswer('parsons',{lines:['a','b']},{lines:['b','a']},
  '[{"line_number":0,"level":0},{"line_number":1,"level":1}]').reply,{lines:[{line_number:1,level:0},{line_number:0,level:1}]});
const directory=await fs.mkdtemp(path.join(os.tmpdir(),'hyperskill-prepared-'));
try {
  let posts=0,delivered;
  const bridge=new Bridge({directory,
    cloud:async(route,_,body)=>{
      if(route==='claim')return {provider:'hyperskill',step_id:1,type:'choice',round_id:'new',next_sequence:1,presentation:prepared};
      if(body.kind==='check')delivered=body.event;
      return {};
    },
    hyperskill:async(method,route,body)=>{
      if(route==='/attempts')return {attempts:[{id:10,step:1,dataset:live}]};
      if(method==='POST') {
        posts++;assert.deepEqual(JSON.parse(body.reply),{choices:[false,true]});return {submissions:[{id:20}]};
      }
      if(route.startsWith('/submissions?'))return {submissions:[]};
      return {submissions:[{id:20,attempt:10,status:'correct',time:new Date().toISOString()}]};
    }});
  const opened=await bridge.open('x'.repeat(43));
  assert.equal(posts,0);assert.deepEqual(opened.interaction,prepared.interaction);
  const legacy=await bridge.load(opened.session);delete legacy.prepared;await bridge.save(legacy);
  const upgraded=await bridge.open('z'.repeat(43));
  assert.equal(upgraded.session,opened.session);assert.deepEqual(upgraded.interaction,prepared.interaction);
  await bridge.submit(opened.session,opened.attempt_id,'{"selected":"0"}','request-000000001');
  assert.equal(posts,1);assert.deepEqual(delivered.receipt.dataset,live);
  console.log('PASS prepared display, shuffled reply identities, content-change rejection, verified dataset receipt');
} finally {await fs.rm(directory,{recursive:true,force:true});}
