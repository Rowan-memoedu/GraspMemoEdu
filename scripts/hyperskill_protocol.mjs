/** Dataset-bound answers. No locally invented answer key or verdict. */
import {createHash} from 'node:crypto';
import {renderSourceHtml,sourcePlainText} from './render_mathacademy.mjs';

export const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fail = message => { throw Error(message); };
export const supportedTypes=['choice','matching','number','string','fill-blanks','sorting','parsons','table','code'];
export function presentation(type, dataset, render=renderSourceHtml, codeOptions={}) {
  const item = (text, id) => ({id:String(id), text:String(text), html:render(String(text)).html});
  if (type === 'choice') {
    if (!Array.isArray(dataset?.options) || !dataset.options.length) fail('练习未返回选项。');
    return {version:1,type:'choice',multiple:Boolean(dataset.is_multiple_choice),
      options:dataset.options.map((s,i)=>item(s,i))};
  }
  if (type === 'matching') {
    if (!Array.isArray(dataset?.pairs) || !dataset.pairs.length) fail('练习未返回配对内容。');
    return {version:1,type:'matching',direction:'forward',fixed_side:'left',
      fixed:dataset.pairs.map((_,i)=>String(i)),row_count:dataset.pairs.length,
      left:dataset.pairs.map((p,i)=>item(p.first,i)),right:dataset.pairs.map((p,i)=>item(p.second,i))};
  }
  if(type==='fill-blanks') {
    const [template,...fields]=dataset?.components||[];
    if(template?.type!=='text'||!fields.length||fields.some(f=>!['input','select'].includes(f.type))||
      (template.text.match(/▭/g)||[]).length!==fields.length)fail('填空题结构不完整。');
    return {type:'hyperskill_fill',template:render(template.text).html,
      fields:fields.map(f=>({type:f.type,options:f.options||[]}))};
  }
  if(type==='sorting'||type==='parsons') {
    const values=type==='sorting'?dataset?.options:dataset?.lines;
    if(!Array.isArray(values)||!values.length)fail('排序题缺少内容。');
    return {type:`hyperskill_${type}`,items:values.map((v,i)=>item(v,i))};
  }
  if(type==='table') {
    if(!dataset?.rows?.length||!dataset?.columns?.length)fail('表格题缺少行列。');
    return {type:'hyperskill_table',multiple:!!dataset.is_checkbox,description:render(dataset.description||'').html,
      rows:dataset.rows.map((v,i)=>item(v,i)),columns:dataset.columns.map((v,i)=>item(v,i))};
  }
  if(type==='code') {
    const templates=codeOptions.code_templates;
    if(!templates||!Object.keys(templates).length)fail('代码题尚未返回语言模板。');
    return {type:'hyperskill_code',templates,samples:codeOptions.samples||[],limits:codeOptions.limits||{}};
  }
  if (!['number','string'].includes(type)) fail('暂不支持此题型。');
  return null;
}
export function answer(type, dataset, raw, codeOptions={}) {
  if (typeof raw !== 'string' || !raw.trim() || raw.length > 8000) fail('请输入有效答案。');
  if (type === 'number') {
    if (!Number.isFinite(Number(raw))) fail('请输入有限数值。');
    return {reply:{number:raw.trim()},display:raw.trim(),semantic:raw.trim()};
  }
  if (type === 'string') return {reply:{files:[],text:raw},display:raw,semantic:raw};
  const spec = presentation(type,dataset,()=>({html:''}),codeOptions);
  let value; try { value = JSON.parse(raw); } catch { fail('答案格式不正确。'); }
  if(type==='code') {
    if(!value||!Object.hasOwn(spec.templates,value.language)||typeof value.code!=='string'||
      !value.code.trim()||value.code.length>7800)fail('请选择有效语言并填写代码。');
    const code=value.code.replace(/\n?$/,'\n');
    return {reply:{language:value.language,code},display:code,semantic:{language:value.language,code}};
  }
  if(type==='fill-blanks') {
    if(!Array.isArray(value)||value.length!==spec.fields.length||
      value.some((v,i)=>typeof v!=='string'||!v.trim()||
        spec.fields[i].type==='select'&&!spec.fields[i].options.includes(v)))fail('请完成所有空格。');
    return {reply:{blanks:value},display:value.map((v,i)=>`${i+1}. ${v}`).join('\n'),semantic:value};
  }
  if(type==='sorting'||type==='parsons') {
    const indices=Array.isArray(value)?value.map(v=>type==='sorting'?v:v?.line_number):[];
    if(indices.length!==spec.items.length||new Set(indices).size!==indices.length||
      indices.some(v=>!Number.isInteger(v)||v<0||v>=spec.items.length)||
      type==='parsons'&&value.some(v=>!Number.isInteger(v.level)||v.level<0||v.level>10))fail('排序或缩进格式不正确。');
    const lines=indices.map((n,i)=>`${type==='parsons'?'  '.repeat(value[i].level):''}${sourcePlainText(spec.items[n].text)}`);
    return {reply:type==='sorting'?{ordering:value}:{lines:value},display:lines.join('\n'),semantic:lines};
  }
  if(type==='table') {
    if(!Array.isArray(value)||value.length!==spec.rows.length||
      value.some(row=>!Array.isArray(row)||row.length!==spec.columns.length||row.some(v=>typeof v!=='boolean')||
        !spec.multiple&&row.filter(Boolean).length!==1))fail('请完成每一行的分类。');
    const choices=dataset.rows.map((name_row,i)=>({name_row,
      columns:dataset.columns.map((name,j)=>({name,answer:value[i][j]}))}));
    const display=choices.map(row=>`${sourcePlainText(row.name_row)} → ${row.columns.filter(c=>c.answer).map(c=>sourcePlainText(c.name)).join(', ')}`);
    return {reply:{choices},display:display.join('\n'),semantic:display};
  }
  if (type === 'choice') {
    const selected = spec.multiple ? value.selected : [value.selected];
    if (!Array.isArray(selected) || !selected.length || new Set(selected).size !== selected.length ||
        selected.some(id=>!spec.options.some(o=>o.id===id))) fail('选项与当前练习不一致。');
    const texts = spec.options.filter(o=>selected.includes(o.id)).map(o=>o.text);
    return {reply:{choices:spec.options.map(o=>selected.includes(o.id))},display:texts.map(sourcePlainText).join('\n'),semantic:[...texts].sort()};
  }
  if (!Array.isArray(value) || value.length!==spec.row_count ||
      new Set(value.map(p=>p.left)).size!==spec.row_count || new Set(value.map(p=>p.right)).size!==spec.row_count ||
      value.some(p=>!spec.left.some(x=>x.id===p.left)||!spec.right.some(x=>x.id===p.right))) fail('请完成所有配对。');
  const ordering = spec.left.map(l=>Number(value.find(p=>p.left===l.id).right));
  const pairs = spec.left.map((l,i)=>[l.text,spec.right[ordering[i]].text]);
  return {reply:{ordering},display:pairs.map(p=>p.map(sourcePlainText).join(' → ')).join('\n'),semantic:pairs.sort((a,b)=>a[0].localeCompare(b[0]))};
}
