import {questionInput,answerReady,choiceTypeField} from './question-input.js?v=7c6cc30f772c71d9';
import {enhanceSourceContent} from './topic-content.js?v=7c6cc30f772c71d9';

export async function localHyperskill(action,body,{timeout=120000}={}) {
  let response;
  try {
    response=await fetch(`http://127.0.0.1:18769/${action}`,{method:'POST',
      headers:{'Content-Type':'application/json','X-Grasp-Local':'1'},
      body:JSON.stringify(body),signal:AbortSignal.timeout(timeout)});
  } catch {
    throw Error('尚未连接本机 Hyperskill 服务。请确认服务已启动，并允许网站访问本地网络。');
  }
  const value=await response.json();
  if(!response.ok)throw Error(value.error||'Hyperskill 连接失败。');
  return value;
}
const node=(tag,cls,text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
const parsed=value=>{try{return JSON.parse(value);}catch{return null;}};
const rich=html=>{const e=node('div','courseContent');e.innerHTML=html||'';enhanceSourceContent(e);return e;};
export function hyperskillInput(spec,{value='',disabled=false}={}) {
  const element=node('div','questionInteraction hyperskillInteraction'),input=node('textarea','structuredAnswer');
  input.hidden=true;input.disabled=disabled;element.append(input);
  let current=parsed(value),complete=()=>true;
  const emit=()=>{input.value=JSON.stringify(current);input.dispatchEvent(new Event('input',{bubbles:true}));};
  const button=(text,title,fn,off=false)=>{
    const b=node('button','matchingMove',text);b.type='button';b.title=title;b.setAttribute('aria-label',title);
    b.disabled=disabled||off;b.addEventListener('click',fn);return b;
  };
  if(spec.type==='hyperskill_fill') {
    if(!Array.isArray(current)||current.length!==spec.fields.length)current=spec.fields.map(()=>'');
    const body=rich(spec.template),walker=document.createTreeWalker(body,NodeFilter.SHOW_TEXT),targets=[];
    while(walker.nextNode())if(walker.currentNode.textContent.includes('▭'))targets.push(walker.currentNode);
    let index=0;
    for(const target of targets) {
      const parts=target.textContent.split('▭'),fragment=document.createDocumentFragment();
      parts.forEach((part,i)=>{
        fragment.append(document.createTextNode(part));if(i===parts.length-1)return;
        const number=index++,field=spec.fields[number],control=node(field.type==='select'?'select':'input','inlineBlank');
        control.setAttribute('aria-label',`第 ${number+1} 空`);control.disabled=disabled;
        if(field.type==='select') {
          const empty=node('option','',`第 ${number+1} 空`);empty.value='';control.append(empty);
          for(const option of field.options) {
            const o=node('option','',option);o.value=option;control.append(o);
          }
        } else {control.type='text';control.autocomplete='off';control.spellcheck=false;}
        control.value=current[number]||'';
        control.addEventListener('input',()=>{current[number]=control.value;emit();});
        fragment.append(control);
      });
      target.replaceWith(fragment);
    }
    complete=()=>index===spec.fields.length&&current.every(v=>typeof v==='string'&&v.trim());
    element.append(body);
  } else if(['hyperskill_sorting','hyperskill_parsons'].includes(spec.type)) {
    const parsons=spec.type==='hyperskill_parsons';
    if(!Array.isArray(current)||current.length!==spec.items.length)current=spec.items.map((_,i)=>parsons?{line_number:i,level:0}:i);
    const list=node('ol','hyperskillOrdering');element.append(node('p','inputHint',parsons?'调整代码行顺序与缩进。':'调整各项顺序。'),list);
    const draw=()=>{
      list.replaceChildren();
      current.forEach((entry,index)=>{
        const n=parsons?entry.line_number:entry,row=node('li','hyperskillOrderRow'),body=rich(spec.items[n]?.html);
        if(parsons) {body.classList.add('hyperskillCodeLine');body.style.paddingLeft=`${entry.level*1.1}em`;}
        const controls=node('div','hyperskillOrderControls');
        for(const [delta,label,title] of [[-1,'↑','上移'],[1,'↓','下移']])
          controls.append(button(label,`第 ${index+1} 行${title}`,()=>{
            [current[index],current[index+delta]]=[current[index+delta],current[index]];
            emit();draw();list.children[index+delta]?.querySelector('button:not(:disabled)')?.focus();
          },index+delta<0||index+delta>=current.length));
        if(parsons)for(const [delta,label,title] of [[-1,'←','减少缩进'],[1,'→','增加缩进']])
          controls.append(button(label,`第 ${index+1} 行${title}`,()=>{entry.level+=delta;emit();draw();},
            entry.level+delta<0||entry.level+delta>10));
        row.append(body,controls);list.append(row);
      });
    };draw();
  } else if(spec.type==='hyperskill_table') {
    if(!Array.isArray(current)||current.length!==spec.rows.length)current=spec.rows.map(()=>spec.columns.map(()=>false));
    if(spec.description)element.append(rich(spec.description));
    const frame=node('div','sourceTable'),table=node('table','hyperskillTable');
    const head=node('thead'),header=node('tr');header.append(node('th'));
    spec.columns.forEach(c=>{const th=node('th');th.scope='col';th.append(rich(c.html));header.append(th);});
    head.append(header);table.append(head);
    const tbody=node('tbody');
    spec.rows.forEach((row,i)=>{
      const tr=node('tr'),th=node('th');th.scope='row';th.append(rich(row.html));tr.append(th);
      spec.columns.forEach((column,j)=>{
        const td=node('td'),c=node('input');c.type=spec.multiple?'checkbox':'radio';c.name=`hyperskill-row-${i}`;
        c.checked=!!current[i]?.[j];c.disabled=disabled;c.setAttribute('aria-label',`${row.text}：${column.text}`);
        c.addEventListener('change',()=>{
          if(spec.multiple)current[i][j]=c.checked;else current[i]=spec.columns.map((_,k)=>k===j);
          emit();
        });td.append(c);tr.append(td);
      });tbody.append(tr);
    });
    table.append(tbody);frame.append(table);element.append(frame);
    complete=()=>spec.multiple||current.every(row=>row.filter(Boolean).length===1);
  } else if(spec.type==='hyperskill_code') {
    const languages=Object.keys(spec.templates);
    if(!current||!languages.includes(current.language)||typeof current.code!=='string')
      current={language:languages[0],code:spec.templates[languages[0]]};
    const label=node('label','','编程语言'),select=node('select','inlineBlank');select.disabled=disabled;
    for(const language of languages){const o=node('option','',language);o.value=language;select.append(o);}
    select.value=current.language;label.append(select);
    const editor=node('textarea','answerInput hyperskillCodeEditor');editor.rows=16;editor.spellcheck=false;
    const limits=node('p','inputHint');
    const showLimits=()=>{
      const limit=spec.limits?.[current.language];
      limits.textContent=limit?`时间限制：${limit.time} 秒 · 内存限制：${limit.memory} MiB`:'';
      limits.hidden=!limit;
    };
    editor.autocomplete='off';editor.setAttribute('aria-label','程序代码');editor.disabled=disabled;editor.value=current.code;
    editor.addEventListener('input',()=>{current.code=editor.value;emit();});
    select.addEventListener('change',()=>{
      // Language changes keep the learner's code; they never replace an edited draft.
      current.language=select.value;showLimits();emit();
    });
    showLimits();element.append(label,limits,editor);
    complete=()=>!!current.code.trim();
  } else throw Error('练习控件尚未加载。');
  input.questionControl={complete,empty:()=>false};emit();
  return {element,input};
}
export function hyperskillQuestion(box,{question,history,disabled,data,draft,onDraft,action,finish,next}) {
  const button=(text,fn,primary=false)=>{
    const b=node('button',primary?'primaryButton':'secondaryButton',text);b.type='button';
    b.disabled=disabled;b.addEventListener('click',fn);return b;
  };
  const records=question.hyperskill.submissions||[];
  if(records.length) {
    const list=node('ol','hyperskillReceipts');
    records.forEach(r=>{
      const li=node('li','',`${r.status==='correct'?'通过':'未通过'} · 提交 ${r.submission_id}`);
      if(r.feedback)li.append(node('p','inputHint',r.feedback));
      list.append(li);
    });
    box.append(list);
  }
  if(question.phase==='feedback') {
    if(question.answer)box.append(node('pre','answerInput',question.answer));
    box.append(node('p','feedback',question.reason));
    if(!history)box.append(button('继续',next,true));
    return box;
  }
  if(history)return box;
  if(!data) {
    box.append(button('连接 Hyperskill 并加载练习',()=>action('open'),true));
    box.append(node('p','inputHint','在此作答，由 Hyperskill 判题；结果同时记入本网站复习记录。'));
    return box;
  }
  if(data.connection) {
    const status=node('p','inputHint hyperskillConnection',data.connection);
    status.setAttribute('role','status');box.append(status);
  }
  if(data.pending) {
    const status=node('p','inputHint',data.message);status.setAttribute('role','status');box.append(status);
    box.append(button('刷新判题结果',()=>action('status')));
    box.append(button('重新连接',()=>action('open')));
    return box;
  }
  if(question.hyperskill.type==='code') {
    if(data.last?.status==='correct') {
      box.append(node('p','inputHint','已通过，正在同步学习记录。'));
      return box;
    }
    box.append(button(data.ide?.opened?'在 IDEA 中继续':'在 IDEA 中解题',()=>action('ide'),true));
    const status=node('p','inputHint',data.ide?.watching?
      data.ide.submitting?'Hyperskill 正在判题，结果将自动显示在这里。':
        '请在 IDEA 的 Hyperskill 插件中点击 Check；结果会自动同步到这里。':
      '通过 Hyperskill 官方插件打开本题，在 IDEA 中编写代码并 Check。');
    status.setAttribute('role','status');box.append(status);
    if(data.last?.status==='wrong') {
      box.append(node('p','inputHint','尚未通过，可在 IDEA 中修改后再次 Check。'),
        button('结束本轮并记为未掌握',finish));
    }
    return box;
  }
  if(data.last?.status==='wrong') {
    box.append(button('再次尝试',()=>action('retry'),true),button('结束本轮并记为未掌握',finish));
    return box;
  }
  if(data.last?.status==='correct') {
    box.append(node('p','inputHint','已通过，正在同步学习记录。'));
    return box;
  }
  const form=node('form','atomicAnswerForm');form.id='hyperskillAnswerForm';
  let input;
  if(data.interaction) {
    const type=choiceTypeField(data.interaction);if(type)form.append(type);
    const widget=data.interaction.type.startsWith('hyperskill_')?
      hyperskillInput(data.interaction,{value:draft,disabled}):
      questionInput(data.interaction,{id:'hyperskillAnswer',value:draft,disabled,formId:form.id});
    input=widget.input;form.append(widget.element);
  } else {
    const label=node('label','','你的答案');label.htmlFor='hyperskillAnswer';
    input=node('textarea','answerInput');input.id='hyperskillAnswer';input.rows=3;input.maxLength=8000;
    input.value=draft;input.disabled=disabled;form.append(label,input);
  }
  const send=node('button','primaryButton',data.local?'提交答案':'提交至 Hyperskill');
  send.dataset.hyperskillSubmit='';send.type='submit';send.disabled=disabled||!answerReady(input);
  input.addEventListener('input',()=>{onDraft(input.value);send.disabled=disabled||!answerReady(input);});
  form.addEventListener('submit',event=>{event.preventDefault();if(!disabled&&answerReady(input))action('submit',input.value);});
  form.append(send);box.append(form);return box;
}
