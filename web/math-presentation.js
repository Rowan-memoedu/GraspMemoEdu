// Typography for legacy plain mathematical expressions, without changing answers.
// Already compiled TeX/SVG, code, URLs and ordinary prose remain untouched.
const namespace = 'http://www.w3.org/1998/Math/MathML';
const greek = /[Α-Ωα-ω]/;
const letter = /[A-Za-zΑ-Ωα-ω]/;
const functions = /^(?:arcsin|arccos|arctan|sin|cos|tan|cot|sec|csc|log|ln)(?=[(A-Za-zΑ-Ωα-ω0-9])/;

function tokenAt(text, index) {
  const tail = text.slice(index), fn = tail.match(functions);
  if (fn) return {kind: 'function', value: fn[0], size: fn[0].length};
  const number = tail.match(/^\d+(?:\.\d+)?/);
  if (number) return {kind: 'number', value: number[0], size: number[0].length};
  if (letter.test(tail[0] || '')) {
    if (/[A-Za-z]/.test(tail[0]) && /[A-Za-z]/.test(tail[1] || '')) return null;
    const match = tail.match(/^([A-Za-zΑ-Ωα-ω])(?:_(\{[A-Za-z0-9]+\}|[A-Za-z0-9]+))?(?:\^(\{[+-]?[A-Za-z0-9]+\}|[+-]?[A-Za-z0-9]+))?/);
    if (match) return {kind: 'identifier', value: match[1], sub: match[2]?.replace(/[{}]/g, ''),
      sup: match[3]?.replace(/[{}]/g, ''), size: match[0].length};
  }
  if (/^[=<>≤≥≠+−\-*/×·±,()[\]]/.test(tail)) return {kind: 'operator', value: tail[0], size: 1};
  return null;
}

export function literalMathSegments(text) {
  const segments = []; let cursor = 0, index = 0;
  while (index < text.length) {
    if (!/[A-Za-zΑ-Ωα-ω0-9(]/.test(text[index]) || letter.test(text[index - 1] || '')
      || /[\\/:._]/.test(text[index - 1] || '')) { index++; continue; }
    const tokens = []; let end = index, depth = 0;
    while (end < text.length) {
      const gap = text.slice(end).match(/^\s+/)?.[0] || '';
      const token = tokenAt(text, end + gap.length);
      if (!token) break;
      if ('(['.includes(token.value)) depth++;
      if (')]'.includes(token.value)) { if (!depth) break; depth--; }
      tokens.push(token); end += gap.length + token.size;
    }
    // A dangling operator/comma belongs to surrounding prose, not the formula.
    while (tokens.at(-1)?.kind === 'operator' && ![')', ']'].includes(tokens.at(-1).value)) {
      end -= tokens.pop().size;
      while (/\s/.test(text[end - 1] || '')) end--;
    }
    const identifiers = tokens.filter(token => token.kind === 'identifier');
    const mathematical = identifiers.some(token => token.sub || token.sup || greek.test(token.value))
      || identifiers.length && tokens.some(token => token.kind === 'operator' && /[=<>≤≥≠+−\-*/×·±]/.test(token.value));
    if (!mathematical || depth || end <= index) { index++; continue; }
    if (cursor < index) segments.push({text: text.slice(cursor, index)});
    segments.push({text: text.slice(index, end), tokens}); cursor = index = end;
  }
  if (cursor < text.length) segments.push({text: text.slice(cursor)});
  return segments;
}

function mathNode(name, value) {
  const element = document.createElementNS(namespace, name);
  if (value !== undefined) element.textContent = value;
  return element;
}

function formula(segment) {
  const math = mathNode('math'), row = mathNode('mrow');
  math.setAttribute('display', 'inline'); math.setAttribute('aria-label', segment.text);
  math.classList.add('literalMath'); math.append(row);
  for (const token of segment.tokens) {
    let item = mathNode(token.kind === 'number' ? 'mn' : token.kind === 'operator' ? 'mo' : 'mi', token.value);
    if (token.kind === 'function') item.setAttribute('mathvariant', 'normal');
    if (token.sub || token.sup) {
      const base = item; item = mathNode(token.sub && token.sup ? 'msubsup' : token.sub ? 'msub' : 'msup'); item.append(base);
      for (const script of [token.sub, token.sup].filter(Boolean)) item.append(mathNode(/^[-+]?\d+$/.test(script) ? 'mn' : 'mi', script));
    }
    row.append(item);
  }
  return math;
}

export function prepareLiteralMath(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement?.closest('mjx-container,math,svg,code,pre,a,[data-tex]')) nodes.push(node);
  }
  for (const node of nodes) {
    const segments = literalMathSegments(node.textContent);
    if (!segments.some(segment => segment.tokens)) continue;
    const fragment = document.createDocumentFragment();
    for (const segment of segments) fragment.append(segment.tokens ? formula(segment) : document.createTextNode(segment.text));
    node.replaceWith(fragment);
  }
  return root;
}
