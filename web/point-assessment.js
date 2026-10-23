// Shared feedback for optional item-to-point assessment. Content labels stay authored.
import {t} from './i18n.js?v=44688b09fce31a75';

export function pointAssessment(result) {
  const points = result?.points;
  if (!Array.isArray(points) || !points.length) return null;
  const section = document.createElement('section');
  section.className = 'pointAssessment';
  const title = document.createElement('h3');
  title.textContent = t('assessment.title');
  const list = document.createElement('ul');
  for (const point of points) {
    const row = document.createElement('li');
    const outcome = document.createElement('strong');
    outcome.className = point.correct ? 'correctResult' : 'fieldError';
    outcome.textContent = t(point.correct ? 'assessment.correct' : 'assessment.again');
    row.append(outcome, document.createTextNode(` · ${point.text || point.id}`));
    const basis = document.createElement('p');
    basis.className = 'inputHint';
    basis.textContent = t(point.basis === 'items' ? 'assessment.items' : 'assessment.whole',
      {items: (point.item_ids || []).join(', ')});
    row.append(basis); list.append(row);
  }
  section.append(title, list);
  return section;
}
