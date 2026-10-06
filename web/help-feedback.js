import {t, translateMessage} from './i18n.js?v=221c916259ceae8b';
import {enhanceTopicContent} from './topic-content.js?v=221c916259ceae8b';

const node = (tag, cls = '', text) => { const el = document.createElement(tag); el.className = cls; if (text !== undefined) el.textContent = text; return el; };
export const feedbackCategories = {
  quality: ['formatting', 'factual_error', 'unnatural', 'other'],
  help: ['missing_prerequisite', 'derivation_steps', 'motivation', 'other'],
};
const previewCategories = new Set(['factual_error', 'unnatural', 'derivation_steps', 'motivation']);

function normalize(ranges) {
  const result = [];
  for (const [token, start, end] of ranges.toSorted((a, b) => a[0] - b[0] || a[1] - b[1])) {
    const last = result.at(-1);
    if (last && last[0] === token && start <= last[2]) last[2] = Math.max(last[2], end);
    else result.push([token, start, end]);
  }
  return result;
}
function overlaps(a, b) { return a[0] === b[0] && a[1] < b[2] && b[1] < a[2]; }
function subtract(ranges, removed) {
  let result = ranges;
  for (const b of removed) result = result.flatMap(a => !overlaps(a, b) ? [a] : [
    ...(a[1] < b[1] ? [[a[0], a[1], b[1]]] : []), ...(a[2] > b[2] ? [[a[0], b[2], a[2]]] : []),
  ]);
  return result;
}

