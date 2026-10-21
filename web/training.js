import {t, translateMessage} from './i18n.js?v=8405fcd52d52c0b6';
import {answerReady, answerEmpty} from './question-input.js?v=8405fcd52d52c0b6';
import {createLearningCache} from './learning-cache.js?v=8405fcd52d52c0b6';
import {appendCatalogOutline} from './catalog-outline.js?v=8405fcd52d52c0b6';
import {helpableContent} from './content-report.js?v=8405fcd52d52c0b6';
import {ratingChoices} from './self-assessment.js?v=8405fcd52d52c0b6';
import {hyperskillQuestion,localHyperskill} from './hyperskill.js?v=8405fcd52d52c0b6';

const node = (tag, cls = '', text) => { const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const button = (text, cls, action) => { const n = node('button', cls, text); n.type = 'button'; n.addEventListener('click', action); return n; };
const encode = encodeURIComponent;
const query = fields => new URLSearchParams(fields).toString();
const ratingKeys = ['', 'training.again', 'training.hard', 'training.good', 'training.easy', 'training.retire'];
import {answerEditor, readerFrame, readerStepTitle, renderReaderStep, readerHistoryGroup, readerProgress, readerNavigation, renderReviewReader} from './learning-ui.js?v=8405fcd52d52c0b6';

export function createTrainingView(bridge) {
  const {root, request, href, formatDate, getAccess} = bridge;
  const readerTemplate = document.getElementById('appLayout').cloneNode(true);
  let cacheAccess = getAccess();
  const cache = createLearningCache(() => cacheAccess);
  let epoch = 0, selection = 0, timer = null, group = null, current = null, sidebar, main;
  let elapsed = 0, activeSince = null, clockKey = null, visibilityHandler = null;
  let reader = null;
  let selectedRoot = null;
  const expandedCatalog = new Map();
  let reviewCard = false, reviewQuestion = null;
  let selectedIntroduction = null;
  let hyperskillBusy = false;
  let hyperskillAvailability = null, hyperskillProbe = null;
  const hyperskillWarm = new Map();
  const attempts = new Map(), histories = new Map(), opening = new Map();
  let validationTimer = null;
  let writes = 0, change = 0;
  const key = (kind, id) => `graspmemoedu:training:${cacheAccess?.learner_id}:${kind}:${id}`;
  const read = (kind, id) => cache.read(localStorage, key(kind, id));
  const save = (kind, id, value) => cache.write(localStorage, key(kind, id), value);
  const alive = (e, s = selection) => e === epoch && s === selection;
  const link = (label, path, cls = '') => { const a = node('a', cls, label); a.href = href(path); return a; };
  const bankPath = bank => `/banks/${encode(bank)}${selectedRoot ? '?root=' + encode(selectedRoot) : ''}`;
  const groupPath = (bank, id, question) => {
    const params = new URLSearchParams();
    if (question) params.set('question', question);
    if (selectedRoot && !reviewCard) params.set('root', selectedRoot);
    return `/banks/${encode(bank)}/${encode(id)}${params.size ? '?' + params : ''}`;
  };
  function remember(value) {
    if (value?.attempt_id) attempts.set(value.attempt_id, value);
    return value;
  }
  async function ensureAttempt(id) {
    const value = attempts.get(id);
    if (!value?.preview) return;
    if (opening.has(id)) return opening.get(id);
    const e = epoch;
    const task = request('training/open', {method: 'POST', body: {request_id: id,
      bank_id: value.bank_id, group_id: value.group_id, question_id: value.question_id}}).then(next => {
      if (e !== epoch || cacheAccess?.learner_id !== getAccess()?.learner_id) throw Error(t('review.invalidState'));
      remember(next);
      const q = group.questions.find(q => q.id === next.question_id);
      if (q) q.latest_attempt_id = next.attempt_id;
      if (current?.attempt_id === id) current = next;
      if (next.version !== value.version) throw Error(t('review.invalidState'));
    }).finally(() => opening.delete(id));
    opening.set(id, task);
    return task;
  }
  const post = async (name, body) => {
    const e = epoch, prior = attempts.get(body.attempt_id);
    writes++; change++;
    try {
      if (body.attempt_id) await ensureAttempt(body.attempt_id);
      const next = remember(await request(`training/${name}`, {method: 'POST', body}));
      if (e === epoch && next.phase === 'done' && prior && prior.phase !== 'done') {
        const q = group?.questions.find(q => q.id === next.question_id && q.latest_attempt_id === next.attempt_id);
        if (q) {
          q.completed = true;
          if (q.completed_count !== null) q.completed_count = (q.completed_count || 0) + 1;
          q.latest_attempt_phase = 'done';
          renderSidebar();
        }
      }
      return next;
    } finally { writes--; change++; }
  };
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
    clearTimeout(validationTimer); attempts.clear(); histories.clear(); opening.clear();
    hyperskillAvailability = hyperskillProbe = null; hyperskillWarm.clear();
    reader?.destroy(); reader = null;
    reviewCard = false; reviewQuestion = null;
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
    reviewCard = params.get('review') === '1' || params.get('card') === '1';
    reviewQuestion = reviewCard ? params.get('question') : null;
    selectedRoot = reviewCard ? null : params.get('root');
    const e = ++epoch; ++selection;
    root.replaceChildren(node('p', '', t('training.loading')));
    const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
    if (parts.length === 1) {
      const data = await request('training/catalog'); if (e !== epoch) return;
      const grid = node('div', 'courseGrid'); root.replaceChildren(node('h1', 'portalPageTitle', t('training.banks')), grid);
      grid.before(bridge.taskVisibilityOptions(root));
      const bankCard = (bank, rootId = null) => {
        const card = node('article', 'courseChoice');
        card.dataset.bankId = bank.id;
        card.dataset.dependencyReady = String(bank.dependency_ready !== false);
        card.append(node('h2', '', bank.title), node('p', 'courseDescription', bank.description),
          link(t('training.train'), `/banks/${encode(bank.id)}${rootId ? '?root=' + encode(rootId) : ''}`, 'primaryButton courseLearn'));
        return card;
      };
      for (const bank of data.banks) {
        const card = bankCard(bank);
        appendCatalogOutline(card, bank, bankCard, {expanded: expandedCatalog, keyPrefix: 'select:bank'}); grid.append(card);
      }
      if (!data.banks.length) grid.append(node('p', 'portalEmpty', t('training.empty')));
    } else if (parts.length === 2) {
      const bank = await request('training/bank?' + query({bank_id: parts[1], ...(selectedRoot ? {root_id: selectedRoot} : {})})); if (e !== epoch) return;
      const count = bank.groups.reduce((n, g) => n + g.question_count, 0);
      const completed = bank.groups.reduce((n, g) => n + (g.completed_count || 0), 0);
      const containers = bank.groups.filter(g => g.id !== selectedRoot && !(g.id === bank.id && !g.parent_id)
        && bank.groups.some(child => child.parent_id === g.id));
      const groupTasks = bank.groups.filter(g => g.question_count || g.introduction_count).map(g => ({id: g.id, topic_id: g.id, type: 'Lesson',
          title: g.title, topic_kind: g.question_count ? 'lesson' : 'introduction',
          status: g.completed ? 'completed' : g.completed_count ? 'in_progress' : 'not_started', completed_at: g.completed_at,
          result: g.completed ? 'completed' : null,
          dependency_ready: g.dependency_ready, prerequisites: g.prerequisites, prerequisite_details: g.prerequisite_details,
          question_count: g.question_count, progress: g.progress ?? (g.question_count ? g.completed_count / g.question_count * 100 : 0)}));
      const data = {course: {id: bank.id, title: bank.title, root_id: selectedRoot, progress: count ? completed / count * 100 : 0},
        tasks: groupTasks.filter(g => g.status !== 'completed'),
        history: groupTasks.filter(g => g.status === 'completed').sort((a, b) => (b.completed_at || '').localeCompare(a.completed_at || '')),
        pending_hierarchy: containers.map(g => ({...g, parent_id: containers.some(parent => parent.id === g.parent_id) ? g.parent_id : null,
          topic_ids: bank.groups.filter(child => (child.question_count || child.introduction_count)
          && (child.id === g.id || child.parent_id === g.id && !containers.some(c => c.id === child.id))).map(child => child.id)}))};
      const layout = node('div', 'dashboardLayout'), tasks = node('div', 'dashboardTasks');
      const pending = node('section', 'incompleteTasks'); pending.setAttribute('aria-label', t('training.groups'));
      tasks.append(bridge.taskVisibilityOptions(tasks));
      const historyList = node('section', 'completedTasks'); historyList.setAttribute('aria-label', t('portal.completed.tasks.68'));
      tasks.append(pending, historyList);
      layout.append(bridge.courseSidebar(data, {training: true, href: bankPath(bank.id), questionCount: count}), tasks);
      root.replaceChildren(layout);
      const view = {training: true, label: t('training.groups'), startLabel: t('training.start'),
        sourceOrder: bank.groups.map(g => g.id),
        target: task => groupPath(bank.id, task.id)};
      bridge.taskTree(pending, data, view);
      bridge.renderHistory(data, historyList, view);
      if (!data.tasks.length) pending.append(node('p', 'portalEmpty', t(data.history.length ? 'portal.all.current.tasks.in.this.course.are.complete.65' : 'training.empty')));
    } else if (parts.length === 3) {
      const nextGroup = await request('training/group?' + query({bank_id: parts[1], group_id: parts[2], reader: 1}));
      if (e !== epoch) return;
      group = cardGroup(nextGroup);
      for (const value of Object.values(nextGroup.reader_states || {})) remember(value);
      for (const [id, value] of Object.entries(nextGroup.reader_histories || {})) histories.set(id + ':0', value);
      if(group.external_providers?.includes('hyperskill')) void probeHyperskill(e);
      const style = node('style'); style.id = 'trainingMathStyle'; style.textContent = group.math_css; document.head.append(style);
      reader = readerFrame(readerTemplate, 'training');
      const refs = reader.refs;
      sidebar = refs.historyPanel; main = refs.stepCard;
      root.hidden = true; root.after(reader.frame); document.body.classList.add('topicPage');
      refs.loadingState.hidden = true; refs.courseShell.hidden = false;
      refs.topicHomeLink.href = href(reviewCard ? '/reviews' : bankPath(group.bank_id));
      refs.topicHomeLink.textContent = '← ' + t(reviewCard ? 'paper.back' : 'training.back');
      refs.topicFeedbackButton.hidden = true;
      refs.lessonTitle.textContent = reviewCard ? group.questions[0].title : group.title;
      reader.frame.querySelector('.eyebrow').textContent = t(reviewCard ? 'paper.title' : group.questions.length ? 'lesson.topic' : 'reader.introductionTask');
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
        && !(selected.latest_attempt_mode === 'review' && selected.latest_attempt_phase !== 'done'),
        reviewCard ? params.get('attempt') : null);
      scheduleValidation();
    } else throw new Error(t('portal.this.page.does.not.exist.24'));
  }
  function cardGroup(source) {
    if (!reviewCard) return source;
    const selected = source.questions.find(question => question.id === reviewQuestion);
    if (!selected) throw new Error(t('portal.this.page.does.not.exist.24'));
    return {...source, questions: [selected], introductions: [],
      content_order: [{type: 'question', id: selected.id}]};
  }
  function renderSidebar() {
    if (reviewCard) {
      const question = group.questions[0];
      renderReviewReader(reader.refs, {items: [{...question, completed: current?.phase === 'done',
        answer: current?.answer, result: current?.result}], phase: current?.phase,
        onSelect: () => { if (current) renderQuestion(); }});
      return;
    }
    sidebar.replaceChildren();
    group.questions.forEach((q, index) => {
      const {group: section, items} = readerHistoryGroup({contentId: q.id,
        title: t('training.exercise', {number: index + 1}),
        status: q.completed ? q.completed_count ? t('training.completed', {count: q.completed_count}) : t('portal.completed.6') : t('training.unanswered'),
        className: q.completed ? 'completed' : '', items: [{label: q.title, className: 'trainingQuestionLink',
          onSelect: () => select(q.id), dataset: {questionId: q.id},
          selected: current?.question_id === q.id && (current.preview || current.attempt_id === q.latest_attempt_id)}]});
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
    const completed = group.questions.filter(q => q.completed).length;
    readerProgress(reader.refs, {label: t('reader.modulesCompleted', {completed, total: group.questions.length}),
      status: t(completed === group.questions.length ? 'training.finished' : 'training.inProgress'),
      hidden: group.questions.length === 0, percent: Math.round(100 * completed / (group.questions.length || 1)),
      segments: group.questions.map(q => ({title: q.title, status: q.completed ? 'completed' : q.latest_attempt_id ? 'in_progress' : 'not_started'}))});
  }
  function navigationFor(id, primary, extra = []) {
    const pages = (group.content_order || group.questions.map(q => ({type: 'question', id: q.id}))).map(item => {
      const value = item.type === 'introduction' ? group.introductions.find(i => i.id === item.id)
        : group.questions.find(q => q.id === item.id);
      return {...value, kind: item.type};
    });
    const index = pages.findIndex(item => item.id === id);
    if (!reviewCard && !primary && group.questions.length && group.questions.every(q => q.completed)
        && (!current || current.phase === 'done')) {
      primary = button(t('training.finishLearning'), 'primaryButton trainingFinish', () => {
        bridge.progressChanged();
        bridge.completeGroup(group.bank_id, group.id, selectedRoot);
      });
    }
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
    history.replaceState(null, '', href(groupPath(group.bank_id, group.id) + (selectedRoot ? '&' : '?') + 'introduction=' + encode(id)));
    navigationFor(id);
  }
  async function select(questionId, fresh = false, attemptId = null) {
    flushClock(); clockKey = null; clearTimeout(timer);
    reader.closeOnMobile();
    selectedIntroduction = null;
    const e = epoch, s = ++selection, q = group.questions.find(q => q.id === questionId);
    try {
      const previous = attemptId || (!fresh && q.latest_attempt_id);
      let state;
      if (!fresh) state = previous ? attempts.get(previous) : group.reader_states?.[questionId];
      if (!state) main.replaceChildren(node('p', '', t('training.loading')));
      if (!state && previous) state = await request('training/state?' + query({attempt_id: previous}));
      else if (!state) {
        const openKey = `${group.bank_id}:${group.id}:${questionId}:open`;
        const requestId = read('submission', openKey) || crypto.randomUUID();
        save('submission', openKey, requestId);
        state = await post('open', {request_id: requestId, bank_id: group.bank_id, group_id: group.id, question_id: questionId});
        if (!alive(e, s)) return;
        save('submission', openKey, null); q.latest_attempt_id = state.attempt_id;
      }
      if (!alive(e, s)) return;
      if (state.bank_id !== group.bank_id || state.group_id !== group.id || state.question_id !== questionId) throw new Error(t('portal.this.page.does.not.exist.24'));
      history.replaceState(null, '', href(groupPath(group.bank_id, group.id, questionId)
        + (reviewCard ? '&card=1&attempt=' + encode(state.attempt_id) : '')));
      current = remember(state); startClock(state); renderSidebar(); renderQuestion();
      if(state.hyperskill && !state.preview && state.phase==='answer' && state.can_submit) void warmHyperskill(state,e);
    } catch (failure) { if (alive(e, s)) error(main, failure, () => select(questionId, fresh, attemptId)); }
  }
  function renderQuestion() {
    clearTimeout(timer);
    const state = current, e = epoch, s = selection;
    remember(state);
    if (reviewCard) renderSidebar();
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
    reader.refs.saveStatus.textContent = state.preview ? '' : t(state.submission_status === 'pending' || state.submission_status === 'running' ? '答案已提交，正在判题' : '学习进度已保存');
    let primary; const extra = [];
    if (state.phase === 'answer' && state.hyperskill) {
      hyperskillAnswer(state, e, s);
    } else if (state.phase === 'answer' && state.academy) {
      primary = academyAnswer(state, e, s);
    } else if (state.phase === 'answer') {
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
      if (state.hyperskill) {
        const receipts=node('div','hyperskillTraining');
        hyperskillQuestion(receipts,{question:{hyperskill:state.hyperskill},history:true,disabled:true});
        main.append(receipts);
      }
    }
    if (!state.academy && !state.hyperskill && (state.phase === 'shown' || state.phase === 'done' && (state.result.self_rating || state.result.stop_requested))) {
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
  function probeHyperskill(e) {
    if(!hyperskillProbe) hyperskillProbe=localHyperskill('health',{}, {timeout:1800})
      .then(value=>value.ready===true).catch(()=>false).then(ready=>{
        if(e===epoch) {hyperskillAvailability=ready;paintHyperskillConnection();}
        if(ready&&e===epoch)void localHyperskill('warmup',{}).catch(()=>{});
        return ready;
      });
    return hyperskillProbe;
  }
  function connectionText(state) {
    if(hyperskillAvailability===null)return '正在后台检查连接，可以先作答。';
    if(hyperskillAvailability)return hyperskillWarm.get(state.attempt_id)?.ready?
      'Hyperskill 已就绪。':'正在后台准备 Hyperskill 判题，可以先作答。';
    return state.hyperskill.local_ready?'已具备验证过的参考答案，可直接提交。':
      state.hyperskill.type==='code'?'代码题需要本机 Hyperskill 服务进行判题。':'此题尚无已验证答案，首次判题需要连接 Hyperskill。';
  }
  function paintHyperskillConnection() {
    if(!current?.hyperskill||!main)return;
    const status=main.querySelector('.hyperskillConnection');
    if(status)status.textContent=connectionText(current);
    const send=main.querySelector('[data-hyperskill-submit]');
    if(send)send.textContent=hyperskillAvailability===false&&current.hyperskill.local_ready?'提交答案':'提交至 Hyperskill';
  }
  async function warmHyperskill(state,e,force=false) {
    if(!force && !await probeHyperskill(e))return null;
    if(e!==epoch)return null;
    let entry=hyperskillWarm.get(state.attempt_id);
    if(entry&&!force)return entry.promise;
    entry={ready:false};hyperskillWarm.set(state.attempt_id,entry);
    entry.promise=(async()=>{
      try {
        await ensureAttempt(state.attempt_id);
        const grant=await request('academy/training/launch',{method:'POST',body:{attempt_id:state.attempt_id}});
        if(e!==epoch)return null;
        const result=await localHyperskill('open',{ticket:grant.ticket});
        if(e!==epoch)return null;
        save('hyperskill',state.attempt_id,JSON.stringify(result));entry.ready=true;
        if(current?.attempt_id===state.attempt_id) {
          if((!state.hyperskill.prepared||result.ide?.watching)&&!hyperskillBusy)renderQuestion();
          else paintHyperskillConnection();
        }
        return result;
      } catch(failure) {entry.error=failure;return null;}
    })();
    return entry.promise;
  }
  function hyperskillAnswer(state, e, s) {
    let data;
    try { data = JSON.parse(read('hyperskill', state.attempt_id)); } catch {}
    if(!data && state.hyperskill.prepared)data={interaction:state.hyperskill.interaction,
      local:!hyperskillAvailability,last:state.hyperskill.local_last};
    if(data)data={...data,connection:connectionText(state)};
    const action = async (name, answer) => {
      if (hyperskillBusy || !state.can_submit) return;
      hyperskillBusy = true; if(name!=='status')renderQuestion();
      try {
        let saved;try{saved=JSON.parse(read('hyperskill',state.attempt_id));}catch{}
        data=saved||data;
        let result;
        if (name === 'open') {
          hyperskillProbe=null;hyperskillAvailability=null;
          if(!await probeHyperskill(e))throw Error('本机 Hyperskill 服务尚未连接。');
          result=await warmHyperskill(state,e,true);
          if(!result)throw hyperskillWarm.get(state.attempt_id)?.error||Error('Hyperskill 连接失败。');
        } else if (name === 'finish') {
          await request('academy/training/finish', {method:'POST',body:{attempt_id:state.attempt_id}});
        } else {
          const ready=await probeHyperskill(e);
          if((name==='status'||name==='submit')&&data?.local_pending) {
            const next=await post('hyperskill-submit',data.local_pending);
            result={interaction:state.hyperskill.interaction,local:true,last:next.hyperskill.local_last};
          } else if(name==='submit' && !ready && !data?.pending) {
            if(!state.hyperskill.local_ready)throw Error('此题需要本机 Hyperskill 服务完成判题。');
            const payload={attempt_id:state.attempt_id,request_id:crypto.randomUUID(),answer,elapsed_ms:flushClock()};
            data={...data,local:true,local_pending:payload};
            save('hyperskill',state.attempt_id,JSON.stringify(data));
            const next=await post('hyperskill-submit',payload);
            result={interaction:state.hyperskill.interaction,local:true,last:next.hyperskill.local_last};
          } else if(name==='retry' && data?.local) {
            result={interaction:state.hyperskill.interaction,local:true};
          } else {
          if(!data?.session && ['submit','ide'].includes(name)) {
            data=await warmHyperskill(state,e);
            if(!data)throw hyperskillWarm.get(state.attempt_id)?.error||Error('本机 Hyperskill 服务尚未连接。');
          }
          const requestId = data.request_id || crypto.randomUUID();
          if (name === 'submit') {
            data.request_id = requestId; save('hyperskill',state.attempt_id,JSON.stringify(data));
          }
          result = await localHyperskill(name,{session:data.session,attempt_id:data.attempt_id,
            ...(name==='submit'?{answer,request_id:requestId}:{})});
          }
        }
        if (!alive(e,s)) return;
        if (result) save('hyperskill',state.attempt_id,JSON.stringify(result));
        if (name==='retry') save('draft',state.attempt_id,null);
        const next = await request('training/state?' + query({attempt_id:state.attempt_id}));
        if (!alive(e,s)) return;
        current = next;
        if (next.phase==='done') { flushClock(); await refreshGroup(e,s); }
      } catch (failure) {
        if (alive(e,s)) {
          if (name==='submit' && data && !data.local && data.request_id) save('hyperskill',state.attempt_id,JSON.stringify({
            ...data,pending:true,message:'提交状态尚未确认，请先刷新结果。'}));
          if(data?.local_pending) {
            const rejected=failure.status>=400&&failure.status<500&&![408,429].includes(failure.status);
            save('hyperskill',state.attempt_id,JSON.stringify(rejected?
              {interaction:state.hyperskill.interaction,local:true}:
              {...data,pending:true,message:'正在确认原答案的判题结果，请稍候。'}));
          }
          hyperskillBusy=false; renderQuestion(); error(main,failure);
          if(name==='status')timer=setTimeout(()=>{if(alive(e,s))void action('status');},5000);
          return;
        }
      } finally { hyperskillBusy=false; }
      if (alive(e,s)) renderQuestion();
    };
    const box=node('div','hyperskillTraining');
    hyperskillQuestion(box,{question:{...state.question,hyperskill:state.hyperskill},history:false,
      disabled:hyperskillBusy||!state.can_submit,data,draft:read('draft',state.attempt_id)||'',
      onDraft:value=>save('draft',state.attempt_id,value),action,finish:()=>action('finish')});
    main.append(box);
    if ((data?.pending||data?.ide?.watching) && !hyperskillBusy) timer=setTimeout(()=>{if(alive(e,s))void action('status');},2500);
    else if (data?.last?.status==='correct' && !hyperskillBusy) timer=setTimeout(()=>pollAcademy(e,s,state),1500);
  }
  function academyAnswer(state, e, s) {
    const adapter = async (name, body) => {
      await ensureAttempt(body.attempt_id);
      return request('academy/training/' + name, {method: 'POST', body});
    };
    const open = button(t(state.academy.opened ? 'academy.resume' : 'academy.open'), 'primaryButton academyTrainingOpen', async () => {
      open.disabled = true;
      try {
        const result = await adapter('launch', {attempt_id: state.attempt_id});
        if (!alive(e, s)) return;
        current = result.state; renderQuestion();
        // Only a deliberate click opens the native app; polling never launches it.
        location.href = result.url;
      } catch (failure) { if (alive(e, s)) { error(main, failure); open.disabled = !state.can_submit; } }
    });
    open.disabled = !state.can_submit;
    main.append(node('p', 'inputHint', t('academy.reset')));
    const status = node('p', 'inputHint academyTrainingStatus', state.academy.opened
      ? t('academy.checks', {count: state.academy.checks}) : t('academy.waiting'));
    status.setAttribute('role', 'status'); main.append(status);
    const finish = async rating => {
      try {
        const next = await adapter('finish', {attempt_id: state.attempt_id, ...(rating ? {rating} : {})});
        if (!alive(e, s)) return;
        flushClock(); current = next; await refreshGroup(e, s); if (alive(e, s)) renderQuestion();
      } catch (failure) { if (alive(e, s)) error(main, failure); }
    };
    if (state.academy.kind === 'manual') main.append(ratingChoices({
      labels: ratingKeys.slice(1, 5), disabled: !state.can_submit || !state.academy.opened, onRate: finish}));
    else {
      main.append(node('p', 'inputHint', t('academy.scoring')));
      const end = button(t('academy.finish'), 'secondaryButton academyTrainingFinish', () => finish());
      end.disabled = !state.can_submit || !state.academy.opened || !state.academy.checks; main.append(end);
    }
    if (state.academy.round_id) timer = setTimeout(() => pollAcademy(e, s, state), 1500);
    return open;
  }
  async function pollAcademy(e, s, state) {
    try {
      const next = await request('training/state?' + query({attempt_id: state.attempt_id}));
      if (!alive(e, s)) return;
      if (next.phase === state.phase && JSON.stringify(next.academy) === JSON.stringify(state.academy)) {
        timer = setTimeout(() => pollAcademy(e, s, next), 1500);
        return;
      }
      current = next;
      if (next.phase === 'done') { flushClock(); await refreshGroup(e, s); }
      if (alive(e, s)) renderQuestion();
    } catch (failure) { if (alive(e, s)) error(main, failure, () => pollAcademy(e, s, state)); }
  }
  async function refreshGroup(e, s) {
    const updated = await request('training/group?' + query({bank_id: group.bank_id, group_id: group.id}));
    if (!alive(e, s)) return;
    group = {...group, ...cardGroup(updated)};
    histories.clear(); renderSidebar();
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
      const key = questionId + ':' + offset;
      const data = histories.get(key) || await request('training/history?' + query({bank_id: group.bank_id, question_id: questionId, offset}));
      if (!alive(e, s) || !box.isConnected) return;
      histories.set(key, data);
      for (const item of data.items) {
        if (item.id === group.questions.find(q => q.id === questionId).latest_attempt_id) continue;
        const entry = button(`${formatDate(item.created_at)} · ${t(item.phase === 'done' ? 'training.saved' : item.phase === 'shown' ? 'training.finishRating' : 'training.resume')}`,
          'historyItem trainingHistoryItem', () => select(questionId, false, item.id));
        if (current.attempt_id === item.id) entry.setAttribute('aria-current', 'step'); box.append(entry);
      }
      if (data.next_offset !== null) { const more = button(t('training.more'), 'textButton', () => { more.remove(); void loadHistory(box, e, s, questionId, data.next_offset); }); box.append(more); }
    } catch (failure) { if (alive(e, s)) error(box, failure, () => loadHistory(box, e, s, questionId, offset)); }
  }
  function scheduleValidation() {
    clearTimeout(validationTimer);
    if (group) validationTimer = setTimeout(async () => {
      const e = epoch, revision = change;
      try {
        if (document.hidden || opening.size || writes || root.inert || !group) return;
        const next = await request('training/group?' + query({bank_id: group.bank_id, group_id: group.id, reader: 1}), {fresh: true});
        if (e !== epoch || revision !== change || writes) return;
        const old = current;
        const sidebarChanged = JSON.stringify(group.questions) !== JSON.stringify(next.questions);
        const versionChanged = group.version !== next.version;
        if (versionChanged) { attempts.clear(); histories.clear(); }
        group = cardGroup(next);
        for (const value of Object.values(next.reader_states || {})) remember(value);
        for (const [id, value] of Object.entries(next.reader_histories || {})) histories.set(id + ':0', value);
        if (versionChanged) {
          if (selectedIntroduction && group.introductions?.some(i => i.id === selectedIntroduction)) selectIntroduction(selectedIntroduction);
          else if (group.questions.length) await select(group.questions.find(q => q.id === old?.question_id)?.id || group.questions[0].id);
          else if (group.introductions?.length) selectIntroduction(group.introductions[0].id);
          else { current = null; main.replaceChildren(); renderSidebar(); }
        } else if (old) {
          const updated = old.preview ? next.reader_states[old.question_id] : attempts.get(old.attempt_id);
          if (updated && JSON.stringify(updated) !== JSON.stringify(old)) {
            current = updated; renderSidebar(); renderQuestion();
          } else if (sidebarChanged) renderSidebar();
        } else if (sidebarChanged) {
          renderSidebar();
        }
      } catch (failure) {
        if (e === epoch && main) error(main, failure);
      } finally { if (e === epoch) scheduleValidation(); }
    }, 5000);
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
