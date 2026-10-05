import {mountMermaid} from './vendor/mermaid/index.js?v=f04cea821ce153af';
import {renderBacklinks} from './vendor/document-backlinks.mjs?v=f04cea821ce153af';
import {t} from './i18n.js?v=f04cea821ce153af';

export function referenceHref(target) {
  const base = `#/subjects/${encodeURIComponent(target.subject_id)}/topic/${encodeURIComponent(target.topic_id)}`;
  return target.node_id ? `${base}?node=${encodeURIComponent(target.node_id)}` : base;
}

export function enhanceTopicContent(root, references) {
  root.classList.add('reading-outline');
  for (const link of root.querySelectorAll('a.topicReference')) {
    const source = link.closest('[id^="node-"]')?.id.slice(5);
    const ref = references?.outgoing?.find(item => item.source_node_id === source && item.target_rem_id === link.dataset.targetRemId);
    if (ref?.status === 'resolved' && ref.target) {
      link.href = referenceHref(ref.target);
      link.removeAttribute('aria-disabled');
      link.removeAttribute('title');
    } else {
      link.removeAttribute('href');
      link.setAttribute('aria-disabled', 'true');
      link.title = t('reader.referenceUnavailable');
    }
  }
  // Defer until the content is attached, so Mermaid can measure SVG text.
  queueMicrotask(() => { if (root.isConnected) void mountMermaid(root); });
}

export function mountTopicBacklinks(root, references) {
  root.querySelector(':scope > .topicBacklinks')?.remove();
  if (!references) return;
  root.classList.add('reading-outline');
  root.dataset.documentTitleSource = root.id;
  const sidebar = document.createElement('aside');
  sidebar.className = 'topicBacklinks sidebar right';
  root.append(sidebar);
  const documents = {[root.id]: {links: []}};
  for (const entry of references.backlinks || []) {
    const id = `${entry.topic_id}:${entry.node_id}`;
    documents[root.id].links.push(id);
    documents[id] = {title: entry.title, href: referenceHref(entry)};
  }
  // Keep the Blog renderer unchanged while adapting its page lookup to a SPA.
  renderBacklinks({
    querySelector: selector => selector === '.reading-outline' ? root : sidebar,
    createElement: tag => document.createElement(tag),
    getElementById: id => id === root.id ? root : null,
    defaultView: {location: {hash: ''}},
  }, {documents});
  sidebar.querySelector('h3').textContent = t('reader.backlinks');
  if (!references.backlinks?.length) sidebar.querySelector('li').textContent = t('reader.noBacklinks');
}

export function focusContentNode(root, nodeId) {
  const target = [...root.querySelectorAll('[id]')].find(node => node.id === `node-${nodeId}`);
  if (!target) return false;
  target.tabIndex = -1;
  target.focus({preventScroll: true});
  target.scrollIntoView({block: 'center'});
  return true;
}
