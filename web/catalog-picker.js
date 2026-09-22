import {t, translateMessage} from './i18n.js?v=969d8540bd10e3fb';
import {subjectLabel, subjectPalette} from './subjects.js?v=969d8540bd10e3fb';

const node = (tag, cls, text) => {
  const item = document.createElement(tag); item.className = cls;
  if (text !== undefined) item.textContent = text;
  return item;
};
export function createCatalogPicker({button, panel, readSubjects, readCatalog, selectCourse, getSubject, onSelected}) {
  let sequence = 0, subjects = [], browsing = null, catalog = null, saving = false;
  const open = () => !panel.hidden;
  const current = ticket => ticket === sequence && open();
  function close(focus = true) {
    if (!open()) return;
    sequence++; panel.hidden = true; button.setAttribute('aria-expanded', 'false');
    if (focus) button.focus();
  }
  function render(error = null, loading = false) {
    button.disabled = saving;
    const subjectId = browsing || getSubject() || 'math';
    panel.dataset.subjectId = subjectId;
    for (const [name, value] of Object.entries(subjectPalette(subjectId))) panel.style.setProperty(name, value);
    panel.replaceChildren(); panel.setAttribute('aria-busy', String(loading || saving));
    const container = node('div', 'catalogPanelContent'), navigation = node('nav', 'catalogSubjects');
    navigation.setAttribute('aria-label', t('nav.subjects'));
    for (const subject of subjects) {
      const item = node('button', 'catalogSubject', subjectLabel(subject)); item.type = 'button';
      item.dataset.subjectId = subject.id; item.disabled = saving;
      item.setAttribute('aria-pressed', String(subject.id === browsing));
      item.addEventListener('click', () => browse(subject.id)); navigation.append(item);
    }
    const content = node('div', 'catalogCourseArea');
    const title = subjects.find(s => s.id === browsing);
    if (title) content.append(node('h2', '', subjectLabel(title)));
    if (loading) content.append(node('p', 'catalogStatus', t('catalog.loading')));
    else if (error) {
      content.append(node('p', 'catalogStatus', translateMessage(error.message)));
      const retry = node('button', 'catalogRetry', t('portal.retry.10')); retry.type = 'button';
      retry.addEventListener('click', () => subjects.length ? browse(browsing) : show()); content.append(retry);
    } else if (catalog) {
      if (!catalog.courses.length) content.append(node('p', 'catalogStatus', t('catalog.empty')));
      const grid = node('div', 'catalogCourseGrid');
      for (const course of catalog.courses) {
        const card = node('button', 'catalogCourse'); card.type = 'button'; card.dataset.courseId = course.id;
        card.setAttribute('aria-pressed', String(course.id === catalog.selected_course_id));
        card.disabled = saving || course.available === false;
        card.append(node('strong', '', course.title));
        if (course.description) card.append(node('span', '', course.description));
        if (course.available === false) card.append(node('span', 'catalogBadge', t('catalog.restricted')));
        else if (course.id === catalog.selected_course_id) card.append(node('span', 'catalogBadge', t('catalog.selected')));
        card.addEventListener('click', async () => {
          if (saving) return;
          saving = true; const ticket = sequence, subjectId = browsing;
          render();
          try {
            await selectCourse(course.id, subjectId);
            if (!current(ticket)) return;
            close(); onSelected(course.id, subjectId);
          } catch (error) {
            if (current(ticket)) { saving = false; render(error); }
          } finally { saving = false; button.disabled = false; }
        }); grid.append(card);
      }
      content.append(grid, node('p', 'catalogDraftHint', t('catalog.draftHint')));
    }
    container.append(navigation, content); panel.append(container);
  }
  async function browse(subjectId) {
    if (saving) return;
    browsing = subjectId; catalog = null; const ticket = ++sequence; render(null, true);
    try {
      const next = await readCatalog(subjectId);
      if (!current(ticket)) return;
      if (!Array.isArray(next.courses)) throw new Error(t('portal.the.course.catalog.is.temporarily.unavailable.17'));
      catalog = next; render();
    } catch (error) { if (current(ticket)) render(error); }
  }
  async function show() {
    panel.hidden = false; button.setAttribute('aria-expanded', 'true');
    const ticket = ++sequence; render(null, true);
    try {
      const result = await readSubjects();
      if (!current(ticket)) return;
      subjects = result.subjects;
      if (!Array.isArray(subjects) || !subjects.length) throw new Error(t('portal.the.course.catalog.is.temporarily.unavailable.17'));
      await browse(getSubject() || subjects[0].id);
    } catch (error) { if (current(ticket)) render(error); }
  }
  button.addEventListener('click', () => open() ? close() : show());
  document.addEventListener('click', event => {
    // A subject click renders new children before it bubbles to document.
    // Use the original event path so that detached target is still inside.
    const path = event.composedPath();
    if (open() && !path.includes(panel) && !path.includes(button)) close(false);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && open()) { event.preventDefault(); close(); } });
  window.addEventListener('hashchange', () => close(false));
  return {reset() { close(false); subjects = []; catalog = null; browsing = null; saving = false; button.disabled = false; sequence++; }};
}
