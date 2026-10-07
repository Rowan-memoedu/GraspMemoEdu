import {t, learningTitle} from './i18n.js?v=469fc84de6dd9665';
import {questionInput, answerReady, choiceTypeField} from './question-input.js?v=469fc84de6dd9665';
import {referenceAnswer, prepareAnswerContent, submittedAnswer} from './self-assessment.js?v=469fc84de6dd9665';
import {enhanceTopicContent} from './topic-content.js?v=469fc84de6dd9665';

const node = (tag, cls = '', text) => {
  const item = document.createElement(tag); item.className = cls;
  if (text !== undefined) item.textContent = text;
  return item;
};

const action = (label, cls, onClick, disabled = false) => {
  const item = node('button', cls, label); item.type = 'button'; item.disabled = disabled;
  item.addEventListener('click', onClick); return item;
};

export function readerStepTitle(step, {formatVersion = 2} = {}) {
  if (step?.kind === 'introduction') return formatVersion === 2
    ? (step.title || 'Introduction').replace(/^(?:Introduction|引论)(?=\s*[:：]|$)/i, t('Introduction')) : t('Introduction');
  if (step?.kind === 'completion') return t('学习结果');
  return learningTitle(step?.title || '');
}

export function readerContent(html, references) {
  const body = node('div', 'courseContent'); body.innerHTML = html || '';
  enhanceTopicContent(body, references); return body;
}

// One presentation path for prose, stems, submitted answers and explanations.
// Controllers supply state and callbacks; this module never submits or schedules.
export function renderReaderStep(target, step, {titleId = 'stepTitle', title = readerStepTitle(step), counter,
  references, decorate = body => body, submittedId = 'submittedInteraction',
  showSubmittedInteraction = !step.actions?.includes('submit'), submittedValue = step.answer || '',
  showAnswer = step.answer !== null && step.answer !== undefined && step.answer !== '',
  feedback = step.feedback, feedbackClass = '', referenceSource = step,
  showExplanation = !(step.revealed && step.actions?.includes('submit')), notice} = {}) {
  const heading = node('div', 'stepHeader'), titleNode = node('h2', 'stepTitle', title);
  titleNode.id = titleId; titleNode.tabIndex = -1; heading.append(titleNode);
  if (counter) heading.append(node('span', 'stepCounter', counter));
  const stem = decorate(readerContent(step.html, references), 'question');
  target.setAttribute('aria-labelledby', titleId); target.replaceChildren(heading, stem);
  const typeField = choiceTypeField(step.interaction); if (typeField) heading.after(typeField);
  if (notice) heading.after(node('p', 'introductionUpdateNotice', notice));
  if (showSubmittedInteraction && step.interaction && step.interaction.type !== 'text')
    target.append(questionInput(step.interaction, {id: submittedId, stem, value: submittedValue, disabled: true}).element);
  if (showAnswer) {
    const answer = node('details', 'submittedAnswer');
    answer.append(node('summary', '', t('查看已提交答案')), submittedAnswer(step, step.answer, step.answer_display ?? step.answer)); target.append(answer);
  }
  if (feedback) {
    const verdict = node('div', `feedback ${feedbackClass} ${feedback.correct ? 'correct' : 'incorrect'}`.replace(/\s+/g, ' '));
    verdict.setAttribute('role', 'status');
    const body = node('div');
    body.append(node('span', 'feedbackTitle', feedback.title || t(feedback.correct ? '回答正确' : '本题回答有误')));
    for (const reason of [feedback.reason, ...(feedback.details || [])].filter(Boolean)) body.append(node('div', 'feedbackReason', reason));
    verdict.append(node('span', 'feedbackIcon', feedback.correct ? '✓' : '!'), body); target.append(verdict);
  }
  if (showExplanation) {
    const reference = referenceAnswer(referenceSource); if (reference) target.append(reference);
    if (step.explanation_html) target.append(node('h3', 'exampleExplanationHeader', t('Explanation · 解析')),
      prepareAnswerContent(decorate(readerContent(step.explanation_html, references), 'explanation')));
  }
  return {heading, title: titleNode, stem};
}

