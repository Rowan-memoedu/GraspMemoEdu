/** Prepared public problem data; never contains an answer key or a session. */
import {presentation,answer,hash} from './hyperskill_protocol.mjs';
import {embedSourceImages} from './hyperskill_media.mjs';
import {renderSourceHtml} from './render_mathacademy.mjs';

export function publicDataset(type,data={}) {
  if(type==='choice')return {options:data.options,is_multiple_choice:!!data.is_multiple_choice};
  if(type==='matching')return {pairs:data.pairs?.map(p=>({first:p.first,second:p.second}))};
  if(type==='fill-blanks')return {components:data.components?.map(c=>c.type==='text'?
    {type:c.type,text:c.text}:{type:c.type,options:c.options||[]})};
  if(type==='sorting')return {options:data.options};
  if(type==='parsons')return {lines:data.lines};
  if(type==='table')return {rows:data.rows,columns:data.columns,is_checkbox:!!data.is_checkbox,description:data.description||''};
  return {};
}
export function publicCodeOptions(options={}) {
  return {code_templates:options.code_templates,samples:options.samples||[],limits:options.limits||{}};
}
export async function preparePresentation(type,dataset,options={},mediaOptions={}) {
  dataset=publicDataset(type,dataset);
  const code_options=type==='code'?publicCodeOptions(options):{};
  const interaction=presentation(type,dataset,s=>({html:s}),code_options);
  const items=interaction?[...(interaction.options||[]),...(interaction.left||[]),...(interaction.right||[]),
    ...(interaction.items||[]),...(interaction.rows||[]),...(interaction.columns||[])]:[];
  for(const key of ['template','description'])
    if(interaction?.[key]!==undefined)items.push({html:interaction[key],key});
  const media=await embedSourceImages(items.map(i=>i.html),mediaOptions);
  items.forEach((item,i)=>item.html=renderSourceHtml(media.html[i]).html);
  for(const item of items)if(item.key)interaction[item.key]=item.html;
  return {schema:1,dataset,code_options,interaction};
}
const mismatch=()=>{throw Error('Hyperskill 的题目内容已更新，请更新题库后再提交；本次尚未提交答案。');};
function permutation(from,to) {
  if(!Array.isArray(from)||!Array.isArray(to)||from.length!==to.length)mismatch();
  const used=new Set();
  return from.map(value=>{
    const index=to.findIndex((v,i)=>v===value&&!used.has(i));
    if(index<0)mismatch();used.add(index);return index;
  });
}
/** Preserve the learner's semantic answer when the live attempt shuffles items. */
export function remapAnswer(type,prepared,live,raw,options={}) {
  live=publicDataset(type,live);
  const original=answer(type,prepared,raw,options);
  if(hash(prepared)===hash(live))return original;
  let value;
  if(type==='choice') {
    if(prepared.is_multiple_choice!==live.is_multiple_choice)mismatch();
    const map=permutation(prepared.options,live.options),v=JSON.parse(raw);
    value={selected:Array.isArray(v.selected)?v.selected.map(i=>String(map[Number(i)])):String(map[Number(v.selected)])};
  } else if(type==='matching') {
    const left=permutation(prepared.pairs.map(p=>p.first),live.pairs?.map(p=>p.first));
    const right=permutation(prepared.pairs.map(p=>p.second),live.pairs?.map(p=>p.second));
    value=JSON.parse(raw).map(p=>({left:String(left[Number(p.left)]),right:String(right[Number(p.right)])}));
  } else if(type==='sorting'||type==='parsons') {
    const field=type==='sorting'?'options':'lines',map=permutation(prepared[field],live[field]);
    value=JSON.parse(raw).map(v=>type==='sorting'?map[v]:{...v,line_number:map[v.line_number]});
  } else if(type==='table') {
    if(prepared.is_checkbox!==live.is_checkbox||prepared.description!==live.description)mismatch();
    const rows=permutation(prepared.rows,live.rows),cols=permutation(prepared.columns,live.columns);
    const input=JSON.parse(raw);value=live.rows.map(()=>live.columns.map(()=>false));
    input.forEach((row,i)=>row.forEach((v,j)=>{value[rows[i]][cols[j]]=v;}));
  } else if(type==='fill-blanks') {
    if(prepared.components.length!==live.components?.length)mismatch();
    prepared.components.forEach((c,i)=>{
      const other=live.components[i];if(c.type!==other.type||c.text!==other.text)mismatch();
      if(c.type==='select')permutation(c.options,other.options);
    });
    value=JSON.parse(raw);
  } else mismatch();
  const remapped=answer(type,live,JSON.stringify(value),options);
  return {...remapped,display:original.display,semantic:original.semantic};
}
