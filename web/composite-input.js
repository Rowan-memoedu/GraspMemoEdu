// Hallmark · component: composite-answer · genre: editorial · theme: existing learning UI
// States: default, hover, focus, active, disabled, loading (parent), error, success.
const node = (tag, cls, text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};

export function compositeInput(spec, {id, value = '', disabled = false, formId} = {}) {
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
  root.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) {
      event.preventDefault(); document.getElementById(formId)?.requestSubmit();
    }
  });
  function render() {
    root.querySelector('.compositeBody')?.remove();
    const body = node('div', 'compositeBody');
    const indexes = disabled ? spec.sections.map((_, i) => i) : [section];
    for (const index of indexes) {
      const part = spec.sections[index], panel = node('section', 'compositeSection');
      if (spec.sections.length > 1) panel.append(node('p', 'stepCounter', `${index + 1} / ${spec.sections.length}`));
      const text = node('div', 'courseContent'); text.innerHTML = part.html; panel.append(text);
      for (const control of spec.controls.filter(c => part.control_ids.includes(c.id))) {
        const marker = [...text.querySelectorAll('[data-control]')].find(el => el.dataset.control === control.id);
        const changed = value => {
          if (disabled) return;
          values[control.id] = value; save();
          const next = body.querySelector('.compositeNext'); if (next) next.disabled = !completeSection(section);
        };
        if (control.type === 'select') {
          const group = node('fieldset', 'compositeControl compositeChoiceControl');
          group.id = `${id}-${control.id}`; group.dataset.controlId = control.id; group.disabled = disabled;
          const label = node('legend', 'compositeLabel', control.label || control.id); label.id = `${group.id}-label`;
          const list = node('div', 'choiceList'); list.setAttribute('role', 'radiogroup'); list.setAttribute('aria-labelledby', label.id);
          control.options.forEach((option, optionIndex) => {
            const choice = node('label', 'choiceOption'), radio = node('input', '');
            radio.type = 'radio'; radio.name = group.id; radio.value = option.id;
            radio.disabled = disabled; radio.checked = values[control.id] === option.id;
            if (formId) radio.setAttribute('form', formId);
            const content = node('span', 'choiceBody');
            // Rich option HTML uses the same compiled, sanitized source as the question.
            if (option.html) content.innerHTML = option.html; else content.textContent = option.text;
            radio.addEventListener('change', () => { if (radio.checked) changed(option.id); });
            choice.append(radio, node('span', 'choiceLetter', String.fromCharCode(65 + optionIndex)), content); list.append(choice);
          });
          group.append(label, list); panel.append(group);
          if (marker) {
            // Keep the article intact; a full list of sentence options belongs after it.
            const anchor = node('button', 'compositeAnchor', `[${spec.controls.indexOf(control) + 1}]`); anchor.type = 'button';
            anchor.setAttribute('aria-label', control.label || control.id); anchor.setAttribute('aria-controls', group.id);
            anchor.addEventListener('click', () => {
              group.scrollIntoView({block: 'center'});
              (group.querySelector('input:checked') || group.querySelector('input'))?.focus({preventScroll: true});
            });
            marker.replaceWith(anchor);
          }
        } else {
          const wrap = node('label', `compositeControl${marker ? '' : ' compositeTextControl'}`);
          const label = node('span', 'compositeLabel', control.label || control.id), field = node('input', 'compositeField');
          field.id = `${id}-${control.id}`; field.disabled = disabled; field.dataset.controlId = control.id;
          field.setAttribute('aria-label', control.label || control.id); if (formId) field.setAttribute('form', formId);
          field.type = 'text'; field.autocomplete = 'off'; if (control.input_type === 'number') field.inputMode = 'decimal';
          field.value = values[control.id]; wrap.append(label, field);
          field.addEventListener('input', () => changed(field.value));
          if (marker) marker.replaceWith(wrap); else panel.append(wrap);
        }
      }
      if (!part.complete) panel.append(node('p', 'featureNotice', '原材料缺少本段题面，无法继续作答。'));
      body.append(panel);
    }
    if (!disabled && section < spec.sections.length - 1) {
      const next = node('button', 'primaryButton compositeNext', '继续'); next.type = 'button';
      next.disabled = !completeSection(section);
      next.addEventListener('click', () => { if (!completeSection(section)) return; section++; save(); render(); root.querySelector('select,input,button')?.focus(); });
      body.append(next);
    }
    root.append(body);
  }
  const complete = () => !disabled && spec.grading !== 'unavailable' && section === spec.sections.length - 1
    && spec.sections.every((_, i) => completeSection(i)) && input.value.length <= 16384;
  input.questionControl = {complete, empty: () => Object.values(values).every(v => !v.trim()),
    focus: () => root.querySelector('select,input')?.focus(),
    setDisabled: value => {
      disabled = value; input.disabled = value;
      for (const field of root.querySelectorAll('fieldset,input')) field.disabled = value;
      const next = root.querySelector('.compositeNext'); if (next) next.disabled = value || !completeSection(section);
    }};
  input.addEventListener('input', () => {
    if (emitting) return;
    try { draft = JSON.parse(input.value); } catch { return; }
    values = Object.fromEntries(spec.controls.map(c => [c.id, typeof draft?.values?.[c.id] === 'string' ? draft.values[c.id] : '']));
    section = Math.max(0, Math.min(spec.sections.length - 1, Number.isInteger(draft?.section) ? draft.section : 0));
    for (let i = 0; i < section; i++) if (!completeSection(i)) { section = i; break; }
    save(false); render();
  });
  save(false); render();
  return {element: root, input, complete, focus: () => root.querySelector('select,input')?.focus()};
}
