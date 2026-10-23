// Hallmark · component: answer feedback · theme: existing learning UI.
// Read-only states: submitted item results and point results; absent results stay absent.
// Shared feedback for optional item-to-point assessment. Content labels stay authored.
import {t} from './i18n.js?v=2de30673708f2e1a';

// Resolve evidence keys against the same public metadata used by the answer inputs.
// An unknown key must never become a learner-facing label or an invented result.
export function assessmentItemLabels(interaction) {
  const labels = new Map(), kind = interaction?.type;
  const rows = interaction?.[{composite: 'controls', fill_blank: 'blanks',
    true_false: 'statements', matching: 'left'}[kind]];
  for (const [index, row] of (Array.isArray(rows) ? rows : []).entries()) {
    let label = kind === 'matching' ? row.text : row.label;
    label = typeof label === 'string' ? label.trim() : '';
    if (/^\d+(?:[.-]\d+)*$/.test(label)) label = t('assessment.itemNumber', {number: label});
    if (!label && kind !== 'matching') label = t(kind === 'fill_blank' ? 'question.blank' : 'assessment.itemNumber', {number: index + 1});
    if (typeof row.id === 'string' && label) labels.set(row.id, label);
  }
  if (kind === 'text' || kind === 'choice') labels.set('answer', t('atomic.answer'));
  return labels;
}

export function pointAssessment(result, interaction) {
  const points = Array.isArray(result?.points) ? result.points : [];
  const labels = assessmentItemLabels(interaction);
  const results = new Map((Array.isArray(result?.items) ? result.items : [])
    .filter(item => typeof item?.correct === 'boolean').map(item => [item.id, item.correct]));
  const items = [...labels].filter(([id]) => results.has(id));
  if (!points.length && !items.length) return null;
  const section = document.createElement('section');
  section.className = 'pointAssessment';
  if (items.length) {
    const title = document.createElement('h3'); title.textContent = t('assessment.itemTitle');
    const list = document.createElement('ul'); list.className = 'itemAssessmentList';
    for (const [id, label] of items) {
      const row = document.createElement('li');
      const name = document.createElement('span'); name.className = 'assessmentItemLabel'; name.textContent = label;
      const outcome = document.createElement('strong');
      outcome.className = results.get(id) ? 'correctResult' : 'fieldError';
      outcome.textContent = t(results.get(id) ? 'assessment.correct' : 'assessment.incorrect');
      row.append(name, outcome); list.append(row);
    }
    section.append(title, list);
  }
  if (!points.length) return section;
  const title = document.createElement('h3');
  title.textContent = t('assessment.title');
  const list = document.createElement('ul'); list.className = 'pointAssessmentList';
  for (const [index, point] of points.entries()) {
    const row = document.createElement('li');
    const outcome = document.createElement('strong');
    outcome.className = point.correct ? 'correctResult' : 'fieldError';
    outcome.textContent = t(point.correct ? 'assessment.correct' : 'assessment.again');
    row.append(outcome, document.createTextNode(` · ${point.text || t('assessment.pointNumber', {number: index + 1})}`));
    const basis = document.createElement('p');
    basis.className = 'inputHint';
    const keys = new Set(point.item_ids || []), names = [...labels].filter(([id]) => keys.has(id)).map(([, label]) => label);
    const key = point.basis !== 'items' ? 'assessment.whole'
      : names.length && names.length === keys.size ? 'assessment.items' : 'assessment.itemBasis';
    basis.textContent = t(key, {items: names.join(', ')});
    row.append(basis); list.append(row);
  }
  section.append(title, list);
  return section;
}
