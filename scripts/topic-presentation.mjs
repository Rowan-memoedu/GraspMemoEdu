// Shared presentation boundary for the mechanical Topic v2 importer.
// The generated helper bundles the pinned Blog prepareMermaid implementation.
import {prepareMermaid} from './generated/prepare-mermaid.mjs';

export const calloutLabels = Object.freeze({hint: 'hint', aside: '题外话', requirement: '要求', source: '题源'});
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));

export function renderCallout({type, html}) {
  if (!Object.hasOwn(calloutLabels, type) || typeof html !== 'string') throw new Error('Invalid Topic Callout');
  return `<aside class="inline-document-callout callout" data-callout="${type}" tabindex="0" aria-label="${calloutLabels[type]}"><div class="callout-label"><strong>${calloutLabels[type]}</strong></div><div class="callout-content">${html}</div></aside>`;
}

export function renderReference({targetRemId, label}) {
  if (typeof targetRemId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(targetRemId) || typeof label !== 'string' || !label.trim()) throw new Error('Invalid Topic reference');
  return `<a class="internal topicReference" data-target-rem-id="${escape(targetRemId)}" aria-disabled="true">${escape(label)}</a>`;
}

export function prepareMermaidHtml(html) {
  if (typeof html !== 'string') throw new Error('Topic HTML must be text');
  return prepareMermaid(html);
}
