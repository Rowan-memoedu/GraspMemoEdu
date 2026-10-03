import test from 'node:test';
import assert from 'node:assert/strict';
import {renderCallout, renderReference, prepareMermaidHtml} from '../scripts/topic-presentation.mjs';

test('all four Callouts retain the supplied field, nested body and fixed label', () => {
  for (const [type, label] of Object.entries({hint:'hint', aside:'题外话', requirement:'要求', source:'题源'})) {
    const html = renderCallout({type, html:'<p><strong>性质</strong>：正文</p><ul><li>下一层</li></ul>'});
    assert.ok(html.includes(`<strong>${label}</strong>`));
    assert.ok(html.includes('<strong>性质</strong>：正文'));
    assert.ok(html.includes('<ul><li>下一层</li></ul>'));
  }
  assert.throws(() => renderCallout({type:'danger', html:'text'}));
});

test('an unresolved Rem reference cannot execute markup or become an external link', () => {
  const html = renderReference({targetRemId:'Exact_Rem-1', label:'<img src=x> & 内容'});
  assert.ok(html.includes('data-target-rem-id="Exact_Rem-1"'));
  assert.ok(html.includes('&lt;img src=x&gt; &amp; 内容'));
  assert.ok(!html.includes('href='));
  assert.throws(() => renderReference({targetRemId:'https://example.com', label:'bad'}));
});

test('explicit Mermaid preserves sequential and branching source without treating ordinary code as a diagram', () => {
  const input = '<pre><code class="language-mermaid">flowchart TD\n  A[step 1] --&gt; B[step 2]\n  B --&gt; C[step 2.1]\n  B --&gt; D[step 2.2]\n</code></pre>';
  const output = prepareMermaidHtml(input);
  assert.equal(output.diagnostics[0].status, 'prepared');
  assert.ok(output.html.includes('mermaid-output'));
  assert.ok(output.html.includes('C[step 2.1]'));
  assert.ok(output.html.includes('D[step 2.2]'));
  const ordinary = '<pre><code class="language-javascript">flowchart TD</code></pre>';
  assert.equal(prepareMermaidHtml(ordinary).html, ordinary);
});
