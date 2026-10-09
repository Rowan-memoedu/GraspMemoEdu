import {t, locale, learningTitle, translateMessage} from './i18n.js?v=fdf8075df78da5d0';
import {renderTaskTree, createOutlineGroup} from './task-tree.js?v=fdf8075df78da5d0';
import {questionInput, choiceTypeField} from './question-input.js?v=fdf8075df78da5d0';
import {submittedAnswer, referenceAnswer, prepareAnswerContent} from './self-assessment.js?v=fdf8075df78da5d0';
import {enhanceTopicContent} from './topic-content.js?v=fdf8075df78da5d0';

const el = (tag, cls = '', text) => { const n = document.createElement(tag); n.className = cls; if (text != null) n.textContent = text; return n; };
const button = (text, action, cls = 'secondaryButton') => { const n = el('button', cls, text); n.type = 'button'; n.addEventListener('click', action); return n; };
const number = value => Number(value || 0).toLocaleString(locale());
const ratio = value => value == null ? '—' : `${Math.round(100 * value)}%`;
const duration = value => t('students.minutes', {value: number(Math.round(value / 60000))});
const hint = text => el('p', 'inputHint', text);
const title = (text, tag = 'h3') => el(tag, '', text);
const blockLabel = reason => t('students.' + ({account_disabled: 'accountDisabled', topic_forbidden: 'topicForbidden',
  feature_forbidden: 'featureForbidden', old_version: 'oldVersion', paused: 'paused', prerequisites: 'prerequisites',
  initial_learning: 'initialLearning', another_review_active: 'otherActive', stopped: 'stopped'}[reason] || reason));
const stateLabel = state => t('students.' + ({completed: 'completed', in_progress: 'inProgress', paused: 'paused', skipped: 'skipped'}[state] || 'notStarted'));
const query = values => new URLSearchParams(Object.entries(values).filter(([, value]) => value != null && value !== '')).toString();
let readerId = 0;

function select(label, choices) {
  const input = el('select'); input.setAttribute('aria-label', label);
  for (const [value, text] of choices) { const option = el('option', '', text); option.value = value; input.append(option); }
  return input;
}

function progress(done, total, label) {
  const root = el('div', 'studentProgress');
  const bar = el('progress'); bar.max = total || 1; bar.value = done; bar.setAttribute('aria-label', label);
  root.append(bar, el('span', '', t('students.progressCount', {done: number(done), total: number(total)})));
  return root;
}

function metric(label, value, note) {
  const card = el('article', 'studentMetric'); card.append(hint(label), el('strong', '', value));
  if (note) card.append(hint(note));
  return card;
}

function statsLine(stats) {
  return hint(t('students.statsLine', {attempts: number(stats.attempts), accuracy: ratio(stats.accuracy), self: number(stats.self_rated)}));
}

function fold(label, cls = '') {
  const root = el('details', 'studentFold ' + cls); const body = el('div', 'studentFoldBody');
  root.append(el('summary', '', label), body); return {root, body};
}

