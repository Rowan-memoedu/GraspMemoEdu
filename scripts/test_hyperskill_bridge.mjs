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
const blanks={components:[{type:'text',text:'x = ▭; y = ▭;'},
  {type:'select',options:['1','2']},{type:'input',options:[]}]};
assert.deepEqual(answer('fill-blanks',blanks,'["2","3"]').reply,{blanks:['2','3']});
assert.throws(()=>answer('fill-blanks',blanks,'["outside","3"]'));
assert.deepEqual(answer('sorting',{options:['a','b']},'[1,0]').reply,{ordering:[1,0]});
assert.throws(()=>answer('sorting',{options:['a','b']},'[1,1]'));
const lines=[{line_number:1,level:0},{line_number:0,level:1}];
assert.deepEqual(answer('parsons',{lines:['inside','outside']},JSON.stringify(lines)).reply,{lines});
assert.throws(()=>answer('parsons',{lines:['a']},'[{"line_number":0,"level":11}]'));
assert.equal(answer('parsons',{lines:['System.out.print(&quot;x&quot;);']},'[{"line_number":0,"level":0}]').display,'System.out.print("x");');
assert.deepEqual(answer('table',{rows:['r'],columns:['a','b'],is_checkbox:false},'[[false,true]]').reply,
  {choices:[{name_row:'r',columns:[{name:'a',answer:false},{name:'b',answer:true}]}]});
assert.throws(()=>answer('table',{rows:['r'],columns:['a','b'],is_checkbox:false},'[[true,true]]'));
const codeOptions={code_templates:{java25:'class Main {}'},samples:[]};
assert.deepEqual(answer('code',{},'{"language":"java25","code":"class Main {}"}',codeOptions).reply,
  {language:'java25',code:'class Main {}\n'});
assert.throws(()=>answer('code',{},'{"language":"outside","code":"x"}',codeOptions));
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
  const codeDir=path.join(directory,'code-test');
  let opened;
  const codeBridge=new Bridge({directory:codeDir,openEditor:async file=>{opened=file;},
    cloud:async(route)=>route==='claim'?{provider:'hyperskill',step_id:20,topic_id:1,mode:'learn',type:'code',round_id:'code-round',next_sequence:1}:{},
    hyperskill:async(_,route)=>route==='/steps/20'?{steps:[{id:20,block:{name:'code',options:codeOptions}}]}:
      {attempts:[{id:20,step:20,dataset:{}}]}});
  const codeRun=await codeBridge.open('y'.repeat(43));
  assert.equal(codeRun.interaction.type,'hyperskill_code');
  const raw='{"language":"java25","code":"public class Example {}"}';
  await codeBridge.editor(codeRun.session,raw);
  assert.equal(path.basename(opened),'Example.java');
  await fs.writeFile(opened,'public class Example { /* saved in editor */ }');
  await codeBridge.editor(codeRun.session,raw); // Opening again must not overwrite learner work.
  const saved=await codeBridge.editor(codeRun.session,null,true);
  assert.match(JSON.parse(saved.draft).code,/saved in editor/);
  console.log('PASS nine reply types, malformed answers, stale dataset, lost responses, durable replay, preserved IDEA draft');
} finally {await fs.rm(directory,{recursive:true,force:true});}
