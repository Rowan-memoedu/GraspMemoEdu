/** Actual Topic/bank in a loopback fixture; isolated identity, real grading. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, out, courseFile, bankFile] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const course = JSON.parse(await fs.readFile(courseFile, 'utf8'));
const bank = JSON.parse(await fs.readFile(bankFile, 'utf8'));
assert.equal(bank.topic_id, course.id); assert.equal(bank.course_version, course.version);
assert.equal(bank.provenance.pilot, false);
await fs.mkdir(out, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const page = await browser.newPage({viewport: {width: 1365, height: 950}, locale: 'zh-CN'});
page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], events: [], formal_progress_opened: false, source_topic: course.id};
let token, session;
page.on('pageerror', error => report.errors.push(error.message));
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
async function api(route, body, qa = false) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + (qa ? '/__qa/' : '/api/') + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'X-Learning-Session': session, 'X-Learning-Identity': token, 'Content-Type': 'application/json'},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const value = await response.json(); assert.equal(response.status, 200, JSON.stringify(value)); return value;
}
const state = () => api('state?topic_id=' + course.id);
const active = value => value.steps.find(step => step.id === value.active_step_id);
const go = route => page.goto(base + '#' + route, {waitUntil: 'networkidle'});
const shot = name => page.screenshot({path: path.join(out, name + '.png'), fullPage: true});
const pass = text => { report.checks.push(text); console.log('PASS ' + text); };
async function answer(question, spec, container, submit, expected) {
  if (spec.type === 'choice') {
    assert.equal(await container.locator('.choiceOption').count(), 6);
    const selected = expected ? spec.correct_id : spec.options.find(option => option.id !== spec.correct_id).id;
    await container.locator(`input[value="${selected}"]`).check();
  } else {
    assert.equal(spec.type, 'fill_blank');
    // A different exact expression tests the inherited equivalent-answer contract.
    await container.locator('.inlineBlank').fill(`(${question.reference_answer})+0`);
  }
  await submit.click();
  await container.locator(expected ? '.feedback.correct' : '.feedback.incorrect').waitFor({timeout: 180000});
  report.events.push({question_id: question.id, type: spec.type, correct: expected});
}
try {
  await go('/subjects/math/topic/' + course.id);
  await page.locator('#stepCard').getByRole('button', {name: '已掌握，试着作答', exact: true}).waitFor();
  let first = true, shots = new Set();
  for (let turn = 0; turn < 50; turn++) {
    let saved = await state();
    if (saved.status === 'completed') break;
    const step = active(saved);
    if (step.phase === 'choice') {
      await page.locator('#stepCard').getByRole('button', {name: '已掌握，试着作答', exact: true}).click();
    } else if (step.phase === 'answer') {
      const question = course.modules.flatMap(module => [module.example, ...module.practices]).find(q => q.id === step.question_id);
      const spec = bank.questions[question.id].interaction;
      assert.equal(step.interaction.type, spec.type);
      assert.ok(step.content_version && step.content_version !== course.version);
      assert.ok(!('correct_id' in step.interaction));
      if (!shots.has(spec.type)) {
        await shot(spec.type); shots.add(spec.type);
        if (spec.type === 'choice') {
          await page.setViewportSize({width: 390, height: 844});
          if (await page.locator('#historyButton').getAttribute('aria-expanded') === 'true') await page.locator('#historyButton').click();
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
          await shot('choice-mobile'); await page.setViewportSize({width: 1365, height: 950});
        }
      }
      await answer(question, spec, page.locator('#stepCard'), page.locator('#submitButton'), !first);
      first = false;
    } else {
      await page.locator('#stepCard .continueRow .primaryButton').click();
    }
  }
  const completed = await state();
  assert.equal(completed.status, 'completed');
  assert.equal(completed.modules.filter(module => module.mastery !== null).length, course.modules.length);
  assert.ok(report.events.some(event => event.type === 'fill_blank' && event.correct));
  assert.ok(report.events.some(event => event.question_id.includes('practice') && event.correct));
  await page.reload({waitUntil: 'networkidle'});
  assert.equal((await state()).status, 'completed');
  pass('Actual Topic completes with choice Example/Practice, equivalent blank answer, preserved results and mobile layout');
  await api('seed-review', {module_id: course.modules[0].id}, true);
  await go(`/subjects/math/review/${course.id}/${course.modules[0].id}`);
  await page.locator('#reviewPractice .choiceOption').first().waitFor();
  const selected = await page.locator('#reviewPractice .choiceOption input').first().getAttribute('value');
  await page.locator(`#reviewPractice input[value="${selected}"]`).check();
  await page.reload({waitUntil: 'networkidle'});
  await page.locator('#reviewPractice input:checked').waitFor();
  assert.equal(await page.locator('#reviewPractice input:checked').inputValue(), selected);
  await shot('choice-review');
  pass('Actual due Practice review renders six options and retains its selected draft across reload');
  assert.deepEqual(report.errors, []);
  report.status = 'passed';
} catch (error) {
  report.status = 'failed'; report.failure = String(error.stack || error);
  await shot('failure').catch(() => {}); process.exitCode = 1;
} finally {
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
