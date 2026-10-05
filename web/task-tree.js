// The same tree renderer serves courses, banks and review source directories.
export function renderTaskTree(pending, data, {renderTask, expanded, keyPrefix = '', sourceOrder = [], defaultOpen = false}) {
  const tasks = new Map(data.tasks.map(task => [task.topic_id || task.id, task]));
  const containers = new Map(), rendered = new Set();
  for (const unit of data.pending_hierarchy || []) {
    const group = document.createElement('details'); group.className = 'courseUnitGroup';
    const summary = document.createElement('summary'); summary.className = 'courseUnitHeading'; summary.textContent = unit.title;
    const body = document.createElement('div'); body.className = 'courseUnitBody';
    const key = `${keyPrefix}:${data.course.id}:${unit.id}`;
    group.dataset.unitId = unit.id;
    group.open = expanded.get(key) ?? (defaultOpen || !unit.parent_id);
    group.addEventListener('toggle', () => expanded.set(key, group.open));
    group.append(summary, body); containers.set(unit.id, {group, body});
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
