import assert from 'node:assert/strict';
import test from 'node:test';
import {captureStorage, importStorage, validateValues, trustedMessage, identityConflict} from '../web/origin-transfer.js';
function storage(entries={}) {
  const data=new Map(Object.entries(entries));
  return {get length(){return data.size},key:i=>[...data.keys()][i]??null,getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,String(v)),removeItem:k=>data.delete(k)};
}
test('only learning namespaces cross origins and original guest identity remains intact',()=>{
  const source=storage({'graspmemoedu:guest-identity:https://example.test':'synthetic-guest','math-learning-web:ui-language':'en','personal-blog:secret':'private'});
  const target=storage();importStorage(target,captureStorage(source));
  assert.equal(target.getItem('graspmemoedu:guest-identity:https://example.test'),'synthetic-guest');
  assert.equal(source.getItem('graspmemoedu:guest-identity:https://example.test'),'synthetic-guest');
  assert.equal(target.getItem('personal-blog:secret'),null);
});
test('foreign namespaces, duplicate fields and oversized input are rejected before writes',()=>{
  for(const values of [[['private:key','x']],[['graspmemoedu:a','x'],['graspmemoedu:a','y']],[['graspmemoedu:a','x'.repeat(4000000)]]]) assert.throws(()=>validateValues(values));
});
test('every message requires the expected origin, exact window and nonce',()=>{
  const source={};const expected={origin:'https://old.example',source,nonce:'n',type:'transfer'};
  const good={origin:expected.origin,source,data:{nonce:'n',type:'transfer'}};
  assert.ok(trustedMessage(good,expected));
  for(const change of [{origin:'https://attacker.example'},{source:{}},{data:{nonce:'wrong',type:'transfer'}},{data:{nonce:'n',type:'other'}}]) assert.equal(trustedMessage({...good,...change},expected),false);
});
test('a new-site identity requires explicit choice and is backed up before replacement',()=>{
  const target=storage({'graspmemoedu:identity:api':'new-guest'});const values=[['graspmemoedu:identity:api','old-guest']];
  assert.ok(identityConflict(target,values));assert.throws(()=>importStorage(target,values),/identity_conflict/);
  assert.equal(target.getItem('graspmemoedu:identity:api'),'new-guest');
  const backup=importStorage(target,values,{replace:true,stamp:1});
  assert.deepEqual(JSON.parse(target.getItem(backup)),[['graspmemoedu:identity:api','new-guest']]);
  assert.equal(target.getItem('graspmemoedu:identity:api'),'old-guest');
});
test('failed imports restore every touched original value',()=>{
  const target=storage({'graspmemoedu:identity:api':'current'});const write=target.setItem;
  target.setItem=(key,value)=>{if(key==='graspmemoedu:fail')throw new Error('quota');return write(key,value)};
  assert.throws(()=>importStorage(target,[['graspmemoedu:identity:api','old'],['graspmemoedu:fail','x']],{replace:true}));
  assert.equal(target.getItem('graspmemoedu:identity:api'),'current');assert.equal(target.getItem('graspmemoedu:fail'),null);
});