export function readerHistoryGroup({title, status = '', className = '', items = [], contentId} = {}) {
  const group = node('section', 'historyGroup'), heading = node('div', 'historyGroupHeader');
  if (contentId) group.dataset.contentId = contentId;
  heading.append(node('span', 'moduleName', title), node('span', `historyStatus ${className}`.trim(), status)); group.append(heading);
  const box = node('div', 'historyItems');
  for (const item of items) {
    const control = action(item.label, `historyItem ${item.className || ''}`.trim(), item.onSelect, item.disabled);
    Object.assign(control.dataset, item.dataset || {});
    if (item.selected) control.setAttribute('aria-current', 'step');
    for (const mark of item.marks || []) control.append(node('span', 'historyMark', mark));
    box.append(control);
  }
  if (items.length) group.append(box);
  return {group, items: box};
}

export function readerProgress(refs, {label, status, percent, segments, reading = false, hidden = false}) {
  refs.progressCaption.hidden = refs.lessonProgress.hidden = hidden;
  refs.progressCaption.replaceChildren(node('span', '', label), node('span', '', status));
  refs.lessonProgress.setAttribute('aria-valuenow', String(percent));
  refs.lessonProgress.setAttribute('aria-valuetext', label);
  if (reading) {
    const fill = node('span', 'introProgressFill'); fill.style.width = `${percent}%`; refs.lessonProgress.replaceChildren(fill);
  } else refs.lessonProgress.replaceChildren(...(segments || []).map(segment => {
    const item = node('span', `progressSegment ${segment.status}`); item.title = segment.title || ''; return item;
  }));
}

export function readerNavigation(target, {previous, next, onSelect, canNavigate = true,
  titleOf = readerStepTitle, idPrefix = '', primary, nextAsPrimary = false, extra = [], disabled = false} = {}) {
  const footer = node('div', 'stepNavigation'), area = node('div', 'continueRow');
  const pageControl = (page, forward) => {
    const label = t(forward ? '下一页' : '上一页');
    const control = action(label, forward && nextAsPrimary ? 'primaryButton' : 'secondaryButton pageButton', () => onSelect(page), disabled);
    control.id = idPrefix + (forward ? 'nextPageButton' : 'previousPageButton'); control.dataset.navAvailable = 'true';
    control.title = t('reader.pageControlTitle', {label, title: titleOf(page)}); return control;
  };
  const navigation = controls => {
    const nav = node('nav', 'historyPagination'); nav.setAttribute('aria-label', t('已学内容翻页')); nav.append(...controls); return nav;
  };
  if (previous && canNavigate) footer.append(navigation([pageControl(previous, false)]));
  footer.append(...extra);
  if (primary) area.append(primary);
  else if (next && canNavigate) area.append(nextAsPrimary ? pageControl(next, true) : navigation([pageControl(next, true)]));
  if (area.childElementCount) footer.append(area);
  if (footer.childElementCount) target.append(footer);
  return footer;
}

export function readerDrawerState(frame, refs, opened, {narrow = matchMedia('(max-width: 1000px)').matches, focus = false} = {}) {
  frame.classList.toggle('historyCollapsed', !opened);
  refs.historyButton.setAttribute('aria-expanded', String(opened));
  refs.historyButton.setAttribute('aria-label', t(opened ? '收起学习记录' : '展开学习记录'));
  refs.historyDrawerToggle.setAttribute('aria-expanded', String(opened));
  refs.historyPanel.hidden = !opened; refs.historyScrim.hidden = !opened || !narrow;
  document.body.classList.toggle('historyDrawerOpen', !frame.hidden && opened && narrow);
  if (focus && narrow) (opened ? refs.historyButton : refs.historyDrawerToggle).focus({preventScroll: true});
}

// Stateless presentation shared by Lesson and single-question training.
// Callers retain their own submission, permissions and scheduling logic.
export function answerEditor(step, stem, value, {formId = 'answerForm', inputId = 'answerInput', submitId = 'submitButton', canSelfRate = false} = {}) {
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
  if (canSelfRate) hint.append(document.createElement('br'), document.createTextNode(t('training.showHint')));
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
  frame.querySelector('#pauseControl')?.remove();
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
    opened = value; readerDrawerState(frame, refs, opened, {narrow: narrow.matches, focus});
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
