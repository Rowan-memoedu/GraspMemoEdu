/** Preserve OCR prose as text while retaining explicit TeX and validated image references. */
export function literalProse(source) {
  const protectedSpan = /!\[[^\]\n]*\]\([^)\n]+\)|\$\$[\s\S]*?\$\$|\$[^$\n]+\$/g;
  const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('[', '&#91;').replaceAll(']', '&#93;');
  let result = '', position = 0;
  for (const match of source.matchAll(protectedSpan)) {
    let protectedText = match[0];
    if (protectedText.startsWith('$')) {
      let depth = 0, unbalanced = false;
      for (const token of protectedText.matchAll(/\\(left|right)\b/g)) {
        depth += token[1] === 'left' ? 1 : -1;
        if (depth < 0) unbalanced = true;
      }
      if (unbalanced || depth !== 0) {
        // OCR can split a scalable fence across separate math spans. Remove
        // only sizing controls (and their invisible-dot delimiters), not math.
        protectedText = protectedText.replace(/\\(?:left|right|middle)\b\s*\.?/g, '');
      }
    }
    result += escape(source.slice(position, match.index)) + protectedText;
    position = match.index + match[0].length;
  }
  return result + escape(source.slice(position));
}
