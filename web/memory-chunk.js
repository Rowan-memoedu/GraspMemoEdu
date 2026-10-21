import {t} from './i18n.js?v=8405fcd52d52c0b6';
import {enhanceSourceContent} from './topic-content.js?v=8405fcd52d52c0b6';
import {prepareAnswerContent} from './self-assessment.js?v=8405fcd52d52c0b6';
import {helpableContent} from './content-report.js?v=8405fcd52d52c0b6';

const node = (tag, cls = '', text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};

// Author offsets count Unicode code points; DOM text ranges count UTF-16 units.
// Split text nodes only, so emphasis, code, images and the original structure survive.
export function underlineAnchors(root, anchors = []) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [];
  let text = '', current;
  while ((current = walker.nextNode())) {
    nodes.push({node: current, start: text.length, end: text.length + current.data.length});
    text += current.data;
  }
  const unicode = Array.from(text);
  const ranges = anchors.flatMap((anchor, index) => {
    let start = unicode.slice(0, anchor.start).join('').length;
    let end = unicode.slice(0, anchor.end).join('').length;
    if (text.slice(start, end) !== anchor.quote) {
      // Older rendered fragments may normalize line endings; do not guess
      // between repeated quotations if the authored offset no longer matches.
      start = text.indexOf(anchor.quote);
      if (start < 0 || text.indexOf(anchor.quote, start + 1) >= 0) return [];
      end = start + anchor.quote.length;
    }
    return [{start, end, index}];
  });
  for (const item of nodes) {
    const hits = ranges.filter(range => range.start < item.end && range.end > item.start);
    if (!hits.length || !item.node.data) continue;
    if (item.node.parentElement.closest('svg, math')) {
      const math = item.node.parentElement.closest('mjx-container, .math, svg, math');
      if (math) { math.classList.add('atomicSourceMark'); math.dataset.anchorIndex = hits[0].index; }
      continue;
    }
    const cuts = [...new Set([0, item.node.data.length, ...hits.flatMap(range =>
      [Math.max(0, range.start - item.start), Math.min(item.node.data.length, range.end - item.start)])])].sort((a, b) => a - b);
    const fragment = document.createDocumentFragment();
    for (let i = 1; i < cuts.length; i++) {
      const from = cuts[i - 1], to = cuts[i], value = item.node.data.slice(from, to);
      const hit = hits.find(range => range.start < item.start + to && range.end > item.start + from);
      const part = hit ? node('mark', 'atomicSourceMark', value) : document.createTextNode(value);
      if (hit) part.dataset.anchorIndex = hit.index;
      fragment.append(part);
    }
    item.node.replaceWith(fragment);
  }
}

function teachingFields(chunk) {
  const core = node('div', 'courseContent atomicChunkCore');
  const mnemonic = node('div', 'courseContent atomicMnemonicBody');
  core.innerHTML = chunk.content_html ?? chunk.html ?? '';
  mnemonic.innerHTML = chunk.mnemonic_html || '';
  // The original structured compiler appended mnemonic output after the
  // content's first outlineNode. Read that exact boundary without rewriting it.
  if (!('content_html' in chunk) && chunk.has_mnemonic && core.firstElementChild?.matches('.outlineNode')) {
    const first = core.firstElementChild;
    while (first.nextSibling) mnemonic.append(first.nextSibling);
  }
  return {core, mnemonic};
}

const enhance = root => {
  prepareAnswerContent(root); enhanceSourceContent(root); return root;
};

function centerSource(viewport) {
  let interacted = false;
  for (const event of ['wheel', 'pointerdown', 'touchstart', 'keydown'])
    viewport.addEventListener(event, () => { interacted = true; }, {once: true, passive: true});
  const center = () => {
    if (!viewport.isConnected || interacted) return;
    const first = viewport.querySelector('.atomicSourceMark');
    if (!first) return;
    const marks = first.dataset.anchorIndex === undefined ? [first] :
      [...viewport.querySelectorAll(`[data-anchor-index="${first.dataset.anchorIndex}"]`)];
    const rects = marks.flatMap(mark => [...mark.getClientRects()]).filter(r => r.width && r.height);
    if (!rects.length) return;
    const bounds = viewport.getBoundingClientRect();
    const x = (Math.min(...rects.map(r => r.left)) + Math.max(...rects.map(r => r.right))) / 2;
    const y = (Math.min(...rects.map(r => r.top)) + Math.max(...rects.map(r => r.bottom))) / 2;
    viewport.scrollLeft += x - bounds.left - viewport.clientWidth / 2;
    viewport.scrollTop += y - bounds.top - viewport.clientHeight / 2;
  };
  requestAnimationFrame(center);
  // Late source images/fonts can shift the target. Stop centering as soon as
  // the learner interacts with this region so their scrolling remains theirs.
  Promise.allSettled([document.fonts.ready,
    ...[...viewport.querySelectorAll('img')].map(img => img.decode())]).then(() => requestAnimationFrame(center));
}

