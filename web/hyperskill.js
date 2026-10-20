import {questionInput,answerReady,choiceTypeField} from './question-input.js?v=262b13fc445222cb';

export async function localHyperskill(action,body) {
  let response;
  try {
    response=await fetch(`http://127.0.0.1:18769/${action}`,{method:'POST',
      headers:{'Content-Type':'application/json','X-Grasp-Local':'1'},
      body:JSON.stringify(body),signal:AbortSignal.timeout(120000)});
  } catch {
    throw Error('尚未连接本机 Hyperskill 服务。请确认服务已启动，并允许网站访问本地网络。');
  }
  const value=await response.json();
  if(!response.ok)throw Error(value.error||'Hyperskill 连接失败。');
  return value;
}
const node=(tag,cls,text)=>{const e=document.createElement(tag);e.className=cls;if(text!==undefined)e.textContent=text;return e;};
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
  if(data.pending) {
    const status=node('p','inputHint',data.message);status.setAttribute('role','status');box.append(status);
    box.append(button('刷新判题结果',()=>action('status')));
    box.append(button('重新连接',()=>action('open')));
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
    const widget=questionInput(data.interaction,{id:'hyperskillAnswer',value:draft,disabled,formId:form.id});
    input=widget.input;form.append(widget.element);
  } else {
    const label=node('label','','你的答案');label.htmlFor='hyperskillAnswer';
    input=node('textarea','answerInput');input.id='hyperskillAnswer';input.rows=3;input.maxLength=8000;
    input.value=draft;input.disabled=disabled;form.append(label,input);
  }
  const send=node('button','primaryButton','提交至 Hyperskill');send.type='submit';send.disabled=disabled||!answerReady(input);
  input.addEventListener('input',()=>{onDraft(input.value);send.disabled=disabled||!answerReady(input);});
  form.addEventListener('submit',event=>{event.preventDefault();if(!disabled&&answerReady(input))action('submit',input.value);});
  form.append(send);box.append(form);return box;
}
