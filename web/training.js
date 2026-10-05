import {t, translateMessage} from './i18n.js?v=6d29867e0d7351ea';
import {questionInput, answerReady, answerEmpty, choiceTypeField} from './question-input.js?v=6d29867e0d7351ea';
import {createLearningCache} from './learning-cache.js?v=6d29867e0d7351ea';
import {helpableContent} from './content-report.js?v=6d29867e0d7351ea';
import {referenceAnswer, prepareAnswerContent} from './self-assessment.js?v=6d29867e0d7351ea';

const node = (tag, cls = '', text) => { const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const button = (text, cls, action) => { const n = node('button', cls, text); n.type = 'button'; n.addEventListener('click', action); return n; };
const encode = encodeURIComponent;
const query = fields => new URLSearchParams(fields).toString();
const html = (content, cls = 'courseContent') => { const n = node('div', cls); n.innerHTML = content || ''; return n; };
const ratingKeys = ['', 'training.again', 'training.hard', 'training.good', 'training.easy', 'training.retire'];
import {answerEditor, readerFrame} from './learning-ui.js?v=6d29867e0d7351ea';

export function createTrainingView(bridge) {
  const {root, request, href, formatDate, getAccess} = bridge;
  const readerTemplate = document.getElementById('appLayout').cloneNode(true);
  let cacheAccess = getAccess();
  const cache = createLearningCache(() => cacheAccess);
  let epoch = 0, selection = 0, timer = null, group = null, current = null, sidebar, main;
  let elapsed = 0, activeSince = null, clockKey = null, visibilityHandler = null;
  let reader = null;
  const key = (kind, id) => `graspmemoedu:training:${cacheAccess?.learner_id}:${kind}:${id}`;
  const read = (kind, id) => cache.read(localStorage, key(kind, id));
  const save = (kind, id, value) => cache.write(localStorage, key(kind, id), value);
  const alive = (e, s = selection) => e === epoch && s === selection;
  const link = (label, path, cls = '') => { const a = node('a', cls, label); a.href = href(path); return a; };
  const groupPath = (bank, id, question) => `/banks/${encode(bank)}/${encode(id)}${question ? '?question=' + encode(question) : ''}`;
  const post = (name, body) => request(`training/${name}`, {method: 'POST', body});
  function flushClock() {
    if (activeSince !== null) elapsed += performance.now() - activeSince;
    activeSince = null;
    if (clockKey) save('elapsed', clockKey, String(Math.min(86400000, Math.round(elapsed))));
    return Math.min(86400000, Math.round(elapsed));
  }
  function startClock(state) {
    clockKey = state.attempt_id; elapsed = Number(read('elapsed', clockKey)) || 0;
    activeSince = state.phase === 'answer' && !state.submission_id && !document.hidden ? performance.now() : null;
  }
  function stop() {
    flushClock(); clockKey = null; ++epoch; ++selection; clearTimeout(timer);
    if (visibilityHandler) document.removeEventListener('visibilitychange', visibilityHandler);
    visibilityHandler = null; group = current = null; cache.clear();
    reader?.destroy(); reader = null;
    document.getElementById('trainingMathStyle')?.remove();
  }
  function error(container, failure, retry) {
    container.querySelector('.trainingError')?.remove();
    const box = node('div', 'trainingError'); box.setAttribute('role', 'alert');
    box.append(node('p', 'fieldError', translateMessage(failure.message)));
    if (retry) box.append(button(t('training.retry'), 'secondaryButton', retry));
    container.append(box);
  }
  async function open(path, params) {
    cacheAccess = getAccess();
    const e = ++epoch; ++selection;
    root.replaceChildren(node('p', '', t('training.loading')));
    const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
    if (parts.length === 1) {
      const data = await request('training/catalog'); if (e !== epoch) return;
      const grid = node('div', 'courseGrid'); root.replaceChildren(node('h1', 'portalPageTitle', t('training.banks')), grid);
      for (const bank of data.banks) {
        const card = node('article', 'courseChoice');
        card.append(node('h2', '', bank.title), node('p', 'courseDescription', bank.description),
          link(t('training.train'), `/banks/${encode(bank.id)}`, 'primaryButton courseLearn'));
        grid.append(card);
      }
      if (!data.banks.length) grid.append(node('p', 'portalEmpty', t('training.empty')));
    } else if (parts.length === 2) {
      const bank = await request('training/bank?' + query({bank_id: parts[1]})); if (e !== epoch) return;
      const count = bank.groups.reduce((n, g) => n + g.question_count, 0);
      const completed = bank.groups.reduce((n, g) => n + (g.completed_count || 0), 0);
      const containers = bank.groups.filter(g => bank.groups.some(child => child.parent_id === g.id));
      const data = {course: {id: bank.id, title: bank.title, progress: count ? completed / count * 100 : 0},
        tasks: bank.groups.filter(g => g.question_count).map(g => ({id: g.id, topic_id: g.id, type: 'Lesson',
          title: g.title, question_count: g.question_count, progress: g.completed_count / g.question_count * 100})),
        pending_hierarchy: containers.map(g => ({...g, topic_ids: bank.groups.filter(child => child.question_count
          && (child.id === g.id || child.parent_id === g.id && !containers.some(c => c.id === child.id))).map(child => child.id)}))};
      const layout = node('div', 'dashboardLayout'), tasks = node('div', 'dashboardTasks');
      const pending = node('section', 'incompleteTasks'); pending.setAttribute('aria-label', t('training.groups'));
      tasks.append(pending);
      layout.append(bridge.courseSidebar(data, {training: true, href: `/banks/${encode(bank.id)}`, questionCount: count,
        onProgress: () => pending.scrollIntoView({block: 'start'})}), tasks);
      root.replaceChildren(layout);
      bridge.taskTree(pending, data, {training: true, label: t('training.groups'), startLabel: t('training.start'),
        sourceOrder: bank.groups.map(g => g.id),
        target: task => groupPath(bank.id, task.id)});
      if (!count) pending.append(node('p', 'portalEmpty', t('training.empty')));
    } else if (parts.length === 3) {
      const nextGroup = await request('training/group?' + query({bank_id: parts[1], group_id: parts[2]}));
      if (e !== epoch) return;
      group = nextGroup;
      const style = node('style'); style.id = 'trainingMathStyle'; style.textContent = group.math_css; document.head.append(style);
      reader = readerFrame(readerTemplate, 'training');
      const refs = reader.refs;
      sidebar = refs.historyPanel; main = refs.stepCard;
      root.hidden = true; root.after(reader.frame); document.body.classList.add('topicPage');
      refs.loadingState.hidden = true; refs.courseShell.hidden = false;
      refs.topicHomeLink.href = href(`/banks/${encode(group.bank_id)}`); refs.topicHomeLink.textContent = '← ' + t('training.back');
      refs.topicFeedbackButton.hidden = true;
      refs.lessonTitle.textContent = group.title;
      reader.frame.querySelector('.eyebrow').textContent = t('training.groups');
      reader.frame.querySelector('.lessonToolbar').hidden = true;
      renderSidebar();
      visibilityHandler = () => { flushClock(); if (!document.hidden && current?.phase === 'answer' && !current.submission_id) activeSince = performance.now(); };
      document.addEventListener('visibilitychange', visibilityHandler);
      const selected = group.questions.find(q => q.id === params.get('question')) || group.questions[0];
      if (selected) await select(selected.id, params.get('review') === '1'
        && !(selected.latest_attempt_mode === 'review' && selected.latest_attempt_phase !== 'done'));
    } else throw new Error(t('portal.this.page.does.not.exist.24'));
  }
  function renderSidebar() {
    sidebar.replaceChildren();
    group.questions.forEach((q, index) => {
      const section = node('section', 'historyGroup'), header = node('div', 'historyGroupHeader');
      const title = t('training.exercise', {number: index + 1});
      header.append(node('span', 'moduleName', title), node('span', `historyStatus${q.completed_count ? ' completed' : ''}`,
        q.completed_count ? t('training.completed', {count: q.completed_count}) : t('training.unanswered')));
      const items = node('div', 'historyItems');
      const item = button(q.title, 'historyItem trainingQuestionLink', () => select(q.id));
      item.dataset.questionId = q.id;
      if (current?.question_id === q.id && current.attempt_id === q.latest_attempt_id) item.setAttribute('aria-current', 'step');
      items.append(item); section.append(header, items); sidebar.append(section);
      if (current?.question_id === q.id && getAccess()?.features?.includes('review_history'))
        void loadHistory(items, epoch, selection, q.id);
    });
    const completed = group.questions.filter(q => q.completed_count).length;
    const refs = reader.refs;
    refs.progressCaption.hidden = false;
    refs.progressCaption.replaceChildren(node('span', '', t('reader.modulesCompleted', {completed, total: group.questions.length})),
      node('span', '', t(completed === group.questions.length ? 'training.finished' : 'training.inProgress')));
    refs.lessonProgress.hidden = false;
    refs.lessonProgress.setAttribute('aria-valuenow', String(Math.round(100 * completed / (group.questions.length || 1))));
    refs.lessonProgress.replaceChildren(...group.questions.map(q => node('span', `progressSegment ${q.completed_count ? 'completed' : q.latest_attempt_id ? 'in_progress' : 'not_started'}`)));
  }
  async function select(questionId, fresh = false, attemptId = null) {
    flushClock(); clockKey = null; clearTimeout(timer);
    if (current) reader.closeOnMobile();
    const e = epoch, s = ++selection, q = group.questions.find(q => q.id === questionId);
    main.replaceChildren(node('p', '', t('training.loading')));
    try {
      const previous = attemptId || (!fresh && q.latest_attempt_id);
      let state;
      if (previous) state = await request('training/state?' + query({attempt_id: previous}));
      else {
        const openKey = `${group.bank_id}:${group.id}:${questionId}:open`;
        const requestId = read('submission', openKey) || crypto.randomUUID();
        save('submission', openKey, requestId);
        state = await post('open', {request_id: requestId, bank_id: group.bank_id, group_id: group.id, question_id: questionId});
        if (!alive(e, s)) return;
        save('submission', openKey, null); q.latest_attempt_id = state.attempt_id;
      }
      if (!alive(e, s)) return;
      if (state.bank_id !== group.bank_id || state.group_id !== group.id || state.question_id !== questionId) throw new Error(t('portal.this.page.does.not.exist.24'));
      history.replaceState(null, '', href(groupPath(group.bank_id, group.id, questionId)));
      current = state; startClock(state); renderSidebar(); renderQuestion();
    } catch (failure) { if (alive(e, s)) error(main, failure, () => select(questionId, fresh, attemptId)); }
  }
  function renderQuestion() {
    const state = current, e = epoch, s = selection;
    const index = group.questions.findIndex(q => q.id === state.question_id);
    const heading = node('div', 'stepHeader'), title = node('h2', 'stepTitle', t('training.exercise', {number: index + 1}));
    title.id = 'trainingStepTitle'; title.tabIndex = -1; main.setAttribute('aria-labelledby', title.id);
    heading.append(title, node('span', 'stepCounter', t('reader.modulePosition', {current: index + 1, total: group.questions.length})));
    const reportContext = {topic_id: 'training.' + state.bank_id, bank_id: state.bank_id,
      group_id: state.group_id, question_id: state.question_id};
    const stem = helpableContent(html(state.question.html), reportContext, `question:${state.question_id}`, state.version);
    main.replaceChildren(heading, stem);
    const typeField = choiceTypeField(state.question.interaction); if (typeField) heading.after(typeField);
    reader.refs.footerPosition.textContent = t('reader.modulePosition', {current: index + 1, total: group.questions.length});
    reader.refs.saveStatus.textContent = t(state.submission_status === 'pending' || state.submission_status === 'running' ? '答案已提交，正在判题' : '学习进度已保存');
    const navigation = node('div', 'stepNavigation'), continueRow = node('div', 'continueRow');
    if (state.phase === 'answer') {
      let savedPayload = null;
      try { savedPayload = JSON.parse(read('submission', state.attempt_id)); } catch {}
      const draft = savedPayload?.answer ?? (state.answer || read('draft', state.attempt_id) || '');
      const {form, input, submit, bottom} = answerEditor(state.question, stem, draft,
        {formId: 'trainingAnswerForm', inputId: 'trainingAnswer', submitId: 'trainingSubmit', canSelfRate: state.can_self_rate && state.can_submit});
      form.classList.add('trainingAnswerForm');
      const disable = value => { if (input.questionControl) input.questionControl.setDisabled(value); else input.disabled = value; };
      const pending = ['pending', 'running'].includes(state.submission_status);
      const failed = state.submission_status === 'error';
      const refresh = () => {
        const show = state.can_self_rate && answerEmpty(input) && !state.submission_id;
        submit.textContent = pending ? t('training.checking') : failed || savedPayload ? t('training.retry') : t(show ? 'training.show' : 'training.submit');
        submit.disabled = pending || !state.can_submit || (!failed && !savedPayload && !show && !answerReady(input));
      };
      disable(pending || failed || Boolean(savedPayload) || !state.can_submit);
      form.append(bottom); main.append(form); continueRow.append(submit);
      input.addEventListener('input', () => { save('draft', state.attempt_id, input.value); refresh(); });
      input.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (submit.disabled) return;
        submit.disabled = true; const duration = flushClock();
        disable(true);
        try {
          let next;
          if (state.can_self_rate && answerEmpty(input) && !state.submission_id && !savedPayload) next = await post('reveal', {attempt_id: state.attempt_id});
          else {
            const payload = savedPayload || {request_id: state.submission_id || crypto.randomUUID(), attempt_id: state.attempt_id, answer: input.value, elapsed_ms: duration};
            savedPayload = payload;
            save('submission', state.attempt_id, JSON.stringify(payload));
            next = await post('submit', payload);
          }
          if (!alive(e, s)) return;
          current = next; renderQuestion();
        } catch (failure) { if (alive(e, s)) {
          error(main, failure);
          if (!savedPayload) disable(!state.can_submit);
          refresh(); if (!document.hidden && !savedPayload) activeSince = performance.now();
        } }
      });
      refresh();
      if (!state.can_submit) main.append(node('p', 'fieldError', t('training.noPermission')));
      if (state.error) main.append(node('p', 'fieldError', translateMessage(state.error)));
      if (pending) {
        const waiting = node('div', 'waiting'); waiting.setAttribute('role', 'status');
        waiting.append(node('span', 'spinner'), node('span', '', t('training.checking'))); main.append(waiting);
        timer = setTimeout(() => poll(e, s, state), 700);
      }
    }
    if (state.phase !== 'answer' && state.question.interaction && state.question.interaction.type !== 'text') {
      main.append(questionInput(state.question.interaction, {id: 'trainingSubmittedInteraction', stem, value: state.answer, disabled: true}).element);
    }
    if (state.answer_display && state.phase === 'done') {
      const answer = node('details', 'submittedAnswer');
      answer.append(node('summary', '', t('查看已提交答案')), node('pre', '', state.answer_display)); main.append(answer);
    }
    if (state.phase === 'done') {
      save('draft', state.attempt_id, null); save('submission', state.attempt_id, null); save('elapsed', state.attempt_id, null); clockKey = null;
      const result = state.result;
      const label = result.stop_requested ? t('training.stopped') : result.self_rating ? t('training.selfResult', {rating: t(ratingKeys[result.self_rating])}) : t(result.correct ? 'training.correct' : 'training.incorrect');
      const correct = result.stop_requested || result.self_rating > 1 || result.correct;
      const verdict = node('div', `feedback trainingVerdict ${correct ? 'correct' : 'incorrect'}`); verdict.setAttribute('role', 'status');
      const body = node('div'); body.append(node('span', 'feedbackTitle', label));
      if (result.reason) body.append(node('div', 'feedbackReason', result.reason));
      if (state.stopped && !result.stop_requested) body.append(node('div', 'feedbackReason', t('training.stopped')));
      else if (result.practice_only && !state.stopped) body.append(node('div', 'feedbackReason', t('training.practice')));
      if (state.due_at) body.append(node('div', 'feedbackReason', t('training.nextDue', {time: formatDate(state.due_at)})));
      verdict.append(node('span', 'feedbackIcon', correct ? '✓' : '!'), body); main.append(verdict);
    }
    const reference = referenceAnswer(state); if (reference) main.append(reference);
    if (state.explanation_html)
      main.append(node('h3', 'exampleExplanationHeader', t('Explanation · 解析')),
        prepareAnswerContent(helpableContent(html(state.explanation_html, 'courseContent trainingExplanation'), reportContext, `explanation:${state.question_id}`, state.version)));
    if (state.phase === 'shown' || state.phase === 'done' && (state.result.self_rating || state.result.stop_requested)) {
      const ratings = node('div', 'trainingRatings');
      for (let rating = 1; rating <= 5; rating++) {
        const item = button('', `trainingRating rating${rating}`, async () => {
          for (const b of ratings.children) b.disabled = true;
          try {
            const next = rating === 5 ? await post('stop', {attempt_id: state.attempt_id})
              : await post('rate', {attempt_id: state.attempt_id, rating, elapsed_ms: flushClock()});
            if (!alive(e, s)) return;
            current = next; await refreshGroup(e, s); if (alive(e, s)) renderQuestion();
          } catch (failure) { if (alive(e, s)) { error(main, failure); for (const b of ratings.children) b.disabled = !state.can_self_rate || !state.can_submit; } }
        });
        item.append(node('span', '', String(rating)), node('small', '', t(ratingKeys[rating])));
        item.disabled = state.phase === 'done' || !state.can_self_rate || !state.can_submit;
        item.setAttribute('aria-pressed', String(state.result?.self_rating === rating || rating === 5 && Boolean(state.result?.stop_requested)));
        item.dataset.rating = rating; ratings.append(item);
      }
      main.append(ratings);
    }
    if (state.can_learn && state.can_review_early) {
      const redo = button(t('training.redo'), 'secondaryButton trainingRedo', () => select(state.question_id, true));
      redo.disabled = ['pending', 'running'].includes(state.submission_status); navigation.append(redo);
    }
    if (continueRow.childElementCount) navigation.append(continueRow);
    main.append(navigation);
  }
  async function refreshGroup(e, s) {
    const updated = await request('training/group?' + query({bank_id: group.bank_id, group_id: group.id}));
    if (!alive(e, s)) return;
    group = updated; renderSidebar();
  }
  async function poll(e, s, state) {
    try {
      const next = await request('training/submission?' + query({submission_id: state.submission_id}));
      if (!alive(e, s)) return;
      current = next;
      if (next.phase === 'done') await refreshGroup(e, s);
      if (alive(e, s)) renderQuestion();
    } catch (failure) { if (alive(e, s)) error(main, failure, () => poll(e, s, state)); }
  }
  async function loadHistory(box, e, s, questionId, offset = 0) {
    try {
      const data = await request('training/history?' + query({bank_id: group.bank_id, question_id: questionId, offset}));
      if (!alive(e, s) || !box.isConnected) return;
      for (const item of data.items) {
        if (item.id === group.questions.find(q => q.id === questionId).latest_attempt_id) continue;
        const entry = button(`${formatDate(item.created_at)} · ${t(item.phase === 'done' ? 'training.saved' : item.phase === 'shown' ? 'training.finishRating' : 'training.resume')}`,
          'historyItem trainingHistoryItem', () => select(questionId, false, item.id));
        if (current.attempt_id === item.id) entry.setAttribute('aria-current', 'step'); box.append(entry);
      }
      if (data.next_offset !== null) { const more = button(t('training.more'), 'textButton', () => { more.remove(); void loadHistory(box, e, s, questionId, data.next_offset); }); box.append(more); }
    } catch (failure) { if (alive(e, s)) error(box, failure, () => loadHistory(box, e, s, questionId, offset)); }
  }
  function renderReviews(data) {
    const section = node('section', 'trainingReviewSection'); section.append(node('h2', 'trainingSectionTitle', t('training.reviewCards')));
    if (data.all_materials) section.append(node('p', 'inputHint', t('admin.allCards')));
    const grid = node('div', 'atomicReviewList'); section.append(grid);
    for (const item of data.items) {
      const card = node('article', 'courseChoice'); card.append(node('h3', '', item.title), node('p', '', `${item.bank_title} · ${item.group_title}`),
        link(t('training.review'), groupPath(item.bank_id, item.group_id, item.question_id) + '&review=1', 'primaryButton')); grid.append(card);
    }
    if (!data.items.length) grid.append(node('p', 'portalEmpty', t('training.noDue')));
    if (data.next_due_at) section.append(node('p', '', t('training.nextDue', {time: formatDate(data.next_due_at)})));
    return section;
  }
  return {open, stop, renderReviews};
}