export function memoryChunk(chunk, {topicId, version} = {}) {
  const box = node('section', 'atomicChunk'); box.dataset.chunkId = chunk.id;
  const heading = node('header', 'atomicChunkHeading');
  heading.append(node('p', 'eyebrow', t('atomic.chunk')), node('h2', '', chunk.title));
  const annotation = node('div', 'atomicAnnotation');
  const source = node('section', 'atomicSource');
  const sourceHeader = node('header', 'atomicSourceHeader');
  sourceHeader.append(node('h3', '', t('atomic.fromIntroduction')), node('p', '', t('atomic.sourceHint')));
  const viewport = node('div', 'atomicSourceViewport');
  viewport.tabIndex = 0; viewport.setAttribute('role', 'region'); viewport.setAttribute('aria-label', t('atomic.fromIntroduction'));
  const document = node('div', 'courseContent atomicSourceDocument');
  document.innerHTML = chunk.introduction?.html || '';
  if (chunk.introduction?.html) underlineAnchors(document, chunk.anchors);
  else for (const anchor of chunk.anchors || []) document.append(node('p', 'atomicSourceMark', anchor.quote));
  enhance(document);
  if (topicId) helpableContent(document, {topic_id: topicId}, 'introduction', version);
  viewport.append(document); source.append(sourceHeader, viewport);
  centerSource(viewport);
  const arrow = node('span', 'atomicArrow', '↘'); arrow.setAttribute('aria-hidden', 'true');
  const note = node('aside', 'atomicChunkNote');
  const {core, mnemonic} = teachingFields(chunk);
  enhance(core); enhance(mnemonic);
  note.append(node('h3', 'atomicFieldLabel', t('atomic.chunkCore')), core);
  if (mnemonic.textContent.trim() || mnemonic.querySelector('img, svg, video')) {
    const field = node('section', 'atomicMnemonic');
    field.append(node('h3', 'atomicFieldLabel', t('atomic.mnemonic')), mnemonic);
    note.append(field);
  }
  if (topicId) helpableContent(note, {topic_id: topicId}, `chunk:${chunk.id}`, version);
  annotation.append(source, arrow, note); box.append(heading, annotation);
  return box;
}

let dialogSequence = 0;
export function chunkPeek(chunks, options = {}) {
  if (!chunks?.length) return null;
  const actions = node('div', 'atomicChunkActions');
  const open = node('button', 'secondaryButton atomicPeekButton', t('atomic.viewChunk'));
  open.type = 'button'; open.dataset.readOnly = 'true';
  open.setAttribute('aria-haspopup', 'dialog');
  open.addEventListener('click', () => {
    const dialog = node('dialog', 'memoryChunkDialog'), id = `memory-chunk-${++dialogSequence}`;
    const header = node('header', 'memoryChunkDialogHeader');
    const title = node('h2', '', t('atomic.chunk')); title.id = id;
    dialog.setAttribute('aria-labelledby', id);
    const close = node('button', 'secondaryButton memoryChunkClose', t('atomic.closeChunk'));
    close.type = 'button'; close.addEventListener('click', () => dialog.close());
    header.append(title, close);
    const body = node('div', 'memoryChunkDialogBody'); body.id = `${id}-body`;
    dialog.append(header);
    let tabs;
    const select = index => {
      body.replaceChildren(memoryChunk(chunks[index], options));
      if (tabs) for (const [i, tab] of [...tabs.children].entries()) {
        tab.setAttribute('aria-selected', String(i === index)); tab.tabIndex = i === index ? 0 : -1;
      }
    };
    if (chunks.length > 1) {
      tabs = node('div', 'memoryChunkTabs'); tabs.setAttribute('role', 'tablist');
      tabs.setAttribute('aria-label', t('atomic.chunk')); body.setAttribute('role', 'tabpanel');
      chunks.forEach((chunk, index) => {
        const tab = node('button', 'memoryChunkTab', chunk.title); tab.type = 'button';
        tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', body.id); tab.id = `${id}-tab-${index}`;
        tab.addEventListener('click', () => { select(index); body.setAttribute('aria-labelledby', tab.id); });
        tab.addEventListener('keydown', event => {
          const offset = {ArrowRight: 1, ArrowLeft: -1, Home: -index, End: chunks.length - index - 1}[event.key];
          if (offset === undefined) return;
          event.preventDefault(); const next = (index + offset + chunks.length) % chunks.length;
          tabs.children[next].click(); tabs.children[next].focus();
        });
        tabs.append(tab);
      });
      dialog.append(tabs); body.setAttribute('aria-labelledby', `${id}-tab-0`);
    }
    dialog.append(body); select(0); actions.append(dialog);
    dialog.addEventListener('click', event => { if (event.target === dialog) {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    }});
    dialog.addEventListener('close', () => { dialog.remove(); open.focus({preventScroll: true}); }, {once: true});
    dialog.showModal(); body.querySelector('.atomicSourceViewport').focus({preventScroll: true});
  });
  actions.append(open); return actions;
}
