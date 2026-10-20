/** Split source HTML at document headings without rewriting any source bytes. */
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
import path from 'node:path';
const require = createRequire(path.join(process.env.GRASPMEMOEDU_NODE_MODULES ||
  'D:/Caches/GraspMemoEdu/node-runtime/node_modules', '_entry.cjs'));
const {parseDocument} = require('htmlparser2');
const {textContent} = require('domutils');
const meaningful = nodes => nodes.some(n => n.type !== 'text' || n.data.trim());
const hash = value => createHash('sha256').update(value).digest('hex');

export function splitTheory(html, topicId) {
  const document = parseDocument(html, {withStartIndices:true, withEndIndices:true});
  let container = document;
  // A single document wrapper is not a section. Preserve it around each slice.
  while (true) {
    const content = container.children.filter(n => n.type !== 'text' || n.data.trim());
    if (content.length !== 1 || !['div','section','article'].includes(content[0].name)) break;
    container = content[0];
  }
  const headings = container.children.filter(n => /^h[1-6]$/.test(n.name || ''));
  const level = Math.min(...headings.map(n => +n.name[1]));
  const boundaries = headings.filter(n => +n.name[1] === level);
  const begin = container === document ? 0 : container.children[0]?.startIndex;
  const end = container === document ? html.length : container.children.at(-1)?.endIndex + 1;
  const prefix = html.slice(0, begin), suffix = html.slice(end);
  const parts = [], titles = new Map();
  function add(title, start, stop) {
    const raw = html.slice(start, stop);
    if (!meaningful(parseDocument(raw).children)) return;
    const occurrence = (titles.get(title) || 0) + 1; titles.set(title, occurrence);
    parts.push({id:`${topicId}-intro-${hash(title + ':' + occurrence).slice(0,16)}`,
      title, source_html:prefix + raw + suffix});
  }
  if (!boundaries.length) add('引言', begin, end);
  else {
    add('引言', begin, boundaries[0].startIndex);
    boundaries.forEach((heading,i) => add(textContent(heading).trim() || '引言', heading.startIndex,
      boundaries[i+1]?.startIndex ?? end));
  }
  if (!parts.length) throw Error('Theory has no content');
  return parts;
}

// Matches training_content.fingerprint (Python JSON, sorted keys, UTF-8).
export function bankFingerprint(bank) {
  const stable = value => Array.isArray(value) ? '[' + value.map(stable).join(', ') + ']'
    : value && typeof value === 'object' ? '{' + Object.keys(value).sort()
      .map(k => JSON.stringify(k) + ': ' + stable(value[k])).join(', ') + '}'
    : JSON.stringify(value);
  const {version,...content} = bank;
  return hash(stable(content));
}