export function createStudentDashboard(data, {request, formatDate, timezone, days, onDaysChange, subjectName, materialHref}) {
  const a = data.analytics, root = el('div', 'studentDashboard'), tabs = el('div', 'studentTabs');
  tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', t('students.sections'));
  const panels = {}, tabButtons = {};
  const sections = [['overview', 'overview'], ['topics', 'allTopics'], ['banks', 'banks'], ['notebook', 'notebook'], ['schedule', 'schedule']];
  const tabPrefix = `student-report-${++readerId}`;
  let notebookLoaded = false;
  function activate(name, focus = false) {
    for (const [key] of sections) {
      tabButtons[key].setAttribute('aria-selected', String(key === name)); tabButtons[key].tabIndex = key === name ? 0 : -1;
      panels[key].hidden = key !== name;
    }
    if (focus) tabButtons[name].focus();
    if (name === 'notebook' && !notebookLoaded) {
      notebookLoaded = true; panels.notebook.append(questionBrowser({kind: 'notebook'}));
    }
  }
  for (const [name, key] of sections) {
    const tab = button(t('students.' + key), () => activate(name), 'studentTab');
    tab.id = `${tabPrefix}-${name}-tab`; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', `${tabPrefix}-${name}`);
    const panel = el('section', 'studentTabPanel'); panel.id = `${tabPrefix}-${name}`;
    panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id);
    tabButtons[name] = tab; panels[name] = panel; tabs.append(tab);
    tab.addEventListener('keydown', event => {
      const index = sections.findIndex(([key]) => key === name);
      const target = event.key === 'ArrowRight' ? (index + 1) % sections.length : event.key === 'ArrowLeft' ? (index + sections.length - 1) % sections.length : event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1 : null;
      if (target !== null) { event.preventDefault(); activate(sections[target][0], true); }
    });
  }
  root.append(tabs, ...Object.values(panels)); activate('overview');

  function historyReader(parameters) {
    const box = fold(t('students.answerHistory'), 'studentAnswerHistory');
    let offset = 0, loaded = false, busy = false;
    const entries = el('div'), more = button(t('help.more'), () => load());
    const status = hint(''); status.setAttribute('role', 'status'); more.hidden = true;
    box.body.append(status, entries, more);
    box.root.addEventListener('toggle', () => { if (box.root.open && !loaded) void load(); });
    async function load() {
      if (busy) return; busy = true; more.disabled = true; status.textContent = t('students.loading');
      try {
        const response = await request('history?' + query({...parameters, offset}));
        if (!root.isConnected) return;
        loaded = true; status.textContent = t('students.historyCount', {count: number(response.total)});
        for (const item of response.items) entries.append(answerCard(item));
        offset = response.next_offset; more.hidden = offset === null;
      } catch (error) { status.textContent = translateMessage(error.message); more.hidden = false; }
      finally { busy = false; more.disabled = false; }
    }
    return box.root;
  }

  function answerCard(item) {
    const card = el('article', 'studentAnswerCard');
    const assessment = item.self_rating != null;
    card.append(title(learningTitle(item.title)), hint(`${formatDate(item.at)} · ${t('students.' + (item.mode === 'review' ? 'reviewAttempt' : 'learningAttempt'))}`));
    card.append(el('p', 'studentVerdict ' + (assessment ? 'self' : item.correct ? 'correct' : 'incorrect'),
      assessment ? t('students.selfRatingValue', {rating: item.self_rating}) : t('students.' + (item.correct ? 'correctAnswer' : 'wrongAnswer'))));
    if (item.elapsed_ms != null) card.append(hint(t('students.attemptTime', {seconds: number(Math.round(item.elapsed_ms / 1000))})));
    if (item.locked) { card.append(hint(t('students.lockedHistory'))); return card; }
    if (item.content_available) {
      const style = el('style'); style.textContent = item.math_css || ''; card.append(style);
      const stem = el('div', 'courseContent studentQuestion'); stem.innerHTML = item.html;
      card.append(stem); prepareAnswerContent(stem); enhanceTopicContent(stem);
      const type = choiceTypeField(item.interaction); if (type) stem.prepend(type);
      if (item.interaction && item.interaction.type !== 'text') {
        const input = questionInput(item.interaction, {id: `student-answer-${++readerId}`, value: item.answer || '', stem, disabled: true});
        card.append(input.element);
      }
    } else card.append(hint(t('students.missingSnapshot')));
    card.append(title(t('students.submittedAnswer')));
    card.append(item.answer ? submittedAnswer(item, item.answer, item.answer_display || item.answer) : hint(t('students.noSubmittedText')));
    if (item.reason) card.append(hint(item.reason));
    if (item.content_available) {
      const reference = referenceAnswer(item); if (reference) card.append(reference);
      const explanation = el('div', 'courseContent studentExplanation'); explanation.innerHTML = item.explanation_html;
      card.append(title(t('Explanation · 解析')), explanation); prepareAnswerContent(explanation); enhanceTopicContent(explanation);
    }
    return card;
  }

  function questionBrowser(parameters) {
    const box = el('div', 'studentQuestionBrowser');
    const toolbar = el('form', 'studentFilters');
    const search = el('input'); search.type = 'search'; search.maxLength = 100; search.placeholder = t('students.searchQuestion'); search.setAttribute('aria-label', search.placeholder);
    const filter = select(t('students.questionFilter'), ['all', 'wrong', 'due', 'unanswered', 'stopped'].map(key => [key, t('students.filter.' + key)]));
    const apply = el('button', 'secondaryButton', t('students.filterApply')); apply.type = 'submit';
    toolbar.append(search, filter, apply);
    const entries = el('div'), status = hint(''), more = button(t('help.more'), () => load(false));
    status.setAttribute('role', 'status'); more.hidden = true; box.append(toolbar, status, entries, more);
    let offset = 0, busy = false, loadedQuery = {}, generation = 0;
    toolbar.addEventListener('submit', event => { event.preventDefault(); void load(true); });
    filter.addEventListener('change', () => void load(true));
    async function load(reset) {
      const ticket = ++generation;
      if (reset) { offset = 0; loadedQuery = {q: search.value.trim(), state: filter.value}; }
      busy = true; more.disabled = true; status.textContent = t('students.loading');
      try {
        const response = await request('questions?' + query({...parameters, ...loadedQuery, offset}));
        if (!root.isConnected || ticket !== generation) return;
        if (reset) entries.replaceChildren();
        status.textContent = t('students.questionCount', {count: number(response.total)});
        for (const item of response.items) {
          const row = el('article', 'studentQuestionRow');
          row.append(title(learningTitle(item.title)), hint([item.container_title, item.group_title].filter(Boolean).join(' · ')), statsLine(item.stats));
          row.append(hint(t('students.errorCounts', {graded: item.stats.wrong, self: item.stats.self_rated - item.stats.self_passed})));
          if (!item.version_current) row.append(el('p', 'studentBlocked', t('students.oldVersion')));
          if (item.stopped) row.append(hint(t('students.stopped')));
          else if (item.due_at) row.append(hint(t('students.dueAt', {time: formatDate(item.due_at)})));
          if (!item.stats.attempts) row.append(hint(t('students.notStarted')));
          else {
            row.append(hint(t('students.latestResult', {result: item.latest_self_rating != null ? t('students.selfRatingValue', {rating: item.latest_self_rating}) : t('students.' + (item.latest_correct ? 'correctAnswer' : 'wrongAnswer'))})));
            row.append(historyReader({kind: item.kind, container: item.container, question: item.question, version: item.version}));
          }
          const href = materialHref(item); if (href && item.version_current) { const link = el('a', 'textButton', t('students.openMaterial')); link.href = href; row.append(link); }
          entries.append(row);
        }
        offset = response.next_offset; more.hidden = offset === null;
      } catch (error) { if (ticket === generation) { status.textContent = translateMessage(error.message); more.hidden = false; } }
      finally { if (ticket === generation) { busy = false; more.disabled = false; } }
    }
    // Attach before the first read so route changes cannot render a late response.
    queueMicrotask(() => { if (root.isConnected) void load(true); });
    return box;
  }

  const overview = panels.overview;
  const metrics = el('div', 'studentMetrics');
  metrics.append(metric(t('students.completedTopics'), `${a.topics.filter(x => x.status === 'completed' && x.version_current).length} / ${a.topics.length}`),
    metric(t('students.answeredQuestions'), number(a.banks.reduce((sum, bank) => sum + bank.answered, 0))),
    metric(t('students.notebook'), number(a.notebook.total), t('students.dueCount', {count: a.notebook.due})),
    metric(t('students.overdue'), number(data.reviews.filter(x => x.overdue && !x.blockers.includes('stopped')).length)));
  overview.append(metrics);
  const activityHeader = el('div', 'studentSectionHeading');
  const range = select(t('students.period'), [7, 30, 90, 365].map(value => [value, t('students.lastDays', {days: value})]));
  range.value = String(days); range.addEventListener('change', () => onDaysChange(Number(range.value)));
  activityHeader.append(title(t('students.activity'), 'h2'), range); overview.append(activityHeader);
  const periodStats = el('div', 'studentMetrics');
  periodStats.append(metric(t('students.activeDays'), number(a.period.active_days)), metric(t('students.attemptCount'), number(a.period.attempts)),
    metric(t('students.accuracy'), ratio(a.period.accuracy), t('students.selfSeparate', {count: a.period.self_rated})),
    metric(t('students.recordedTime'), a.period.timed_attempts ? duration(a.period.elapsed_ms) : '—', t('students.timeCoverage', {count: a.period.timed_attempts, total: a.period.attempts})));
  overview.append(periodStats, hint(t('students.metricRules')));
  const charts = el('div', 'studentCharts'), heatBox = el('section', 'studentChart'), trendBox = el('section', 'studentChart');
  heatBox.append(title(t('students.activityGrid'))); trendBox.append(title(t('students.accuracyTrend')));
  const heat = el('div', 'studentHeatmap'), dayDetail = hint(t('students.selectDay'));
  const maximum = Math.max(1, ...a.activity.map(day => day.attempts));
  for (const day of a.activity) {
    const label = `${day.date} · ${t('students.dayActivity', {count: day.attempts, accuracy: ratio(day.accuracy)})}`;
    const cell = button('', () => { dayDetail.textContent = label; }, 'studentHeatCell');
    cell.title = label; cell.setAttribute('aria-label', label); cell.style.setProperty('--level', day.attempts ? .2 + .8 * day.attempts / maximum : 0);
    heat.append(cell);
  }
  heatBox.append(heat, hint(`${a.activity[0].date} — ${a.activity.at(-1).date}`), dayDetail);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 480 170');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', t('students.accuracyTrend'));
  const svgNode = (tag, attrs, text) => { const node = document.createElementNS(svg.namespaceURI, tag); for (const [key, val] of Object.entries(attrs)) node.setAttribute(key, val); if (text != null) node.textContent = text; svg.append(node); return node; };
  for (const rate of [0, .5, 1]) { const y = 140 - rate * 120; svgNode('line', {x1: 38, x2: 465, y1: y, y2: y, class: 'studentChartGrid'}); svgNode('text', {x: 0, y: y + 4}, ratio(rate)); }
  svgNode('text', {x: 38, y: 165}, a.activity[0].date.slice(5));
  svgNode('text', {x: 465, y: 165, 'text-anchor': 'end'}, a.activity.at(-1).date.slice(5));
  const measured = a.activity.map((day, index) => ({...day, x: 40 + index * 422 / Math.max(1, a.activity.length - 1)})).filter(day => day.accuracy != null);
  if (measured.length) {
    svgNode('polyline', {points: measured.map(day => `${day.x},${140 - day.accuracy * 120}`).join(' '), class: 'studentTrendLine'});
    for (const day of measured) { const point = svgNode('circle', {cx: day.x, cy: 140 - day.accuracy * 120, r: 4, class: 'studentTrendPoint'}); const tooltip = document.createElementNS(svg.namespaceURI, 'title'); tooltip.textContent = `${day.date}: ${ratio(day.accuracy)} (${day.judged})`; point.append(tooltip); }
  }
  trendBox.append(svg, hint(measured.length ? t('students.trendHint') : t('students.noJudged')));
  const trendData = fold(t('students.chartData'));
  for (const day of a.activity.filter(day => day.attempts)) trendData.body.append(hint(`${day.date} · ${t('students.dayActivity', {count: day.attempts, accuracy: ratio(day.accuracy)})}`));
  trendBox.append(trendData.root); charts.append(heatBox, trendBox); overview.append(charts);

  const calendar = el('section', 'studentChart studentCalendar'); calendar.append(title(t('students.calendar'), 'h2'), hint(t('students.calendarHint')));
  const month = el('input'); month.type = 'month'; month.setAttribute('aria-label', t('students.calendarMonth')); month.value = a.activity.at(-1).date.slice(0, 7);
  const grid = el('div', 'studentCalendarGrid'), calendarDetail = el('div');
  calendar.append(month, grid, calendarDetail); overview.append(calendar);
  const dateKey = value => new Intl.DateTimeFormat('en-CA', {timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date(value));
  const dates = new Map(a.calendar.map(day => [day.date, day]));
  function calendarMonth() {
    grid.replaceChildren(); calendarDetail.replaceChildren(); if (!/^\d{4}-\d{2}$/.test(month.value)) return;
    const [year, m] = month.value.split('-').map(Number), first = new Date(Date.UTC(year, m - 1, 1));
    for (let day = 0; day < 7; day++) grid.append(el('span', 'studentCalendarWeekday', new Intl.DateTimeFormat(locale(), {weekday: 'short', timeZone: 'UTC'}).format(new Date(Date.UTC(2026, 9, 5 + day)))));
    for (let blank = 0; blank < (first.getUTCDay() + 6) % 7; blank++) grid.append(el('span'));
    for (let day = 1; day <= new Date(Date.UTC(year, m, 0)).getUTCDate(); day++) {
      const key = `${month.value}-${String(day).padStart(2, '0')}`, counts = dates.get(key) || {count: 0, blocked: 0};
      const cell = button('', () => {
        calendarDetail.replaceChildren(title(key), hint(t('students.calendarCount', counts)));
        for (const item of data.reviews.filter(item => dateKey(item.due_at) === key)) calendarDetail.append(hint(`${learningTitle(item.title)} · ${formatDate(item.due_at)}${item.blockers.length ? ' · ' + item.blockers.map(blockLabel).join(' / ') : ''}`));
      }, 'studentCalendarDay');
      cell.append(el('span', '', day), el('small', '', counts.count || '')); cell.classList.toggle('hasReviews', counts.count > 0);
      cell.setAttribute('aria-label', `${key} · ${t('students.calendarCount', counts)}`); grid.append(cell);
    }
  }
  month.addEventListener('change', calendarMonth); calendarMonth();

  const weak = el('section', 'studentWeakPoints'); weak.append(title(t('students.weakPoints'), 'h2'), hint(t('students.weakHint')));
  if (!a.weak_points.length) weak.append(hint(t('students.noWeakPoints')));
  for (const point of a.weak_points) { const row = el('article', 'studentQuestionRow'); row.append(title(learningTitle(point.title)), hint(t('students.failureCount', {count: point.failures})), historyReader({kind: point.kind, container: point.container, point: point.point, version: point.version})); weak.append(row); }
  overview.append(weak);
  const access = fold(t('students.accessStatus'));
  access.body.append(hint(t('students.accessCounts', {count: a.permissions.topic_count, features: a.permissions.features.length})),
    hint(data.student.is_advanced_learner ? t('account.advancedLearner') : t('account.standardLearner')),
    hint(data.student.enabled ? t('students.accountEnabled') : t('students.accountDisabled')));
  for (const topic of a.topics.filter(topic => topic.blockers.length || topic.modules.some(module => module.status === 'skipped'))) access.body.append(hint(`${topic.topic_title} · ${[...topic.blockers.map(blockLabel), ...topic.modules.filter(m => m.status === 'skipped').map(m => `${learningTitle(m.title)}: ${t('students.skipped')}`)].join(' / ')}`));
  overview.append(access.root);

  const topicFilters = el('div', 'studentFilters');
  const subjects = [...new Set(a.topics.map(topic => topic.subject_id).filter(Boolean))];
  const subject = select(t('students.subjectFilter'), [['', t('students.allSubjects')], ...subjects.map(id => [id, subjectName(id)])]);
  const topicState = select(t('students.topicFilter'), [['', t('students.allStates')], ...['not_started', 'in_progress', 'completed', 'paused'].map(key => [key, stateLabel(key)]), ['blocked', t('students.blocked')]]);
  const topicSearch = el('input'); topicSearch.type = 'search'; topicSearch.placeholder = t('students.searchTopic'); topicSearch.setAttribute('aria-label', topicSearch.placeholder);
  const topicList = el('div'); topicFilters.append(subject, topicState, topicSearch); panels.topics.append(topicFilters, hint(t('students.progressRules')), topicList);
  const expansion = new Map();
  function renderTopics() {
    topicList.replaceChildren();
    const filtered = a.topics.filter(topic => (!subject.value || topic.subject_id === subject.value) && (!topicState.value || (topicState.value === 'blocked' ? topic.blockers.length : topic.status === topicState.value)) && `${topic.topic_title} ${topic.course_title}`.toLowerCase().includes(topicSearch.value.toLowerCase()));
    topicList.append(hint(t('students.topicCount', {count: filtered.length})));
    const renderTopic = topic => {
      const box = fold(topic.topic_title, 'studentTopic'); box.root.dataset.topicId = topic.topic_id;
      box.body.append(hint(stateLabel(topic.status)), progress(topic.done, topic.total, topic.topic_title), statsLine(topic.stats));
      for (const reason of topic.blockers) box.body.append(el('p', 'studentBlocked', blockLabel(reason)));
      for (const item of topic.guidance) box.body.append(hint(`${learningTitle(item.title)} · ${t('students.' + (item.read ? 'guidanceRead' : 'notStarted'))}`));
      for (const item of topic.modules) box.body.append(hint(`${learningTitle(item.title)} · ${stateLabel(item.status)}${item.mastery != null ? ' · ' + t('students.mastery', {rating: item.mastery}) : ''}`));
      for (const point of topic.points) box.body.append(hint(`${point.chunk_title} · ${point.title} · ${t(point.mastered ? 'atomic.mastered' : 'atomic.unmastered')}`));
      const link = el('a', 'textButton', t('students.openMaterial')); link.href = materialHref({kind: 'topic', container: topic.topic_id, subject_id: topic.subject_id}); box.body.append(link);
      const questions = fold(t('students.questionsAndHistory')); box.body.append(questions.root); let loaded = false;
      questions.root.addEventListener('toggle', () => { if (questions.root.open && !loaded) { loaded = true; questions.body.append(questionBrowser({kind: 'topic', container: topic.topic_id})); } });
      // Progress stays visible before expansion.
      box.root.firstChild.append(el('span', 'studentTopicSummary', `${stateLabel(topic.status)} · ${topic.done}/${topic.total}`));
      return box.root;
    };
    for (const course of a.courses) {
      const topics = filtered.filter(topic => topic.course_id === course.id); if (!topics.length) continue;
      const section = createOutlineGroup(`${subjectName(course.subject_id)} · ${course.title}`, {expanded: expansion, key: course.id, open: true});
      section.body.append(progress(topics.filter(t => t.status === 'completed' && t.version_current).length, topics.length, course.title));
      renderTaskTree(section.body, {course, tasks: topics, pending_hierarchy: course.units}, {renderTask: renderTopic, expanded: expansion, keyPrefix: 'report', defaultOpen: true});
      topicList.append(section.group);
    }
    for (const topic of filtered.filter(topic => !topic.course_id)) topicList.append(renderTopic(topic));
  }
  subject.addEventListener('change', renderTopics); topicState.addEventListener('change', renderTopics); topicSearch.addEventListener('input', renderTopics); renderTopics();

  panels.banks.append(hint(t('students.bankRules')));
  if (!a.banks.length) panels.banks.append(hint(t('students.noBanks')));
  for (const bank of a.banks) {
    const section = fold(`${subjectName(bank.subject_id)} · ${bank.title}`, 'studentBank');
    section.body.append(progress(bank.answered, bank.total, bank.title), statsLine(bank.stats), hint(t('students.dueCount', {count: bank.due})));
    if (!bank.allowed) section.body.append(el('p', 'studentBlocked', t('students.topicForbidden')));
    const tree = el('div'); section.body.append(tree);
    const renderGroup = group => {
      const box = fold(`${group.title} · ${group.answered}/${group.total}`, 'studentBankGroup');
      box.body.append(progress(group.answered, group.total, group.title)); let loaded = false;
      box.root.addEventListener('toggle', () => { if (box.root.open && !loaded) { loaded = true; box.body.append(questionBrowser({kind: 'training', container: bank.id, group: group.id})); } }); return box.root;
    };
    renderTaskTree(tree, {course: {id: bank.id}, tasks: bank.groups.filter(g => g.total).map(g => ({...g, topic_id: 'questions:' + g.id})),
      pending_hierarchy: bank.groups.map(g => ({...g, topic_ids: g.total ? ['questions:' + g.id] : []}))},
      {renderTask: renderGroup, expanded: expansion, keyPrefix: 'bank-report', defaultOpen: false});
    panels.banks.append(section.root);
  }
  panels.notebook.append(hint(t('students.notebookRules')));
  return {root, overview, schedule: panels.schedule, activate};
}
