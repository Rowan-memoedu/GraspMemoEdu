import {t} from './i18n.js?v=969d8540bd10e3fb';

const blocks = new WeakMap();
export function reportableContent(element, context, contentBlockId, contentVersion) {
  if (!contentVersion || !context.topic_id) return element;
  element.dataset.reportBlock = contentBlockId;
  blocks.set(element, {...context, correction: {content_block_id: contentBlockId, content_version: contentVersion}});
  return element;
}

// Blanks replace author markers in the live DOM. Restore the markers in a
// detached range clone so the reported character offsets match stored HTML.
function rangeText(range) {
  const fragment = range.cloneContents();
  for (const input of fragment.querySelectorAll('[data-blank-id]')) input.replaceWith(document.createTextNode(`{{blank:${input.dataset.blankId}}}`));
  return fragment.textContent;
}

export function installContentReporting(showFeedback) {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'reportSelectionButton'; button.id = 'reportSelectionButton';
  button.hidden = true; button.textContent = t('correction.report'); document.body.append(button);
  let captured = null;
  const blockOf = node => (node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement)?.closest('[data-report-block]');
  function capture() {
    const selection = document.getSelection();
    if (!selection?.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0), block = blockOf(range.startContainer);
    if (!block || block !== blockOf(range.endContainer) || !blocks.has(block) || block.closest('[hidden]')) return null;
    const exact = rangeText(range);
    if (!exact.trim() || [...exact].length > 2000) return null;
    const before = document.createRange(), after = document.createRange();
    before.selectNodeContents(block); before.setEnd(range.startContainer, range.startOffset);
    after.selectNodeContents(block); after.setStart(range.endContainer, range.endOffset);
    const prefix = [...rangeText(before)], suffix = [...rangeText(after)];
    const context = blocks.get(block);
    return {...context, correction: {...context.correction, quote: {
      exact, prefix: prefix.slice(-50).join(''), suffix: suffix.slice(0, 50).join(''),
      start: prefix.length, end: prefix.length + [...exact].length,
    }}};
  }
  function open(context) {
    if (!context) return;
    button.hidden = true; showFeedback(context);
  }
  document.addEventListener('selectionchange', () => {
    if (document.querySelector('dialog[open]')) { button.hidden = true; return; }
    captured = capture(); button.hidden = !captured;
  });
  button.addEventListener('pointerdown', event => event.preventDefault());
  button.addEventListener('click', () => open(captured));
  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey) || event.key !== 'Enter' || event.isComposing
      || event.target.closest('input,textarea,[contenteditable="true"],dialog')) return;
    const context = capture();
    if (context) { event.preventDefault(); event.stopPropagation(); open(context); }
  }, true);
  const clear = () => { captured = null; button.hidden = true; };
  window.addEventListener('hashchange', clear);
  return {clear};
}
