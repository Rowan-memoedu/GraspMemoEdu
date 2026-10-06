import {t, translateMessage} from './i18n.js?v=0da1084cc9b59bb4';
import {answerReady, answerEmpty} from './question-input.js?v=0da1084cc9b59bb4';
import {createLearningCache} from './learning-cache.js?v=0da1084cc9b59bb4';
import {helpableContent} from './content-report.js?v=0da1084cc9b59bb4';
import {ratingChoices} from './self-assessment.js?v=0da1084cc9b59bb4';

const node = (tag, cls = '', text) => { const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const button = (text, cls, action) => { const n = node('button', cls, text); n.type = 'button'; n.addEventListener('click', action); return n; };
const encode = encodeURIComponent;
const query = fields => new URLSearchParams(fields).toString();
const ratingKeys = ['', 'training.again', 'training.hard', 'training.good', 'training.easy', 'training.retire'];
import {answerEditor, readerFrame, readerStepTitle, renderReaderStep, readerHistoryGroup, readerProgress, readerNavigation} from './learning-ui.js?v=0da1084cc9b59bb4';

export function createTrainingView(bridge) {
  const {root, request, href, formatDate, getAccess} = bridge;
  const readerTemplate = document.getElementById('appLayout').cloneNode(true);
  let cacheAccess = getAccess();
  const cache = createLearningCache(() => cacheAccess);
  let epoch = 0, selection = 0, timer = null, group = null, current = null, sidebar, main;
  let elapsed = 0, activeSince = null, clockKey = null, visibilityHandler = null;
  let reader = null;
  let selectedIntroduction = null;
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
    visibilityHandler = null; group = current = selectedIntroduction = null; cache.clear();
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
        tasks: bank.groups.filter(g => g.question_count || g.introduction_count).map(g => ({id: g.id, topic_id: g.id, type: 'Lesson',
          title: g.title, topic_kind: g.question_count ? 'lesson' : 'introduction',
          question_count: g.question_count, progress: g.question_count ? g.completed_count / g.question_count * 100 : 0})),
        pending_hierarchy: containers.map(g => ({...g, topic_ids: bank.groups.filter(child => (child.question_count || child.introduction_count)
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
      if (!data.tasks.length) pending.append(node('p', 'portalEmpty', t('training.empty')));
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
      reader.frame.querySelector('.eyebrow').textContent = t(group.questions.length ? 'lesson.topic' : 'reader.introductionTask');
      reader.frame.querySelector('.lessonToolbar').hidden = true;
      renderSidebar();
      visibilityHandler = () => { flushClock(); if (!document.hidden && current?.phase === 'answer' && !current.submission_id) activeSince = performance.now(); };
      document.addEventListener('visibilitychange', visibilityHandler);
      const selected = group.questions.find(q => q.id === params.get('question')) || group.questions[0];
      const intro = group.introductions?.find(i => i.id === params.get('introduction'))
        || (!params.has('question') && group.content_order?.[0]?.type === 'introduction'
          ? group.introductions.find(i => i.id === group.content_order[0].id) : null);
      if (intro) selectIntroduction(intro.id);
      else if (selected) await select(selected.id, params.get('review') === '1'
        && !(selected.latest_attempt_mode === 'review' && selected.latest_attempt_phase !== 'done'));
    } else throw new Error(t('portal.this.page.does.not.exist.24'));
  }
  function renderSidebar() {
    sidebar.replaceChildren();
    group.questions.forEach((q, index) => {
      const {group: section, items} = readerHistoryGroup({contentId: q.id,
        title: t('training.exercise', {number: index + 1}),
        status: q.completed_count ? t('training.completed', {count: q.completed_count}) : t('training.unanswered'),
        className: q.completed_count ? 'completed' : '', items: [{label: q.title, className: 'trainingQuestionLink',
          onSelect: () => select(q.id), dataset: {questionId: q.id},
          selected: current?.question_id === q.id && current.attempt_id === q.latest_attempt_id}]});
      sidebar.append(section);
      if (current?.question_id === q.id && getAccess()?.features?.includes('review_history')) void loadHistory(items, epoch, selection, q.id);
    });
    for (const intro of group.introductions || []) {
      const label = readerStepTitle({...intro, kind: 'introduction'});
      const {group: section} = readerHistoryGroup({contentId: intro.id, title: label,
        status: selectedIntroduction === intro.id ? t('阅读中') : '', items: [{
          label, className: 'trainingIntroductionLink', onSelect: () => selectIntroduction(intro.id),
          dataset: {introductionId: intro.id}, selected: selectedIntroduction === intro.id}]});
      sidebar.append(section);
    }
    if (group.content_order) for (const item of group.content_order) {
      const section = [...sidebar.children].find(n => n.dataset.contentId === item.id);
      if (section) sidebar.append(section);
    }
    const completed = group.questions.filter(q => q.completed_count).length;
    readerProgress(reader.refs, {label: t('reader.modulesCompleted', {completed, total: group.questions.length}),
      status: t(completed === group.questions.length ? 'training.finished' : 'training.inProgress'),
      hidden: group.questions.length === 0, percent: Math.round(100 * completed / (group.questions.length || 1)),
      segments: group.questions.map(q => ({title: q.title, status: q.completed_count ? 'completed' : q.latest_attempt_id ? 'in_progress' : 'not_started'}))});
  }
  function navigationFor(id, primary, extra = []) {
    const pages = (group.content_order || group.questions.map(q => ({type: 'question', id: q.id}))).map(item => {
      const value = item.type === 'introduction' ? group.introductions.find(i => i.id === item.id)
        : group.questions.find(q => q.id === item.id);
      return {...value, kind: item.type};
    });
    const index = pages.findIndex(item => item.id === id);
    readerNavigation(main, {previous: index > 0 ? pages[index - 1] : null, next: index >= 0 ? pages[index + 1] : null,
      idPrefix: 'training', primary, nextAsPrimary: pages[index]?.kind === 'introduction', extra,
      onSelect: page => page.kind === 'introduction' ? selectIntroduction(page.id) : select(page.id)});
  }
  function selectIntroduction(id) {
    const intro = group.introductions?.find(i => i.id === id); if (!intro) return;
    flushClock(); clockKey = null; clearTimeout(timer); ++selection; current = null;
    selectedIntroduction = id; reader.closeOnMobile(); renderSidebar();
    const step = {...intro, kind: 'introduction'};
    renderReaderStep(main, step, {titleId: 'trainingStepTitle', counter: t('阅读')});
    reader.refs.footerPosition.textContent = readerStepTitle(step); reader.refs.saveStatus.textContent = '';
    history.replaceState(null, '', href(groupPath(group.bank_id, group.id) + '?introduction=' + encode(id)));
    navigationFor(id);
  }
  async function select(questionId, fresh = false, attemptId = null) {
    flushClock(); clockKey = null; clearTimeout(timer);
    if (current) reader.closeOnMobile();
    selectedIntroduction = null;
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
    const reportContext = {topic_id: 'training.' + state.bank_id, bank_id: state.bank_id,
      group_id: state.group_id, question_id: state.question_id};
    const result = state.phase === 'done' ? state.result : null;
    const feedback = result ? {correct: Boolean(result.stop_requested || result.self_rating > 1 || result.correct),
      title: result.stop_requested ? t('training.stopped') : result.self_rating ? t('training.selfResult', {rating: t(ratingKeys[result.self_rating])}) : t(result.correct ? 'training.correct' : 'training.incorrect'),
      reason: result.reason, details: [
        state.stopped && !result.stop_requested ? t('training.stopped') : result.practice_only && !state.stopped ? t('training.practice') : null,
        state.due_at ? t('training.nextDue', {time: formatDate(state.due_at)}) : null]} : null;
    const {stem} = renderReaderStep(main, {...state.question, answer: state.answer, answer_display: state.answer_display,
      explanation_html: state.explanation_html}, {titleId: 'trainingStepTitle', title: t('training.exercise', {number: index + 1}),
      counter: t('reader.modulePosition', {current: index + 1, total: group.questions.length}),
      showSubmittedInteraction: state.phase !== 'answer', submittedId: 'trainingSubmittedInteraction',
      showAnswer: Boolean(state.answer_display && state.phase === 'done'), feedback, feedbackClass: 'trainingVerdict',
      referenceSource: state, decorate: (body, kind) => {
        helpableContent(body, reportContext, `${kind}:${state.question_id}`, state.version);
        if (kind === 'question' && state.can_learn) {
          const actions = node('div', 'trainingNotebookActions'); actions.dataset.helpAction = 'notebook';
          const add = button('', 'textButton trainingNotebookButton', async () => {
            add.disabled = true;
            try {
              const next = await post('notebook', {attempt_id: state.attempt_id});
              if (!alive(e, s)) return;
              // Keep the current input, submission and elapsed-time clock intact.
              for (const key of ['in_error_notebook', 'awaiting_first_learning', 'can_review_early', 'due_at']) current[key] = next[key];
              refreshNotebook();
            } catch (failure) { if (alive(e, s)) {
              refreshNotebook();
              const message = node('p', 'fieldError', translateMessage(failure.message)); message.setAttribute('role', 'alert');
              actions.querySelector('.fieldError')?.remove(); actions.append(message);
            } }
          });
          const refreshNotebook = () => {
            add.replaceChildren();
            const icon = node('span', 'trainingNotebookIcon', '⊕'); icon.setAttribute('aria-hidden', 'true');
            add.append(icon, document.createTextNode(t(state.stopped ? 'training.stopped' : state.in_error_notebook ? 'training.notebookAdded' : 'training.addToNotebook')));
            add.disabled = Boolean(state.in_error_notebook || state.stopped);
            add.classList.toggle('isAdded', Boolean(state.in_error_notebook));
          };
          refreshNotebook(); actions.append(add); body.prepend(actions);
        }
        return body;
      }});
    reader.refs.footerPosition.textContent = t('reader.modulePosition', {current: index + 1, total: group.questions.length});
    reader.refs.saveStatus.textContent = t(state.submission_status === 'pending' || state.submission_status === 'running' ? '答案已提交，正在判题' : '学习进度已保存');
    let primary; const extra = [];
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
      form.append(bottom); main.append(form); primary = submit;
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
    if (state.phase === 'done') {
      save('draft', state.attempt_id, null); save('submission', state.attempt_id, null); save('elapsed', state.attempt_id, null); clockKey = null;
    }
    if (state.phase === 'shown' || state.phase === 'done' && (state.result.self_rating || state.result.stop_requested)) {
      const ratings = ratingChoices({labels: ratingKeys.slice(1),
        selected: state.result?.stop_requested ? 5 : state.result?.self_rating,
        disabled: state.phase === 'done' || !state.can_self_rate || !state.can_submit,
        onRate: async rating => {
          try {
            const next = rating === 5 ? await post('stop', {attempt_id: state.attempt_id})
              : await post('rate', {attempt_id: state.attempt_id, rating, elapsed_ms: flushClock()});
            if (!alive(e, s)) return;
            current = next; await refreshGroup(e, s); if (alive(e, s)) renderQuestion();
          } catch (failure) { if (alive(e, s)) error(main, failure); }
        }});
      main.append(ratings);
    }
    if (state.can_learn && state.can_review_early) {
      const redo = button(t('training.redo'), 'secondaryButton trainingRedo', () => select(state.question_id, true));
      redo.disabled = ['pending', 'running'].includes(state.submission_status); extra.push(redo);
    }
    navigationFor(state.question_id, primary, extra);
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
