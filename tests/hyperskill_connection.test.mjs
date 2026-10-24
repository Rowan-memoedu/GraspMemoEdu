import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import vm from 'node:vm';
import test from 'node:test';
import {createLearningCache} from '../web/learning-cache.js';

// Exercise the real view connection lifecycle without a browser or live answers.
const source=readFileSync(new URL('../web/training.js',import.meta.url),'utf8')
  .replace(/^import .*;\r?\n/gm,'').replace('export function createTrainingView','function createTrainingView')
  .replace('function renderSidebar() {','function renderSidebar() { observed.sidebar++; return;')
  .replace('function renderQuestion() {','function renderQuestion() { clearTimeout(timer); observed.question++; if(observed.renderAnswer)hyperskillAnswer(current,epoch,selection); return;')
  .replace('function error(container, failure, retry) {','function error(container, failure, retry) { observed.errors.push(failure); return;')
  .replace('return {open, stop, renderReviews};',`return {stop,
    probe: force=>probeHyperskill(epoch,force),refresh:()=>refreshHyperskill(epoch),
    warm: force=>warmHyperskill(current,epoch,force),resume:()=>resumeHyperskill(epoch),
    invoke:action=>requestHyperskill(action,{}),answer:()=>hyperskillAnswer(current,epoch,selection),
    status:()=>({available:hyperskillAvailability,
      ready:hyperskillWarm.get(current?.attempt_id)?.ready,error:hyperskillWarm.get(current?.attempt_id)?.error?.message}),
    validate:()=>scheduleValidation(),current:()=>current,
    configure(state,element){current=state;main=element;group={bank_id:'bank',id:'group',version:'v1',questions:[]};attempts.set(state.attempt_id,state);}};`);

