/** Shared course/bank outline: terminal singletons lose no identity or context. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium}=require('playwright');
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage();await page.setContent('<main></main>');
const source=await fs.readFile(new URL('../web/task-tree.js',import.meta.url),'utf8');
const plans=await Promise.all(process.argv.slice(2).map(async original=>JSON.parse(await fs.readFile(path.join(original,'course-install-plan.json')))));
const banks=await Promise.all(plans.map(async p=>JSON.parse(await fs.readFile(p.bank.path))));
try {
const results=await page.evaluate(async ({source,plans,banks})=>{
const {renderTaskTree}=await import('data:text/javascript;base64,'+btoa(source));
const assert={equal:(a,b,message='')=>{if(a!==b)throw Error(message||`${a} != ${b}`);},
 notEqual:(a,b)=>{if(a===b)throw Error('Unexpected equal values');},
 match:(a,b)=>{if(!b.test(a))throw Error('Missing original title');},ok:(value,message)=>{if(!value)throw Error(message);}};
const results=[];
const root=document.querySelector('main');
const renderTask=task=>{const a=document.createElement('article');a.dataset.taskId=task.topic_id||task.id;
 a.textContent=[task.outline_title,task.title].filter(Boolean).join(' / ');return a;};
const task=id=>({id,topic_id:id,title:'Content '+id});
const units=[{id:'chapter',title:'Chapter',parent_id:null,topic_ids:[]},
 {id:'single',title:'Section A',parent_id:'chapter',topic_ids:['one']},
 {id:'multiple',title:'Section B',parent_id:'chapter',topic_ids:['two','three']}];
for(const ids of [['one','two','three'],['one','two']]){
 root.replaceChildren();renderTaskTree(root,{course:{id:'fixture'},tasks:ids.map(task),pending_hierarchy:units},
  {renderTask,sourceOrder:['chapter','single','multiple']});
 assert.equal(root.querySelector('details[data-unit-id="single"]'),null);
 assert.equal(root.querySelector('[data-unit-id="single"]').dataset.taskId,'one');
 assert.match(root.querySelector('[data-task-id="one"]').textContent,/Section A/);
 assert.ok(root.querySelector('details[data-unit-id="multiple"]'),'A filtered multiple-content group must retain its structure');
 assert.equal(root.querySelectorAll('article').length,ids.length);
}
for(const plan of plans){
 root.replaceChildren();renderTaskTree(root,{course:{id:plan.course_id},pending_hierarchy:plan.units,
  tasks:plan.topics.map(t=>task(t.topic_id))},{renderTask});
 const parents=new Set(plan.units.map(u=>u.parent_id));
 const single=plan.units.filter(u=>!parents.has(u.id)&&u.topic_ids.length===1);
 assert.equal(root.querySelectorAll('article').length,plan.topics.length);
 for(const u of single)assert.notEqual(root.querySelector(`[data-unit-id="${u.id}"]`).tagName,'DETAILS');
 results.push({course:plan.course_id,topics:plan.topics.length,singletons_flattened:single.length});
}
for(const bank of banks){
 const containers=bank.groups.filter(g=>!(g.id===bank.id&&!g.parent_id)&&bank.groups.some(c=>c.parent_id===g.id));
 const hasContent=g=>g.questions.length||(g.introductions||[]).length;
 const pending=containers.map(g=>({...g,parent_id:containers.some(p=>p.id===g.parent_id)?g.parent_id:null,
  topic_ids:bank.groups.filter(c=>hasContent(c)&&(c.id===g.id||c.parent_id===g.id&&!containers.some(p=>p.id===c.id))).map(c=>c.id)}));
 const tasks=bank.groups.filter(hasContent).map(g=>task(g.id));
 root.replaceChildren();renderTaskTree(root,{course:{id:bank.id},pending_hierarchy:pending,tasks},{renderTask});
 assert.equal(root.querySelectorAll('article').length,tasks.length);
 const redundant=[...root.querySelectorAll('details')].filter(n=>!n.querySelector('details')&&n.querySelectorAll('article').length===1);
 assert.equal(redundant.length,0,'A terminal bank group still wraps only one content card');
 results.push({bank:bank.id,groups_with_content:tasks.length,singletons_flattened:root.querySelectorAll('article[data-unit-id]').length});
}
return results;
},{source,plans,banks});
for(const result of results)console.log(JSON.stringify(result));
console.log('PASS shared course/bank terminal singleton and filtered multi-item regressions');
}finally{await browser.close();}
