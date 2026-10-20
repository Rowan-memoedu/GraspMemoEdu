import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {Bridge} from './hyperskill_bridge.mjs';
import {answer,presentation} from './hyperskill_protocol.mjs';
import {renderSourceHtml} from './render_mathacademy.mjs';

assert.match(renderSourceHtml('<p><code>10._025</code></p><pre>2^5 * 1 + 2^4 * 1</pre>').html,/10\._025/);
assert.match(renderSourceHtml('<p><code>10._025</code></p><pre>2^5 * 1 + 2^4 * 1</pre>').html,/2\^5 \* 1/);
assert.deepEqual(answer('choice',{options:['A','B'],is_multiple_choice:false},'{"selected":"1"}').reply,{choices:[false,true]});
assert.deepEqual(answer('matching',{pairs:[{first:'a',second:'1'},{first:'b',second:'2'}]},
  '[{"left":"0","right":"1"},{"left":"1","right":"0"}]').reply,{ordering:[1,0]});
assert.deepEqual(answer('number',{},'12').reply,{number:'12'});
assert.deepEqual(answer('string',{},'x').reply,{files:[],text:'x'});
assert.throws(()=>answer('number',{},'NaN'));
assert.throws(()=>answer('choice',{options:['A']},'{"selected":"7"}'));
assert.equal(presentation('matching',{pairs:[{first:'a',second:'1'}]}).fixed[0],'0');
const directory=await fs.mkdtemp(path.join(os.tmpdir(),'hyperskill-test-'));
try {
  let attempts=0,posts=0,delivered=0,lost=true,cloudLost=true;
  const submissions=[];
  const bridge=new Bridge({directory,
    cloud:async(route,_,body)=>{
      if(route==='claim')return {provider:'hyperskill',step_id:10,topic_id:1,mode:'learn',type:'choice',round_id:'r',next_sequence:1};
      if(body.kind==='check'){delivered++;if(cloudLost){cloudLost=false;throw Error('lost ACK');}}
      return {};
    },
    hyperskill:async(method,route,body)=>{
      if(route==='/attempts'){attempts++;return {attempts:[{id:attempts,step:10,dataset:{options:['A','B']}}]};}
      if(method==='POST'){
        posts++;submissions.push({id:posts,attempt:body.attempt,reply:body.reply,time:new Date().toISOString(),status:posts===1?'wrong':'correct'});
        if(lost){lost=false;throw Error('lost response');}
        return {submissions:[submissions.at(-1)]};
      }
      if(route.startsWith('/submissions?'))return {submissions};
      return {submissions:[submissions.find(s=>s.id===Number(route.split('/').at(-1)))]};
    }});
  const initial=await bridge.open('x'.repeat(43));
  await assert.rejects(bridge.submit(initial.session,1,'{"selected":"0"}','request-000000001'),/lost ACK/);
  await bridge.flush(); // A closed browser must not strand an acknowledged upstream result.
  const recovered=await bridge.status(await bridge.load(initial.session));
  assert.equal(posts,1);assert.equal(recovered.last.status,'wrong');assert.equal(delivered,2);
  const replay=await bridge.submit(initial.session,1,'{"selected":"0"}','request-000000001');
  assert.equal(replay.last.submission_id,1);assert.equal(posts,1);
  const next=await bridge.retry(initial.session);assert.equal(next.attempt_id,2);assert.equal(next.last,null);
  await assert.rejects(bridge.submit(initial.session,1,'{"selected":"1"}','request-000000002'),/实例已变化/);
  const done=await bridge.submit(initial.session,2,'{"selected":"1"}','request-000000002');
  assert.equal(done.last.status,'correct');assert.equal(posts,2);
  console.log('PASS four reply types, stale dataset, lost upstream response, durable cloud ACK replay, retry');
} finally {await fs.rm(directory,{recursive:true,force:true});}
