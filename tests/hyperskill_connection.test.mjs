import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import {createLearningCache} from '../web/learning-cache.js';

// Exercise the real view connection lifecycle without a browser or live answers.
const source=readFileSync(new URL('../web/training.js',import.meta.url),'utf8')
  .replace(/^import .*;\r?\n/gm,'').replace('export function createTrainingView','function createTrainingView')
  .replace('function renderSidebar() {','function renderSidebar() { observed.sidebar++; return;')
  .replace('function renderQuestion() {','function renderQuestion() { observed.question++; return;')
  .replace('function error(container, failure, retry) {','function error(container, failure, retry) { throw failure;')
  .replace('return {open, stop, renderReviews};',`return {stop,
    probe: force=>probeHyperskill(epoch,force),refresh:()=>refreshHyperskill(epoch),
    warm:()=>warmHyperskill(current,epoch),status:()=>({available:hyperskillAvailability,
      ready:hyperskillWarm.get(current?.attempt_id)?.ready,error:hyperskillWarm.get(current?.attempt_id)?.error?.message}),
    validate:()=>scheduleValidation(),current:()=>current,
    configure(state,element){current=state;main=element;group={bank_id:'bank',id:'group',version:'v1',questions:[]};attempts.set(state.attempt_id,state);}};`);

function fixture() {
  let now=10000, healthy=false, cloudFailure=null, healthWait=null, sequence=0;
  let nextGroup=null;
  const observed={sidebar:0,question:0};
  const calls=[],timers=new Map(),values=new Map(),status={textContent:''},reconnect={hidden:true};
  const access={learner_id:'connection-test'};
  const localHyperskill=async(action,body)=>{
    calls.push({target:'local',action,body});
    if(action==='health') {if(healthWait)await healthWait;return {ready:healthy};}
    if(action==='open')return {session:'local-round',interaction:null,ide:null};
    if(action==='warmup')return {ready:true};
    throw Error('Unexpected action: '+action);
  };
  const request=async(route,options)=>{
    calls.push({target:'cloud',route,body:options?.body});
    if(cloudFailure)throw cloudFailure;
    if(route.startsWith('training/group?'))return nextGroup;
    assert.equal(route,'academy/training/launch');return {ticket:'synthetic-capability'};
  };
  const element={querySelector:selector=>selector==='.hyperskillConnection'?status:
    selector==='.hyperskillReconnect'?reconnect:null};
  const storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const context=vm.createContext({createLearningCache,localHyperskill,observed,URLSearchParams,
    bridge:{root:{},request,getAccess:()=>access},
    localStorage:storage,document:{hidden:false,getElementById:()=>({cloneNode:()=>({}),remove(){}}),removeEventListener(){}},
    window:{removeEventListener(){}},performance:{now:()=>now},Date:class extends Date {static now(){return now;}},
    setTimeout:(fn,delay)=>{const id=++sequence;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(source+'\nglobalThis.driver=createTrainingView(bridge);',context);
  context.driver.configure({attempt_id:'attempt',hyperskill:{prepared:true,local_ready:false,type:'choice'},
    phase:'answer',can_submit:true,preview:false},element);
  return {driver:context.driver,calls,timers,values,status,reconnect,observed,
    async validate(next){nextGroup={bank_id:'bank',id:'group',version:'v1',questions:[],reader_states:{question:next}};
      context.driver.validate();await [...timers.values()].find(t=>t.delay===5000).fn();},
    advance(ms){now+=ms;},healthy(value){healthy=value;},cloudFailure(value){cloudFailure=value;},waitForHealth(value){healthWait=value;}};
}

test('a failed probe expires and an explicit action bypasses the cached failure',async()=>{
  const f=fixture();assert.equal(await f.driver.probe(),false);
  f.healthy(true);assert.equal(await f.driver.probe(),false);
  assert.equal(await f.driver.probe(true),true);
  f.healthy(false);assert.equal(await f.driver.probe(true),false);
  f.healthy(true);f.advance(5001);assert.equal(await f.driver.probe(),true);
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
  assert.equal(f.timers.size,1);assert.equal([...f.timers.values()][0].delay,10000);
});

test('a transient round preparation failure is retried instead of being cached forever',async()=>{
  const f=fixture();f.healthy(true);f.cloudFailure(Error('Temporary upstream failure'));
  assert.equal(await f.driver.warm(),null);assert.equal(f.driver.status().error,'Temporary upstream failure');
  f.cloudFailure(null);f.advance(5001);await f.driver.refresh();
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
  f.values.set(key,pending);f.healthy(false);await f.driver.refresh();
  assert.equal(f.driver.status().ready,undefined);assert.equal(f.values.get(key),pending);
  assert.equal(f.calls.filter(c=>c.action==='submit').length,0);
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
