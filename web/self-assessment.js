import {t} from './i18n.js?v=82d46e20de08f529';
import {answerEmpty} from './question-input.js?v=82d46e20de08f529';
import {prepareLiteralMath} from './math-presentation.js?v=82d46e20de08f529';

const element = (tag, cls, text) => {
  const node = document.createElement(tag); node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

let mathFragment = 0;
export function prepareAnswerContent(root) {
  prepareLiteralMath(root);
  // Each mounted SVG fragment must resolve its own glyphs, even when the same
  // explanation appears elsewhere in history or in another hidden reader.
  const ids = new Map();
  const prefix = `answer-math-${++mathFragment}-`;
  for (const glyph of root.querySelectorAll('svg defs [id]')) {
    const old = glyph.id; glyph.id = prefix + old; ids.set(old, glyph.id);
  }
  for (const use of root.querySelectorAll('svg use')) {
    for (const attr of ['href', 'xlink:href']) {
      const value = use.getAttribute(attr);
      if (value?.startsWith('#') && ids.has(value.slice(1))) {
        const next = '#' + ids.get(value.slice(1));
        if (attr === 'xlink:href') use.setAttributeNS('http://www.w3.org/1999/xlink', attr, next);
        else use.setAttribute(attr, next);
      }
    }
  }
  return root;
}

export function submittedAnswer(question, answer, display = answer) {
  const spec = question.interaction || question.question?.interaction;
  let value; try { value = JSON.parse(answer); } catch { /* Keep literal free answers. */ }
  const selected = spec?.type === 'choice' ? new Set(spec.multiple ? value?.selected || [] : [value?.selected]) : new Set();
  const options = spec?.options?.filter(option => selected.has(option.id)) || [];
  if (!options.length) return element('pre', '', display || '');
  const body = element('div', 'submittedAnswerBody');
  for (const option of options) {
    const row = element('div', 'choiceAnswer');
    if (option.html) row.innerHTML = option.html;
    else row.textContent = option.text;
    body.append(row);
  }
  return prepareAnswerContent(body);
}

export function referenceAnswer(question) {
  const type = question.interaction?.type || question.question?.interaction?.type;
  if (type === 'text' || !type && !question.reference_answer_html) return null;
  if (!question.reference_answer_html && !question.reference_answer) return null;
  const box = element('section', 'referenceAnswer');
  box.append(element('h3', 'exampleExplanationHeader', t('training.referenceAnswer')));
  const body = element('div', 'courseContent referenceAnswerBody');
  if (question.reference_answer_html) body.innerHTML = question.reference_answer_html;
  else body.textContent = question.reference_answer;
  if (question.reference_math_css) { const style = element('style', ''); style.textContent = question.reference_math_css; box.append(style); }
  box.append(prepareAnswerContent(body)); return box;
}

export function ratingChoices({labels, selected, disabled = false, onRate}) {
  const ratings = element('div', 'trainingRatings');
  labels.forEach((label, index) => {
    const rating = index + 1, control = element('button', `trainingRating rating${rating}`);
    control.type = 'button'; control.disabled = disabled; control.dataset.rating = rating;
    control.setAttribute('aria-pressed', String(selected === rating));
    control.append(element('span', '', String(rating)), element('small', '', t(label)));
    control.addEventListener('click', async () => {
      for (const child of ratings.children) child.disabled = true;
      try { await onRate(rating); }
      finally { if (ratings.isConnected) for (const child of ratings.children) child.disabled = disabled; }
    });
    ratings.append(control);
  });
  return ratings;
}

// Shared controls; each reader keeps its original submission and grading policy.
export function selfAssessment(question, input, {disabled = false, reveal, rate, explanation} = {}) {
  if (!question.can_self_rate) return null;
  const box = element('div', 'selfAssessment');
  if (!question.revealed) {
    const show = element('button', 'secondaryButton showAnswer', t('training.show'));
    show.type = 'button'; show.disabled = disabled;
    const refresh = () => { show.hidden = !answerEmpty(input); };
    input?.addEventListener('input', refresh); refresh();
    show.addEventListener('click', () => { if (!show.disabled) void reveal(); });
    box.append(show); return box;
  }
  const reference = referenceAnswer(question); if (reference) box.append(reference);
  if (explanation) box.append(element('h3', 'exampleExplanationHeader', t('Explanation · 解析')), prepareAnswerContent(explanation));
  const labels = ['training.again', 'training.hard', 'training.good', 'training.easy'];
  const ratings = ratingChoices({labels, disabled, selected: question.self_rating, onRate: rate});
  box.append(ratings); return box;
}
