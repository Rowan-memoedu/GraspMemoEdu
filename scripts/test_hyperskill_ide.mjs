import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {Bridge} from './hyperskill_bridge.mjs';
import {officialIDE} from './hyperskill_ide.mjs';
const calls=[];
const launch=officialIDE({ports:[63342],fetcher:async(url,options)=>{
  calls.push({url,options});
  return {ok:true,json:async()=>({version:'IntelliJ IDEA 2026.2',edutools:'2026.7'})};
}});
await launch({step:20,user:7,language:'java'});
assert.equal(calls[1].url,'http://127.0.0.1:63342/api/hyperskill?step_id=20&language=java&user_id=7');
assert.equal(calls[1].options.headers.Origin,'https://hyperskill.org');
assert.equal(calls[1].options.body,undefined);

const directory=await fs.mkdtemp(path.join(os.tmpdir(),'hyperskill-ide-'));
try {
  const base={id:1,step:20,user_id:7,attempt:40,status:'correct',time:'2020-01-01T00:00:00Z',
    reply:{language:'java25',code:'old code'}};
  const rows=[base],events=[];
  let opened=0,lost=true,round='code-round';
  const options={directory,expectedUserId:7,openIDE:async(args)=>{
    assert.deepEqual(args,{step:20,user:7,language:'java'});opened++;
  },cloud:async(route,_,body)=>{
    if(route==='claim')return {provider:'hyperskill',step_id:20,type:'code',round_id:round,next_sequence:1};
    if(body.kind==='check'){
      events.push(body.event);
      if(lost){lost=false;throw Error('lost ACK');}
      return {closed:body.event.category==='passed'};
    }
    return {};
  },hyperskill:async(method,route)=>{
    assert.equal(method,'GET','Code attempts/submissions belong exclusively to the official plugin');
    if(route==='/steps/20')return {steps:[{id:20,block:{name:'code',options:{code_templates:{java25:'class Main {}'}}}}]};
    return {submissions:rows};
  }};
  const bridge=new Bridge(options),state=await bridge.open('y'.repeat(43));
  assert.equal(state.attempt_id,undefined);assert.equal(opened,0);
  assert.equal(state.interaction.type,'hyperskill_code');
  await assert.rejects(bridge.submit(state.session,1,'code','request-000000001'),/Check/);
  const started=await bridge.editor(state.session);
  assert.equal(opened,1);assert.equal(started.ide.watching,true);
  const run=await bridge.load(state.session);
  const time=new Date(Date.parse(run.ide.started_at)+5).toISOString();
  rows.push({...base,id:2,step:21,time},{...base,id:3,user_id:8,time},
    {...base,id:4,time:base.time},
    {...base,id:5,attempt:50,time,status:'evaluation',reply:{language:'java25',code:'wrong code'}},
    {...base,id:6,attempt:60,time,status:'correct',reply:{language:'java25',code:'valid code'}});
  const poll=async(b=bridge)=>{
    const r=await b.load(state.session);r.ide.polled=0;await b.save(r);return b.status(r);
  };
  assert.equal((await poll()).ide.submitting,true);assert.equal(events.length,0);
  rows.find(r=>r.id===5).status='wrong';
  await assert.rejects(poll(),/lost ACK/);
  const resumed=new Bridge(options);
  const done=await poll(resumed);
  assert.equal(done.last.submission_id,6);assert.equal(done.ide.watching,false);
  assert.deepEqual(events.map(e=>e.receipt.submission_id),[5,5,6]);
  assert.equal(events[0].id,events[1].id);
  assert.deepEqual(events.map(e=>e.sequence),[1,1,2]);
  assert.equal(events[2].receipt.answer_display,'valid code');
  await resumed.flush();assert.equal(events.length,3);
  assert.deepEqual((await fs.readdir(directory)).filter(n=>n==='code'),[]);
  // A fresh website round excludes every preceding upstream Check.
  round='next-round';
  const fresh=await resumed.open('z'.repeat(43));
  await resumed.editor(fresh.session);
  assert.equal((await resumed.status(await resumed.load(fresh.session))).last,null);
  console.log('PASS official launch, no bridge code POST/files, old/foreign isolation, ordered Check sync, crash replay, new review baseline');
} finally {await fs.rm(directory,{recursive:true,force:true});}
