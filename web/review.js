import { t, translateMessage } from './i18n.js';

const node = (tag, cls = '', text) => {
  const item = document.createElement(tag); item.className = cls;
  if (text !== undefined) item.textContent = text;
  return item;
};
const button = (text, cls, action, id) => {
  const item = node('button', cls, text); item.type = 'button';
  if (id) item.id = id;
  item.addEventListener('click', action); return item;
};
const content = html => { const item = node('div', 'courseContent'); item.innerHTML = html || ''; return item; };
const read = key => { try { return sessionStorage.getItem(key); } catch { return null; } };
const write = (key, value) => { try { value === null ? sessionStorage.removeItem(key) : sessionStorage.setItem(key, value); } catch { /* Server progress remains authoritative. */ } };
const json = key => { try { return JSON.parse(read(key)); } catch { return null; } };

// The backend owns queue growth, mastery and FSRS. This view only submits the
// current Practice and renders saved responses, using the Lesson transport/UI.
export function createReviewView(bridge) {
  let state = null, topic = null, module = null, epoch = 0, timer = null, busy = false;
  let selected = null, failure = null, learner = null, clock = { key: null, total: 0, started: null };
  let rendered = null, historyOpen = true;
  const root = bridge.root;
  const active = ticket => ticket === epoch && topic && bridge.getAccess()?.learner_id === learner;
  const can = feature => Boolean((state?.access || bridge.getAccess())?.features?.includes(feature));
  const prefix = () => `math-learning-web:review:${bridge.getApiOrigin()}:${learner}:${topic}:${state?.session_id}`;
  const draftKey = () => `${prefix()}:draft:${state.practice.id}`;
  const submissionKey = () => `${prefix()}:submission`;
  const call = (path, options) => bridge.request(`review/${path}?topic_id=${encodeURIComponent(topic)}`, options);
  const sessionPath = suffix => `sessions/${encodeURIComponent(state.session_id)}${suffix ? '/' + suffix : ''}`;
  const payload = () => ({request_id: crypto.randomUUID(), expected_revision: state.revision, question_id: state.practice.id});
  const current = () => !selected || selected === state?.practice.id;

  function tick(pause = false) {
    const now = performance.now();
    if (clock.started !== null) clock.total += Math.max(0, now - clock.started);
    if (clock.key) write(clock.key, String(Math.round(clock.total)));
    const key = state && `${prefix()}:elapsed:${state.practice.id}`;
    if (key !== clock.key) clock = {key, total: Math.max(0, Number(read(key)) || 0), started: null};
    clock.started = !pause && topic && !document.hidden && document.hasFocus() && !busy && current()
      && state?.status === 'in_progress' && state.phase === 'answer' && !state.pending_submission_id ? now : null;
    return Math.min(86400000, Math.round(clock.total));
  }
  function stop() {
    tick(true); epoch++; clearTimeout(timer); timer = null; topic = null; state = null;
    busy = false; selected = null; rendered = null; failure = null;
  }
  function notice(message) {
    const target = root.querySelector('#reviewNoticeBox');
    if (target) { target.hidden = !message; target.textContent = message || ''; }
  }
  function schedule() {
    clearTimeout(timer);
    if (!topic || !state) return;
    timer = setTimeout(refresh, state.pending_submission_id ? 1000 : 5000);
  }
  function accept(next, force = false) {
    if (!next || next.learner_id !== learner || next.topic_id !== topic || !next.practice) throw new Error(t('review.invalidState'));
    if (state?.session_id === next.session_id && next.revision < state.revision) return;
    tick(true);
    const changed = JSON.stringify([next.revision, next.access?.features, next.access?.topics]) !== rendered;
    const oldQuestion = state?.practice.id;
    state = next;
    if (next.access) bridge.setAccess(next.access);
    if (oldQuestion && oldQuestion !== next.practice.id) selected = null;
    if (!can('review_history')) selected = null;
    if (state.phase === 'feedback' || state.status !== 'in_progress') write(submissionKey(), null);
    if (changed || force) render();
    schedule(); tick();
  }
  async function refresh() {
    if (!topic || !state || busy) return;
    if (document.hidden) { schedule(); return; }
    const ticket = epoch;
    try {
      const pending = state.pending_submission_id;
      const result = await call(pending ? `submissions/${encodeURIComponent(pending)}` : sessionPath());
      if (!active(ticket)) return;
      failure = result.status === 'error' ? translateMessage(result.reason || t('review.judgeError')) : null;
      accept(pending ? result.state : result);
      notice(failure);
    } catch (error) {
      if (active(ticket)) {
        failure = translateMessage(error.message); notice(failure);
        if (error.status === 403 || error.status === 404) { clearTimeout(timer); renderUnavailable(failure); return; }
      }
    } finally { if (active(ticket)) schedule(); }
  }
  function renderUnavailable(message) {
    state = null;
    const box = node('section', 'portalMessage'); box.setAttribute('role', 'status');
    box.append(node('h1', '', t('review.unavailable')), node('p', '', message), home());
    root.replaceChildren(box);
  }
  async function open(topicId, moduleId, sessionId) {
    stop(); topic = topicId; module = moduleId; learner = bridge.getAccess()?.learner_id;
    const ticket = epoch;
    root.replaceChildren(node('p', 'portalLoading', t('review.loading')));
    try {
      const result = sessionId
        ? await call(`sessions/${encodeURIComponent(sessionId)}`)
        : await call('begin', {method: 'POST', body: {module_id: module, request_id: crypto.randomUUID()}});
      if (!active(ticket)) return;
      if (result.module_id !== module) throw new Error(t('review.invalidState'));
      bridge.setSession(result.session_id);
      document.title = `${t('portal.review')} · ${result.module_title || result.topic_title}`;
      accept(result, true);
      const saved = json(submissionKey());
      if (saved?.question_id === state.practice.id && !state.pending_submission_id && state.phase === 'answer') {
        try {
          const submitted = await call(`submissions/${encodeURIComponent(saved.request_id)}`);
          if (active(ticket)) { accept(submitted.state); if (submitted.status === 'error') notice(translateMessage(submitted.reason || t('review.judgeError'))); }
        } catch { /* An unaccepted request can be retried with the saved id. */ }
      }
    } catch (error) { if (active(ticket)) renderUnavailable(translateMessage(error.message)); }
  }
  async function mutate(action, body) {
    if (busy || !state) return;
    const ticket = epoch; busy = true; tick(true); failure = null; render();
    try {
      const result = await call(sessionPath(action), {method: 'POST', body});
      if (!active(ticket)) return;
      busy = false;
      failure = result.status === 'error' ? translateMessage(result.reason || t('review.judgeError')) : null;
      accept(action === 'submit' ? result.state : result, true);
      bridge.progressChanged();
    } catch (error) {
      if (!active(ticket)) return;
      failure = translateMessage(error.message);
      if (['stale_revision', 'already_answered', 'submission_pending', 'review_inactive', 'topic_paused'].includes(error.code)) {
        if (action === 'submit') write(submissionKey(), null);
        busy = false; await refresh();
      }
    } finally { if (active(ticket)) { busy = false; if (state) render(); notice(failure); schedule(); tick(); } }
  }
  function submit(event) {
    event?.preventDefault();
    const input = root.querySelector('#reviewAnswerInput');
    if (busy || !input?.value.trim() || !state?.actions.includes('submit') || !can('submit_answer')) return;
    const answer = input.value.trim(); const previous = json(submissionKey());
    const body = previous?.question_id === state.practice.id && previous.answer === answer ? previous : {...payload(), answer, elapsed_ms: tick(true)};
    write(submissionKey(), JSON.stringify(body));
    void mutate('submit', body);
  }
  function home() { const item = node('a', 'primaryButton', t('nav.backHome')); item.href = '#/learn'; return item; }
  function choose(question) { selected = question; tick(true); render(); tick(); }
  function pages() {
    const prior = can('review_history') ? state.answers || [] : [];
    return [...new Set([...prior.map(answer => answer.question_id), state.practice.id])];
  }
  function renderHistory() {
    const sidebar = node('aside', 'reviewHistory'); sidebar.setAttribute('aria-label', t('history.title'));
    const toggle = button(t('history.title'), 'textButton historyButton', () => { historyOpen = !historyOpen; render(); });
    toggle.setAttribute('aria-expanded', String(historyOpen)); toggle.setAttribute('aria-controls', 'reviewHistoryList');
    sidebar.append(toggle);
    const list = node('nav', 'reviewHistoryList'); list.id = 'reviewHistoryList'; list.hidden = !historyOpen;
    for (const [index, id] of pages().entries()) {
      const answer = state.answers?.find(item => item.question_id === id);
      const item = button(t('review.questionNumber', {number: index + 1}), 'historyItem', () => choose(id));
      item.disabled = busy; item.classList.toggle('active', (selected || state.practice.id) === id);
      if ((selected || state.practice.id) === id) item.setAttribute('aria-current', 'step');
      if (answer) item.append(node('span', `historyMark ${answer.correct ? 'correct' : 'incorrect'}`, answer.correct ? '✓' : '×'));
      else item.append(node('span', 'historyMark', t('当前')));
      list.append(item);
    }
    sidebar.append(list); return sidebar;
  }
  function render() {
    if (!state || !topic) return;
    const oldInput = root.querySelector('#reviewAnswerInput');
    const focus = oldInput && document.activeElement === oldInput ? [oldInput.selectionStart, oldInput.selectionEnd] : null;
    rendered = JSON.stringify([state.revision, state.access?.features, state.access?.topics]);
    const layout = node('div', 'reviewLayout'), main = node('section', 'reviewMain'); main.id = 'reviewContent'; main.tabIndex = -1;
    const back = node('a', '', t('nav.backHome')); back.href = '#/learn';
    const breadcrumb = node('div', 'topicBreadcrumb'); breadcrumb.append(back, button(t('lesson.feedback'), 'textButton', () => bridge.showFeedback({topic_id: topic, task_id: `review:${state.session_id}`, question_id: selected || state.practice.id})));
    main.append(breadcrumb, node('p', 'eyebrow', t('portal.review')), node('h1', 'lessonTitle', state.module_title || state.topic_title), node('p', 'reviewTopicTitle', state.topic_title));
    const count = node('p', 'reviewProgress', t('review.progress', {done: state.practice_answered_count, total: state.practice_target_count, errors: state.practice_error_count})); count.setAttribute('aria-live', 'polite'); main.append(count);
    const message = node('div', 'notice'); message.id = 'reviewNoticeBox'; message.setAttribute('role', 'status'); message.hidden = !failure; message.textContent = failure || ''; main.append(message);
    if (state.status === 'in_progress') {
      const toolbar = node('div', 'lessonToolbar'), pause = node('div', 'pauseControl');
      const pauseButton = button(t('lesson.pause'), 'textButton pauseButton', () => {
        if (!can('pause_topic')) { notice(bridge.getAccess()?.role === 'guest' ? t('review.accountRequired') : t('review.featureUnavailable')); return; }
        void mutate('pause', {request_id: crypto.randomUUID()});
      }, 'reviewPauseButton'); pauseButton.disabled = busy;
      pause.append(pauseButton, node('p', 'pauseHint', t('lesson.pauseHint'))); toolbar.append(pause); main.append(toolbar);
    } else {
      const outcome = node('section', state.status === 'completed' ? 'moduleResult' : 'pauseNotice'); outcome.id = 'reviewResult'; outcome.setAttribute('role', 'status');
      const copy = node('div');
      if (state.status === 'completed') {
        const labels = {1: 'Again', 2: 'Hard', 3: 'Good', 4: 'Easy'};
        copy.append(node('h2', 'resultTitle', t('review.completed')), node('p', 'resultDetail', t('review.nextDue', {time: bridge.formatDate(state.due_at)})));
        outcome.append(copy, node('strong', 'reviewMastery', t('review.rating', {rating: state.mastery, label: labels[state.mastery]})));
      } else {
        copy.append(node('h2', '', t('review.paused')), node('p', '', t('review.pauseReason'))); outcome.append(copy);
      }
      main.append(outcome);
    }
    const card = node('article', 'step'); card.id = 'reviewPractice';
    const isCurrent = current();
    const answer = state.answers?.find(item => item.question_id === (selected || state.practice.id));
    const question = isCurrent ? state.practice : answer;
    if (!question) { selected = null; return render(); }
    const pageIds = pages(); const index = pageIds.indexOf(selected || state.practice.id);
    card.append(node('h2', 'stepTitle', t('review.questionNumber', {number: index + 1})), content(question.html));
    if (answer) {
      const submitted = node('details', 'submittedAnswer'); submitted.append(node('summary', '', t('查看已提交答案')), node('pre', '', answer.answer)); card.append(submitted);
    }
    const feedback = isCurrent ? state.feedback : answer?.feedback || answer;
    if (feedback && typeof feedback.correct === 'boolean') {
      const box = node('div', `feedback ${feedback.correct ? 'correct' : 'incorrect'}`); box.setAttribute('role', 'status');
      const detail = node('div'); detail.append(node('strong', 'feedbackTitle', feedback.correct ? t('回答正确') : t('本题回答有误')));
      if (feedback.reason) detail.append(node('p', 'feedbackReason', feedback.reason));
      box.append(node('span', 'feedbackIcon', feedback.correct ? '✓' : '!'), detail); card.append(box);
      if (question.explanation_html) card.append(node('h3', 'exampleExplanationHeader', t('Explanation · 解析')), content(question.explanation_html));
    }
    if (isCurrent && state.status === 'in_progress' && state.actions.includes('submit')) {
      const form = node('form', 'learningActions answerForm'); form.id = 'reviewAnswerForm'; form.addEventListener('submit', submit);
      const label = node('label', '', t('你的答案')); label.htmlFor = 'reviewAnswerInput';
      const input = node('textarea', 'answerInput'); input.id = 'reviewAnswerInput'; input.rows = 3; input.maxLength = 2000; input.spellcheck = false;
      input.value = read(draftKey()) || ''; input.disabled = busy;
      input.addEventListener('input', () => { write(draftKey(), input.value); root.querySelector('#reviewSubmitButton').disabled = busy || !input.value.trim(); });
      input.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) submit(event); });
      form.append(label, input); card.append(form);
    }
    if (isCurrent && state.pending_submission_id) { const waiting = node('p', 'waiting', t('review.judging')); waiting.setAttribute('role', 'status'); card.append(waiting); }
    if (isCurrent && state.status === 'in_progress' && !state.pending_submission_id && !state.actions.length) card.append(node('p', 'featureNotice', t('review.featureUnavailable')));
    const nav = node('div', 'stepNavigation');
    if (index > 0) nav.append(button(t('上一页'), 'secondaryButton pageButton', () => choose(pageIds[index - 1])));
    if (!isCurrent) nav.append(button(t('下一页'), 'primaryButton', () => choose(pageIds[index + 1])));
    else if (state.actions.includes('continue')) nav.append(button(t('下一页'), 'primaryButton', () => mutate('continue', payload()), 'reviewContinueButton'));
    else if (state.actions.includes('submit')) {
      const send = button(t('Submit'), 'primaryButton', submit, 'reviewSubmitButton'); send.disabled = busy || !root.querySelector('#reviewAnswerInput')?.value.trim();
      send.setAttribute('form', 'reviewAnswerForm'); nav.append(send);
    } else if (state.status !== 'in_progress') nav.append(home());
    for (const item of nav.querySelectorAll('button')) if (busy) item.disabled = true;
    card.append(nav); main.append(card);
    layout.append(renderHistory(), main); root.replaceChildren(layout);
    const input = root.querySelector('#reviewAnswerInput'), send = root.querySelector('#reviewSubmitButton');
    if (send) send.disabled = busy || !input?.value.trim() || !can('submit_answer');
    if (focus && input && !input.disabled) { input.focus({preventScroll: true}); input.setSelectionRange(...focus); }
    let css = document.getElementById('reviewMathStyle'); if (!css) { css = node('style'); css.id = 'reviewMathStyle'; document.head.append(css); }
    css.textContent = state.math_css || '';
    void prefill();
  }
  async function prefill() {
    const input = root.querySelector('#reviewAnswerInput');
    if (!state || bridge.getAccess()?.role !== 'guest' || !bridge.demoEnabled() || !input || input.value || read(draftKey()) !== null) return;
    const ticket = epoch, key = draftKey(), question = state.practice.id;
    try {
      const result = await call(sessionPath('demo-answer'));
      if (!active(ticket) || !input.isConnected || !bridge.demoEnabled() || input.value || read(key) !== null || result.question_id !== question) return;
      input.value = result.answer; input.dispatchEvent(new Event('input', {bubbles: true}));
    } catch { /* A missing demo answer must never block practice. */ }
  }
  window.addEventListener('focus', () => { tick(); if (topic) refresh(); });
  window.addEventListener('blur', () => tick(true));
  window.addEventListener('pagehide', () => tick(true));
  window.addEventListener('online', () => { if (topic) refresh(); });
  document.addEventListener('visibilitychange', () => { tick(document.hidden); if (!document.hidden && topic) refresh(); });
  document.getElementById('guestDemoCheckbox').addEventListener('change', prefill);
  return {open, stop};
}
