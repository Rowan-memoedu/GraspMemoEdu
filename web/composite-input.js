// Hallmark · component: composite-answer · genre: editorial · theme: existing learning UI
// States: default, hover, focus, active, disabled, loading (parent), error, success.
const node = (tag, cls, text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};

const numberLabel = value => {
  const label = String(value || '').trim();
  const exact = label.match(/^(?:\((\d+)\)|\[(\d+)\]|（(\d+)）|(\d+))$/);
  if (exact) return exact.slice(1).find(Boolean);
  const named = label.match(/\b(?:gap|blank)\s*[[(（]?(\d+)/i)?.[1];
  if (named) return named;
  const prefix = label.match(/^(?:\((\d+)\)|\[(\d+)\]|（(\d+)）|(\d+)[.．、)）])\s*(.+)$/);
  if (!prefix) return null;
  const instruction = prefix[5];
  const gapInstruction = /^(?:请|請)?\s*(?:(?:选择|選擇|选出|選出)[^。！？?\n]*(?:补全|填补|填入)|(?:补全|填补|填入|填写))[^。！？?\n]*(?:空白|空缺|缺口|空格|空处)/.test(instruction)
    || /^(?:please\s+)?(?:fill(?:\s+in)?|complete)\s+(?:(?:the|each|this|a)\s+)?(?:gaps?|blanks?|missing\s+(?:word|phrase|expression))\b/i.test(instruction)
    || /^(?:please\s+)?(?:choose|select)\b[^?!.\n]*\b(?:for|to\s+(?:fill|complete)(?:\s+in)?)\s+(?:(?:the|each|this|a)\s+)?(?:gaps?|blanks?|missing\s+(?:word|phrase|expression))\b/i.test(instruction);
  return gapInstruction ? prefix.slice(1, 5).find(Boolean) : null;
};
const taskParagraph = text => /^(?:(?:read|choose|select|fill|complete|match|answer|write|use|put|for\s+(?:each|the|gaps?|blanks?|questions?))\b|(?:为|請|请|阅读|根據|根据|选择|選擇|选出|填写|填入|完成|将|將|在第|对第))/i.test(text.trim())
  && /\b(?:gap|blank|option|sentence|question|answer|passage|paragraph)\b|空|缺口|选|選|题|題|填/i.test(text);
const ignoredPassageText = element => element?.closest?.('code,pre,kbd,samp,script,style,textarea,input,select,button,label,legend,svg,math,mjx-container,.MathJax,.passageBlank');

// Only presentation metadata participates in linking. Explicit IDs win; legacy
// numbers must have an unambiguous label and one non-instruction occurrence.
export function mountPassageBlanks(container, controls, createField, {numbered = true, bare = false} = {}) {
  if (!container) return [];
  const byId = new Map(controls.map(control => [control.id, control])), mounted = [], explicit = new Set(), targets = [];
  const inside = element => {
    for (let current = element; current; current = current.parentElement) if (current === container) return true;
    return false;
  };
  const make = (control, number = null) => {
    number ||= numberLabel(control.label) || String(controls.indexOf(control) + 1);
    const wrapper = node('span', 'passageBlank'); wrapper.dataset.passageId = control.id;
    const badge = node('span', 'passageBlankNumber', number); badge.setAttribute('aria-hidden', 'true');
    const field = createField(control, number);
    wrapper.append(badge, field); mounted.push({id: control.id, wrapper, field, number}); return wrapper;
  };
  const elementTargets = [...container.querySelectorAll('[data-control]')].filter(marker => byId.has(marker.dataset.control) && !ignoredPassageText(marker));
  for (const marker of elementTargets) explicit.add(marker.dataset.control);
  const texts = [];
  if (document.createTreeWalker) {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) if (!ignoredPassageText(walker.currentNode.parentElement)
      && !walker.currentNode.parentElement.closest?.('[data-control]')) texts.push(walker.currentNode);
  }
  for (const text of texts) for (const match of text.textContent.matchAll(/\{\{(?:blank|control):([A-Za-z][A-Za-z0-9_-]*)\}\}/g)) {
    if (!byId.has(match[1])) continue;
    explicit.add(match[1]); targets.push({text, start: match.index, end: match.index + match[0].length, control: byId.get(match[1])});
  }
  const candidates = new Map(), byNumber = new Map();
  for (const control of controls) {
    const number = numberLabel(control.label);
    if (number && !explicit.has(control.id)) byNumber.set(number, [...(byNumber.get(number) || []), control]);
  }
  const addCandidate = target => candidates.set(target.control.id, [...(candidates.get(target.control.id) || []), target]);
  const gapCue = /\b(?:gap|blank|cloze)\b|空白|空缺|填空|完形/i.test(container.textContent || '');
  for (const text of texts) {
    // Headings can carry explicit source markers, but are not prose evidence
    // from which a numbered gap may be inferred.
    if (text.parentElement.closest?.('h1,h2,h3,h4,h5,h6')) continue;
    const block = text.parentElement.closest?.('p,li,td,th,blockquote') || text.parentElement;
    const content = text.textContent, paragraph = block?.textContent || content;
    if (/\$|\\\(|\\\[|\\begin\b/.test(content)) continue;
    const instruction = taskParagraph(paragraph), colon = paragraph.search(/[：:]/), offset = paragraph.indexOf(content);
    // A directive and its example can share one paragraph. A colon is an
    // explicit boundary; references before it remain plain instructional text.
    const inPassage = index => !instruction || (colon >= 0 && offset >= 0 && offset + index > colon
      && paragraph.indexOf(content, offset + 1) < 0 && /[A-Za-z]{2}/.test(paragraph.slice(colon + 1)));
    if (numbered) for (const match of content.matchAll(/(?:\((\d+)\)|\[(\d+)\]|（(\d+)）)(?:[ \t]*_{2,})?/g)) {
      if (!inPassage(match.index)) continue;
      const number = match.slice(1).find(Boolean), choices = byNumber.get(number);
      if (choices?.length !== 1) continue;
      const control = choices[0], before = content.slice(0, match.index), after = content.slice(match.index + match[0].length);
      if (/\d|\p{L}/u.test(before.slice(-1)) || /[)\]）]\s*(?:[-–—~～]|to|至)\s*$/i.test(before)
          || /^\s*(?:[-–—~～]|to|至)\s*[[(（]\d/i.test(after)) continue;
      // A plain numeric question label alone is not evidence of a passage gap.
      if (/^\d+$/.test(String(control.label || '').trim()) && !gapCue && !/_/.test(match[0])) continue;
      if (!/\p{L}{2}/u.test(paragraph.replace(/\([^)]*\)|\[[^\]]*\]/g, ''))) continue;
      addCandidate({text, start: match.index, end: match.index + match[0].length, control, number});
    }
    if (bare && controls.length === 1 && !explicit.has(controls[0].id)) {
      for (const match of content.matchAll(/_{3,}/g)) {
        if (!inPassage(match.index)) continue;
        if (/[\p{L}\p{N}_]/u.test(content[match.index - 1] || '') || /[\p{L}\p{N}_]/u.test(content[match.index + match[0].length] || '')) continue;
        addCandidate({text, start: match.index, end: match.index + match[0].length, control: controls[0]});
      }
    }
  }
  for (const matches of candidates.values()) if (matches.length === 1) targets.push(matches[0]);
  for (const marker of elementTargets) if (inside(marker)) marker.replaceWith(make(byId.get(marker.dataset.control)));
  for (const text of texts) {
    if (!inside(text)) continue;
    const matches = targets.filter(target => target.text === text).sort((a, b) => a.start - b.start);
    if (!matches.length) continue;
    const fragment = document.createDocumentFragment(); let offset = 0;
    for (const match of matches) {
      fragment.append(document.createTextNode(text.textContent.slice(offset, match.start)), make(match.control, match.number));
      offset = match.end;
    }
    fragment.append(document.createTextNode(text.textContent.slice(offset))); text.replaceWith(fragment);
  }
  return mounted;
}

