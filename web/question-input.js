import {t} from './i18n.js?v=842cc571a4ea8008';

const node = (tag, cls, text) => {
  const element = document.createElement(tag); element.className = cls;
  if (text !== undefined) element.textContent = text;
  return element;
};
const parse = value => { try { return JSON.parse(value); } catch { return null; } };
export const answerReady = input => Boolean(input && !input.disabled && input.value.trim()
  && (!input.questionControl || input.questionControl.complete()));

// The serialized answer uses the existing draft, retry and idempotency path.
// This component receives presentation metadata only, never answer keys.
export function questionInput(spec, {id, value = '', stem, disabled = false, formId} = {}) {
  const root = node('div', 'questionInteraction');
  root.dataset.questionType = spec.type;
  const input = node('textarea', 'structuredAnswer'); input.id = id;
  input.name = 'answer'; input.hidden = true; input.disabled = disabled;
  input.value = value; root.append(input);
  let values = parse(value), emitting = false, selected = null;
  const options = side => new Map(spec[side].map(item => [item.id, item.text]));
  const optionContent = (element, item) => {
    if (item?.html) element.innerHTML = item.html; // Compiled with the existing safe Markdown/MathJax importer.
    else element.textContent = item?.text || '';
  };
  const normalize = () => {
    if (spec.type === 'matching') {
      const used = {left: new Set(), right: new Set()}, previous = Array.isArray(values) ? values : [];
      values = Array.from({length: spec.row_count}, (_, index) => Object.fromEntries(['left', 'right'].map(side => {
        const candidate = spec.fixed_side === side ? spec.fixed[index] : previous[index]?.[side];
        const valid = options(side).has(candidate) && !used[side].has(candidate);
        if (valid) used[side].add(candidate);
        return [side, valid ? candidate : null];
      })));
    } else {
      const previous = values && !Array.isArray(values) && typeof values === 'object' ? values : {};
      const rows = spec.type === 'true_false' ? spec.statements : spec.blanks;
      values = Object.fromEntries(rows.map(item => [item.id, spec.type === 'true_false'
        ? (typeof previous[item.id] === 'boolean' ? previous[item.id] : null)
        : (typeof previous[item.id] === 'string' ? previous[item.id] : '')]));
    }
  };
  const complete = () => input.value.length <= 2000 && (spec.type === 'matching'
    ? values.every(pair => pair.left && pair.right)
    : Object.values(values).every(v => spec.type === 'true_false' ? typeof v === 'boolean' : Boolean(v.trim())));
  function emit() {
    input.value = JSON.stringify(values); emitting = true;
    input.dispatchEvent(new Event('input', {bubbles: true})); emitting = false;
  }
  function updateSlot(side, row, candidate) {
    if (disabled || side === spec.fixed_side || (candidate !== null && !options(side).has(candidate))) return;
    const from = candidate ? values.findIndex(pair => pair[side] === candidate) : -1;
    if (from >= 0 && from !== row) values[from][side] = values[row][side];
    values[row][side] = candidate; selected = null; emit(); renderMatching();
    root.querySelector(`.matchingSlot[data-side="${side}"][data-row="${row}"]`)?.focus({preventScroll: true});
  }
  function moveSlot(side, from, to) {
    if (disabled || side === spec.fixed_side || to < 0 || to >= values.length) return;
    [values[from][side], values[to][side]] = [values[to][side], values[from][side]];
    emit(); renderMatching();
    root.querySelector(`.matchingSlot[data-side="${side}"][data-row="${to}"]`)?.focus({preventScroll: true});
    root.querySelector('.matchingStatus').textContent = t('question.moved', {number: to + 1});
  }
  function dropData(event, side) {
    event.preventDefault();
    const data = parse(event.dataTransfer?.getData('text/plain'));
    return data?.side === side && options(side).has(data.id) ? data.id : null;
  }
  function renderMatching() {
    root.querySelector('.matchingBoard')?.remove();
    const board = node('div', 'matchingBoard');
    board.append(node('p', 'interactionHint', t('question.matchHint')));
    const rows = node('div', 'matchingRows');
    values.forEach((pair, index) => {
      const row = node('div', 'matchingRow');
      for (const side of ['left', 'right']) {
        if (side === 'right') {
          const arrow = node('span', 'matchingArrow', spec.direction === 'both' ? '↔' : '→');
          arrow.setAttribute('aria-hidden', 'true'); row.append(arrow);
        }
        const fixed = side === spec.fixed_side;
        const cell = node('div', 'matchingCell');
        const slot = node('button', `matchingSlot${fixed ? ' fixedSlot' : ''}${pair[side] ? ' occupied' : ''}`);
        if (pair[side]) optionContent(slot, spec[side].find(item => item.id === pair[side]));
        else slot.textContent = t('question.slot', {number: index + 1});
        slot.type = 'button'; slot.disabled = disabled || fixed;
        slot.dataset.side = side; slot.dataset.row = index;
        slot.setAttribute('aria-label', t('question.slotLabel', {side: t(`question.${side}`), number: index + 1})
          + ': ' + slot.textContent);
        slot.addEventListener('click', () => {
          if (selected && selected.side !== side) return;
          updateSlot(side, index, selected?.id || null);
        });
        slot.addEventListener('dragover', event => { if (!slot.disabled) event.preventDefault(); });
        slot.addEventListener('drop', event => { const candidate = dropData(event, side); if (candidate) updateSlot(side, index, candidate); });
        slot.draggable = !slot.disabled && Boolean(pair[side]);
        slot.addEventListener('dragstart', event => event.dataTransfer.setData('text/plain', JSON.stringify({side, id:pair[side]})));
        cell.append(slot);
        if (!fixed) {
          const moves = node('div', 'matchingMoves');
          for (const [offset, icon, key] of [[-1, '↑', 'question.moveUp'], [1, '↓', 'question.moveDown']]) {
            const move = node('button', 'matchingMove', icon); move.type = 'button';
            move.disabled = disabled || !pair[side] || index + offset < 0 || index + offset >= values.length;
            move.setAttribute('aria-label', t(key, {side: t(`question.${side}`), number: index + 1}));
            move.addEventListener('click', () => moveSlot(side, index, index + offset)); moves.append(move);
          }
          cell.append(moves);
        }
        row.append(cell);
      }
      rows.append(row);
    });
    board.append(rows);
    const banks = node('div', 'matchingBanks');
    for (const side of ['left', 'right']) {
      const bank = node('div', 'matchingBank'); bank.dataset.side = side;
      bank.setAttribute('aria-label', t(`question.${side}`));
      bank.append(node('p', 'matchingBankTitle', t(`question.${side}`)));
      for (const option of spec[side]) {
        const used = values.some(pair => pair[side] === option.id);
        const chip = node('button', 'matchingOption'); chip.type = 'button'; optionContent(chip, option);
        chip.dataset.optionId = option.id; chip.dataset.side = side;
        chip.disabled = disabled || side === spec.fixed_side || used;
        chip.draggable = !chip.disabled;
        chip.setAttribute('aria-pressed', String(selected?.side === side && selected.id === option.id));
        chip.addEventListener('click', () => {
          selected = selected?.side === side && selected.id === option.id ? null : {side, id:option.id};
          renderMatching();
          root.querySelector(`.matchingOption[data-side="${side}"][data-option-id="${option.id}"]`)?.focus({preventScroll: true});
        });
        chip.addEventListener('dragstart', event => { selected = {side, id:option.id}; event.dataTransfer.setData('text/plain', JSON.stringify(selected)); });
        bank.append(chip);
      }
      bank.addEventListener('dragover', event => { if (!disabled && side !== spec.fixed_side) event.preventDefault(); });
      bank.addEventListener('drop', event => {
        const candidate = dropData(event, side), index = values.findIndex(pair => pair[side] === candidate);
        if (candidate && index >= 0) updateSlot(side, index, null);
      });
      banks.append(bank);
    }
    const status = node('span', 'matchingStatus srOnly'); status.setAttribute('role', 'status');
    board.append(banks, status); root.append(board);
  }
  function renderBooleans() {
    root.querySelector('.judgmentList')?.remove();
    const list = node('div', 'judgmentList');
    spec.statements.forEach((statement, index) => {
      const group = node('fieldset', 'judgmentItem');
      const legend = node('legend', ''); legend.append(document.createTextNode(`${index + 1}. `));
      const statementBody = node('span', 'statementBody'); optionContent(statementBody, statement);
      legend.append(statementBody); group.append(legend);
      for (const choice of [true, false]) {
        const label = node('label', 'judgmentChoice'), radio = node('input', ''); radio.type = 'radio';
        radio.name = `${id}-${statement.id}`; radio.value = String(choice); radio.disabled = disabled;
        if (formId) radio.setAttribute('form', formId);
        radio.checked = values[statement.id] === choice;
        radio.addEventListener('change', () => { values[statement.id] = choice; emit(); });
        label.append(radio, node('span', '', t(choice ? 'question.true' : 'question.false'))); group.append(label);
      }
      list.append(group);
    });
    root.append(list);
  }
  function mountBlanks() {
    const textNodes = [], walker = document.createTreeWalker(stem, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) if (/\{\{blank:[A-Za-z][A-Za-z0-9_-]*\}\}/.test(walker.currentNode.textContent)) textNodes.push(walker.currentNode);
    for (const original of textNodes) {
      const parts = original.textContent.split(/(\{\{blank:[A-Za-z][A-Za-z0-9_-]*\}\})/g), fragment = document.createDocumentFragment();
      for (const part of parts) {
        const key = part.match(/^\{\{blank:([^}]+)\}\}$/)?.[1], index = spec.blanks.findIndex(item => item.id === key);
        if (index < 0) { fragment.append(document.createTextNode(part)); continue; }
        const field = node('input', 'inlineBlank'); field.type = 'text'; field.dataset.blankId = key;
        field.maxLength = 2000; field.autocomplete = 'off'; field.spellcheck = false; field.disabled = disabled;
        field.setAttribute('aria-label', spec.blanks[index].label || t('question.blank', {number: index + 1}));
        if (formId) field.setAttribute('form', formId);
        field.value = values[key];
        field.addEventListener('input', () => { values[key] = field.value; emit(); });
        field.addEventListener('keydown', event => {
          if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) {
            event.preventDefault(); document.getElementById(formId)?.requestSubmit();
          }
        });
        fragment.append(field);
      }
      original.replaceWith(fragment);
    }
  }
  normalize();
  input.questionControl = {
    complete,
    focus: () => (stem?.querySelector('.inlineBlank') || root.querySelector('input,button'))?.focus(),
    setDisabled: value => {
      if (disabled === value) return;
      disabled = value; input.disabled = value;
      if (spec.type === 'matching') renderMatching();
      if (spec.type === 'true_false') for (const field of root.querySelectorAll('input')) field.disabled = value;
      if (spec.type === 'fill_blank') for (const field of stem.querySelectorAll('[data-blank-id]')) field.disabled = value;
    },
  };
  if (spec.type === 'true_false') renderBooleans();
  if (spec.type === 'matching') renderMatching();
  if (spec.type === 'fill_blank') mountBlanks();
  input.addEventListener('input', () => {
    if (emitting) return;
    values = parse(input.value); normalize();
    if (spec.type === 'matching') renderMatching();
    if (spec.type === 'true_false') renderBooleans();
    if (spec.type === 'fill_blank') for (const field of stem.querySelectorAll('[data-blank-id]')) field.value = values[field.dataset.blankId] || '';
  });
  return {element:root, input};
}
