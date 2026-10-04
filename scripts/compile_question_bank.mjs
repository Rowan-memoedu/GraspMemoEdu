import fs from 'node:fs/promises';
import {renderer} from './import_course.mjs';

const [source, target, coursePath] = process.argv.slice(2);
if (!source || !target || source === target) throw new Error('Specify distinct source and compiled output');
const bank = JSON.parse(await fs.readFile(source, 'utf8'));
const course = coursePath ? JSON.parse(await fs.readFile(coursePath, 'utf8')) : null;
const originals = new Map((course?.modules || []).flatMap(m => [m.example, ...m.practices]).map(q => [q.id, q]));
const render = await renderer();
const html = text => render.nodes([{head: text, lines: [], children: [], line: 1}]);
// Option prose is literal answer data. Preserve multiplication stars,
// backslashes and underscores instead of interpreting them as Markdown.
// Authored dollar-delimited mathematics still goes through the safe renderer.
const optionHtml = text => html(text.split(/(\$\$[\s\S]*?\$\$|\$[^$\n]+\$)/g)
  .map((part, index) => index % 2 ? part : part.replace(/[!-/:-@\[-`{-~]/g, char => `&#${char.codePointAt(0)};`)).join(''));
for (const [id, question] of Object.entries(bank.questions)) {
  const original = originals.get(id);
  if (course && (!original || !question.question.startsWith(original.question))) throw new Error('Conversion changed the source stem');
  // The source HTML already contains validated, embedded diagrams. Preserve it
  // exactly and render only the appended input instruction.
  question.html = original ? original.html + html(question.question.slice(original.question.length)) : html(question.question);
  for (const option of question.interaction.options || []) option.html = optionHtml(option.text);
}
bank.math_css = render.css;
await fs.writeFile(target, JSON.stringify(bank, null, 2) + '\n');
console.log(`COMPILED ${Object.keys(bank.questions).length} question variants`);
