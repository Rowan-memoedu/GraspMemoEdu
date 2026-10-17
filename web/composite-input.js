// Hallmark · component: composite-answer · genre: editorial · theme: existing learning UI
// States: default, hover, focus, active, disabled, loading (parent), error, success.
const node = (tag, cls, text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};

export function compositeInput(spec, {id, value = '', disabled = false} = {}) {
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
  function render() {
    root.querySelector('.compositeBody')?.remove();
    const body = node('div', 'compositeBody');
    const indexes = disabled ? spec.sections.map((_, i) => i) : [section];
    for (const index of indexes) {
      const part = spec.sections[index], panel = node('section', 'compositeSection');
      if (spec.sections.length > 1) panel.append(node('p', 'stepCounter', `${index + 1} / ${spec.sections.length}`));
      const text = node('div', 'courseContent'); text.innerHTML = part.html; panel.append(text);
      for (const control of spec.controls.filter(c => part.control_ids.includes(c.id))) {
        const wrap = node('label', 'compositeControl'), label = node('span', 'compositeLabel', control.label || control.id);
        const field = node(control.type === 'select' ? 'select' : 'input', 'compositeField');
        field.id = `${id}-${control.id}`; field.disabled = disabled; field.dataset.controlId = control.id;
        field.setAttribute('aria-label', control.label || control.id);
        if (control.type === 'select') {
          field.append(new Option('请选择', ''));
          for (const option of control.options) field.append(new Option(option.text, option.id));
        } else { field.type = 'text'; field.autocomplete = 'off'; if (control.input_type === 'number') field.inputMode = 'decimal'; }
        field.value = values[control.id]; wrap.append(label, field);
        field.addEventListener('input', () => {
          values[control.id] = field.value; save();
          const next = body.querySelector('.compositeNext'); if (next) next.disabled = !completeSection(section);
        });
        const marker = [...text.querySelectorAll('[data-control]')].find(el => el.dataset.control === control.id);
        if (marker) marker.replaceWith(wrap); else panel.append(wrap);
        if (control.type === 'select' && control.options.some(o => o.html)) {
          const options = node('details', 'compositeOptions');
          options.append(node('summary', '', `${control.label || control.id} · 选项`));
          const list = node('ol', 'courseContent');
          for (const option of control.options) {
            const item = node('li', ''); item.innerHTML = option.html || '';
            if (!option.html) item.textContent = option.text;
            list.append(item);
          }
          options.append(list); panel.append(options);
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
      for (const field of root.querySelectorAll('select,input')) field.disabled = value;
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
