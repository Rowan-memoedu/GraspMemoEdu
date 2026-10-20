/** Aggregate complete source Topics; shared source ancestry is merged by stable ID. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {prepareTopic} from './prepare_hyperskill.mjs';
const [source,translated,output]=process.argv.slice(2);
if(!output)throw Error('Usage: prepare_hyperskill_branch source-directory translated-directory output');
await fs.mkdir(output,{recursive:true});
if((await fs.readdir(output)).length)throw Error('Output must be empty');
const inventory=JSON.parse(await fs.readFile(path.join(source,'inventory.json')));
const units=new Map(),topics=[],reports=[],hashes={};
for(const row of inventory.topics) {
  const part=path.join(output,'parts',String(row.id));
  const {topic,course,report}=await prepareTopic(path.join(translated,`translated.step-${row.theory}.json`),
    path.join(source,`topic-${row.id}.json`),part);
  const manifest=JSON.parse(await fs.readFile(path.join(part,'manifest.json')));
  topics.push({id:topic.id,path:`parts/${row.id}/topics/${topic.id}.json`,prerequisites:[]});
  for(const unit of course.units) {
    if(!units.has(unit.id))units.set(unit.id,structuredClone(unit));
    else {
      const old=units.get(unit.id);
      if(old.title!==unit.title||old.parent_id!==unit.parent_id)throw Error('Conflicting source ancestry');
      old.topic_ids.push(...unit.topic_ids.filter(id=>!old.topic_ids.includes(id)));
    }
  }
  for(const [name,digest] of Object.entries(manifest.files))
    if(!['catalog.json','report.json'].includes(name))hashes[`parts/${row.id}/${name}`]=digest;
  reports.push(report);
}
const encode=value=>JSON.stringify(value,null,2)+'\n';
const hash=value=>createHash('sha256').update(value).digest('hex');
for(const [name,value] of Object.entries({
  'catalog.json':{courses:[{id:'hyperskill',title:'Hyperskill',description:'',topics,units:[...units.values()]}]},
  'report.json':{source_root:inventory.root,topic_count:topics.length,practice_count:reports.reduce((n,r)=>n+r.practice_count,0),
    types:inventory.types,reports,learner_progress_imported:false},
})) {
  const bytes=encode(value);await fs.writeFile(path.join(output,name),bytes,{flag:'wx'});hashes[name]=hash(bytes);
}
await fs.writeFile(path.join(output,'manifest.json'),encode({schema:1,adapter:'hyperskill',
  test_materials:{topics:topics.map(t=>t.id)},files:hashes}),{flag:'wx'});
console.log(JSON.stringify({status:'prepared',topics:topics.length,problems:inventory.practice_count,units:units.size}));
