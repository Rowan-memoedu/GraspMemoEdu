// Native details/summary groups are shared by task trees and Markdown outlines.
export function createOutlineGroup(title, {expanded, key, open = false, className = ''} = {}) {
  const group = document.createElement('details'); group.className = `courseUnitGroup ${className}`.trim();
  const summary = document.createElement('summary'); summary.className = 'courseUnitHeading';
  if (typeof title === 'string') summary.textContent = title;
  else summary.append(title);
  const body = document.createElement('div'); body.className = 'courseUnitBody';
  group.open = expanded?.get(key) ?? open;
  if (expanded) group.addEventListener('toggle', () => expanded.set(key, group.open));
  group.append(summary, body);
  return {group, summary, body};
}

export function enhanceMarkdownOutline(root, {expanded = new Map(), keyPrefix = ''} = {}) {
  // Fold heading sections, then list branches; preserve sanitized content nodes.
  const sections = [], nodes = [...root.childNodes];
  let ordinal = 0;
  for (const item of nodes) {
    const level = /^H[1-6]$/.test(item.nodeName) ? Number(item.nodeName[1]) : 0;
    if (level) {
      while (sections.length && sections.at(-1).level >= level) sections.pop();
      const key = `${keyPrefix}:heading:${item.id || ordinal++}`;
      const parent = sections.at(-1)?.body || root;
      const {group, body} = createOutlineGroup(item, {expanded, key, open: !sections.length, className: 'guideOutline'});
      parent.append(group); sections.push({level, body, key});
    } else if (sections.length) sections.at(-1).body.append(item);
  }
  const visit = (list, prefix) => {
    [...list.children].forEach((li, index) => {
      if (li.tagName !== 'LI') return;
      const children = [...li.children].filter(n => n.tagName === 'UL' || n.tagName === 'OL');
      const key = `${prefix}:item:${index}`;
      if (children.length) {
        const beforeList = [...li.childNodes].slice(0, [...li.childNodes].indexOf(children[0]));
        // Tight Markdown lists use text and inline nodes instead of a paragraph.
        if (beforeList.some(n => n.textContent.trim())) {
          const label = document.createElement('span');
          for (const node of beforeList) {
            if (node.nodeName === 'P') { label.append(...node.childNodes); node.remove(); }
            else label.append(node);
          }
          const {group, body} = createOutlineGroup(label, {expanded, key, className: 'guideOutline'});
          body.append(...li.childNodes); li.append(group);
        }
      }
      for (const child of children) visit(child, key);
    });
  };
  for (const list of [...root.querySelectorAll('ul,ol')].filter(n => !n.parentElement.closest('ul,ol'))) {
    const owner = list.closest('details')?.querySelector('summary [id]')?.id || 'section';
    visit(list, `${keyPrefix}:${owner}:list:${ordinal++}`);
  }
  root.onclick = event => {
    const anchor = event.target.closest('a[href^="#"]');
    if (!anchor) return;
    let id; try { id = decodeURIComponent(anchor.getAttribute('href').slice(1)); } catch { return; }
    const target = [...root.querySelectorAll('[id]')].find(n => n.id === id);
    if (!target) return;
    event.preventDefault();
    for (let parent = target.parentElement; parent && parent !== root; parent = parent.parentElement) {
      if (parent.tagName === 'DETAILS') parent.open = true;
    }
    target.scrollIntoView({block: 'start'});
  };
  return root;
}

// The same tree renderer serves courses, banks and review source directories.
export function renderTaskTree(pending, data, {renderTask, expanded, keyPrefix = '', sourceOrder = [], defaultOpen = false}) {
  const tasks = new Map(data.tasks.map(task => [task.topic_id || task.id, task]));
  const containers = new Map(), rendered = new Set();
  for (const unit of data.pending_hierarchy || []) {
    const key = `${keyPrefix}:${data.course.id}:${unit.id}`;
    const {group, body} = createOutlineGroup(unit.title, {expanded, key, open: defaultOpen || !unit.parent_id});
    group.dataset.unitId = unit.id;
    containers.set(unit.id, {group, body});
    for (const id of unit.topic_ids || []) if (tasks.has(id)) {
      body.append(renderTask(tasks.get(id))); rendered.add(id);
    }
  }
  for (const unit of data.pending_hierarchy || []) {
    (containers.get(unit.parent_id)?.body || pending).append(containers.get(unit.id).group);
  }
  for (const [id, task] of tasks) if (!rendered.has(id)) pending.append(renderTask(task));
  const order = new Map(sourceOrder.map((id, index) => [id, index]));
  if (order.size) {
    const position = element => order.get(element.dataset.unitId || element.dataset.taskId) ?? Number.MAX_SAFE_INTEGER;
    for (const body of [pending, ...[...containers.values()].map(item => item.body)]) {
      body.append(...[...body.children].sort((a, b) => position(a) - position(b)));
    }
  }
}
