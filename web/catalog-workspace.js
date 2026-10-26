import {appendCatalogOutline} from './catalog-outline.js?v=8f6629f316cc79a6';

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
