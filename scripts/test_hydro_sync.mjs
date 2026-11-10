import assert from 'node:assert/strict';
import {summarize} from './hydro_sync_fetch.mjs';
const problems=[{pid:'T1',docId:1}],uid=208;
const result=summarize(problems,{},[{pid:1,uid,status:2,_id:'6add5952ac9b69220616aa5e'},{pid:1,uid,status:1,_id:'6add5752ac9b69220616aa5e'}],uid);
assert.equal(result.T1.accepted,true);assert.equal(result.T1.latest_status,2);
assert.equal(summarize(problems,{},[],uid).T1.submitted,false);
assert.throws(()=>summarize(problems,{'1':{uid:999,status:1}},[],uid));
console.log('PASS AC survives later failure, empty history, and identity isolation');
