import fs from 'node:fs';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import {transform, Features} from 'lightningcss';
import {loadCalloutConfig,validateCalloutConfig} from './callout-config.mjs';

const upstreamDirectory = new URL('./upstream/callout/', import.meta.url);
const read = name => fs.readFileSync(new URL(name, upstreamDirectory), 'utf8').replace(/\r\n/g, '\n');
const selector = '.reading-outline .document-annotations.callout';
const quoteSelector = 'div[style*="border-inline-start"]';

export function composeCalloutStyles(config=loadCalloutConfig()) {
  validateCalloutConfig(config);
  const {layout:settings}=config;
  const palette=colors=>Object.entries(colors).filter(([key])=>['background','accent','text','link'].includes(key))
    .map(([key,value])=>'  --document-callout-'+key+': '+value+';').join('\n');
  const manifest = JSON.parse(read('manifest.json'));
  const originals = new Map(manifest.files.map(file => {
    const source = read(file.name);
    assert.equal(crypto.createHash('sha256').update(source).digest('hex'), file.sha256,
      `Pinned callout source differs: ${file.name}`);
    return [file.name, source];
  }));

  // Reuse the al-folio frame and colored component with scoped theme tokens.
  const typography = originals.get('al-folio-typography.scss');
  const frameStart = typography.indexOf('\nblockquote {');
  const frameEnd = typography.indexOf('\n  &.block-warning {', frameStart);
  assert.ok(frameStart >= 0 && frameEnd > frameStart, 'Pinned al-folio blockquote boundaries changed');
  const frame = (typography.slice(frameStart, frameEnd) + '\n}')
    .replace('blockquote {', selector + ' {')
    .replace('&.block-tip', '&[data-callout="note"]')
    .replace('padding: 1em;', 'padding: '+settings.paddingEm+'em;')
    .replace('margin: 1.5em 0;', 'margin: '+settings.marginBlockEm+'em 0;')
    .replace('font-size: 1.2rem;', 'font-size: inherit;')
    .replaceAll('--global-bg-color', '--document-callout-background')
    .replaceAll('--global-theme-color', '--document-callout-accent')
    .replaceAll('--global-tip-block-bg', '--document-callout-background')
    .replaceAll('--global-tip-block-text', '--document-callout-text')
    .replaceAll('--global-tip-block-title', '--document-callout-link')
    .replaceAll('--global-tip-block', '--document-callout-accent');

  // Retain Tufted's quote layout and paragraph rules. The width and font mapping
  // bind that source to the requested five-percent side gutters and body type.
  const layout = originals.get('tufted-quote-layout.css')
    .replaceAll(quoteSelector, selector)
    .replace('width: 55% !important;', 'width: '+settings.focusedWidthPercent+'%;')
    .replace('font-size: 1.4rem;', 'font-size: inherit;')
    .replace('line-height: 2.2rem;', 'line-height: inherit;')
    .replace('margin: 1.5em 0;', 'margin: '+settings.marginBlockEm+'em auto;')
    .replace('padding-right: 0;', 'padding-right: '+settings.paddingEm+'em;');
  const text = originals.get('tufted-quote-text.css')
    .replaceAll(quoteSelector, selector)
    .replace('font-size: 1.4rem;', 'font-size: inherit;')
    .replace('> figure.math-block', '.math-block, ' + selector + ' math[display="block"]');
  const mobile = originals.get('tufted-quote-mobile.css')
    .replaceAll(quoteSelector, selector)
    .replace('width: 100% !important;', 'width: '+settings.focusedWidthPercent+'%;');

  // Theme and accessibility binding. Explicit resets neutralize the engine's
  // still-loaded generic .callout grid, border and overflow defaults.
  const compatibility = `${selector} {
${palette(config.annotations)}
  --document-callout-margin: ${settings.marginBlockEm}em;
  --document-callout-padding: ${settings.paddingEm}em;
  color: var(--document-callout-text);
  min-width: 0;
  max-width: 100%;
  max-height: ${settings.maxHeightPx}px;
  overflow-x: auto;
  overflow-y: auto;
  overscroll-behavior-y: contain;
  scrollbar-gutter: stable both-edges;
  display: block;
  border-top: 0;
  border-right: 0;
  border-bottom: 0;
  border-radius: 0;
  box-shadow: none;
}
${selector} > .callout-content {
  display: block;
  min-width: 0;
  max-width: 100%;
  overflow: visible;
  transition: none;
}
${selector}:focus-visible {
  outline: 2px solid var(--document-callout-accent);
  outline-offset: 3px;
}
`;
  const css = transform({
    filename: 'al-folio-tufted-document-callout.css',
    code: Buffer.from([frame, layout, text, compatibility,
      '@media (max-width: 760px) {\n' + mobile + '\n}'].join('\n')),
    include: Features.Nesting,
  }).code.toString();
  const notices = manifest.components.map(component =>
    '/*! ' + component.name + ', commit ' + component.commit + '.\n'
    + originals.get(component.license) + '*/').join('\n');
  // Reuse the same compiled upstream component for source-tagged callouts.
  // Only the selector and requested palette differ from the annotation frame.
  const inline = config.tags.map(tag=>css
    .replaceAll('.document-annotations.callout', '.inline-document-callout.callout[data-callout="'+tag.key+'"]')
    .replaceAll('data-callout=note', 'data-callout='+tag.key)
    .replaceAll('data-callout="note"', 'data-callout="'+tag.key+'"')
    + '\n.reading-outline .inline-document-callout.callout[data-callout="'+tag.key+'"] {\n'+palette(tag)+'\n}')
    .join('\n');
  const draft = css.replaceAll('.document-annotations.callout', '.document-draft.callout')
    .replaceAll('data-callout=note', 'data-callout=danger')
    .replaceAll('data-callout="note"', 'data-callout="danger"');
  const draftPalette = `.reading-outline .document-draft.callout {
${palette(config.draft)}
}`;
  // A callout viewed inside its parent's outline uses five-sixths of its
  // focused width. Keep the upstream frame and the focused reading dimensions.
  const expandedWidth = `.reading-outline .outline-node:not(.document-current) > .document-body > .document-annotations.callout,
.reading-outline .outline-node:not(.document-current) > .document-body > .document-draft.callout,
.reading-outline .outline-node.inline-document-callout.callout:not(.is-focused):not(.document-current) {
  width: ${settings.expandedWidthPercent}%;
  margin-left: auto;
  margin-right: auto;
}`;
  const leadingAnnotation = `.reading-outline .outline-node[data-leading-annotation="true"].inline-document-callout.callout[data-callout="inline"],
.reading-outline .outline-node[data-leading-annotation="true"] > .inline-document-callout.callout[data-callout="inline"] {
${palette(config.annotations)}
}`;
  return notices + '\n' + css + '\n' + inline + '\n' + draft + '\n' + draftPalette + '\n' + expandedWidth + '\n' + leadingAnnotation;
}
