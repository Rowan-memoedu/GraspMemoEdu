import assert from 'node:assert/strict';
import {splitTheory} from './hyperskill_sections.mjs';

const source='<p>引入 &amp; context</p><h5>One</h5><pre><code>a &lt; b\n  x_y</code></pre><h6>Child</h6><p>tail</p><h5>Two</h5><img src="data:image/png;base64,YQ==">';
const parts=splitTheory(source,'topic');
assert.deepEqual(parts.map(p=>p.title),['引言','One','Two']);
assert.equal(parts.map(p=>p.source_html).join(''),source);
assert.equal(parts[1].source_html.includes('<h6>Child</h6>'),true);
assert.equal(splitTheory('<h2>Title</h2><p>body</p>','other').length,1);
assert.equal(splitTheory('<article><h3>A</h3><p>X</p><h3>A</h3><p>Y</p></article>','other').length,2);
const duplicates=splitTheory('<h2>A</h2>x<h2>A</h2>y','other');
assert.notEqual(duplicates[0].id,duplicates[1].id);
assert.equal(splitTheory('<p>prefix</p>'+source,'topic')[1].id,parts[1].id);
console.log('Hyperskill source section invariants passed');
