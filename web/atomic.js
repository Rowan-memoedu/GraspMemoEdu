import {t, translateMessage} from './i18n.js?v=c9b4ecad728ac974';
import {questionInput, answerReady} from './question-input.js?v=c9b4ecad728ac974';
import {reportableContent} from './content-report.js?v=c9b4ecad728ac974';
import {createLearningCache} from './learning-cache.js?v=c9b4ecad728ac974';

const node = (tag, cls = '', text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};
const button = (text, action, cls = 'secondaryButton') => {
  const el = node('button', cls, text); el.type = 'button'; el.addEventListener('click', action); return el;
};
const content = html => { const el = node('div', 'courseContent'); el.innerHTML = html || ''; return el; };

export function createAtomicView(bridge) {
  const cache = createLearningCache(() => state?.access || bridge.getAccess());
  const read = key => cache.read(sessionStorage, key);
  const write = (key, value) => cache.write(sessionStorage, key, value);
  const root = bridge.root;
  let state = null, topic = null, learner = null, epoch = 0, timer = null, busy = false, selected = null, failure = null;
  const active = ticket => ticket === epoch && topic && learner === bridge.getAccess()?.learner_id;
  const prefix = () => `graspmemoedu:atomic:${bridge.getApiOrigin()}:${learner}:${topic}:${state?.course_version}`;
  const draftKey = () => `${prefix()}:draft:${state.active_step_id}`;
  const submissionKey = () => `${prefix()}:submission`;
  const savedSubmission = () => { try { return JSON.parse(read(submissionKey())); } catch { return null; } };
  const call = (path, options) => bridge.request(`${path}?topic_id=${encodeURIComponent(topic)}`, options);
  const payload = () => ({request_id: crypto.randomUUID(), expected_revision: state.revision,
    attempt_id: state.attempt_id, step_id: state.active_step_id});
  const can = feature => (state?.access || bridge.getAccess())?.features?.includes(feature);
  function stop() { epoch++; clearTimeout(timer); topic = null; state = null; busy = false; selected = null; failure = null; }
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
    if (next?.training_mode !== 'atomic_retrieval' || next.topic_id !== topic || next.learner_id !== learner) throw new Error(t('review.invalidState'));
    if (state && next.revision < state.revision) return;
    const changed = !state || state.revision !== next.revision || JSON.stringify(state.access) !== JSON.stringify(next.access);
    if (state?.active_step_id !== next.active_step_id) selected = null;
    state = next;
    if (state.access) bridge.setAccess(state.access);
    if (state.phase !== 'answer' || state.pause) write(submissionKey(), null);
    if (changed || force) render();
    schedule();
  }
  async function refresh() {
    if (!topic || busy || document.hidden) { schedule(); return; }
    const ticket = epoch;
    try {
      const pending = state?.pending_submission_id || savedSubmission()?.request_id;
      const result = await call(pending ? `submissions/${encodeURIComponent(pending)}` : 'state');
      if (!active(ticket)) return;
      if (result.status === 'error') { write(submissionKey(), null); failure = result.reason; }
      accept(pending ? result.state : result);
      notice(failure);
      if (state.phase === 'waiting' && state.next_available_at && !state.pause && !selected
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
  async function submit(input) {
    if (busy || !answerReady(input)) return;
    const ticket = epoch;
    const saved = savedSubmission();
    const body = saved?.step_id === state.active_step_id ? saved : {...payload(), answer: input.value};
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
    return reportableContent(content(html), {topic_id: topic, ...(cardId ? {question_id: cardId} : {}),
      content_block_id: block, content_version: state.course_version});
  }
  function renderQuestion(question, history = false) {
    const box = node('section', 'atomicQuestion'); box.dataset.cardId = question.card_id;
    box.append(node('h2', '', question.title));
    const stem = marked(question.html, `question:${question.card_id}`, question.card_id);
    box.append(stem);
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
    if (!history && question.phase === 'answer' && question.interaction?.grading) {
      box.append(node('p', 'inputHint', t(question.interaction.grading === 'exact' ? 'question.exactHint' : 'question.semanticHint')));
    }
    if (question.phase === 'feedback') {
      const feedback = node('div', `feedback ${question.correct ? 'correct' : 'incorrect'}`);
      feedback.append(node('strong', '', t(question.correct ? 'atomic.correct' : 'atomic.incorrect')));
      if (question.reason) feedback.append(node('p', '', question.reason));
      feedback.append(marked(question.explanation_html, `explanation:${question.card_id}`, question.card_id));
      box.append(feedback);
      if (!history && !state.pause) box.append(button(t('atomic.continue'), () => mutate('continue'), 'primaryButton'));
    }
    return box;
  }
  function renderChunk(chunk) {
    const box = node('section', 'atomicChunk'); box.dataset.chunkId = chunk.id;
    box.append(node('h2', '', chunk.title));
    const annotation = node('div', 'atomicAnnotation'), source = node('div', 'atomicSource');
    source.append(node('p', 'eyebrow', t('atomic.fromIntroduction')));
    for (const anchor of chunk.anchors) source.append(node('p', 'atomicAnchor', anchor.quote));
    const arrow = node('span', 'atomicArrow', '↘'); arrow.setAttribute('aria-hidden', 'true');
    const note = node('aside', 'atomicChunkNote'); note.append(content(chunk.html));
    annotation.append(source, arrow, note); box.append(annotation);
    box.append(button(t('atomic.startCards'), () => mutate('continue'), 'primaryButton'));
    return box;
  }
  function render() {
    if (!state) return;
    const style = document.getElementById('atomicMathStyle') || node('style'); style.id = 'atomicMathStyle';
    style.textContent = state.math_css || ''; if (!style.isConnected) document.head.append(style);
    const page = node('div', 'atomicLayout'), sidebar = node('aside', 'atomicHistory'), main = node('main', 'atomicMain');
    sidebar.append(node('h2', '', t('history.title')));
    const intro = button(t('Introduction'), () => { selected = 'introduction'; render(); });
    intro.disabled = !state.introduction; sidebar.append(intro);
    if (!state.introduction) sidebar.append(node('p', 'atomicLockedHint', t('atomic.introLocked')));
    state.history.forEach((record, index) => {
      const row = button(t('atomic.cardNumber', {number: index + 1}), () => { selected = record.id; render(); });
      row.disabled = !record.viewable; row.dataset.historyId = record.id;
      row.append(node('span', record.mastered ? 'atomicMastered' : 'atomicUnmastered', t(record.mastered ? 'atomic.mastered' : 'atomic.unmastered')));
      sidebar.append(row);
    });
    const back = node('a', 'subjectBack', t('nav.backHome')); back.href = bridge.homeHref(); main.append(back);
    main.append(node('p', 'eyebrow', t(state.mode === 'review' ? 'atomic.reviewTitle' : 'atomic.title')),
      node('h1', 'lessonTitle', state.title), node('p', 'atomicProgress', t('atomic.progress', {done: state.learned_count, total: state.point_count})));
    const status = node('p', 'fieldError', failure || ''); status.id = 'atomicNotice'; status.hidden = !failure; status.setAttribute('role', 'status'); main.append(status);
    if (selected) {
      const previous = selected === 'introduction' ? null : state.history.find(r => r.id === selected && r.viewable);
      if (selected === 'introduction' && state.introduction) main.append(marked(state.introduction.html, 'introduction'));
      else if (previous) main.append(renderQuestion(previous, true));
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
    else if (can('pause_topic') && state.phase !== 'completed') main.append(button(t('atomic.pause'), () => mutate('pause'), 'textButton atomicPause'));
    for (const control of main.querySelectorAll('button')) {
      if (control.dataset.readOnly) continue;
      const pause = control.classList.contains('atomicPause');
      if (busy || state.pause || (!pause && (state.pending_submission_id || !state.can_learn))) control.disabled = true;
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
    if (mode === 'review' && state.mode !== 'review' && state.completed_at && !state.pause) await mutate('choose', {choice: 'review'});
    else if (mode === 'review' && state.mode === 'review' && state.phase === 'waiting') await mutate('continue');
    if (state.pending_submission_id || savedSubmission()) await refresh();
  }
  return {open, stop, reset() { stop(); cache.clear(); }};
}
