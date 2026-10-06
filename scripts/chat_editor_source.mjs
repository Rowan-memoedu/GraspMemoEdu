// This module is bundled with pinned open-source dependencies for offline use.
import {EditorView, keymap} from '@codemirror/view';
import {EditorState, Compartment} from '@codemirror/state';
import {defaultKeymap, history, historyKeymap, indentWithTab, temporarilySetTabFocusMode} from '@codemirror/commands';
import {markdown} from '@codemirror/lang-markdown';
import {indentUnit, syntaxHighlighting, defaultHighlightStyle} from '@codemirror/language';
import {Marked} from 'marked';
import DOMPurify from 'dompurify';
import katex from 'katex';

const escape = text => text.replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const mathHtml = (text, displayMode) => katex.renderToString(text, {
  displayMode, throwOnError: false, trust: false, maxExpand: 300, maxSize: 10, strict: 'ignore', macros: {}});
const md = new Marked({gfm: true, breaks: true, renderer: {html: token => escape(token.text)}});
md.use({extensions: [
  {name: 'chatDisplayMath', level: 'block', start: src => src.search(/\$\$|\\\[/),
    tokenizer(src) {
      const m = /^(?:\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\])(?:\n|$)/.exec(src);
      if (m) return {type: 'chatDisplayMath', raw: m[0], text: m[1] || m[2]};
    }, renderer: token => mathHtml(token.text, true)},
  {name: 'chatInlineMath', level: 'inline', start: src => src.search(/\$|\\\(/),
    tokenizer(src) {
      const m = /^(?:\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\])/.exec(src);
      if (m) return {type: 'chatInlineMath', raw: m[0], text: m[1] || m[2] || m[3] || m[4], display: !!(m[1] || m[4])};
    }, renderer: token => mathHtml(token.text, token.display)}
]});

export function renderChatMarkdown(element, text) {
  element.innerHTML = DOMPurify.sanitize(md.parse(text || ''), {
    FORBID_TAGS: ['img', 'video', 'audio', 'iframe', 'form', 'input', 'button', 'style'],
    FORBID_ATTR: ['id', 'name'], ALLOW_DATA_ATTR: false});
  for (const link of element.querySelectorAll('a')) {
    const href = link.getAttribute('href') || '';
    if (!/^https?:\/\//i.test(href)) link.removeAttribute('href');
    else { link.target = '_blank'; link.rel = 'noopener noreferrer'; }
  }
  return element;
}

export function attachChatEditor(input, labels) {
  input.hidden = true; input.required = false;
  const host = document.createElement('div'); host.className = 'chatEditor';
  const toolbar = document.createElement('div'); toolbar.className = 'chatEditorToolbar';
  toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', labels.editor);
  const mount = document.createElement('div'), preview = document.createElement('div');
  preview.className = 'helpReplyText chatPreview'; preview.hidden = true;
  const hint = document.createElement('p'); hint.className = 'inputHint'; hint.textContent = labels.hint;
  host.append(toolbar, mount, preview, hint); input.after(host);
  const editable = new Compartment(); let disabled = false;
  const view = new EditorView({parent: mount, state: EditorState.create({doc: input.value, extensions: [
    history(), markdown(), syntaxHighlighting(defaultHighlightStyle), indentUnit.of('  '),
    keymap.of([{key: 'Escape', run: temporarilySetTabFocusMode}, ...defaultKeymap, ...historyKeymap, indentWithTab]), EditorView.lineWrapping,
    editable.of(EditorView.editable.of(true)),
    EditorView.contentAttributes.of({'aria-label': labels.editor, 'aria-labelledby': input.id + '-label', 'spellcheck': 'true'}),
    EditorView.updateListener.of(update => {
      if (!update.docChanged) return;
      input.value = update.state.doc.toString(); input.dispatchEvent(new Event('input', {bubbles: true}));
      if (!preview.hidden) renderChatMarkdown(preview, input.value);
    }),
    EditorState.transactionFilter.of(transaction => transaction.newDoc.length > input.maxLength ? [] : transaction),
    EditorView.theme({'.cm-content': {minHeight: '170px', padding: '12px', fontFamily: 'inherit'},
      '&.cm-focused': {outline: '2px solid var(--link-color)'}, '.cm-scroller': {overflow: 'auto'}})
  ]})});
  const buttons = [];
  function button(label, action) {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
    b.addEventListener('click', () => {if (!disabled) action();}); toolbar.append(b); buttons.push(b); return b;
  }
  function wrap(before, after = before) {
    const {from, to} = view.state.selection.main, selected = view.state.sliceDoc(from, to);
    view.dispatch({changes: {from, to, insert: before + selected + after},
      selection: {anchor: from + before.length, head: from + before.length + selected.length}}); view.focus();
  }
  button(labels.bold, () => wrap('**')); button(labels.italic, () => wrap('*'));
  button(labels.list, () => wrap('- ', '')); button(labels.math, () => wrap('$'));
  const toggle = button(labels.preview, () => {
    preview.hidden = !preview.hidden; toggle.setAttribute('aria-pressed', String(!preview.hidden));
    if (!preview.hidden) renderChatMarkdown(preview, input.value);
  }); toggle.setAttribute('aria-pressed', 'false');
  return {host, focus: () => view.focus(), destroy: () => view.destroy(),
    setValue(value) {if (value !== view.state.doc.toString()) view.dispatch({changes: {from: 0, to: view.state.doc.length, insert: value}});},
    setDisabled(value) {disabled = value; buttons.forEach(b => b.disabled = value); view.dispatch({effects: editable.reconfigure(EditorView.editable.of(!value))});}
  };
}
