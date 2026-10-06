import assert from 'node:assert/strict';
import {literalMathSegments} from '../web/math-presentation.js';

const expressions = [
  '位置为 x_A, y_A, θ；满足 x_B=x_A+l cosθ。',
  '能量 E_k=m v^2/2，且 u_0^2+v_0^2=c^2。',
  '参数 q=(r,φ)，其中 r_1≤r_2。',
];
for (const text of expressions) {
  const segments = literalMathSegments(text);
  assert.equal(segments.map(segment => segment.text).join(''), text, 'Typography must preserve all source characters');
  assert.ok(segments.some(segment => segment.tokens?.some(token => token.sub)));
}
const trig = literalMathSegments('x_B=x_A+l cosθ').find(segment => segment.tokens).tokens;
assert.ok(trig.some(token => token.kind === 'function' && token.value === 'cos'));
assert.ok(trig.some(token => token.sub === 'B'));
for (const text of ['A plain answer with words.', 'Release 2026-10-06', 'abc_def', 'https://example.org/a_b', 'file_name.txt']) {
  const segments = literalMathSegments(text);
  assert.equal(segments.map(segment => segment.text).join(''), text);
  assert.equal(segments.some(segment => segment.tokens), false, text);
}
console.log('PASS literal math preserves answers, scripts and functions; prose, dates, URLs and file names remain literal');
