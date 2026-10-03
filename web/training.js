import {t, translateMessage} from './i18n.js?v=318501d54750dd15';
import {questionInput, answerReady} from './question-input.js?v=318501d54750dd15';
import {createLearningCache} from './learning-cache.js?v=318501d54750dd15';

const node = (tag, cls = '', text) => { const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const button = (text, cls, action) => { const n = node('button', cls, text); n.type = 'button'; n.addEventListener('click', action); return n; };
const encode = encodeURIComponent;
const query = fields => new URLSearchParams(fields).toString();
const html = (content, cls = 'courseContent') => { const n = node('div', cls); n.innerHTML = content || ''; return n; };
const ratingKeys = ['', 'training.again', 'training.hard', 'training.good', 'training.easy'];

export function createTrainingView(bridge) {
  const {root, request, href, formatDate, getAccess} = bridge;
  let cacheAccess = getAccess();
  const cache = createLearningCache(() => cacheAccess);
  let epoch = 0, selection = 0, timer = null, group = null, current = null, sidebar, main;
  let elapsed = 0, activeSince = null, clockKey = null, visibilityHandler = null;
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
      const tree = node('div', 'trainingTree');
      root.replaceChildren(link(t('training.back'), '/banks', 'textButton'), node('h1', 'portalPageTitle', bank.title),
        node('h2', 'trainingSectionTitle', t('training.groups')), tree);
      function branch(parent, target) {
        for (const group of bank.groups.filter(g => (g.parent_id || null) === parent)) {
          const children = bank.groups.some(g => g.parent_id === group.id);
          const row = node(children ? 'details' : 'article', 'trainingGroup');
          const heading = node(children ? 'summary' : 'h3', '', group.title); row.append(heading);
          if (children) row.open = true;
          if (group.question_count) row.append(node('span', 'inputHint', t('training.count', {count: group.question_count})),
            link(t('training.start'), groupPath(bank.id, group.id), 'primaryButton'));
          if (children) { const nested = node('div', 'trainingBranch'); branch(group.id, nested); row.append(nested); }
          target.append(row);
        }
      }
      branch(null, tree);
    } else if (parts.length === 3) {
      const nextGroup = await request('training/group?' + query({bank_id: parts[1], group_id: parts[2]}));
      if (e !== epoch) return;
      group = nextGroup;
      const style = node('style'); style.id = 'trainingMathStyle'; style.textContent = group.math_css; document.head.append(style);
      const layout = node('div', 'trainingLayout'); sidebar = node('aside', 'trainingSidebar'); main = node('section', 'trainingQuestion');
      sidebar.setAttribute('aria-label', t('training.history')); layout.append(sidebar, main);
      root.replaceChildren(link(t('training.back'), `/banks/${encode(group.bank_id)}`, 'textButton'),
        node('h1', 'portalPageTitle', group.title), layout);
      renderSidebar();
      visibilityHandler = () => { flushClock(); if (!document.hidden && current?.phase === 'answer' && !current.submission_id) activeSince = performance.now(); };
      document.addEventListener('visibilitychange', visibilityHandler);
      const selected = group.questions.find(q => q.id === params.get('question')) || group.questions[0];
      if (selected) await select(selected.id, params.get('review') === '1'
        && !(selected.latest_attempt_mode === 'review' && selected.latest_attempt_phase !== 'done'));
    } else throw new Error(t('portal.this.page.does.not.exist.24'));
  }
  function renderSidebar() {
    sidebar.replaceChildren(node('h2', '', t('training.history')));
    group.questions.forEach((q, index) => {
      const item = button('', 'trainingQuestionLink', () => select(q.id));
      item.dataset.questionId = q.id;
      item.append(node('strong', '', t('training.exercise', {number: index + 1})), node('span', '', q.title));
      if (q.completed_count !== null) item.append(node('small', '', q.completed_count ? t('training.completed', {count: q.completed_count}) : t('training.unanswered')));
      if (current?.question_id === q.id) item.setAttribute('aria-current', 'step');
      sidebar.append(item);
    });
  }
  async function select(questionId, fresh = false, attemptId = null) {
    flushClock(); clockKey = null; clearTimeout(timer);
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
    const heading = node('h2', '', `${t('training.exercise', {number: index + 1})} · ${state.question.title}`);
    const stem = html(state.question.html); main.replaceChildren(heading, stem);
    if (state.mode === 'practice' && state.phase !== 'done') main.append(node('p', 'inputHint', t('training.practiceHint')));
    if (state.phase === 'answer') {
      const form = node('form', 'trainingAnswerForm'); form.id = 'trainingAnswerForm';
      const label = node('label', '', t('training.answer')); label.htmlFor = 'trainingAnswer';
      let input, field;
      let savedPayload = null;
      try { savedPayload = JSON.parse(read('submission', state.attempt_id)); } catch {}
      const draft = savedPayload?.answer ?? (state.answer || read('draft', state.attempt_id) || '');
      if (state.question.interaction && state.question.interaction.type !== 'text') {
        const widget = questionInput(state.question.interaction, {id: 'trainingAnswer', value: draft, stem, formId: form.id});
        input = widget.input; field = widget.element;
      } else { input = field = node('textarea', 'answerInput'); input.id = 'trainingAnswer'; input.value = draft; input.maxLength = 2000; input.rows = 5; }
      const submit = node('button', 'primaryButton'); submit.type = 'submit';
      const pending = ['pending', 'running'].includes(state.submission_status);
      const failed = state.submission_status === 'error';
      const refresh = () => {
        const show = state.can_self_rate && !input.value.trim() && !state.submission_id;
        submit.textContent = pending ? t('training.checking') : failed || savedPayload ? t('training.retry') : t(show ? 'training.show' : 'training.submit');
        submit.disabled = pending || !state.can_submit || (!failed && !savedPayload && !show && !answerReady(input));
      };
      input.disabled = pending || failed || Boolean(savedPayload) || !state.can_submit;
      if (input.questionControl && input.disabled)
        for (const control of field.querySelectorAll('input,select,button,textarea')) control.disabled = true;
      form.append(label, field, submit); main.append(form);
      form.addEventListener('input', () => { save('draft', state.attempt_id, input.value); refresh(); });
      input.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
      form.addEventListener('submit', async event => {
        event.preventDefault(); if (submit.disabled) return;
        submit.disabled = true; const duration = flushClock();
        for (const control of field.matches('textarea') ? [field] : field.querySelectorAll('input,select,button,textarea')) control.disabled = true;
        try {
          let next;
          if (state.can_self_rate && !input.value.trim() && !state.submission_id && !savedPayload) next = await post('reveal', {attempt_id: state.attempt_id});
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
          if (!savedPayload) for (const control of field.matches('textarea') ? [field] : field.querySelectorAll('input,select,button,textarea')) control.disabled = !state.can_submit;
          refresh(); if (!document.hidden && !savedPayload) activeSince = performance.now();
        } }
      });
      refresh();
      if (!state.can_submit) main.append(node('p', 'fieldError', t('training.noPermission')));
      if (state.error) main.append(node('p', 'fieldError', translateMessage(state.error)));
      if (pending) timer = setTimeout(() => poll(e, s, state), 700);
    }
    if (state.explanation_html) {
      if (state.answer_display) main.append(node('h3', '', t('training.answer')), node('pre', 'trainingSavedAnswer', state.answer_display));
      main.append(node('h3', '', t('training.explanation')), html(state.explanation_html, 'courseContent trainingExplanation'));
    }
    if (state.phase === 'shown') {
      main.append(node('p', '', t('training.ratingHint')));
      const ratings = node('div', 'trainingRatings');
      for (let rating = 1; rating <= 4; rating++) {
        const item = button(t(ratingKeys[rating]), `trainingRating rating${rating}`, async () => {
          for (const b of ratings.children) b.disabled = true;
          try {
            const next = await post('rate', {attempt_id: state.attempt_id, rating, elapsed_ms: flushClock()});
            if (!alive(e, s)) return;
            current = next; await refreshGroup(e, s); if (alive(e, s)) renderQuestion();
          } catch (failure) { if (alive(e, s)) { error(main, failure); for (const b of ratings.children) b.disabled = !state.can_self_rate || !state.can_submit; } }
        });
        item.disabled = !state.can_self_rate || !state.can_submit; item.dataset.rating = rating; ratings.append(item);
      }
      main.append(ratings);
    }
    if (state.phase === 'done') {
      save('draft', state.attempt_id, null); save('submission', state.attempt_id, null); save('elapsed', state.attempt_id, null); clockKey = null;
      const result = state.result;
      const label = result.self_rating ? t('training.selfResult', {rating: t(ratingKeys[result.self_rating])}) : t(result.correct ? 'training.correct' : 'training.incorrect');
      const verdict = node('div', 'trainingVerdict'); verdict.setAttribute('role', 'status');
      verdict.append(node('strong', '', label)); if (result.reason) verdict.append(node('p', '', result.reason));
      if (result.practice_only) verdict.append(node('p', '', t('training.practice')));
      if (state.due_at) verdict.append(node('p', '', t('training.nextDue', {time: formatDate(state.due_at)})));
      main.append(verdict);
    }
    if (state.can_learn) main.append(button(t('training.redo'), 'secondaryButton trainingRedo', () => select(state.question_id, true)));
    if (getAccess()?.features?.includes('review_history')) {
      const box = node('details', 'trainingHistory'); box.append(node('summary', '', t('training.history'))); main.append(box);
      box.addEventListener('toggle', () => { if (box.open && !box.dataset.loaded) { box.dataset.loaded = 'true'; void loadHistory(box, e, s, state.question_id); } });
    }
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
      if (!alive(e, s)) return;
      for (const item of data.items) box.append(button(`${formatDate(item.created_at)} · ${t(item.phase === 'done' ? 'training.saved' : item.phase === 'shown' ? 'training.finishRating' : 'training.resume')}`,
        'trainingHistoryItem', () => select(questionId, false, item.id)));
      if (!data.items.length && !offset) box.append(node('p', '', t('training.historyEmpty')));
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
