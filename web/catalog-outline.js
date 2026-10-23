import {createOutlineGroup} from './task-tree.js?v=0d7891b1a8fc5db8';

// Selection and navigation are views of the existing course/bank tree. They
// never create synthetic courses or change the ownership of learning records.
export function appendCatalogOutline(container, entry, renderEntry, {expanded, keyPrefix = ''} = {}) {
  const units = entry.outline || [];
  if (!units.length) return;
  const outline = document.createElement('div'); outline.className = 'catalogOutline';
  const parents = new Set(units.map(unit => unit.parent_id)), containers = new Map();
  for (const unit of units) {
    const row = renderEntry({...entry, title: unit.title, description: '', root_id: unit.id,
      dependency_ready: unit.dependency_ready ?? entry.dependency_ready}, unit.id);
    row.classList.add('catalogOutlineRow'); row.dataset.rootId = unit.id;
    if (parents.has(unit.id)) {
      const branch = createOutlineGroup(row, {expanded, key: `${keyPrefix}:${entry.id}:${unit.id}`,
        open: !unit.parent_id, className: 'catalogOutlineBranch'});
      branch.group.dataset.catalogRoot = unit.id;
      containers.set(unit.id, {element: branch.group, body: branch.body});
    } else containers.set(unit.id, {element: row, body: row});
  }
  for (const unit of units) (containers.get(unit.parent_id)?.body || outline).append(containers.get(unit.id).element);
  // Buttons/links inside a summary select a scope; only its disclosure area
  // expands the outline. Native keyboard behaviour remains available.
  outline.addEventListener('click', event => { if (event.target.closest('button,a')) event.stopPropagation(); });
  container.append(outline); container.classList.add('hasCatalogOutline');
}
