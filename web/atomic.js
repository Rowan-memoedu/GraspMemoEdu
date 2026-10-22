import {t, translateMessage} from './i18n.js?v=c9b135ee2b396ff5';
import {questionInput, answerReady, choiceTypeField} from './question-input.js?v=c9b135ee2b396ff5';
import {selfAssessment, referenceAnswer, prepareAnswerContent, ratingChoices} from './self-assessment.js?v=c9b135ee2b396ff5';
import {reportableContent} from './content-report.js?v=c9b135ee2b396ff5';
import {createLearningCache} from './learning-cache.js?v=c9b135ee2b396ff5';
import {hyperskillQuestion,localHyperskill} from './hyperskill.js?v=c9b135ee2b396ff5';
import {enhanceSourceContent} from './topic-content.js?v=c9b135ee2b396ff5';
import {memoryChunk, chunkPeek} from './memory-chunk.js?v=c9b135ee2b396ff5';
import {topicProgress, topicHistory} from './learning-ui.js?v=c9b135ee2b396ff5';
import {pointAssessment} from './point-assessment.js?v=c9b135ee2b396ff5';
import {trainingAudio} from './training-audio.js?v=c9b135ee2b396ff5';

const node = (tag, cls = '', text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};
const button = (text, action, cls = 'secondaryButton') => {
  const el = node('button', cls, text); el.type = 'button'; el.addEventListener('click', action); return el;
};
const content = html => {
  const el = node('div', 'courseContent'); el.innerHTML = html || '';
  enhanceSourceContent(el); return el;
};

