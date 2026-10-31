import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {rawFromReply,verify,processJob,read,save} from '../scripts/hyperskill_cache_submit.mjs';
import {answer} from '../scripts/hyperskill_protocol.mjs';

const dataset={options:['A','B'],is_multiple_choice:false};
const job={problem_key:'a'.repeat(64),question:{id:'q1',hyperskill:{step_id:1,type:'choice'},
  hyperskill_presentation:{dataset,interaction:{type:'choice',options:[]}}},raw_answer:'{"selected":"0"}'};

test('all eight official reply formats round-trip into website input',()=>{
  const fixtures=[['choice',dataset,'{"selected":"0"}'],['string',{},'Case Sensitive'],['number',{},'1.00'],
    ['matching',{pairs:[{first:'A',second:'1'},{first:'B',second:'2'}]},'[{"left":"0","right":"1"},{"left":"1","right":"0"}]'],
    ['sorting',{options:['A','B']},'[1,0]'],['parsons',{lines:['A','B']},'[{"line_number":1,"level":0},{"line_number":0,"level":1}]'],
    ['fill-blanks',{components:[{type:'text',text:'▭'},{type:'input',options:[]}]},'["A"]'],
    ['table',{rows:['A'],columns:['B','C'],is_checkbox:false,description:''},'[[true,false]]']];
  for(const [kind,data,raw] of fixtures) {
    const value=answer(kind,data,raw);
    assert.deepEqual(answer(kind,data,rawFromReply(kind,data,value.reply)).reply,value.reply);
  }
});
test('independent readback maps shuffled options and checks actual user',async()=>{
  const submission={id:10,attempt:20,step:1,user_id:30,status:'correct',time:'2026-10-31T00:00:00Z',reply:{choices:[false,true]}};
  const api=async(m,p)=>p.startsWith('/submissions')?{submissions:[submission]}:
    {attempts:[{id:20,step:1,dataset:{...dataset,options:['B','A']}}]};
  const ref=await verify(job,10,api,30);
  assert.equal(ref.raw_answer,job.raw_answer);assert.equal(ref.source_verified,true);
  await assert.rejects(verify(job,10,api,31),/identity mismatch/);
});
test('uncertain receipt is queried on resume without a second submission',async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'answer-cache-test-'));
  let posts=0;
  const api=async(method,route)=>{
    if(route==='/attempts')return {attempts:[{id:20,step:1,dataset}]};
    if(route.startsWith('/submissions?'))return {submissions:[]};
    if(method==='POST'){posts++;return {submissions:[{id:10}]};}
    return {submissions:[{id:10,attempt:20,step:1,status:'evaluation'}]};
  };
  try {
    const options={directory:dir,api,expectedUserId:30,polls:0};
    assert.equal((await processJob(job,options)).status,'pending');
    assert.equal((await processJob({...job,raw_answer:'{"selected":"1"}'},options)).status,'pending');
    assert.equal(posts,1);
  } finally {await fs.rm(dir,{recursive:true});}
});
test('crash before posting a replacement candidate does not mislabel the old verdict',async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'answer-cache-test-'));
  let posts=0,attempt=20;
  const submissions=new Map();
  const api=async(method,route,body)=>{
    if(route==='/attempts')return {attempts:[{id:++attempt,step:1,dataset}]};
    if(route.startsWith('/attempts/'))return {attempts:[{id:Number(route.split('/').at(-1)),step:1,dataset}]};
    if(route.startsWith('/submissions?'))return {submissions:[...submissions.values()]};
    if(method==='POST') {
      const id=10+(posts++),reply=JSON.parse(body.reply);
      const s={id,attempt:body.attempt,step:1,user_id:30,status:reply.choices[1]?'correct':'wrong',
        time:'2026-10-31T00:00:00Z',reply};
      submissions.set(id,s);return {submissions:[s]};
    }
    return {submissions:[submissions.get(Number(route.split('/').at(-1)))]};
  };
  try {
    const options={directory:dir,api,expectedUserId:30,polls:0};
    assert.equal((await processJob(job,options)).status,'wrong');
    const file=path.join(dir,job.problem_key,'state.json'),state=await read(file);
    state.current_request={request_id:'crashed-before-post',candidate_hash:'new',raw:'{"selected":"1"}'};
    await save(file,state);
    assert.equal((await processJob({...job,raw_answer:'{"selected":"1"}'},options)).status,'correct');
    assert.equal(posts,2);
  } finally {await fs.rm(dir,{recursive:true});}
});
test('sampled distractors require matching the installed variant before submitting',async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'answer-cache-test-'));
  const variantJob={...job,question:{...job.question,question:'Choose A.'}};
  let attempts=0,posts=0;
  const api=async(method,route,body)=>{
    if(route==='/steps/1')return {steps:[{id:1,block:{name:'choice',text:'Choose A.'}}]};
    if(route==='/attempts')return {attempts:[{id:20+(++attempts),step:1,dataset:attempts===1?{...dataset,options:['A','C']}:dataset}]};
    if(route.startsWith('/attempts/'))return {attempts:[{id:22,step:1,dataset}]};
    if(route.startsWith('/submissions?'))return {submissions:[]};
    if(method==='POST'){posts++;assert.equal(body.attempt,22);}
    return {submissions:[{id:10,attempt:22,step:1,user_id:30,status:'correct',time:'2026-10-31T00:00:00Z',reply:{choices:[true,false]}}]};
  };
  try {
    assert.equal((await processJob(variantJob,{directory:dir,api,expectedUserId:30})).status,'correct');
    assert.equal(attempts,2);assert.equal(posts,1);
  } finally {await fs.rm(dir,{recursive:true});}
});
test('official outer whitespace normalization is accepted but content changes are rejected',async()=>{
  const textJob={...job,question:{...job.question,hyperskill:{step_id:1,type:'string'},hyperskill_presentation:{dataset:{},interaction:null}}};
  const reply={files:[],text:'6 7 8 9'};
  const s={id:10,attempt:20,step:1,user_id:30,status:'correct',time:'2026-10-31T00:00:00Z',reply};
  const api=async(method,route)=>route.startsWith('/submissions')?{submissions:[s]}:{attempts:[{id:20,step:1,dataset:{}}]};
  const expected={attempt_id:20,time:s.time,dataset:{},reply:{files:[],text:'6 7 8 9 '}};
  assert.equal((await verify(textJob,10,api,30,expected)).raw_answer,'6 7 8 9');
  expected.reply.text='6 7 8';
  await assert.rejects(verify(textJob,10,api,30,expected),/differs/);
});
test('lost POST response with official whitespace normalization recovers the same submission',async()=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'answer-cache-test-'));
  const textJob={...job,raw_answer:'6 7 8 9 ',question:{...job.question,hyperskill:{step_id:1,type:'string'},
    hyperskill_presentation:{dataset:{},interaction:null}}};
  let posts=0,submission=null;
  const api=async(method,route)=>{
    if(route.startsWith('/attempts'))return {attempts:[{id:20,step:1,dataset:{}}]};
    if(route.startsWith('/submissions?'))return {submissions:submission?[submission]:[]};
    if(method==='POST') {
      posts++;submission={id:10,attempt:20,step:1,user_id:30,status:'correct',time:new Date().toISOString(),
        reply:{files:[],text:'6 7 8 9'}};
      throw Error('lost response');
    }
    return {submissions:[submission]};
  };
  try {
    const options={directory:dir,api,expectedUserId:30,polls:0};
    assert.equal((await processJob(textJob,options)).status,'correct');
    assert.equal((await processJob(textJob,options)).status,'correct');
    assert.equal(posts,1);
  } finally {await fs.rm(dir,{recursive:true});}
});
