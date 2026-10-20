/** Dataset-bound answers. No locally invented answer key or verdict. */
import {createHash} from 'node:crypto';
import {renderSourceHtml} from './render_mathacademy.mjs';

export const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fail = message => { throw Error(message); };
export function presentation(type, dataset) {
  const item = (text, id) => ({id:String(id), text:String(text), html:renderSourceHtml(String(text)).html});
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
  if (!['number','string'].includes(type)) fail('暂不支持此题型。');
  return null;
}
export function answer(type, dataset, raw) {
  if (typeof raw !== 'string' || !raw.trim() || raw.length > 8000) fail('请输入有效答案。');
  if (type === 'number') {
    if (!Number.isFinite(Number(raw))) fail('请输入有限数值。');
    return {reply:{number:raw.trim()},display:raw.trim(),semantic:raw.trim()};
  }
  if (type === 'string') return {reply:{files:[],text:raw},display:raw,semantic:raw};
  const spec = presentation(type,dataset);
  let value; try { value = JSON.parse(raw); } catch { fail('答案格式不正确。'); }
  if (type === 'choice') {
    const selected = spec.multiple ? value.selected : [value.selected];
    if (!Array.isArray(selected) || !selected.length || new Set(selected).size !== selected.length ||
        selected.some(id=>!spec.options.some(o=>o.id===id))) fail('选项与当前练习不一致。');
    const texts = spec.options.filter(o=>selected.includes(o.id)).map(o=>o.text);
    return {reply:{choices:spec.options.map(o=>selected.includes(o.id))},display:texts.join('\n'),semantic:[...texts].sort()};
  }
  if (!Array.isArray(value) || value.length!==spec.row_count ||
      new Set(value.map(p=>p.left)).size!==spec.row_count || new Set(value.map(p=>p.right)).size!==spec.row_count ||
      value.some(p=>!spec.left.some(x=>x.id===p.left)||!spec.right.some(x=>x.id===p.right))) fail('请完成所有配对。');
  const ordering = spec.left.map(l=>Number(value.find(p=>p.left===l.id).right));
  const pairs = spec.left.map((l,i)=>[l.text,spec.right[ordering[i]].text]);
  return {reply:{ordering},display:pairs.map(p=>p.join(' → ')).join('\n'),semantic:pairs.sort((a,b)=>a[0].localeCompare(b[0]))};
}