export function createAtomicView(bridge) {
  const cache = createLearningCache(() => state?.access || bridge.getAccess());
  const read = key => cache.read(sessionStorage, key);
  const write = (key, value) => cache.write(sessionStorage, key, value);
  const root = bridge.root;
  let state = null, topic = null, learner = null, epoch = 0, timer = null, busy = false, selected = null, failure = null;
  let academyLink = null;
  let audioControl = null;
  const active = ticket => ticket === epoch && topic && learner === bridge.getAccess()?.learner_id;
  const prefix = () => `graspmemoedu:atomic:${bridge.getApiOrigin()}:${learner}:${topic}:${state?.course_version}`;
  const draftKey = () => `${prefix()}:draft:${state.active_step_id}`;
  const submissionKey = () => `${prefix()}:submission`;
  const savedSubmission = () => { try { return JSON.parse(read(submissionKey())); } catch { return null; } };
  const hyperskillKey = () => `${prefix()}:hyperskill:${state.active_step_id}`;
  const hyperskillData = () => { try { return JSON.parse(read(hyperskillKey())); } catch { return null; } };
  const call = (path, options) => bridge.request(`${path}?topic_id=${encodeURIComponent(topic)}`, options);
  const payload = () => ({request_id: crypto.randomUUID(), expected_revision: state.revision,
    attempt_id: state.attempt_id, step_id: state.active_step_id});
  const can = feature => (state?.access || bridge.getAccess())?.features?.includes(feature);
  function stop() { audioControl?.dispose(); audioControl = null; epoch++; clearTimeout(timer); topic = null; state = null; busy = false; selected = null; failure = null; academyLink = null; }
  function notice(message) {
    failure = message;
    const target = root.querySelector('#atomicNotice');
    if (target) { target.textContent = message || ''; target.hidden = !message; }
  }
  function schedule() {
    clearTimeout(timer);
    if (topic) timer = setTimeout(refresh, state?.pending_submission_id ? 800 : 5000);
  }
  function accept(next, force = false) {
    if (state && next?.course_version === state.course_version && next.revision < state.revision) return;
    if (next?.unified_topic && next.training_mode !== 'atomic_retrieval'
        && next.topic_id === topic && next.learner_id === learner) {
      stop(); bridge.resumeTopic(next); return;
    }
    if (next?.training_mode !== 'atomic_retrieval' || next.topic_id !== topic || next.learner_id !== learner) throw new Error(t('review.invalidState'));
    if (state && next.revision < state.revision) return;
    const changed = !state || state.revision !== next.revision || JSON.stringify(state.access) !== JSON.stringify(next.access);
    const stepChanged = state?.active_step_id !== next.active_step_id;
    if (stepChanged) { selected = null; academyLink = null; }
    state = next;
    if (state.access) bridge.setAccess(state.access);
    if (state.phase !== 'answer' || state.pause) write(submissionKey(), null);
    if (changed || force) render();
    if (stepChanged) window.scrollTo(0, 0);
    schedule();
  }
  async function refresh() {
    if (!topic || busy || document.hidden) { schedule(); return; }
    const ticket = epoch;
    try {
      if(state.current?.hyperskill && (hyperskillData()?.pending||hyperskillData()?.ide?.watching)) {
        await hyperskillAction('status'); return;
      }
      const pending = state?.pending_submission_id || savedSubmission()?.request_id;
      const result = await call(pending ? `submissions/${encodeURIComponent(pending)}` : 'state');
      if (!active(ticket)) return;
      if (result.status === 'error') { write(submissionKey(), null); failure = result.reason; }
      accept(pending ? result.state : result);
      notice(failure);
      if (state?.phase === 'waiting' && state.next_available_at && !state.pause && !selected
          && state.can_learn && Date.parse(state.server_time) >= Date.parse(state.next_available_at)) await mutate('continue');
    } catch (error) {
      if (active(ticket)) notice(translateMessage(error.message));
    } finally { if (active(ticket)) schedule(); }
  }
  async function mutate(path, extras = {}) {
    if (busy) return;
    const ticket = epoch; busy = true; failure = null; render();
    try {
      const body = path === 'pause' ? {request_id: crypto.randomUUID()} : {...payload(), ...extras};
      const next = await call(path, {method: 'POST', body});
      if (active(ticket)) { busy = false; accept(next, true); bridge.progressChanged(); }
    } catch (error) {
      if (active(ticket)) { busy = false; render(); notice(translateMessage(error.message)); }
    } finally { if (active(ticket)) { busy = false; schedule(); } }
  }
  async function submit(input, rating = null) {
    if (busy || rating === null && !answerReady(input)) return;
    const ticket = epoch;
    const saved = savedSubmission();
    const body = saved?.step_id === state.active_step_id ? saved : {...payload(),
      answer: rating === null ? input.value : `自主评分：${rating}`, ...(rating === null ? {} : {self_rating: rating})};
    write(submissionKey(), JSON.stringify(body)); busy = true; failure = null; render();
    try {
      const result = await call('submit', {method: 'POST', body});
      if (active(ticket)) { busy = false; accept(result.state, true); bridge.progressChanged(); }
    } catch (error) {
      if (active(ticket)) {
        if (error.status && error.status < 500 && ![408, 429].includes(error.status)) write(submissionKey(), null);
        busy = false; render(); notice(translateMessage(error.message));
      }
    } finally { if (active(ticket)) { busy = false; schedule(); } }
  }
  function marked(html, block, cardId) {
    return reportableContent(content(html), {topic_id: topic, ...(cardId ? {question_id: cardId} : {})}, block, state.course_version);
  }
  async function launchAcademy() {
    if (busy) return;
    const ticket = epoch; busy = true; failure = null; render();
    try {
      const result = await call('academy/launch', {method: 'POST', body: payload()});
      if (!active(ticket)) return;
      if (!/^(?:graspmemoedu:\/\/academy|jetbrains:\/\/idea\/graspmemoedu)\/open\?ticket=[A-Za-z0-9_-]{40,100}$/.test(result.url)) throw new Error(t('review.invalidState'));
      academyLink = result.url; busy = false; accept(result.state, true);
      const link = root.querySelector('[data-academy-link]');
      link?.click();
    } catch (error) {
      if (active(ticket)) { busy = false; render(); notice(translateMessage(error.message)); }
    } finally { if (active(ticket)) { busy = false; schedule(); } }
  }
  async function hyperskillAction(action,answer) {
    if(busy)return;
    const ticket=epoch;busy=true;failure=null;render();
    try {
      let data=hyperskillData(), result;
      if(action==='open') {
        const grant=await call('academy/launch',{method:'POST',body:payload()});
        if(!active(ticket))return;
        accept(grant.state);
        result=await localHyperskill('open',{ticket:grant.ticket});
      } else {
        const requestId=data.request_id||crypto.randomUUID();
        if(action==='submit') {data.request_id=requestId;write(hyperskillKey(),JSON.stringify(data));}
        result=await localHyperskill(action,{session:data.session,attempt_id:data.attempt_id,
          ...(action==='submit'?{answer,request_id:requestId}:{})});
      }
      if(!active(ticket))return;
      write(hyperskillKey(),JSON.stringify(result));
      if(action==='retry')write(draftKey(),null);
      busy=false;
      const next=await call('state');
      if(active(ticket)){accept(next,true);bridge.progressChanged();}
    } catch(error) {
      if(active(ticket)){
        if(action==='submit') {
          const saved=hyperskillData();
          if(saved)write(hyperskillKey(),JSON.stringify({...saved,pending:true,message:'提交状态尚未确认，请先刷新结果。'}));
        }
        busy=false;render();notice(error.message);
      }
    } finally {if(active(ticket)){busy=false;schedule();}}
  }
  function academyQuestion(box, question, history) {
    box.classList.add('academyQuestion');
    const disabled = history || busy || Boolean(state.pause) || !state.can_submit;
    if (question.phase === 'feedback') {
      const feedback = node('div', 'feedback');
      feedback.append(node('strong', '', t('academy.recorded')), node('p', '', question.reason || ''));
      box.append(feedback);
      if (!history && !state.pause) box.append(button(t('atomic.continue'), () => mutate('continue'), 'primaryButton'));
      return box;
    }
    const open = button(t(busy ? 'academy.launching' : question.academy.round_id ? 'academy.resume' : 'academy.open'), launchAcademy, 'primaryButton');
    open.disabled = disabled; box.append(open, node('p', 'inputHint', t('academy.reset')));
    if (academyLink && !history) {
      const link = node('a', 'secondaryButton', t('academy.retry'));
      link.href = academyLink; link.dataset.academyLink = ''; link.rel = 'noreferrer';
      box.append(node('p', 'inputHint', t('academy.connection')), link);
    }
    const status = node('p', 'inputHint', t(question.academy.checks ? 'academy.checks' : 'academy.waiting', {count: question.academy.checks}));
    status.setAttribute('role', 'status');
    if (question.academy.round_id) box.append(status);
    if (question.academy.kind === 'manual') {
      box.append(node('p', '', t('academy.manual')), ratingChoices({
        labels: ['training.again', 'training.hard', 'training.good', 'training.easy'],
        disabled: disabled || !question.academy.opened,
        onRate: rating => mutate('academy/finish', {rating}),
      }));
    } else if (question.academy.checks && !history) {
      const finish = button(t('academy.finish'), () => mutate('academy/finish'));
      finish.disabled = disabled; box.append(finish);
    }
    return box;
  }
  function renderQuestion(question, history = false) {
    const box = node('section', 'atomicQuestion'); box.dataset.cardId = question.card_id;
    box.append(node('h2', '', question.title));
    const stem = marked(question.html, `question:${question.card_id}`, question.card_id);
    const typeField = choiceTypeField(question.interaction); if (typeField) box.append(typeField);
    box.append(stem);
    if(question.hyperskill)return hyperskillQuestion(box,{question,history,
      disabled:history||busy||Boolean(state.pause)||!state.can_submit,
      data:history?null:hyperskillData(),draft:history?'':read(draftKey())||'',
      onDraft:value=>write(draftKey(),value),action:hyperskillAction,
      finish:()=>mutate('academy/finish'),next:()=>mutate('continue')});
    if (question.academy) return academyQuestion(box, question, history);
    const form = node('form', 'atomicAnswerForm'); form.id = 'atomicAnswerForm';
    const disabled = history || question.phase !== 'answer' || busy || Boolean(state.pause || state.pending_submission_id) || !state.can_submit;
    let input;
    const value = history || question.phase === 'feedback' ? question.answer || '' : read(draftKey()) || '';
    if (question.interaction && question.interaction.type !== 'text') {
      const widget = questionInput(question.interaction, {id: 'atomicAnswer', value, stem, disabled, formId: form.id});
      input = widget.input; form.append(widget.element);
    } else {
      const label = node('label', '', t('atomic.answer')); label.htmlFor = 'atomicAnswer';
      input = node('textarea', 'answerInput'); input.id = 'atomicAnswer'; input.maxLength = 2000;
      input.value = value; input.disabled = disabled; input.rows = 3;
      form.append(label, input);
    }
    const send = node('button', 'primaryButton', t(state.pending_submission_id ? 'atomic.judging' : 'atomic.submit'));
    send.type = 'submit'; send.disabled = disabled || !answerReady(input);
    input.addEventListener('input', () => { if (!history) write(draftKey(), input.value); send.disabled = disabled || !answerReady(input); });
    input.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) { event.preventDefault(); form.requestSubmit(); }
    });
    form.addEventListener('submit', event => { event.preventDefault(); if (!disabled) void submit(input); });
    if (!history && question.phase === 'answer') form.append(send);
    box.append(form);
    audioControl = trainingAudio(question, {request: bridge.request, contentVersion: state.course_version, disabled,
      ...(!disabled && (!question.interaction || question.interaction.type === 'text') ? {
        onTranscript: text => { input.value = text; input.dispatchEvent(new Event('input', {bubbles: true})); },
      } : {})});
    if (audioControl) box.insertBefore(audioControl, form);
    if (!history && question.phase === 'answer') {
      const controls = selfAssessment(question, input, {disabled,
        reveal: () => mutate('reveal'), rate: rating => submit(input, rating),
        explanation: question.revealed ? marked(question.explanation_html, `explanation:${question.card_id}`, question.card_id) : null});
      if (controls) box.append(controls);
      if (question.revealed) { send.hidden = true; if (input.questionControl) input.questionControl.setDisabled(true); else input.disabled = true; }
    }
    if (!history && question.phase === 'answer' && question.interaction?.type === 'choice') {
      box.append(node('p', 'inputHint', t('question.choiceHint')));
    }
    if (!history && question.phase === 'answer' && question.interaction?.grading) {
      box.append(node('p', 'inputHint', t(question.interaction.grading === 'exact' ? 'question.exactHint' : 'question.semanticHint')));
    }
    if (question.phase === 'feedback') {
      const feedback = node('div', `feedback atomicFeedback ${question.correct ? 'correct' : 'incorrect'}`);
      feedback.append(node('strong', '', t(question.correct ? 'atomic.correct' : 'atomic.incorrect')));
      if (question.reason) feedback.append(node('p', '', question.reason));
      const points = pointAssessment(question.assessment_result); if (points) feedback.append(points);
      const reference = referenceAnswer(question); if (reference) feedback.append(reference);
      feedback.append(prepareAnswerContent(marked(question.explanation_html, `explanation:${question.card_id}`, question.card_id)));
      box.append(feedback);
      if (!history && !state.pause) box.append(button(t('atomic.continue'), () => mutate('continue'), 'primaryButton'));
    }
    if (!history) {
      const peek = chunkPeek(question.memory_chunks, {topicId: topic, version: state.course_version});
      if (peek) box.append(peek);
    }
    return box;
  }
  function renderChunk(chunk) {
    const box = memoryChunk(chunk, {topicId: topic, version: state.course_version});
    const footer = node('footer', 'atomicChunkFooter');
    footer.append(node('p', 'inputHint', t('atomic.startCardsHint')),
      button(t('atomic.startCards'), () => mutate('continue'), 'primaryButton'));
    box.append(footer);
    return box;
  }
  function render() {
    if (!state) return;
    audioControl?.dispose(); audioControl = null;
    const style = document.getElementById('atomicMathStyle') || node('style'); style.id = 'atomicMathStyle';
    style.textContent = state.math_css || ''; if (!style.isConnected) document.head.append(style);
    const page = node('div', 'atomicLayout'), sidebar = node('aside', 'atomicHistory'), main = node('main', 'atomicMain');
    if (state.chunk && !selected) page.classList.add('atomicReadingLayout');
    if (state.current) {
      selected = null;
      sidebar.hidden = true;
      page.classList.add('atomicAnswerLayout');
    }
    sidebar.append(node('h2', '', t('history.title')));
    const cardRow = record => {
      const row = button(record.title || t('atomic.cardNumber', {number: state.history.indexOf(record) + 1}), () => { selected = record.id; render(); });
      row.disabled = !record.viewable; row.dataset.historyId = record.id;
      row.append(node('span', record.mastered ? 'atomicMastered' : 'atomicUnmastered', t(record.mastered ? 'atomic.mastered' : 'atomic.unmastered')));
      return row;
    };
    sidebar.append(...topicHistory(state.learning_sections, {
      records: state.history, currentIntroduction: state.introduction_id || state.learning_sections?.[0]?.id,
      currentChunk: state.chunk?.id, card: cardRow,
      introduction: section => {
        const previous = state.unified_history?.find(item => item.introduction_id === section.id);
        const current = section.id === (state.introduction_id || state.learning_sections?.[0]?.id);
        const row = button(section.title.replace(/^Introduction(?=\s*[:：]|$)/i, t('Introduction')),
          () => { selected = current ? 'introduction' : previous.id; render(); });
        row.classList.add('topicHistoryIntroduction');
        row.disabled = current ? !state.introduction : !previous;
        if (previous) row.dataset.historyId = previous.id;
        return row;
      }
    }));
    const back = node('a', 'subjectBack', bridge.homeLabel?.() || t('nav.backHome')); back.href = bridge.homeHref(); main.append(back);
    main.append(node('p', 'eyebrow', t(state.mode === 'review' ? 'atomic.reviewTitle' : 'atomic.title')),
      node('h1', 'lessonTitle', state.title), topicProgress(state.topic_progress));
    const status = node('p', 'fieldError', failure || ''); status.id = 'atomicNotice'; status.hidden = !failure; status.setAttribute('role', 'status'); main.append(status);
    if (selected) {
      const previous = selected === 'introduction' ? null : state.history.find(r => r.id === selected && r.viewable);
      const section = state.unified_history?.find(item => item.id === selected);
      if (selected === 'introduction' && state.introduction) main.append(marked(state.introduction.html, 'introduction'));
      else if (previous) main.append(renderQuestion(previous, true));
      else if (section) main.append(node('h2', '', section.title), content(section.html));
      else selected = null;
      if (selected) {
        const back = button(t('atomic.backCurrent'), () => { selected = null; render(); });
        back.dataset.readOnly = 'true'; main.append(back);
      }
    }
    if (!selected) {
      if (state.phase === 'introduction') {
        main.append(marked(state.introduction.html, 'introduction'), node('p', 'atomicIntroPrompt', t('atomic.introPrompt')),
          button(t('atomic.understood'), () => mutate('choose', {choice: 'learn'}), 'primaryButton'));
      } else if (state.chunk) main.append(renderChunk(state.chunk));
      else if (state.current) main.append(renderQuestion(state.current));
      else if (state.phase === 'completed') {
        main.append(node('p', 'atomicComplete', t(state.mode === 'review' ? 'atomic.reviewCompleted' : 'atomic.completed')));
        const review = node('a', 'primaryButton', t('atomic.reviewTitle')); review.href = bridge.reviewHref(); main.append(review);
      } else main.append(node('p', 'atomicWaiting', t('atomic.waiting')),
        ...(state.next_available_at ? [node('p', '', t('atomic.availableAt', {time: bridge.formatDate(state.next_available_at)}))] : []),
        button(t('atomic.refresh'), () => mutate('continue')));
    }
    if (state.pause) main.append(node('p', 'atomicPaused', t('atomic.paused')));
    for (const control of main.querySelectorAll('button')) {
      if (control.dataset.readOnly || control.dataset.helpAction) continue;
      if (busy || state.pause || state.pending_submission_id || !state.can_learn) control.disabled = true;
    }
    page.append(sidebar, main); root.replaceChildren(page);
  }
  async function open(topicId, mode = 'learn', initial = null, cardId = null) {
    stop(); topic = topicId; learner = bridge.getAccess()?.learner_id;
    const ticket = epoch;
    const next = cardId ? await call('materials/open', {method: 'POST', body: {request_id: crypto.randomUUID(), kind: 'atomic_card', id: cardId}})
      : initial || await call('state');
    if (!active(ticket)) return;
    accept(next, true);
    if (!state) return;
    if (mode === 'review' && state.mode !== 'review' && state.completed_at && !state.pause) await mutate('choose', {choice: 'review'});
    else if (mode === 'review' && state.mode === 'review' && state.phase === 'waiting') await mutate('continue');
    if (state.pending_submission_id || savedSubmission()) await refresh();
  }
  return {open, stop, reset() { stop(); cache.clear(); }};
}
