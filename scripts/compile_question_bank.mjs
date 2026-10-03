import fs from 'node:fs/promises';
import {renderer} from './import_course.mjs';

const [source, target] = process.argv.slice(2);
if (!source || !target || source === target) throw new Error('Specify distinct source and compiled output');
const bank = JSON.parse(await fs.readFile(source, 'utf8'));
const render = await renderer();
const html = text => render.nodes([{head: text, lines: [], children: [], line: 1}]);
for (const question of Object.values(bank.questions)) {
  question.html = html(question.question);
  for (const option of question.interaction.options || []) option.html = html(option.text);
}
bank.math_css = render.css;
await fs.writeFile(target, JSON.stringify(bank, null, 2) + '\n');
console.log(`COMPILED ${Object.keys(bank.questions).length} question variants`);
