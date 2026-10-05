import {t} from './i18n.js?v=7751db6e04572b2f';
import {answerEmpty} from './question-input.js?v=7751db6e04572b2f';

const element = (tag, cls, text) => {
  const node = document.createElement(tag); node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

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
  box.append(element('h3', 'exampleExplanationHeader', t('training.referenceAnswer')),
    element('pre', 'referenceAnswer', question.reference_answer || ''));
  if (explanation) box.append(element('h3', 'exampleExplanationHeader', t('Explanation · 解析')), explanation);
  const ratings = element('div', 'trainingRatings');
  const labels = ['training.again', 'training.hard', 'training.good', 'training.easy'];
  labels.forEach((label, index) => {
    const rating = index + 1, control = element('button', `trainingRating rating${rating}`);
    control.type = 'button'; control.disabled = disabled;
    control.dataset.rating = rating;
    control.setAttribute('aria-pressed', String(question.self_rating === rating));
    control.append(element('span', '', String(rating)), element('small', '', t(label)));
    control.addEventListener('click', async () => {
      for (const child of ratings.children) child.disabled = true;
      try { await rate(rating); } finally { if (ratings.isConnected) for (const child of ratings.children) child.disabled = disabled; }
    });
    ratings.append(control);
  });
  box.append(ratings); return box;
}