// Anchors follow text, not screen coordinates, so wrapping does not change what
// was selected. Browser Range rects identify the actual displayed reading lines.
function lineSelector(stage, content, onChange) {
  const layer = node('div', 'feedbackLineLayer'), marquee = node('div', 'feedbackMarquee');
  marquee.hidden = true; layer.append(marquee); stage.append(layer);
  let selected = [], rows = [], frame = 0, stopped = false, disabled = false, drag = null, suppressClick = false;
  const paint = () => {
    for (const row of rows) {
      const active = row.ranges.some(a => selected.some(b => overlaps(a, b)));
      row.button.setAttribute('aria-pressed', String(active)); row.button.classList.toggle('selected', active);
    }
    onChange(rows.filter(row => row.button.getAttribute('aria-pressed') === 'true').length);
  };
  function measure() {
    frame = 0; if (stopped || !stage.isConnected || !stage.getClientRects().length) return;
    const base = stage.getBoundingClientRect(), bands = [], atoms = [];
    function add(rect, range, atomic = false) {
      if (rect.width < .1 || rect.height < .1) return;
      const center = (rect.top + rect.bottom) / 2;
      let band = bands.find(b => Math.abs(b.center - center) < Math.min(b.height, rect.height) * .4);
      if (!band || atomic) {
        band = {top: rect.top, bottom: rect.bottom, center, height: rect.height, ranges: []}; bands.push(band);
      }
      band.ranges.push(range); band.top = Math.min(band.top, rect.top); band.bottom = Math.max(band.bottom, rect.bottom);
    }
    for (const token of content.querySelectorAll('[data-feedback-text]')) {
      const text = token.firstChild;
      if (!text || text.nodeType !== Node.TEXT_NODE) continue;
      const range = document.createRange(); let offset = 0, point = 0;
      for (const char of text.data) {
        range.setStart(text, offset); offset += char.length; range.setEnd(text, offset);
        const rect = range.getBoundingClientRect();
        add(rect, [Number(token.dataset.feedbackText), point, point + 1]); point++;
      }
    }
    for (const atom of content.querySelectorAll('[data-feedback-atom]')) {
      const rect = atom.getBoundingClientRect(), range = [Number(atom.dataset.feedbackAtom), 0, 1];
      if (getComputedStyle(atom).display.startsWith('inline')) atoms.push({rect, range});
      else add(rect, range, true);
    }
    for (const {rect, range} of atoms) {
      const band = bands.find(b => Math.min(b.bottom, rect.bottom) - Math.max(b.top, rect.top) > b.height * .4);
      if (band) { band.ranges.push(range); band.top = Math.min(band.top, rect.top); band.bottom = Math.max(band.bottom, rect.bottom); }
      else add(rect, range, true);
    }
    rows = bands.sort((a, b) => a.top - b.top).map((band, index) => {
      const button = node('button', 'feedbackLine'); button.type = 'button'; button.disabled = disabled;
      button.setAttribute('aria-label', t('help.selectLine', {number: index + 1}));
      const top = Math.max(0, band.top - base.top - 1), height = band.bottom - band.top + 2;
      Object.assign(button.style, {top: `${top}px`, height: `${height}px`});
      const row = {button, ranges: normalize(band.ranges), top, bottom: top + height};
      button.addEventListener('click', event => {
        if (disabled || (suppressClick && event.detail)) { suppressClick = false; return; }
        selected = row.ranges.some(a => selected.some(b => overlaps(a, b))) ? subtract(selected, row.ranges) : normalize([...selected, ...row.ranges]);
        paint();
      });
      return row;
    });
    layer.replaceChildren(...rows.map(row => row.button), marquee); paint();
  }
  const schedule = () => { if (!stopped && !frame) frame = requestAnimationFrame(measure); };
  const observer = new ResizeObserver(schedule); observer.observe(stage);
  const mutation = new MutationObserver(schedule); mutation.observe(content, {childList: true, subtree: true});
  content.addEventListener('load', schedule, true);
  layer.addEventListener('pointerdown', event => {
    suppressClick = false;
    if (disabled || event.button !== 0 || event.pointerType === 'touch') return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, initial: selected, moved: false};
    layer.setPointerCapture(event.pointerId);
  });
  layer.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) return;
    drag.moved = true; event.preventDefault();
    const rect = layer.getBoundingClientRect(), left = Math.min(drag.x, event.clientX) - rect.left,
      top = Math.min(drag.y, event.clientY) - rect.top, right = Math.max(drag.x, event.clientX) - rect.left,
      bottom = Math.max(drag.y, event.clientY) - rect.top;
    marquee.hidden = false; Object.assign(marquee.style, {left: `${left}px`, top: `${top}px`, width: `${right - left}px`, height: `${bottom - top}px`});
    selected = normalize([...drag.initial, ...rows.filter(row => left < rect.width && right > 0 && top < row.bottom && bottom > row.top).flatMap(row => row.ranges)]);
    paint();
  });
  const finish = event => {
    if (!drag || event.pointerId !== drag.id) return;
    if (event.type === 'pointercancel') selected = drag.initial;
    // Capture retargets click to the layer: perform the click toggle here.
    if (!drag.moved && event.type === 'pointerup') {
      const y = event.clientY - layer.getBoundingClientRect().top;
      const row = rows.find(row => y >= row.top && y <= row.bottom);
      if (row) selected = row.ranges.some(a => selected.some(b => overlaps(a, b))) ? subtract(selected, row.ranges) : normalize([...selected, ...row.ranges]);
    }
    suppressClick = true; drag = null; marquee.hidden = true; paint();
  };
  layer.addEventListener('pointerup', finish); layer.addEventListener('pointercancel', finish);
  schedule();
  return {
    value: () => selected, clear() { selected = []; paint(); }, refresh: schedule,
    disable(value) { disabled = value; for (const row of rows) row.button.disabled = value; },
    destroy() { stopped = true; cancelAnimationFrame(frame); observer.disconnect(); mutation.disconnect(); content.removeEventListener('load', schedule, true); layer.remove(); },
  };
}

