/** Structured atomic input uses the existing safe renderer and Python contract. */
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import crypto from 'node:crypto';
import {renderer} from './import_course.mjs';

export async function compileStructured(source, bytes) {
  const repo = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
  const python = (command, value) => {
    const result = spawnSync(process.env.GRASPMEMOEDU_PYTHON || 'python',
      ['-B', '-m', 'graspmemoedu.structured_atomic', command],
      {cwd: repo, input: JSON.stringify(value), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024});
    if (result.status !== 0) throw new Error(result.stderr || 'Structured atomic validation failed');
    return JSON.parse(result.stdout);
  };
  const topic = python('normalize', source);
  const render = await renderer(new Map(), {literalCode: true});
  const html = text => {
    try { return render.nodes([{head:text, lines:[], children:[], line:1}]); }
    catch (error) { throw new Error(error.message + ': ' + JSON.stringify(text).slice(0, 1800)); }
  };
  for (const chunk of topic.memory_chunks) {
    chunk.content_html = html(chunk.content);
    chunk.mnemonic_html = (chunk.mnemonic_markdown ? html(chunk.mnemonic_markdown) : '') + (chunk.mnemonic_html || '');
    chunk.html = chunk.content_html + chunk.mnemonic_html;
    delete chunk.mnemonic_markdown;
  }
  for (const card of topic.flashcards) {
    card.html = html(card.question);
    card.explanation_html = html(card.explanation);
    delete card.explanation;
    const spec = card.interaction;
    for (const item of [...(spec?.left || []), ...(spec?.right || []), ...(spec?.statements || []), ...(spec?.options || [])]) item.html = html(item.text);
  }
  topic.math_css = render.css;
  topic.source_sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
  return python('finalize', topic);
}
