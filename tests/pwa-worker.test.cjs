const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script = fs.readFileSync(require('node:path').join(__dirname, '../web/sw.js'), 'utf8');
function worker(fetchImpl) {
  const events = {};
  const self = {location:{href:'https://example.test/GraspMemoEdu-Pages/sw.js'},
    addEventListener:(name, fn) => {events[name] = fn;}, skipWaiting:async()=>{}, clients:{claim:async()=>{}}};
  vm.runInNewContext(script, {self, URL, Response, fetch:fetchImpl});
  return events;
}
test('offline navigation has a scoped retry and no stale learning data', async () => {
  const events = worker(async()=>{throw new Error('offline');});
  let result;
  events.fetch({request:{method:'GET',mode:'navigate',url:'https://example.test/GraspMemoEdu-Pages/'},respondWith:p=>{result=p;}});
  const response = await result;
  assert.equal(response.status,503);
  assert.equal(response.headers.get('Cache-Control'),'no-store');
  assert.match(await response.text(),/href="https:\/\/example.test\/GraspMemoEdu-Pages\/"/);
});
test('API reads, mutations, foreign scopes and subresources pass untouched', () => {
  const events = worker(()=>{throw new Error('must not intercept');});
  for (const request of [
    {method:'POST',mode:'cors',url:'https://example.test/api/submit'},
    {method:'GET',mode:'cors',url:'https://api.example.test/api/state'},
    {method:'GET',mode:'navigate',url:'https://example.test/other/'},
    {method:'GET',mode:'cors',url:'https://example.test/GraspMemoEdu-Pages/app.js'}
  ]) events.fetch({request,respondWith:()=>assert.fail('unexpected interception')});
});
test('online navigation preserves the original response', async () => {
  const response = new Response('current page');
  const events = worker(async(request, options)=>{
    assert.equal(options.cache,'no-store','offline navigation must not fall back to HTTP disk cache');
    return response;
  });
  let result;
  events.fetch({request:{method:'GET',mode:'navigate',url:'https://example.test/GraspMemoEdu-Pages/download.html'},respondWith:p=>{result=p;}});
  assert.equal(await result,response);
});
