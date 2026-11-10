import {appendCatalogOutline} from './catalog-outline.js?v=460c32ec9479f538';

const node = (tag, className) => {
  const element = document.createElement(tag); element.className = className; return element;
};

// Courses and banks share navigation and page structure, while their adapters
// retain ownership of content, permissions and learning state.
export function catalogPath(kind, id = null, rootId = null) {
  const path = `/${kind}` + (id ? '/' + encodeURIComponent(id) : '');
  return path + (id && rootId ? '?root=' + encodeURIComponent(rootId) : '');
}

export function courseWorkspaceId(route) {
  const match = route?.path.match(/^\/courses\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

export function workspaceSection(route) {
  if (!route?.subjectId) return null;
  if (/^\/banks(?:\/|$)/.test(route.path)) {
    return route.params.get('review') === '1' || route.params.get('card') === '1' ? null : 'banks';
  }
  if (/^\/courses(?:\/|$)/.test(route.path) || /^\/topic\//.test(route.path)) {
    return route.params.has('bank') ? 'banks' : 'courses';
  }
  return null;
}

export function createWorkspaceLocations(parseRoute) {
  const locations = new Map(), sidebarScrolls = new Map();
  const key = (subject, section) => `${subject}:${section}`;
  return {
    remember(hash, sidebarScroll) {
      const route = parseRoute(hash), section = workspaceSection(route);
      if (!section) return;
      locations.set(key(route.subjectId, section), hash);
      if (sidebarScroll !== undefined) sidebarScrolls.set(hash, sidebarScroll);
    },
    href(subject, section, fallback) { return locations.get(key(subject, section)) || fallback; },
    sidebarScroll(hash) { return sidebarScrolls.get(hash) || 0; },
    clear() { locations.clear(); sidebarScrolls.clear(); },
  };
}

export function catalogView({title, entries, renderEntry, expanded, keyPrefix}) {
  const heading = node('h1', 'portalPageTitle'); heading.textContent = title;
  const grid = node('div', 'courseGrid');
  for (const entry of entries) {
    const card = renderEntry(entry);
    appendCatalogOutline(card, entry, renderEntry, {expanded, keyPrefix});
    grid.append(card);
  }
  return {heading, grid};
}

export function workspaceLayout(sidebar) {
  const layout = node('div', 'dashboardLayout'), tasks = node('div', 'dashboardTasks');
  layout.append(sidebar, tasks);
  return {layout, tasks};
}

// Draft cards are navigation entries only. Keep the server's learning tasks,
// progress and dependency state intact while placing drafts in the same outline.
export function courseDisplayEntries(data, outline = []) {
  const drafts = (data.topics || []).filter(topic => topic.status === 'read_only').map(topic => ({
    id: `draft:${topic.id}`, topic_id: topic.id, title: topic.title, presentation: 'draft',
  }));
  if (!drafts.length) return {entries: data.tasks, drafts, hierarchy: data.pending_hierarchy, sourceOrder: []};
  const units = outline.length ? outline : data.units || [];
  const root = data.course.root_id;
  const visible = new Set((data.topics || []).map(topic => topic.id));
  const hierarchy = units.filter(unit => unit.id !== root).map(unit => ({
    ...unit, parent_id: unit.parent_id === root ? null : unit.parent_id,
    topic_ids: (unit.topic_ids || []).filter(id => visible.has(id)),
  }));
  const lessons = [...data.tasks.filter(task => task.type === 'Lesson'), ...drafts];
  const byTopic = new Map(lessons.map(task => [task.topic_id, task]));
  const sourceOrder = units.flatMap(unit => [unit.id, ...(unit.topic_ids || []).map(id => byTopic.get(id)?.id || id)]);
  const positions = new Map(sourceOrder.map((id, index) => [id, index]));
  lessons.sort((a, b) => (positions.get(a.id) ?? Infinity) - (positions.get(b.id) ?? Infinity));
  return {entries: [...data.tasks.filter(task => task.type !== 'Lesson'), ...lessons], drafts, hierarchy, sourceOrder};
}
