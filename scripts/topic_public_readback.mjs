/** Verify the exact static export and read-only production UI after release. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [exportDirectory, output] = process.argv.slice(2);
const base = 'https://rowan-memoedu.github.io/GraspMemoEdu-Pages/';
const api = 'https://124.221.144.155';
const digest = data => crypto.createHash('sha256').update(data).digest('hex');
const files = [];
async function inventory(folder) {
  for (const entry of await fs.readdir(folder, {withFileTypes:true})) {
    const full = path.join(folder, entry.name);
    if (entry.isDirectory()) await inventory(full);
    else files.push({name:path.relative(exportDirectory,full).replaceAll('\\','/'),sha256:digest(await fs.readFile(full))});
  }
}
await inventory(exportDirectory);
await fs.mkdir(output,{recursive:true});
let failures = [...files];
for (let attempt=0;attempt<16 && failures.length;attempt++) {
  const pending=[];
  for (let start=0;start<failures.length;start+=8) {
    await Promise.all(failures.slice(start,start+8).map(async item=>{
      try {
        const response=await fetch(base+item.name+'?v='+item.sha256.slice(0,16),{signal:AbortSignal.timeout(30000)});
        if (!response.ok || digest(Buffer.from(await response.arrayBuffer()))!==item.sha256) pending.push(item);
      } catch {pending.push(item);}
    }));
  }
  failures=pending;
  console.log(`Public asset readback: ${files.length-failures.length}/${files.length} verified`);
  if (failures.length) await new Promise(resolve=>setTimeout(resolve,30000));
}
assert.deepEqual(failures,[],'Published assets differ from the reviewed export');
const privateAccounts=JSON.parse(await fs.readFile('D:/AppData/GraspMemoEdu/admin/invitations-initial-20260920.json','utf8'));
const account=privateAccounts.accounts.find(item=>item.purpose==='test');
assert.ok(account);
let token, browser;
const request=async(endpoint,body)=>{
  const response=await fetch(api+'/api/'+endpoint,{method:body===undefined?'GET':'POST',
    headers:{Origin:new URL(base).origin,'Content-Type':'application/json',...(token?{'X-Learning-Identity':token}:{})},
    ...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(30000)});
  assert.equal(response.status,200,'Public API check failed: '+endpoint);
  return response.json();
};
try {
  const login=await request('access/login',{invitation_code:account.invitation_code});
  token=login.identity_token;
  assert.equal(login.access.purpose,'test');
  assert.equal(login.access.learner_id,account.learner_id);
  const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
  const {chromium}=require('playwright');
  browser=await chromium.launch({headless:true,executablePath:path.join(os.homedir(),'AppData/Local/Google/Chrome/Application/chrome.exe')});
  const context=await browser.newContext({viewport:{width:1365,height:1000},locale:'zh-CN'});
  await context.addInitScript(({api,token})=>{
    localStorage.setItem('graspmemoedu:identity:'+api,token);
    localStorage.setItem('graspmemoedu:identity-role:'+api,'account');
  },{api,token});
  const page=await context.newPage(), errors=[], blockedWrites=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route(api+'/**',async route=>{
    if(route.request().method()==='POST') {blockedWrites.push(new URL(route.request().url()).pathname);await route.abort();}
    else await route.continue();
  });
  const version=files.find(item=>item.name==='index.html').sha256.slice(0,16);
  await page.goto(base+'?v='+version+'#/',{waitUntil:'networkidle'});
  await page.locator('.subjectCard').first().waitFor();
  assert.equal(await page.locator('.subjectCard').count(),6);
  await page.screenshot({path:path.join(output,'public-homepage.png'),fullPage:true});
  await page.goto(base+'?v='+version+'#/subjects/math/learn',{waitUntil:'networkidle'});
  await page.locator('.portalPageTitle').waitFor();
  await page.screenshot({path:path.join(output,'public-learning.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  assert.deepEqual(blockedWrites,[]);
  await fs.writeFile(path.join(output,'public-readback.json'),JSON.stringify({passed:true,assets_verified:files,
    browser_errors:errors,learning_mutations:blockedWrites,existing_test_account:true,homepage_subjects:6},null,2));
  console.log('PASS public asset hashes, homepage, learning page and zero learning mutations.');
} finally {
  if(browser) await browser.close();
  if(token) await request('access/logout',{});
}