export function createHelpFeedback(request) {
  const element = node('div', 'helpFeedbackFields'); element.hidden = true;
  const choices = node('fieldset', 'feedbackCategories'); choices.append(node('legend', '', t('help.categoriesHint')));
  const prerequisite = node('input', 'feedbackPrerequisite'); prerequisite.type = 'text'; prerequisite.maxLength = 1000;
  prerequisite.placeholder = t('help.prerequisitePlaceholder'); prerequisite.setAttribute('aria-label', t('help.prerequisitePlaceholder')); prerequisite.hidden = true;
  const preview = node('section', 'feedbackPreview'); preview.hidden = true;
  const hint = node('p', 'inputHint', t('help.selectionHint')), status = node('p', 'feedbackPreviewStatus'); status.setAttribute('role', 'status');
  const clear = node('button', 'textButton', t('help.clearSelection')); clear.type = 'button'; clear.hidden = true;
  const retry = node('button', 'textButton', t('portal.retry.10')); retry.type = 'button'; retry.hidden = true;
  const scroll = node('div', 'feedbackPreviewScroll'), stage = node('div', 'feedbackPreviewStage'), content = node('div', 'courseContent');
  const style = node('style'); stage.append(content); scroll.append(stage); preview.append(hint, status, retry, clear, style, scroll);
  element.append(choices, prerequisite, preview);
  let context = null, epoch = 0, loaded = null, selector = null, loading = false, disabled = false;
  const selectedCategories = () => [...choices.querySelectorAll('input:checked')].map(input => input.value);
  const needsPreview = () => selectedCategories().some(id => previewCategories.has(id));
  async function load() {
    if (loading || loaded || !context) return;
    const ticket = epoch; loading = true; retry.hidden = true; status.textContent = t('help.previewLoading');
    try {
      const result = await request('help-requests/preview', {method: 'POST', body: context});
      if (ticket !== epoch) return;
      loaded = result; style.textContent = result.math_css || ''; content.innerHTML = result.content_html;
      // A preview is selectable reading material, not an active link surface.
      content.inert = true; enhanceTopicContent(content);
      selector = lineSelector(stage, content, count => { status.textContent = t('help.selectedLines', {count}); });
      selector.disable(disabled); clear.hidden = false;
    } catch (error) { if (ticket === epoch) { status.textContent = translateMessage(error.message); retry.hidden = false; } }
    finally { if (ticket === epoch) loading = false; }
  }
  function update() {
    prerequisite.hidden = !selectedCategories().includes('missing_prerequisite');
    preview.hidden = !needsPreview();
    if (!preview.hidden) { void load(); selector?.refresh(); }
  }
  choices.addEventListener('change', update); retry.addEventListener('click', () => { void load(); });
  clear.addEventListener('click', () => selector?.clear());
  function reset() {
    epoch++; selector?.destroy(); selector = null; loaded = null; context = null; loading = false; disabled = false;
    choices.replaceChildren(node('legend', '', t('help.categoriesHint'))); content.replaceChildren();
    prerequisite.value = ''; prerequisite.disabled = false; prerequisite.hidden = true; preview.hidden = true; element.hidden = true; clear.hidden = true; style.textContent = '';
    clear.disabled = retry.disabled = false;
  }
  return {element, reset,
    open(value) {
      reset(); if (!value.help_request) return;
      const {topic_id, question_id, content_block_id, content_version, kind, bank_id, group_id} = value;
      context = {topic_id, question_id, content_block_id, content_version, kind, bank_id, group_id};
      for (const id of feedbackCategories[kind || 'help']) {
        const label = node('label', 'feedbackCategory'), input = node('input'); input.type = 'checkbox'; input.value = id;
        label.append(input, node('span', '', t('help.category.' + id))); choices.append(label);
      }
      element.hidden = false; update();
    },
    value() {
      const categories = selectedCategories(), selection = needsPreview() ? selector?.value() || [] : [];
      return {categories, prerequisite: categories.includes('missing_prerequisite') ? prerequisite.value.trim() : '',
        selection, selection_hash: selection.length ? loaded.content_hash : '', selection_version: 1};
    },
    disable(value) { disabled = value; for (const input of element.querySelectorAll('input, button')) input.disabled = value; selector?.disable(value); },
  };
}

export function feedbackDetails(request) {
  const box = node('div', 'helpFeedbackDetails'), categories = request.categories || [];
  if (categories.length) {
    const list = node('ul', 'helpCategoryTags');
    for (const id of categories) list.append(node('li', '', t('help.category.' + id)));
    box.append(list);
  }
  if (request.prerequisite) box.append(node('p', 'helpPrerequisiteText', t('help.prerequisiteDetail') + request.prerequisite));
  if (request.selection?.length) box.append(node('p', 'inputHint', t('help.selectionLegend')));
  return box;
}
