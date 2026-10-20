import fs from 'node:fs/promises';
import {loadAssets} from './import_course.mjs';
import {renderSourceMarkdown} from './render_mathacademy.mjs';

const [input, output] = process.argv.slice(2), result = [];
for (const task of JSON.parse(await fs.readFile(input, 'utf8'))) {
  const assets = await loadAssets(task.source, task.markdown);
  const markdown = task.markdown.replace(/!\[([^\]\n]*)\]\(([^)\n]+)\)/g, (_m, alt, relative) =>
    `![${alt}](data:image/png;base64,${assets.get(relative).data.toString('base64')})`);
  result.push(renderSourceMarkdown(markdown));
}
await fs.writeFile(output, JSON.stringify(result));
console.log(`Rendered ${result.length} Academy tasks with the shared content renderer`);