function fixture() {
  let now=10000, healthy=false, cloudFailure=null, healthWait=null, sequence=0;
  let localFailure=null,localState={session:'local-round',interaction:null,ide:null};
  let nextGroup=null;
  const observed={sidebar:0,question:0,errors:[]};
  const calls=[],timers=new Map(),values=new Map(),status={textContent:''},reconnect={hidden:true};
  const access={learner_id:'connection-test'};
  const localHyperskill=async(action,body)=>{
    calls.push({target:'local',action,body});
    if(action==='health') {if(healthWait)await healthWait;return {ready:healthy};}
    if(action==='warmup')return {ready:true};
    if(localFailure)throw localFailure;
    if(['open','status'].includes(action))return {...localState};
    throw Error('Unexpected action: '+action);
  };
  const request=async(route,options)=>{
    calls.push({target:'cloud',route,body:options?.body});
    if(cloudFailure)throw cloudFailure;
    if(route.startsWith('training/group?'))return nextGroup;
    if(route.startsWith('training/state?'))return context.driver.current();
    assert.equal(route,'academy/training/launch');return {ticket:'synthetic-capability'};
  };
  const element={append(){},querySelector:selector=>selector==='.hyperskillConnection'?status:
    selector==='.hyperskillReconnect'?reconnect:null};
  const storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const context=vm.createContext({createLearningCache,localHyperskill,observed,URLSearchParams,crypto:{randomUUID},
    bridge:{root:{},request,getAccess:()=>access},
    hyperskillQuestion:(box,options)=>{observed.action=options.action;},
    localStorage:storage,document:{hidden:false,createElement:()=>({}),getElementById:()=>({cloneNode:()=>({}),remove(){}}),removeEventListener(){}},
    window:{removeEventListener(){}},performance:{now:()=>now},Date:class extends Date {static now(){return now;}},
    setTimeout:(fn,delay)=>{const id=++sequence;timers.set(id,{fn,delay,at:now+delay});return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(source+'\nglobalThis.driver=createTrainingView(bridge);',context);
  context.driver.configure({attempt_id:'attempt',hyperskill:{prepared:true,local_ready:false,type:'choice'},
    phase:'answer',can_submit:true,preview:false},element);
  return {driver:context.driver,calls,timers,values,status,reconnect,observed,
    async validate(next){nextGroup={bank_id:'bank',id:'group',version:'v1',questions:[],reader_states:{question:next}};
      context.driver.validate();await [...timers.values()].find(t=>t.delay===5000).fn();},
    async tick(ms){const end=now+ms;let count=0;
      for(;;){const next=[...timers.entries()].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
        if(!next)break;assert(++count<100,'Timer loop did not settle');
        now=next[1].at;timers.delete(next[0]);await next[1].fn();
        for(let i=0;i<20;i++)await Promise.resolve();}
      now=end;},
    hidden(value){context.document.hidden=value;context.driver.resume();},
    advance(ms){now+=ms;},healthy(value){healthy=value;},cloudFailure(value){cloudFailure=value;},
    localFailure(value){localFailure=value;},localState(value){localState=value;},waitForHealth(value){healthWait=value;}};
}

test('connection knowledge stays cached until an explicit check or scheduled recovery',async()=>{
  const f=fixture();assert.equal(await f.driver.probe(),false);
  f.healthy(true);f.advance(60000);assert.equal(await f.driver.probe(),false);
  assert.equal(await f.driver.probe(true),true);
  f.healthy(false);assert.equal(await f.driver.probe(true),false);
  f.healthy(true);assert.equal(await f.driver.probe(true),true);
});

test('simultaneous connection probes share one request and release it after failure',async()=>{
  const f=fixture();let release;f.waitForHealth(new Promise(resolve=>release=resolve));
  const first=f.driver.probe(true),second=f.driver.probe(true);assert.equal(first,second);
  release();await first;
  assert.equal(f.calls.filter(c=>c.action==='health').length,1);
  f.healthy(true);f.waitForHealth(null);assert.equal(await f.driver.probe(true),true);
});

test('background recovery prepares the same question while preserving its answer and never submits it',async()=>{
  const f=fixture(),draft='graspmemoedu:training:connection-test:draft:attempt';
  f.values.set(draft,'unfinished learner answer');
  await f.driver.refresh();assert.equal(f.driver.status().available,false);assert.equal(f.reconnect.hidden,false);
  f.healthy(true);await f.driver.refresh();
  assert.equal(f.driver.status().ready,true);assert.equal(f.reconnect.hidden,true);
  assert.equal(f.values.get(draft),'unfinished learner answer');
  assert.equal(f.calls.filter(c=>c.route==='academy/training/launch').length,1);
  assert.equal(f.calls.filter(c=>['submit','ide'].includes(c.action)||c.route?.includes('submit')).length,0);
  assert.equal(f.timers.size,0,'A recovered connection must have no recovery timer');
});

test('healthy connections stay silent through idle time, return-to-page and online events',async()=>{
  const f=fixture();f.healthy(true);await f.driver.refresh();
  const calls=f.calls.length;assert.equal(f.timers.size,0);
  await f.tick(120000);f.hidden(true);f.hidden(false);f.driver.resume();
  assert.equal(await f.driver.probe(),true);
  assert.equal(f.calls.length,calls);assert.equal(f.timers.size,0);
  await f.driver.probe(true);await f.driver.probe(true);
  assert.equal(f.calls.filter(c=>c.action==='warmup').length,1,'Explicit checks do not repeat prewarming');
});

test('only a failed connection retries with capped backoff, then becomes silent',async()=>{
  const f=fixture();await f.driver.refresh();
  for(const delay of [2000,4000,8000,16000,30000,30000]) {
    assert.equal(f.timers.size,1);assert.equal([...f.timers.values()][0].delay,delay);
    const before=f.calls.length;await f.tick(delay-1);assert.equal(f.calls.length,before);
    await f.tick(1);
  }
  f.healthy(true);await f.tick(30000);assert.equal(f.driver.status().available,true);
  assert.equal(f.timers.size,0);const count=f.calls.length;await f.tick(120000);assert.equal(f.calls.length,count);
});

test('hiding a disconnected page pauses recovery and returning resumes immediately',async()=>{
  const f=fixture();await f.driver.refresh();f.hidden(true);
  assert.equal(f.timers.size,0);const calls=f.calls.length;await f.tick(60000);assert.equal(f.calls.length,calls);
  f.healthy(true);f.hidden(false);await f.driver.refresh();assert.equal(f.driver.status().ready,true);
  assert.equal(f.timers.size,0);
});

test('an upstream preparation error allows explicit retry without starting healthy connection polling',async()=>{
  const f=fixture();f.healthy(true);f.cloudFailure(Error('Temporary upstream failure'));
  assert.equal(await f.driver.warm(),null);assert.equal(f.driver.status().error,'Temporary upstream failure');
  assert.equal(f.timers.size,0);
  f.cloudFailure(null);await f.driver.warm(true);
  assert.equal(f.driver.status().ready,true);assert.equal(f.driver.status().error,undefined);
  assert.equal(f.calls.filter(c=>c.route==='academy/training/launch').length,2);
});

test('leaving the group cancels recovery and ignores a late health response',async()=>{
  const f=fixture();let release;f.waitForHealth(new Promise(resolve=>release=resolve));
  const recovery=f.driver.refresh();f.driver.stop();f.healthy(true);release();await recovery;
  assert.equal(f.driver.status().available,null);assert.equal(f.timers.size,0);
  assert.equal(f.calls.filter(c=>c.action==='open').length,0);
});

test('a lost connection clears prepared readiness but keeps pending request identity',async()=>{
  const f=fixture();f.healthy(true);await f.driver.refresh();assert.equal(f.driver.status().ready,true);
  const key='graspmemoedu:training:connection-test:hyperskill:attempt';
  const pending=JSON.stringify({session:'local-round',pending:true,request_id:'original-request'});
  f.values.set(key,pending);f.localFailure(Object.assign(Error('Service exited'),{code:'hyperskill_unavailable'}));
  await assert.rejects(f.driver.invoke('status'),/Service exited/);
  assert.equal(f.driver.status().ready,undefined);assert.equal(f.values.get(key),pending);
  assert.equal([...f.timers.values()][0].delay,2000);
  f.localFailure(null);await f.tick(2000);
  assert.equal(JSON.parse(f.values.get(key)).request_id,'original-request');
  assert.equal(f.timers.size,0);
  assert.equal(f.calls.filter(c=>c.action==='submit').length,0);
});

test('a transport failure during preparation starts recovery after a successful health response',async()=>{
  const f=fixture();f.healthy(true);f.localFailure(Object.assign(Error('Connection closed'),{code:'hyperskill_unavailable'}));
  await f.driver.refresh();assert.equal(f.driver.status().available,false);
  assert.equal(f.timers.size,1);f.localFailure(null);await f.tick(2000);
  assert.equal(f.driver.status().ready,true);assert.equal(f.timers.size,0);
});

test('IDEA result queries continue without health polling and pause only during disconnection',async()=>{
  const f=fixture();f.healthy(true);f.localState({session:'local-round',ide:{watching:true}});
  f.observed.renderAnswer=true;await f.driver.refresh();
  assert.equal(f.driver.status().error,undefined);
  assert.equal(f.timers.size,1,JSON.stringify({calls:f.calls,observed:f.observed}));
  const healthCalls=f.calls.filter(c=>c.action==='health').length;
  await f.tick(10000);
  assert.equal(f.calls.filter(c=>c.action==='status').length,4,JSON.stringify({calls:f.calls,observed:f.observed,timers:[...f.timers.values()]}));
  assert.equal(f.calls.filter(c=>c.action==='health').length,healthCalls);
  f.localFailure(Object.assign(Error('Service exited'),{code:'hyperskill_unavailable'}));
  await f.tick(2500);assert.equal(f.driver.status().available,false);
  assert.equal(f.timers.size,1);assert.equal([...f.timers.values()][0].delay,2000);
  f.localFailure(null);await f.tick(2000);
  const before=f.calls.filter(c=>c.action==='status').length;await f.tick(2500);
  assert.equal(f.calls.filter(c=>c.action==='status').length,before+1);
  assert.equal(f.calls.filter(c=>['submit','ide','retry'].includes(c.action)).length,0);
});

test('a pending server-only verdict keeps confirming the original request without a local bridge',async()=>{
  const f=fixture();await f.driver.refresh();f.hidden(true);
  const payload={attempt_id:'attempt',request_id:'original-local-request',answer:'saved answer'};
  f.values.set('graspmemoedu:training:connection-test:hyperskill:attempt',JSON.stringify({local:true,pending:true,local_pending:payload}));
  f.cloudFailure(Error('Cloud acknowledgement temporarily unavailable'));f.observed.renderAnswer=true;f.driver.answer();
  await f.tick(2500);
  const sent=f.calls.filter(c=>c.route==='training/hyperskill-submit');
  assert.equal(sent.length,1);assert.deepEqual(JSON.parse(JSON.stringify(sent[0].body)),payload);
  assert.equal(f.timers.size,1);assert.equal([...f.timers.values()][0].delay,5000);
  const localCalls=f.calls.filter(c=>c.target==='local').length;await f.tick(5000);
  assert.equal(f.calls.filter(c=>c.route==='training/hyperskill-submit').length,2);
  assert.equal(f.calls.filter(c=>c.target==='local').length,localCalls);
  assert.equal(f.calls.filter(c=>['submit','ide','retry'].includes(c.action)).length,0);
});

test('background round creation and opening preserve controls, while verdict and permission changes redraw',async()=>{
  const f=fixture();let state=f.driver.current();
  state.hyperskill.round_id=null;state.hyperskill.opened=false;
  for(const metadata of [{round_id:'round',opened:false},{round_id:'round',opened:true}]) {
    state={...state,hyperskill:{...state.hyperskill,...metadata}};
    await f.validate(state);
    assert.equal(f.observed.question,0,'Connection metadata must not replace the active input');
    assert.equal(f.driver.current().hyperskill.round_id,'round');
  }
  state={...state,can_submit:false};await f.validate(state);
  assert.equal(f.observed.question,1,'Permission changes must update disabled controls');
  state={...state,phase:'done',result:{correct:true}};await f.validate(state);
  assert.equal(f.observed.question,2,'A completed answer must display the verdict');
});
