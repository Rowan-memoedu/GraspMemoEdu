import {t, translateMessage} from './i18n.js?v=bd5a901caca49899';
import {answerEditor, readerFrame} from './learning-ui.js?v=bd5a901caca49899';
import {answerReady, questionInput, choiceTypeField} from './question-input.js?v=bd5a901caca49899';
import {createLearningCache} from './learning-cache.js?v=bd5a901caca49899';
import {helpableContent, reportableContent} from './content-report.js?v=bd5a901caca49899';

const node = (tag, cls = '', text) => { const x = document.createElement(tag); x.className = cls; if (text !== undefined) x.textContent = text; return x; };
const button = (label, cls, action) => { const x = node('button', cls, label); x.type = 'button'; x.addEventListener('click', action); return x; };
const html = content => { const x = node('div', 'courseContent'); x.innerHTML = content || ''; return x; };
import {createContentTimer} from './content-timer.js?v=bd5a901caca49899';
import {renderReviewDirectory, paperNumber} from './review-directory.js?v=bd5a901caca49899';
import {selfAssessment, referenceAnswer, prepareAnswerContent} from './self-assessment.js?v=bd5a901caca49899';

export function createPaperView({root, request, href, getAccess, formatDate, progressChanged, courseSidebar, taskTree}) {
  const template = document.getElementById('appLayout').cloneNode(true);
  let epoch = 0, reader = null, state = null, selected = 0, pollTimer, saveTimer, input = null, since = null;
  let access, cache, dirty = new Map(), chain = Promise.resolve(), busy = false;
  let contentTimer = null, timeoutSync = false, timeoutRetry = null, timerElement = null;
  let activityTimer = null, activitySession = null, activityChain = Promise.resolve();
  function activity(active, keepalive = false) {
    if (!state?.timing || state.phase !== 'answer' || !activitySession) return Promise.resolve();
    const body = {paper_id: state.id, session_id: activitySession, active};
    const subject = state.subject, e = epoch;
    // Capture the paper's subject before routing changes the portal scope.
    activityChain = activityChain.catch(() => {}).then(async () => {
      const next = await request(`review-papers/activity?subject_id=${encodeURIComponent(subject)}`, {method: 'POST', body, keepalive});
      if (e === epoch && state?.id === body.paper_id) {
        state.timing = next.timing; contentTimer?.update(state.timing);
        if (next.phase !== 'answer' || next.can_edit === false) { state = next; render(); poll(e); }
      }
    });
    return activityChain;
  }
  function heartbeat() {
    clearTimeout(activityTimer);
    if (!state?.timing || state.phase !== 'answer') return;
    activityTimer = setTimeout(() => {
      if (!document.hidden) void activity(true).catch(error).finally(heartbeat);
      else heartbeat();
    }, 5000);
  }
  function pagehide() { remember(); clock(); void activity(false, true).catch(() => {}); }
  const key = (kind, suffix = '') => `graspmemoedu:papers:${access?.learner_id}:${kind}:${state?.id || 'open'}:${suffix}`;
  const read = (kind, suffix) => cache?.read(localStorage, key(kind, suffix));
  const save = (kind, suffix, value) => cache?.write(localStorage, key(kind, suffix), value);
  const post = (path, body) => request(`review-papers/${path}`, {method: 'POST', body});
  const path = id => href(`/reviews/${encodeURIComponent(id)}`);
  const anchor = (label, target, cls = '') => { const x = node('a', cls, label); x.href = target; return x; };
  function content(item, kind, value) {
    const element = html(value), context = item.content_context;
    if (!context) return element;
    return (item.source_kind === 'atomic' ? reportableContent : helpableContent)(element, context,
      `${kind}:${context.question_id}`, item.content_version);
  }
  function clock() {
    if (since !== null && state?.items[selected]) {
      state.items[selected].elapsed_ms = Math.min(86400000, state.items[selected].elapsed_ms + Math.round(performance.now() - since));
      since = null;
    }
  }
  function remember() {
    if (!input || !state || state.phase !== 'answer' || state.can_edit === false || state.items[selected].completed) return;
    if (state.items[selected].revealed) return;
    clock(); const item = state.items[selected]; item.answer = input.value;
    const draft = {index: selected, answer: item.answer, elapsed_ms: item.elapsed_ms};
    dirty.set(selected, draft); save('draft', selected, JSON.stringify(draft));
    if (!document.hidden) since = performance.now();
  }
  function visibility() {
    remember();
    if (document.hidden) { clock(); void activity(false, true).catch(error); void flush().catch(error); }
    else if (state?.timing) void activity(true).then(heartbeat).catch(error);
  }
  function stop() {
    void activity(false, true).catch(() => {}); clearTimeout(activityTimer); activitySession = null;
    remember(); clock(); ++epoch; clearTimeout(pollTimer); clearTimeout(saveTimer);
    contentTimer?.destroy(); contentTimer = null; timerElement = null; clearTimeout(timeoutRetry); timeoutSync = false;
    document.removeEventListener('visibilitychange', visibility);
    window.removeEventListener('pagehide', pagehide);
    reader?.destroy(); reader = null; state = null; input = null; dirty = new Map();
    cache?.clear(); chain = Promise.resolve(); busy = false;
    document.getElementById('paperMathStyle')?.remove();
  }
  function error(failure) {
    if (!reader) return;
    const main = reader.refs.stepCard; main.querySelector('.paperError')?.remove();
    const box = node('p', 'fieldError paperError', translateMessage(failure.message)); box.setAttribute('role', 'alert'); main.append(box);
  }
  async function inbox(allMaterials = false) {
    const e = ++epoch, data = await request('review-papers/inbox' + (allMaterials ? '?materials=1' : '')); if (e !== epoch) return;
    const requests = new Map();
    const body = renderReviewDirectory(root, data, {courseSidebar, taskTree, href, start: async item => {
      if (item.material) {
        location.hash = href(item.source.kind === 'atomic'
          ? '/review/' + encodeURIComponent(item.topic_id) + '/atomic?card=' + encodeURIComponent(item.card_id)
          : '/banks/' + encodeURIComponent(item.bank_id) + '/' + encodeURIComponent(item.group_id) + '?question=' + encodeURIComponent(item.question_id));
        return;
      }
      if (item.can_resume) { location.hash = href('/review/' + encodeURIComponent(item.topic_id) + '/atomic'); return; }
      if (item.active) { location.hash = path(item.id); return; }
      if (!requests.has(item.id)) requests.set(item.id, {request_id: crypto.randomUUID(), candidate_id: item.id});
      const opened = await post('open', requests.get(item.id));
      if (e === epoch) location.hash = path(opened.id);
    }});
    if (getAccess()?.is_admin) body.prepend(anchor(t(allMaterials ? 'paper.back' : 'admin.allCards'), href(allMaterials ? '/reviews' : '/reviews?materials=1'), 'secondaryButton'));
    if (data.next_due_at) body.append(node('p', 'inputHint', t('atomic.availableAt', {time: formatDate(data.next_due_at)})));
    function refresh() {
      pollTimer = setTimeout(() => {
        if (e !== epoch) return;
        if (document.hidden) refresh(); else void inbox(allMaterials).catch(() => refresh());
      }, 15000);
    }
    refresh();
  }
  async function open(id) {
    const e = ++epoch; access = getAccess(); cache = createLearningCache(() => access);
    state = await request('review-papers/state?paper_id=' + encodeURIComponent(id)); if (e !== epoch) return;
    activitySession = crypto.randomUUID();
    if (!document.hidden) await activity(true);
    if (e !== epoch) return;
    for (const item of state.items) if (!item.completed && state.phase === 'answer' && state.can_edit !== false) {
      try { const draft = JSON.parse(read('draft', item.index)); if (draft) { Object.assign(item, draft); dirty.set(item.index, draft); } } catch {}
    }
    selected = state.items.findIndex(item => !item.completed); if (selected < 0) selected = 0;
    reader = readerFrame(template, 'paper');
    reader.refs.loadingState.hidden = true; reader.refs.courseShell.hidden = false;
    reader.refs.topicHomeLink.href = href('/reviews'); reader.refs.topicHomeLink.textContent = '← ' + t('paper.back');
    reader.refs.topicFeedbackButton.hidden = true; reader.refs.lessonTitle.textContent = state.title + (state.paper_number ? ' · ' + paperNumber(state.paper_number) : '');
    reader.frame.querySelector('.eyebrow').textContent = t('paper.title'); reader.frame.querySelector('.lessonToolbar').hidden = true;
    root.hidden = true; root.after(reader.frame); document.body.classList.add('topicPage');
    const style = node('style'); style.id = 'paperMathStyle'; style.textContent = state.math_css; document.head.append(style);
    timerElement = node('div', 'contentTimer inputHint'); timerElement.id = 'paperTimeLimit';
    timerElement.setAttribute('role', 'timer'); reader.refs.stepCard.before(timerElement);
    contentTimer = createContentTimer({element: timerElement, render: renderTimer, onExpire: () => { void synchronizeTimeout(); }});
    document.addEventListener('visibilitychange', visibility); window.addEventListener('pagehide', pagehide);
    render(); poll(e); heartbeat();
  }
  function renderTimer(element, timing) {
    const seconds = timing.remaining_seconds;
    const time = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    const label = timing.timed_out ? t('paper.timeElapsed') : t('paper.remaining', {time});
    element.classList.toggle('fieldError', timing.timed_out);
    element.replaceChildren(node('strong', '', label), node('p', 'inputHint', t('paper.timerHint', {
      minutes: timing.limit_minutes, action: t(timing.timeout_action === 'mark' ? 'paper.timeoutMark' : 'paper.timeoutSubmit'),
    })));
    if (timing.timed_out && !timing.finished_at) element.append(node('p', '', t(timing.timeout_action === 'mark' ? 'paper.markOnly' : 'paper.autoSubmitting')));
    if (timing.timed_out && timing.timeout_action === 'submit' && !timing.finished_at && state?.phase === 'answer') {
      state.can_edit = false; clock();
      if (input?.questionControl) input.questionControl.setDisabled(true); else if (input) input.disabled = true;
      reader?.refs.stepCard.querySelector('#paperSubmit')?.setAttribute('disabled', '');
    }
  }
  async function synchronizeTimeout() {
    if (!state || !reader || timeoutSync) return;
    const e = epoch; timeoutSync = true; clearTimeout(timeoutRetry);
    try {
      await chain.catch(() => {});
      const next = await request('review-papers/state?paper_id=' + encodeURIComponent(state.id));
      if (e !== epoch) return;
      if (next.phase === 'answer' && next.can_edit !== false) {
        state.timing = next.timing; contentTimer?.update(state.timing);
      } else {
        dirty.clear(); state = next; render(); poll(e);
      }
      if (next.timeout_pending) error(new Error(next.timeout_pending));
      if (next.phase === 'answer' && next.can_edit === false) timeoutRetry = setTimeout(() => { void synchronizeTimeout(); }, 3000);
    } catch (failure) {
      if (e === epoch) { error(failure); timeoutRetry = setTimeout(() => { void synchronizeTimeout(); }, 3000); }
    } finally { if (e === epoch) timeoutSync = false; }
  }
  function flush() {
    const e = epoch;
    chain = chain.catch(() => {}).then(async () => {
      while (e === epoch && dirty.size && state?.phase === 'answer') {
        const [index, draft] = dirty.entries().next().value;
        reader.refs.saveStatus.textContent = t('paper.saving');
        const next = await post('draft', {paper_id: state.id, expected_revision: state.revision, answers: [draft]});
        if (e !== epoch) return;
        if (dirty.get(index) === draft) { dirty.delete(index); save('draft', index, null); }
        state = next;
        contentTimer?.update(state.timing);
        if (state.phase !== 'answer' || state.can_edit === false) { dirty.clear(); render(); poll(e); break; }
        for (const [i, value] of dirty) Object.assign(state.items[i], value);
      }
      if (reader && e === epoch) reader.refs.saveStatus.textContent = t('paper.saved');
    });
    return chain;
  }
  async function select(index) {
    if (busy) return;
    remember(); clock(); busy = true;
    try { await flush(); selected = index; reader.closeOnMobile(); render(); }
    catch (failure) { error(failure); }
    finally { busy = false; }
  }
  async function submit(index = null, retry = false) {
    if (busy) return;
    busy = true; const e = epoch; remember(); clock();
    try {
      await flush();
      let payload = retry ? state.retry_submission || JSON.parse(read('submission', 'pending') || 'null') : null;
      if (!payload) payload = {paper_id: state.id, request_id: crypto.randomUUID(), expected_revision: state.revision, ...(index === null ? {} : {index})};
      save('submission', 'pending', JSON.stringify(payload));
      const next = await post('submit', payload); if (e !== epoch) return;
      state = next; render(); poll(e);
    } catch (failure) { error(failure); }
    finally { if (e === epoch) busy = false; }
  }
  async function revealCurrent() {
    if (busy) return;
    const e = epoch; busy = true; remember(); clock();
    try {
      await flush();
      const next = await post('reveal', {paper_id: state.id, index: selected, expected_revision: state.revision});
      if (e !== epoch) return;
      state = next; render();
    } catch (failure) { if (e === epoch) error(failure); }
    finally { if (e === epoch) busy = false; }
  }
  async function rateCurrent(rating) {
    if (busy) return;
    const e = epoch; busy = true; clock();
    try {
      const item = state.items[selected];
      item.self_rating = rating; item.answer = `自主评分：${rating}`;
      const draft = {index: selected, answer: item.answer, elapsed_ms: item.elapsed_ms, self_rating: rating};
      dirty.set(selected, draft); save('draft', selected, JSON.stringify(draft));
      await flush(); if (e !== epoch) return;
      busy = false;
      if (state.mode === 'question') await submit(selected);
      else if (selected + 1 < state.items.length) await select(selected + 1);
      else await submit();
    } catch (failure) { if (e === epoch) error(failure); }
    finally { if (e === epoch) { busy = false; render(); } }
  }
  function poll(e) {
    clearTimeout(pollTimer);
    if (state?.phase !== 'grading' || state.job_status === 'error') return;
    pollTimer = setTimeout(async () => {
      try {
        const next = await request('review-papers/state?paper_id=' + encodeURIComponent(state.id));
        if (e !== epoch) return;
        state = next;
        if (state.job_status === 'judged') { save('submission', 'pending', null); progressChanged(); }
        render(); poll(e);
      } catch (failure) { if (e === epoch) { error(failure); poll(e); } }
    }, 700);
  }
  function render() {
    if (!reader || !state) return;
    clock(); input = null;
    contentTimer?.update(state.timing);
    const item = state.items[selected], refs = reader.refs, main = refs.stepCard, sidebar = refs.historyPanel;
    const questions = node('div', 'historyItems'); sidebar.replaceChildren(questions);
    state.items.forEach((q, i) => {
      const row = button(`${i + 1}. ${q.title || t('training.exercise', {number: i + 1})}`, 'historyItem', () => select(i)); row.dataset.questionIndex = i;
      if (i === selected) row.setAttribute('aria-current', 'step');
      const status = q.completed ? (q.result?.correct ? 'training.correct' : 'training.incorrect') : q.answer ? 'paper.answered' : 'paper.unanswered';
      row.append(node('span', 'historyMark', t(status))); questions.append(row);
    });
    const completed = state.items.filter(q => q.completed).length;
    refs.progressCaption.hidden = false; refs.progressCaption.textContent = `${completed} / ${state.items.length}`;
    refs.lessonProgress.hidden = false; refs.lessonProgress.setAttribute('aria-valuenow', String(100 * completed / state.items.length));
    refs.lessonProgress.replaceChildren(...state.items.map(q => node('span', 'progressSegment ' + (q.completed ? 'completed' : q.answer ? 'in_progress' : 'not_started'))));
    refs.footerPosition.textContent = t('reader.modulePosition', {current: selected + 1, total: state.items.length});
    refs.saveStatus.textContent = t(state.phase === 'grading' ? 'paper.checking' : 'paper.saved');
    const heading = node('div', 'stepHeader'); heading.append(node('h2', 'stepTitle', t('training.exercise', {number: selected + 1})), node('span', 'stepCounter', t(state.mode === 'paper' ? 'paper.paperMode' : 'paper.questionMode')));
    const stem = content(item, 'question', item.html); main.replaceChildren(heading);
    if (state.phase === 'done') main.append(node('p', 'completionBanner', t('paper.finished')), node('p', '', t('paper.summary', {correct: state.items.filter(q => q.result?.correct).length, count: state.items.length})));
    main.append(node('p', 'inputHint', t(state.mode === 'paper' ? 'paper.paperHint' : 'paper.questionHint')));
    const type = choiceTypeField(item.interaction); if (type) main.append(type); main.append(stem);
    if (state.phase === 'answer' && state.can_edit !== false && !item.completed) {
      const editor = answerEditor(item, stem, item.answer, {formId: 'paperAnswerForm', inputId: 'paperAnswer', submitId: 'paperSubmit'}); input = editor.input;
      const label = state.mode === 'question' ? 'paper.submitOne' : selected < state.items.length - 1 ? 'paper.next' : 'paper.submitAll'; editor.submit.textContent = t(label);
      editor.form.append(editor.bottom); main.append(editor.form);
      const refresh = () => { editor.submit.disabled = !state.can_submit || !answerReady(input); };
      input.addEventListener('input', () => { remember(); refresh(); clearTimeout(saveTimer); saveTimer = setTimeout(() => { void flush().catch(failure => { if (reader) reader.refs.saveStatus.textContent = t('paper.savedLocal'); error(failure); }); }, 650); });
      input.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) { event.preventDefault(); editor.form.requestSubmit(); } });
      editor.form.addEventListener('submit', event => { event.preventDefault(); if (editor.submit.disabled) return; if (state.mode === 'question') void submit(selected); else if (selected < state.items.length - 1) void select(selected + 1); else void submit(); });
      if (!state.can_submit) { if (input.questionControl) input.questionControl.setDisabled(true); else input.disabled = true; }
      refresh(); if (!document.hidden) since = performance.now();
      const controls = selfAssessment(item, input, {disabled: !state.can_submit,
        reveal: revealCurrent, rate: rateCurrent,
        explanation: item.revealed ? content(item, 'explanation', item.explanation_html) : null});
      if (controls) main.append(controls);
      if (item.revealed) { editor.submit.hidden = true; if (input.questionControl) input.questionControl.setDisabled(true); else input.disabled = true; }
    } else if (item.interaction && item.interaction.type !== 'text') {
      main.append(questionInput(item.interaction, {id: 'paperReadAnswer', stem, value: item.answer, disabled: true}).element);
    } else {
      const answer = node('textarea', 'answerInput'); answer.value = item.answer; answer.disabled = true;
      answer.rows = 3; answer.setAttribute('aria-label', t('你的答案')); main.append(answer);
    }
    if (item.result) {
      const reference = referenceAnswer(item); if (reference) main.append(reference);
      main.append(node('p', item.result.correct ? 'correctResult' : 'fieldError', t(item.result.correct ? 'training.correct' : 'training.incorrect')), node('p', '', item.result.reason === '限时已到，本题未完成作答。' ? t('paper.unansweredTimeout') : item.result.reason), prepareAnswerContent(content(item, 'explanation', item.explanation_html)));
      if (item.result.due_at) main.append(node('p', 'inputHint', t('atomic.availableAt', {time: formatDate(item.result.due_at)})));
    }
    if (state.error) { main.append(node('p', 'fieldError', translateMessage(state.error))); if (state.phase === 'grading') main.append(button(t('training.retry'), 'primaryButton', () => submit(null, true))); }
    else if (state.phase === 'grading') main.append(node('p', '', t('paper.checking')));
    if (['cancelled', 'expired', 'conflict'].includes(state.phase)) main.append(node('p', '', t('paper.closed')));
    const navigation = node('div', 'stepNavigation');
    if (selected > 0) navigation.append(button(t('paper.previous'), 'secondaryButton', () => select(selected - 1)));
    if (selected + 1 < state.items.length) navigation.append(button(t('paper.next'), 'secondaryButton', () => select(selected + 1)));
    navigation.append(anchor(t('paper.back'), href('/reviews'), 'secondaryButton'));
    if (state.phase === 'answer' || state.error && state.phase === 'grading') navigation.append(button(t('paper.cancel'), 'secondaryButton', async () => {
      if (!confirm(t('paper.cancelConfirm'))) return;
      try { remember(); await flush(); state = await post('cancel', {paper_id: state.id}); render(); } catch (failure) { error(failure); }
    }));
    main.append(navigation);
  }
  return {inbox, open, stop};
}
