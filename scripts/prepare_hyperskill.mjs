/** One source Topic -> ordered theory sections and a native practice group. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {renderSourceHtml} from './render_mathacademy.mjs';
import {embedSourceImages} from './hyperskill_media.mjs';
import {supportedTypes} from './hyperskill_protocol.mjs';
import {fileURLToPath} from 'node:url';
import {splitTheory,bankFingerprint} from './hyperskill_sections.mjs';
import {preparePresentation} from './hyperskill_presentation.mjs';

export async function prepareTopic(translatedPath, livePath, output) {
if (!output) throw Error('Usage: prepare_hyperskill.mjs translated.json live-source.json output');
const translatedRaw = await fs.readFile(translatedPath);
const liveRaw = await fs.readFile(livePath);
const translated = JSON.parse(translatedRaw), live = JSON.parse(liveRaw);
if (translated.topic.id !== live.topic.id || translated.topic.theory !== live.theory.id)
  throw Error('Theory and practice identities differ');
if (live.practices.some(p => !supportedTypes.includes(p.type)))
  throw Error('Unsupported problem type; no partial import');
if(live.practices.some(p=>!p.presentation_source))
  throw Error('Capture complete problem presentations before importing');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const encode = value => JSON.stringify(value, null, 2) + '\n';
await fs.mkdir(output, {recursive:true});
if ((await fs.readdir(output)).length) throw Error('Output must be empty');
const topicId = `hyperskill-topic-${live.topic.id}`;
const media = await embedSourceImages([translated.step.block.text,...live.practices.map(p=>p.block.text)],{
  directory:path.join(output,'assets'),manifest:path.join(path.dirname(translatedPath),'assets.json'),
  base_url:translated.source_url,
});
const rendered = renderSourceHtml(media.html[0]);
const introductions = splitTheory(media.html[0],topicId).map(({source_html,...section}) =>
  ({...section,html:renderSourceHtml(source_html).html}));
const escape=value=>String(value).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const samples=p=>(p.block.options?.samples||[]).map((sample,i)=>
  `<section><h5>示例 ${i+1}</h5><p>输入</p><pre><code>${escape(sample[0])}</code></pre><p>输出</p><pre><code>${escape(sample[1])}</code></pre></section>`).join('');
const topic = {
  id: topicId, title: translated.topic.title, subject_id:'cs', source_adapter:'hyperskill',
  source_url:translated.source_url, training_mode:'paradigm', topic_format_version:2,
  topic_kind:'introduction',
  hyperskill:{topic_id:live.topic.id,theory_id:live.theory.id},
  introductions, content_order:introductions.map(i=>({kind:'introduction',id:i.id})),
  math_css:rendered.css || '',
  modules:[], memory_chunks:[], memory_points:[], flashcards:[],
};
topic.content_version=hash(encode(introductions));
const questions=await Promise.all(live.practices.map(async(p,i)=>({
    id:`hyperskill-step-${p.id}`, title:p.title, question:p.block.text,
    html:renderSourceHtml(media.html[i+1]+samples(p)).html,
    reference_answer:'', explanation_html:'', answer_type:'external',
    constraints:'由 Hyperskill 判题；选项与答案绑定当前练习实例。',
    source_url:`https://hyperskill.org/learn/step/${p.id}`,
    hyperskill:{...topic.hyperskill,step_id:p.id,type:p.type},
    hyperskill_presentation:await preparePresentation(p.type,p.presentation_source.dataset,
      p.presentation_source.code_options,{directory:path.join(output,'assets')}),
  })));
topic.version = hash(encode(topic));
const units = translated.knowledge_graph.path.map((p,i,rows)=>({
  id:`hyperskill-group-${p.id}`, title:p.title,
  parent_id:i ? `hyperskill-group-${rows[i-1].id}` : null,
  topic_ids:i===rows.length-1 ? [topicId] : [],
}));
const course = {id:'hyperskill',title:'Hyperskill',description:'',
  topics:[{id:topicId,path:`topics/${topicId}.json`,prerequisites:[]}],units};
const bank = {schema:1,id:'hyperskill-practice',title:'Hyperskill',subject_id:'cs',description:'',
  math_css:topic.math_css,groups:[
    ...units.map(({topic_ids,...u})=>({...u,questions:[]})),
    {id:topicId,title:topic.title,parent_id:units.at(-1)?.id || null,questions},
  ]};
bank.version=bankFingerprint(bank);
const report = {topic_id:topicId,practice_count:questions.length,introduction_count:introductions.length,memory_chunks:0,
  theory_sha256:hash(translatedRaw),practice_source_sha256:hash(liveRaw),
  source_hierarchy:translated.knowledge_graph.path_text,
  learner_progress_imported:false,answer_keys_invented:false};
if(Object.keys(media.assets).length)report.assets=media.assets;
const files = {
  [`topics/${topicId}.json`]:encode(topic),'catalog.json':encode({courses:[course]}),
  'training-banks/hyperskill-practice.json':encode(bank),
  'report.json':encode(report),
  [`sources/${topicId}.translated.json`]:translatedRaw,
  [`sources/${topicId}.practices.json`]:liveRaw,
};
for (const [name,body] of Object.entries(files)) {
  await fs.mkdir(path.dirname(path.join(output,name)),{recursive:true});
  await fs.writeFile(path.join(output,name),body,{flag:'wx'});
}
const hashes=Object.fromEntries(Object.entries(files).map(([name,body])=>[name,hash(body)]));
for(const name of await fs.readdir(path.join(output,'assets')).catch(()=>[]))
  hashes['assets/'+name]=hash(await fs.readFile(path.join(output,'assets',name)));
await fs.writeFile(path.join(output,'manifest.json'),encode({
  schema:1,adapter:'hyperskill',topic_format_version:2,
  test_materials:{topics:[topicId],banks:[bank.id]},
  files:hashes,
}),{flag:'wx'});
console.log(JSON.stringify({topic_id:topicId,practice_count:report.practice_count,images:Object.keys(media.assets).length}));
return {topic,course,bank,report};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))
  await prepareTopic(...process.argv.slice(2));
