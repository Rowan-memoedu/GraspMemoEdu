/** One source Topic -> translated Introduction, independent problem memories. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {renderSourceHtml} from './render_mathacademy.mjs';

const [translatedPath, livePath, output] = process.argv.slice(2);
if (!output) throw Error('Usage: prepare_hyperskill.mjs translated.json live-source.json output');
const translatedRaw = await fs.readFile(translatedPath);
const liveRaw = await fs.readFile(livePath);
const translated = JSON.parse(translatedRaw), live = JSON.parse(liveRaw);
if (translated.topic.id !== live.topic.id || translated.topic.theory !== live.theory.id)
  throw Error('Theory and practice identities differ');
if (live.practices.some(p => !['choice','matching','number','string'].includes(p.type)))
  throw Error('Unsupported problem type; no partial import');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const encode = value => JSON.stringify(value, null, 2) + '\n';
await fs.mkdir(output, {recursive:true});
if ((await fs.readdir(output)).length) throw Error('Output must be empty');
const topicId = `hyperskill-topic-${live.topic.id}`;
const rendered = renderSourceHtml(translated.step.block.text);
const topic = {
  id: topicId, title: translated.topic.title, subject_id:'cs', source_adapter:'hyperskill',
  source_url:translated.source_url, training_mode:'atomic_retrieval',
  hyperskill:{topic_id:live.topic.id,theory_id:live.theory.id},
  introduction:{html:rendered.html}, math_css:rendered.css || '',
  modules:[], memory_chunks:[],
  memory_points:live.practices.map(p=>({id:`problem-${p.id}`,text:p.title})),
  flashcards:live.practices.map(p=>({
    id:`hyperskill-step-${p.id}`, title:p.title, question:p.block.text,
    html:renderSourceHtml(p.block.text).html,
    point_ids:[`problem-${p.id}`],
    reference_answer:'', explanation_html:'', answer_type:'external',
    constraints:'由 Hyperskill 判题；选项与答案绑定当前练习实例。',
    hyperskill:{step_id:p.id,type:p.type},
  })),
};
topic.version = hash(encode(topic));
const units = translated.knowledge_graph.path.map((p,i,rows)=>({
  id:`hyperskill-group-${p.id}`, title:p.title,
  parent_id:i ? `hyperskill-group-${rows[i-1].id}` : null,
  topic_ids:i===rows.length-1 ? [topicId] : [],
}));
const course = {id:'hyperskill',title:'Hyperskill',description:'',
  topics:[{id:topicId,path:`topics/${topicId}.json`,prerequisites:[]}],units};
const report = {topic_id:topicId,practice_count:topic.flashcards.length,memory_chunks:0,
  theory_sha256:hash(translatedRaw),practice_source_sha256:hash(liveRaw),
  source_hierarchy:translated.knowledge_graph.path_text,
  learner_progress_imported:false,answer_keys_invented:false};
const files = {
  [`topics/${topicId}.json`]:encode(topic),'catalog.json':encode({courses:[course]}),
  'report.json':encode(report),
};
for (const [name,body] of Object.entries(files)) {
  await fs.mkdir(path.dirname(path.join(output,name)),{recursive:true});
  await fs.writeFile(path.join(output,name),body,{flag:'wx'});
}
await fs.writeFile(path.join(output,'manifest.json'),encode({
  schema:1,adapter:'hyperskill',test_materials:{topics:[topicId]},
  files:Object.fromEntries(Object.entries(files).map(([name,body])=>[name,hash(body)])),
}),{flag:'wx'});
console.log(JSON.stringify(report));