export function passageSelect(control, number, {formId, disabled = false} = {}) {
  const field = node('select', 'passageBlankField passageBlankSelect'); field.dataset.controlId = control.id;
  field.setAttribute('aria-label', control.label || `第 ${number} 空`); field.disabled = disabled;
  if (formId) field.setAttribute('form', formId);
  const empty = node('option', '', '—'); empty.value = ''; field.append(empty);
  control.options.forEach((option, index) => {
    const item = node('option', '', `${String.fromCharCode(65 + index)} · ${option.text}`); item.value = option.id; field.append(item);
  });
  return field;
}

export function answerShortcut(event, formId, disabled = false) {
  if (!disabled && !event.defaultPrevented && (event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) {
    event.preventDefault(); document.getElementById(formId)?.requestSubmit();
  }
}

export function compositeInput(spec, {id, value = '', stem, disabled = false, formId} = {}) {
  const root = node('div', 'questionInteraction compositeInteraction');
  const input = node('textarea', 'structuredAnswer'); input.id = id; input.name = 'answer';
  input.hidden = true; input.disabled = disabled; root.append(input);
  let draft; try { draft = JSON.parse(value); } catch { draft = {}; }
  let section = Math.max(0, Math.min(spec.sections.length - 1, Number.isInteger(draft?.section) ? draft.section : 0));
  let values = Object.fromEntries(spec.controls.map(c => [c.id, typeof draft?.values?.[c.id] === 'string' ? draft.values[c.id] : '']));
  let emitting = false;
  const valid = c => c.type === 'select' ? c.options.some(o => o.id === values[c.id]) : Boolean(values[c.id].trim());
  const completeSection = index => spec.sections[index].complete && spec.controls.filter(c => spec.sections[index].control_ids.includes(c.id)).every(valid);
  // A forged/stale draft must not navigate beyond its first unfinished section.
  if (!disabled) for (let i = 0; i < section; i++) if (!completeSection(i)) { section = i; break; }
  const save = (emit = true) => {
    input.value = JSON.stringify({section, values});
    if (emit) { emitting = true; input.dispatchEvent(new Event('input', {bubbles: true})); emitting = false; }
  };
  let sectionFields = [];
  const enabled = controlId => !disabled && spec.sections[section].control_ids.includes(controlId);
  const sync = () => {
    for (const {id: controlId, field} of [...stemFields, ...sectionFields]) {
      field.value = values[controlId]; field.disabled = !enabled(controlId);
    }
    for (const field of root.querySelectorAll('input')) {
      if (field.type === 'radio') field.checked = values[field.dataset.controlId] === field.value;
      else if (field.dataset.controlId) field.value = values[field.dataset.controlId];
      if (field.dataset.controlId) field.disabled = !enabled(field.dataset.controlId);
    }
    for (const group of root.querySelectorAll('fieldset')) if (group.dataset.controlId) group.disabled = !enabled(group.dataset.controlId);
  };
  const changed = (control, value) => {
    if (!enabled(control.id)) return;
    values[control.id] = value; sync(); save();
    const next = root.querySelector('.compositeNext'); if (next) next.disabled = !completeSection(section);
  };
  const inlineField = (control, number) => {
    const field = control.type === 'select' ? passageSelect(control, number, {formId})
      : node('input', 'compositeField passageBlankField passageBlankText');
    field.dataset.controlId = control.id;
    if (control.type !== 'select') {
      field.type = 'text'; field.autocomplete = 'off'; field.maxLength = 2000;
      field.setAttribute('aria-label', control.label || `第 ${number} 空`);
      if (formId) field.setAttribute('form', formId);
      if (control.input_type === 'number') field.inputMode = 'decimal';
    }
    field.addEventListener(control.type === 'select' ? 'change' : 'input', () => changed(control, field.value));
    field.addEventListener('keydown', event => answerShortcut(event, formId, field.disabled));
    return field;
  };
  const stemFields = mountPassageBlanks(stem, spec.controls, inlineField);
  root.addEventListener('keydown', event => answerShortcut(event, formId, disabled));
  function render() {
    root.querySelector('.compositeBody')?.remove();
    sectionFields = [];
    const body = node('div', 'compositeBody');
    const indexes = disabled ? spec.sections.map((_, i) => i) : [section];
    for (const index of indexes) {
      const part = spec.sections[index], panel = node('section', 'compositeSection');
      if (spec.sections.length > 1) panel.append(node('p', 'stepCounter', `${index + 1} / ${spec.sections.length}`));
      const text = node('div', 'courseContent'); text.innerHTML = part.html; panel.append(text);
      const localFields = mountPassageBlanks(text, spec.controls.filter(c => part.control_ids.includes(c.id)), inlineField);
      sectionFields.push(...localFields);
      for (const control of spec.controls.filter(c => part.control_ids.includes(c.id))) {
        if (control.type === 'select') {
          const group = node('fieldset', 'compositeControl compositeChoiceControl');
          group.id = `${id}-${control.id}`; group.dataset.controlId = control.id; group.disabled = disabled;
          const label = node('legend', 'compositeLabel', control.label || control.id); label.id = `${group.id}-label`;
          const list = node('div', 'choiceList'); list.setAttribute('role', 'radiogroup'); list.setAttribute('aria-labelledby', label.id);
          control.options.forEach((option, optionIndex) => {
            const choice = node('label', 'choiceOption'), radio = node('input', '');
            radio.type = 'radio'; radio.name = group.id; radio.value = option.id;
            radio.dataset.controlId = control.id;
            radio.disabled = disabled; radio.checked = values[control.id] === option.id;
            if (formId) radio.setAttribute('form', formId);
            const content = node('span', 'choiceBody');
            // Rich option HTML uses the same compiled, sanitized source as the question.
            if (option.html) content.innerHTML = option.html; else content.textContent = option.text;
            radio.addEventListener('change', () => { if (radio.checked) changed(control, option.id); });
            choice.append(radio, node('span', 'choiceLetter', String.fromCharCode(65 + optionIndex)), content); list.append(choice);
          });
          group.append(label, list); panel.append(group);
        } else if (![...stemFields, ...localFields].some(field => field.id === control.id)) {
          const wrap = node('label', 'compositeControl compositeTextControl');
          const label = node('span', 'compositeLabel', control.label || control.id), field = node('input', 'compositeField');
          field.id = `${id}-${control.id}`; field.disabled = disabled; field.dataset.controlId = control.id;
          field.setAttribute('aria-label', control.label || control.id); if (formId) field.setAttribute('form', formId);
          field.type = 'text'; field.autocomplete = 'off'; if (control.input_type === 'number') field.inputMode = 'decimal';
          field.value = values[control.id]; wrap.append(label, field);
          field.addEventListener('input', () => changed(control, field.value)); panel.append(wrap);
        }
      }
      if (!part.complete) panel.append(node('p', 'featureNotice', '原材料缺少本段题面，无法继续作答。'));
      body.append(panel);
    }
    if (!disabled && section < spec.sections.length - 1) {
      const next = node('button', 'primaryButton compositeNext', '继续'); next.type = 'button';
      next.disabled = !completeSection(section);
      next.addEventListener('click', () => { if (disabled || !completeSection(section)) return; section++; save(); render(); focus(); });
      body.append(next);
    }
    root.append(body); sync();
  }
  const complete = () => !disabled && spec.grading !== 'unavailable' && section === spec.sections.length - 1
    && spec.sections.every((_, i) => completeSection(i)) && input.value.length <= 16384;
  const focus = () => (stemFields.find(item => !item.field.disabled)?.field || [...root.querySelectorAll('select,input')].find(field => !field.disabled))?.focus();
  input.questionControl = {complete, empty: () => Object.values(values).every(v => !v.trim()),
    focus,
    setDisabled: value => {
      disabled = value; input.disabled = value;
      for (const field of root.querySelectorAll('fieldset,input,select')) field.disabled = value;
      sync();
      const next = root.querySelector('.compositeNext'); if (next) next.disabled = value || !completeSection(section);
    }};
  input.addEventListener('input', () => {
    if (emitting) return;
    try { draft = JSON.parse(input.value); } catch { return; }
    values = Object.fromEntries(spec.controls.map(c => [c.id, typeof draft?.values?.[c.id] === 'string' ? draft.values[c.id] : '']));
    section = Math.max(0, Math.min(spec.sections.length - 1, Number.isInteger(draft?.section) ? draft.section : 0));
    if (!disabled) for (let i = 0; i < section; i++) if (!completeSection(i)) { section = i; break; }
    save(false); render();
  });
  save(false); render();
  return {element: root, input, complete, focus};
}
