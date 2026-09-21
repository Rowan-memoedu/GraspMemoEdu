import { portalMessages } from './portal-messages.js?v=aa1b73113c05cc38';
// One dependency-free renderer for the website and self-contained local previews.
export function renderCourseGraph(target, graph, {language = 'zh', onOpenTopic} = {}) {
  const t = key => portalMessages[key][language === 'en' ? 1 : 0];
  const el = (tag, cls, text) => {
    const item = document.createElement(tag); if (cls) item.className = cls;
    if (text !== undefined) item.textContent = text; return item;
  };
  const ns = 'http://www.w3.org/2000/svg';
  const svgEl = (tag, attributes = {}) => {
    const item = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) item.setAttribute(key, String(value));
    return item;
  };
  const nodes = graph.nodes || [], edges = graph.edges || [], byId = new Map(nodes.map(n => [n.topic_id, n]));
  target.replaceChildren(); target.classList.add('courseGraph');
  if (!nodes.length) { target.append(el('p', '', t('graph.empty'))); return; }
  const parents = new Map(nodes.map(n => [n.topic_id, []])), children = new Map(nodes.map(n => [n.topic_id, []]));
  for (const edge of edges) if (byId.has(edge.from) && byId.has(edge.to)) {
    parents.get(edge.to).push(edge.from); children.get(edge.from).push(edge.to);
  }
  const levels = new Map(nodes.map(n => [n.topic_id, 0]));
  const remaining = new Map(nodes.map(n => [n.topic_id, parents.get(n.topic_id).length]));
  const queue = nodes.filter(n => !remaining.get(n.topic_id)).map(n => n.topic_id);
  for (let i = 0; i < queue.length; i++) for (const child of children.get(queue[i])) {
    levels.set(child, Math.max(levels.get(child), levels.get(queue[i]) + 1));
    remaining.set(child, remaining.get(child) - 1); if (!remaining.get(child)) queue.push(child);
  }
  if (queue.length !== nodes.length) { target.append(el('p', '', t('graph.cycle'))); return; }
  const rows = Array.from({length: Math.max(...levels.values()) + 1}, () => []);
  for (const node of nodes) rows[levels.get(node.topic_id)].push(node);
  const width = Math.max(720, Math.max(...rows.map(r => r.length)) * 260), height = rows.length * 128 + 50;
  const positions = new Map();
  rows.forEach((row, level) => row.forEach((node, i) => positions.set(node.topic_id, {
    x: (width - row.length * 260) / 2 + (i + 0.5) * 260, y: height - 60 - level * 128,
  })));
  const toolbar = el('div', 'cgToolbar'), search = el('input'), matches = el('select');
  search.type = 'search'; search.placeholder = t('graph.search');
  search.setAttribute('aria-label', search.placeholder); matches.setAttribute('aria-label', t('graph.select'));
  const viewport = el('div', 'cgViewport'), details = el('div', 'cgDetails');
  details.setAttribute('aria-live', 'polite');
  const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, width, height,
    role: 'group', 'aria-label': t('graph.diagram')});
  const arrowId = 'cgArrow-' + Math.random().toString(36).slice(2);
  const defs = svgEl('defs'), marker = svgEl('marker', {id: arrowId, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: 'auto'});
  marker.append(svgEl('path', {d: 'M0,0 L8,4 L0,8 Z', fill: '#7b8c9b'})); defs.append(marker); svg.append(defs);
  const edgeElements = [];
  for (const edge of edges) {
    const from = positions.get(edge.from), to = positions.get(edge.to); if (!from || !to) continue;
    const path = svgEl('path', {d: `M${from.x},${from.y - 33} C${from.x},${from.y - 80} ${to.x},${to.y + 80} ${to.x},${to.y + 33}`,
      fill: 'none', stroke: '#9daebc', 'stroke-width': 1.6, 'marker-end': `url(#${arrowId})`});
    edgeElements.push([edge, path]); svg.append(path);
  }
  let selected = '', zoom = 1;
  const groups = new Map();
  const status = node => node.content_status === 'not_imported' ? t('graph.pending') :
    ({completed: t('graph.completed'), in_progress: t('graph.inProgress'), paused: t('graph.paused')})[node.status] || t('graph.imported');
  const ancestors = id => {
    const result = new Set(), pending = [id];
    while (pending.length) { const next = pending.pop(); if (result.has(next)) continue; result.add(next); pending.push(...parents.get(next)); }
    return result;
  };
  function select(id, focus = false) {
    selected = id;
    const active = id ? ancestors(id) : null;
    for (const [key, group] of groups) {
      group.setAttribute('opacity', !active || active.has(key) ? 1 : 0.22);
      group.querySelector('rect').setAttribute('stroke-width', key === id ? 3 : 1.2);
      group.setAttribute('aria-pressed', String(key === id));
    }
    for (const [edge, path] of edgeElements) {
      const highlighted = active && active.has(edge.from) && active.has(edge.to);
      path.setAttribute('opacity', !active || highlighted ? 1 : 0.12);
      path.setAttribute('stroke-width', highlighted ? 2.8 : 1.6);
    }
    matches.value = id;
    details.replaceChildren();
    if (!id) { details.append(el('p', '', t('graph.hint'))); return; }
    const current = byId.get(id);
    details.append(el('strong', '', current.title), el('p', '', status(current)));
    for (const [label, list] of [[t('graph.parents'), parents.get(id)], [t('graph.children'), children.get(id)]]) {
      const row = el('p', '', label + ': ');
      if (!list.length) row.append(document.createTextNode(t('graph.none')));
      for (const key of list) { const button = el('button', '', byId.get(key).title); button.type = 'button'; button.onclick = () => select(key, true); row.append(button); }
      details.append(row);
    }
    if (onOpenTopic && current.content_status === 'available') {
      const button = el('button', '', t('graph.open')); button.type = 'button'; button.onclick = () => onOpenTopic(id); details.append(button);
    }
    if (focus) {
      const pos = positions.get(id);
      viewport.scrollTo({left: pos.x * zoom - viewport.clientWidth / 2, top: pos.y * zoom - viewport.clientHeight / 2, behavior: 'smooth'});
    }
  }
  for (const node of nodes) {
    const pos = positions.get(node.topic_id), pending = node.content_status === 'not_imported';
    const group = svgEl('g', {role: 'button', tabindex: 0, 'data-topic-id': node.topic_id, 'aria-label': node.title + ' · ' + status(node)});
    const fill = node.status === 'completed' ? '#cfdfd9' : node.status === 'in_progress' ? '#dce8f1' : '#fff';
    group.append(svgEl('rect', {x: pos.x - 112, y: pos.y - 33, width: 224, height: 66, rx: 6, fill,
      stroke: '#506e86', 'stroke-dasharray': pending ? '5 3' : 'none'}));
    const title = svgEl('title'); title.textContent = node.title + ' · ' + status(node); group.append(title);
    const label = svgEl('text', {x: pos.x, y: pos.y + (pending ? -5 : 5), 'text-anchor': 'middle', fill: '#20364a', 'font-size': 13});
    label.textContent = [...node.title].length > 15 ? [...node.title].slice(0, 14).join('') + '…' : node.title;
    group.append(label);
    if (pending) {
      const badge = svgEl('text', {x: pos.x, y: pos.y + 18, 'text-anchor': 'middle', fill: '#596e7e', 'font-size': 11}); badge.textContent = t('graph.pending');
      group.append(badge);
    }
    group.addEventListener('click', () => select(node.topic_id));
    group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(node.topic_id); } });
    groups.set(node.topic_id, group); svg.append(group);
  }
  function updateMatches() {
    const value = search.value.toLocaleLowerCase(); matches.replaceChildren(el('option', '', t('graph.choose'))); matches.firstChild.value = '';
    for (const node of nodes.filter(n => (n.title + ' ' + n.topic_id).toLocaleLowerCase().includes(value))) {
      const option = el('option', '', node.title); option.value = node.topic_id; matches.append(option);
    }
    if ([...matches.options].some(o => o.value === selected)) matches.value = selected;
  }
  search.addEventListener('input', updateMatches);
  search.addEventListener('keydown', event => { if (event.key === 'Enter' && matches.options.length > 1) select(matches.options[1].value, true); });
  matches.addEventListener('change', () => select(matches.value, true)); updateMatches();
  const zoomLabel = el('span', 'cgZoom', '100%');
  const scale = value => { zoom = Math.max(0.25, Math.min(2, value)); svg.setAttribute('width', width * zoom); svg.setAttribute('height', height * zoom); zoomLabel.textContent = Math.round(zoom * 100) + '%'; };
  toolbar.append(search, matches);
  for (const [label, action] of [['−', () => scale(zoom - 0.15)], ['+', () => scale(zoom + 0.15)],
    [t('graph.fit'), () => scale((viewport.clientWidth - 24) / width)],
    [t('graph.clear'), () => { search.value = ''; updateMatches(); select(''); }]]) {
    const button = el('button', '', label); button.type = 'button'; button.onclick = action; toolbar.append(button);
  }
  toolbar.append(zoomLabel); viewport.append(svg); target.append(toolbar, viewport, details); select('');
}
