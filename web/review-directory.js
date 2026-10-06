import {t, getLanguage} from './i18n.js?v=5ed0a69081654e02';

export function paperNumber(number) {
  if (getLanguage() !== 'zh-CN') return t('paper.number', {number});
  const digits = '零一二三四五六七八九';
  function han(n, leading = true) {
    if (n < 10) return digits[n];
    for (const [power, unit] of [[1000, '千'], [100, '百'], [10, '十']]) if (n >= power) {
      const head = Math.floor(n / power), rest = n % power;
      return (leading && power === 10 && head === 1 ? '' : han(head)) + unit
        + (rest ? (rest < power / 10 ? '零' : '') + han(rest, false) : '');
    }
  }
  return t('paper.number', {number: number < 10000 ? han(number) : number});
}

export function renderReviewDirectory(root, data, {courseSidebar, taskTree, start, href}) {
  const element = (tag, cls, text) => { const x = document.createElement(tag); x.className = cls; if (text !== undefined) x.textContent = text; return x; };
  const layout = element('div', 'dashboardLayout'), body = element('div', 'dashboardTasks');
  const summary = {course: {id: 'reviews', title: t('paper.title'), progress: 0}};
  layout.append(courseSidebar(summary, {training: true, review: true, allMaterials: data.all_materials, href: '/reviews', questionCount: data.due_count,
    onProgress: () => body.scrollIntoView({block: 'start'})}), body);
  root.replaceChildren(layout);
  function render(data, container, label) {
    const tasks = data.tasks.map(item => ({...item, entry: item, topic_id: item.id, type: 'Review',
      title: item.kind === 'paper' ? paperNumber(item.paper_number || 1) : item.title,
      question_count: item.count, time_limit_minutes: item.time_limit_minutes || item.timing?.limit_minutes,
      started: item.active || item.can_resume, dependency_ready: !item.paused && item.can_resume !== false,
      progress: item.count && item.done ? item.done / item.count * 100 : 0}));
    taskTree(container, {...data, tasks}, {review: true, label: t('portal.reviewTask'), sourceOrder: data.source_order,
      start: task => start(task.entry), startLabel: t(data.all_materials ? 'admin.enterCard' : 'paper.start')});
  }
  for (const [kind, label] of [['atomic', 'paper.atomicCards'], ['training', 'training.reviewCards']]) {
    const trees = (data.trees || []).filter(tree => tree.kind === kind);
    if (kind === 'atomic' && !trees.length) continue;
    const section = element('section', 'incompleteTasks'); section.dataset.reviewKind = kind;
    section.append(element('h2', 'trainingSectionTitle', t(label))); body.append(section);
    for (const tree of trees) {
      const container = element('div', 'courseTree'); section.append(container); render(tree, container, t(label));
    }
    if (!trees.length) section.append(element('p', 'portalEmpty', t('paper.none')));
  }
  if (data.all_materials) return body;
  const papers = element('section', 'incompleteTasks'); papers.dataset.reviewKind = 'paper';
  papers.append(element('h2', 'trainingSectionTitle', t('paper.papers')), element('p', 'inputHint', t('paper.overlap')));
  body.append(papers);
  const entries = [...data.active, ...data.items].filter(item => item.kind === 'paper');
  const groups = new Map();
  for (const entry of entries) {
    const id = entry.paper_type_id || 'legacy-paper';
    if (!groups.has(id)) groups.set(id, {title: entry.title, items: []});
    groups.get(id).items.push(entry);
  }
  for (const [id, group] of groups) {
    const items = group.items.sort((a, b) => (a.paper_number || 1) - (b.paper_number || 1));
    const container = element('div', 'courseTree'); papers.append(container);
    render({course: {id: 'paper:' + id}, tasks: items,
      pending_hierarchy: [{id: 'paper:' + id, title: group.title, parent_id: null, topic_ids: items.map(i => i.id)}]}, container, t('paper.papers'));
  }
  if (!groups.size) papers.append(element('p', 'portalEmpty', t(data.due_count ? 'paper.noPaper' : 'paper.none')));
  return body;
}
