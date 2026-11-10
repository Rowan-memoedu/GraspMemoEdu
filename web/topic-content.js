import {mountMermaid} from './vendor/mermaid/index.js?v=460c32ec9479f538';
import {renderBacklinks} from './vendor/document-backlinks.mjs?v=460c32ec9479f538';
import {t} from './i18n.js?v=460c32ec9479f538';

export function referenceHref(target) {
  const base = `#/subjects/${encodeURIComponent(target.subject_id)}/topic/${encodeURIComponent(target.topic_id)}`;
  return target.node_id ? `${base}?node=${encodeURIComponent(target.node_id)}` : base;
}

export function enhanceTopicContent(root, references) {
  enhanceSourceContent(root);
  root.classList.add('reading-outline');
  for (const callout of root.querySelectorAll('[data-callout="requirement"]')) {
    for (const paragraph of callout.querySelectorAll(':scope > p')) {
      if (/^Calculator\s*:\s*(?:None)?$/i.test(paragraph.textContent.trim())
          && !paragraph.querySelector('img,svg,math,mjx-container,input,select,textarea')) paragraph.remove();
    }
    const body = callout.cloneNode(true);
    body.querySelector(':scope > strong')?.remove();
    if (!body.textContent.trim() && !body.querySelector('img,svg,math,mjx-container,video,audio,table,input,select,textarea')) callout.remove();
  }
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

// Source headings describe document structure, independently of learning cards.
// Move the original nodes so formatting, list numbering and code text survive.
export function enhanceSourceContent(root) {
  if (root.dataset.sourceEnhanced) return;
  root.dataset.sourceEnhanced = 'true';
  root.classList.add('sourceReading');
  const book = Boolean(root.querySelector('[data-body-layout="prose"]'));
  root.classList.toggle('sourceBook', book);
  // Block images and their adjacent original captions share one alignment.
  // Inline illustrations stay in their original sentence.
  for (const paragraph of root.querySelectorAll('p')) {
    if (paragraph.closest('figure,figcaption') || !paragraph.querySelector('img')
        || paragraph.textContent.trim() || paragraph.querySelector('svg,math,mjx-container,code')) continue;
    const figure = document.createElement('figure'); figure.className = 'sourceFigure';
    paragraph.before(figure); figure.append(paragraph);
    const caption = document.createElement('figcaption');
    while (figure.nextElementSibling?.tagName === 'P') {
      const next = figure.nextElementSibling, text = next.textContent.trim();
      if (text.length > 300 || !/^(?:[（(][a-zA-Z\d][)）]$|(?:图|表|Figure|Fig\.)\s*[A-Z\d]+(?:[.．-]\d+)*(?:\s|[:：]|$))/u.test(text)) break;
      // Move separators with the caption so the original text-node sequence
      // remains byte-for-byte identical after enhancement.
      while (figure.nextSibling && figure.nextSibling !== next) caption.append(figure.nextSibling);
      caption.append(next);
    }
    if (caption.childNodes.length) figure.append(caption);
  }
  if (book) {
    // PDF geometry, not caption guesses, decides which original figures share a row.
    const owners = [...root.querySelectorAll('[data-figure-group]')].map(node => ({
      node: node.closest('.outlineNode') || node, key: node.dataset.figureGroup,
    }));
    for (let index = 0; index < owners.length;) {
      const first = owners[index++], group = [first.node];
      while (index < owners.length && owners[index].key === first.key
          && group.at(-1).nextElementSibling === owners[index].node) group.push(owners[index++].node);
      if (group.length < 2) continue;
      const row = document.createElement('div'); row.className = 'sourceFigureRow';
      row.style.setProperty('--source-figure-count', String(group.length));
      group[0].before(row);
      for (const item of group) row.append(item);
      for (const caption of group.at(-1).querySelectorAll('figcaption p')) {
        if (/^(?:图|表|Figure|Fig\.)\s*[A-Z\d]/u.test(caption.textContent.trim())) caption.classList.add('sourceGroupCaption');
      }
    }
  }
  if (!root.querySelector('[id^="node-"]')) {
    const containers = [root, ...root.querySelectorAll('div,section,article')].reverse();
    for (const container of containers) {
      if (!Array.from(container.children).some(child => /^H[1-6]$/.test(child.tagName))) continue;
      const nodes = Array.from(container.childNodes), stack = [];
      for (const child of nodes) {
        if (/^H[1-6]$/.test(child.nodeName)) {
          const level = Number(child.nodeName[1]);
          while (stack.length && stack.at(-1).level >= level) stack.pop();
          const section = document.createElement('details');
          section.className = 'sourceOutlineSection'; section.open = true;
          const summary = document.createElement('summary');
          const heading = document.createElement(`h${Math.min(3 + stack.length, 6)}`);
          for (const attr of child.attributes) heading.setAttribute(attr.name, attr.value);
          heading.classList.add('sourceSectionTitle');
          heading.dataset.sourceHeadingLevel = String(level);
          heading.append(...child.childNodes); summary.append(heading);
          const body = document.createElement('div'); body.className = 'sourceSectionBody';
          section.append(summary, body);
          if (stack.length) stack.at(-1).body.append(section);
          else container.insertBefore(section, child);
          child.remove(); stack.push({level, body});
        } else if (stack.length) stack.at(-1).body.append(child);
      }
    }
  }
  for (const pre of root.querySelectorAll('pre')) {
    if (pre.closest('.local-mermaid,.sourceCodeBlock')) continue;
    const code = pre.querySelector('code');
    const language = [...(code?.classList || []), ...pre.classList]
      .find(value => /^language-[a-z0-9_+-]+$/i.test(value))?.slice(9);
    const block = document.createElement('div'); block.className = 'sourceCodeBlock';
    if (language) {
      const label = document.createElement('div'); label.className = 'sourceCodeLanguage';
      label.textContent = language; block.append(label);
    }
    pre.before(block); block.append(pre);
    pre.tabIndex = 0; pre.setAttribute('aria-label', language ? `${language} 代码` : '代码');
  }
  for (const table of root.querySelectorAll('table')) {
    if (table.closest('.sourceTable')) continue;
    const frame = document.createElement('div'); frame.className = 'sourceTable'; frame.tabIndex = 0;
    frame.setAttribute('role', 'region'); frame.setAttribute('aria-label', '表格');
    table.before(frame); frame.append(table);
  }
  for (const image of root.querySelectorAll('img')) {
    image.decoding = 'async';
    const failed = () => {
      if (image.nextElementSibling?.classList.contains('sourceImageError')) return;
      const notice = document.createElement('span'); notice.className = 'sourceImageError';
      notice.setAttribute('role', 'status'); notice.textContent = `图片未能加载${image.alt ? `：${image.alt}` : ''}`;
      image.after(notice);
    };
    image.addEventListener('error', failed, {once:true});
    if (!image.getAttribute('src') || image.complete && !image.naturalWidth) failed();
  }
}

export function mountTopicBacklinks(root, references) {
  root.querySelector(':scope > .topicBacklinks')?.remove();
  root.classList.toggle('reading-outline', Boolean(references));
  if (!references?.backlinks?.length) {
    delete root.dataset.documentTitleSource;
    return;
  }
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
}

export function focusContentNode(root, nodeId) {
  const target = [...root.querySelectorAll('[id]')].find(node => node.id === `node-${nodeId}`);
  if (!target) return false;
  target.tabIndex = -1;
  target.focus({preventScroll: true});
  target.scrollIntoView({block: 'center'});
  return true;
}
