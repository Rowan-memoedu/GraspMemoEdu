import {t} from './i18n.js?v=a4ab0a0627eabccf';
import {questionInput, answerReady} from './question-input.js?v=a4ab0a0627eabccf';

const node = (tag, cls = '', text) => {
  const item = document.createElement(tag); item.className = cls;
  if (text !== undefined) item.textContent = text;
  return item;
};

// Stateless presentation shared by Lesson and single-question training.
// Callers retain their own submission, permissions and scheduling logic.
export function answerEditor(step, stem, value, {formId = 'answerForm', inputId = 'answerInput', submitId = 'submitButton'} = {}) {
  const form = node('form', 'learningActions answerForm'); form.id = formId;
  const structured = step.interaction && step.interaction.type !== 'text';
  const control = structured ? questionInput(step.interaction, {id: inputId, stem, value, formId}) : null;
  const label = node(structured ? 'p' : 'label', '', t('你的答案')); label.htmlFor = inputId;
  const input = control?.input || node('textarea', 'answerInput');
  input.id = inputId; input.name = 'answer'; input.rows = 3; input.maxLength = 2000;
  input.spellcheck = false; input.autocomplete = 'off'; input.value = value;
  const error = node('p', 'inputError', t(step.interaction?.type === 'choice' ? 'question.choiceHint' : structured ? 'question.completeHint' : '请先填写答案。'));
  error.id = inputId === 'answerInput' ? 'answerError' : `${inputId}Error`; error.hidden = true;
  const bottom = node('div', 'answerBottom');
  const hint = node('p', 'inputHint', t(step.interaction?.type === 'choice' ? 'question.choiceHint' : step.interaction?.grading === 'exact' ? 'question.exactHint'
    : step.interaction?.grading === 'semantic' ? 'question.semanticHint'
    : structured && step.interaction.type !== 'fill_blank' ? 'question.completeHint'
    : '只要描述清楚正确答案的形式即可，表达方式不限，夹杂口语也没关系。Ctrl + Enter 提交。'));
  hint.id = inputId === 'answerInput' ? 'answerHint' : `${inputId}Hint`;
  input.setAttribute('aria-describedby', `${hint.id} ${error.id}`);
  const submit = node('button', 'primaryButton', t('Submit'));
  submit.id = submitId; submit.type = 'submit'; submit.setAttribute('form', formId); submit.disabled = !answerReady(input);
  bottom.append(hint, submit); form.append(label, control?.element || input, error);
  return {form, input, submit, bottom};
}

// Clone the original reader markup, with isolated IDs and event handlers.
// This keeps the sidebar, lesson, progress, card and mobile drawer geometry identical.
export function readerFrame(template, prefix) {
  const frame = template.cloneNode(true), refs = {};
  for (const item of [frame, ...frame.querySelectorAll('[id]')]) {
    if (!item.id) continue;
    refs[item.id] = item; item.id = prefix + item.id;
  }
  for (const item of frame.querySelectorAll('*')) {
    for (const attr of ['aria-controls', 'aria-labelledby', 'aria-describedby', 'for']) {
      if (item.hasAttribute(attr)) item.setAttribute(attr, item.getAttribute(attr).split(' ').map(id => refs[id] ? prefix + id : id).join(' '));
    }
  }
  frame.hidden = false;
  const narrow = matchMedia('(max-width: 1000px)'); let opened = true;
  function toggle(value, focus = false) {
    opened = value; frame.classList.toggle('historyCollapsed', !opened);
    refs.historyButton.setAttribute('aria-expanded', String(opened));
    refs.historyButton.setAttribute('aria-label', t(opened ? '收起学习记录' : '展开学习记录'));
    refs.historyDrawerToggle.setAttribute('aria-expanded', String(opened));
    refs.historyPanel.hidden = !opened; refs.historyScrim.hidden = !opened || !narrow.matches;
    document.body.classList.toggle('historyDrawerOpen', opened && narrow.matches);
    if (focus && narrow.matches) (opened ? refs.historyButton : refs.historyDrawerToggle).focus({preventScroll: true});
  }
  refs.historyButton.addEventListener('click', () => toggle(!opened, true));
  refs.historyDrawerToggle.addEventListener('click', () => toggle(true, true));
  refs.historyScrim.addEventListener('click', () => toggle(false, true));
  const resize = () => toggle(opened), escape = event => { if (event.key === 'Escape' && narrow.matches && !document.querySelector('dialog[open]')) toggle(false, true); };
  const skipLink = document.getElementById('skipContent');
  const skip = event => { event.preventDefault(); event.stopImmediatePropagation(); refs.lessonContent.focus(); };
  skipLink?.addEventListener('click', skip, true);
  narrow.addEventListener('change', resize); document.addEventListener('keydown', escape); toggle(opened);
  return {frame, refs, closeOnMobile: () => { if (narrow.matches) toggle(false); },
    destroy: () => { narrow.removeEventListener('change', resize); document.removeEventListener('keydown', escape); skipLink?.removeEventListener('click', skip, true); frame.remove(); document.body.classList.remove('historyDrawerOpen', 'topicPage'); }};
}
