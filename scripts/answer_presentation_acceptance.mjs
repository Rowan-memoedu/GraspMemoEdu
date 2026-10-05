/** Disposable Chrome acceptance for answer fields, SVG math and expanded review trees. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const [base, fixture] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const identities = JSON.parse(await fs.readFile(path.join(fixture, 'identities.json'), 'utf8'));
const output = path.join(fixture, 'presentation-browser'); await fs.mkdir(output, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const browser = await chromium.launch({headless: true, executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
const context = await browser.newContext({viewport: {width: 1365, height: 950}, locale: 'zh-CN'});
const page = await context.newPage(); page.setDefaultTimeout(25000);
const report = {checks: [], errors: [], screenshots: [], formal_data_opened: false};
let token, session;
page.on('pageerror', error => report.errors.push(error.message));
page.on('request', request => { token = request.headers()['x-learning-identity'] || token; });
const pass = text => { report.checks.push(text); console.log('PASS ' + text); };
const go = route => page.goto(base + '#' + route, {waitUntil: 'networkidle'});
async function api(route, body) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET',
    headers: {Origin: base, 'Content-Type': 'application/json', 'X-Learning-Session': session, 'X-Learning-Identity': token},
    ...(body ? {body: JSON.stringify(body)} : {})});
  const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result)); return result;
}
async function shot(name) {
  const file = path.join(output, name + '.png'); await page.screenshot({path: file, fullPage: true}); report.screenshots.push(file);
}
async function math(selector) {
  const scope = page.locator(selector); await scope.locator('mjx-container svg').first().waitFor();
  const metrics = await scope.evaluate(root => ({
    formulas: [...root.querySelectorAll('mjx-container svg')].map(svg => ({
      width: svg.getBoundingClientRect().width, height: svg.getBoundingClientRect().height,
      missing: [...svg.querySelectorAll('use')].filter(use => {
        const href = use.getAttribute('href') || use.getAttribute('xlink:href');
        return href?.startsWith('#') && !svg.querySelector('[id="' + href.slice(1) + '"]');
      }).length,
    })),
    raw: root.textContent.includes('$'),
  }));
  assert.equal(metrics.raw, false); assert.ok(metrics.formulas.length);
  assert.ok(metrics.formulas.every(f => f.width > 0 && f.height > 0 && !f.missing), JSON.stringify(metrics));
}
const bank = q => '/subjects/math/banks/tree-bank-a/section?review=1&question=' + q;
try {
  await go('/subjects/math/reviews');
  await page.locator('#userMenuButton').hover(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities.advanced); await page.locator('#identitySubmitButton').click();
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
  await go('/subjects/math/reviews'); await page.getByRole('heading', {name: '我的错题本', exact: true}).waitFor();
  assert.ok(await page.locator('details.courseUnitGroup').count());
  assert.equal(await page.locator('details.courseUnitGroup:not([open])').count(), 0);
  await shot('review-expanded');
  await page.locator('details.courseUnitGroup > summary').first().click();
  assert.ok(await page.locator('details.courseUnitGroup:not([open])').count());
  pass('All review hierarchy levels default open and remain manually collapsible');
  const candidate = (await api('review-papers/inbox?subject_id=math')).items.find(i => i.kind === 'single' && i.title === '复现填空');
  assert.ok(candidate);
  const paper = await api('review-papers/open?subject_id=math', {candidate_id: candidate.id, request_id: crypto.randomUUID()});
  await go('/subjects/math/reviews/' + paper.id); await page.locator('.showAnswer').click();
  await math('.selfAssessment .referenceAnswerBody'); await math('.selfAssessment .courseContent:not(.referenceAnswerBody)');
  await shot('paper-blank-math'); pass('Review Show Answer renders existing fill-blank reference and explanation as SVG math');
  await page.locator('.selfAssessment [data-rating="3"]').click(); await page.getByText('本次复习已完成', {exact: true}).waitFor();
  await go(bank('semantic')); await page.locator('#trainingSubmit').click();
  await math('.trainingExplanation'); assert.equal(await page.locator('.referenceAnswer').count(), 0);
  await shot('bank-text-explanation'); pass('Bank text Show Answer contains explanation only, with rendered math');
  await go(bank('semantic-blank')); await page.locator('#trainingSubmit').click();
  await math('.referenceAnswerBody'); await math('.trainingExplanation');
  await shot('bank-blank-math'); pass('Bank semantic blank uses the authored fractional answer without generating content');
  for (const q of ['choice-0', 'boolean', 'matching']) {
    await go(bank(q)); await page.locator('#trainingSubmit').waitFor();
    assert.equal(await page.locator('.referenceAnswer').count(), 0);
    if (q.startsWith('choice')) await page.locator('.choiceOption input[value="a"]').check();
    if (q === 'boolean') {
      await page.locator('.judgmentItem').nth(0).locator('input[value="true"]').check();
      await page.locator('.judgmentItem').nth(1).locator('input[value="false"]').check();
    }
    if (q === 'matching') {
      for (const [row, id] of [[0, 'l2'], [1, 'l3']]) {
        await page.locator('.matchingOption[data-side="left"][data-option-id="' + id + '"]').click();
        await page.locator('.matchingSlot[data-side="left"][data-row="' + row + '"]').click();
      }
      for (const [row, id] of [[0, 'r4'], [1, 'r9']]) {
        await page.locator('.matchingOption[data-side="right"][data-option-id="' + id + '"]').click();
        await page.locator('.matchingSlot[data-side="right"][data-row="' + row + '"]').click();
      }
    }
    await page.locator('#trainingSubmit').click(); await page.locator('.trainingVerdict').waitFor();
    await page.locator('.referenceAnswerBody').waitFor(); await math('.trainingExplanation');
    assert.ok((await page.locator('.referenceAnswerBody').innerText()).trim());
    await shot('bank-' + q + '-reference');
  }
  pass('Choice, true/false and matching expose existing answer keys only after grading');
  await go('/subjects/math/topic/synthetic-course');
  await page.getByRole('button', {name: '已掌握，试着作答', exact: true}).click();
  await page.locator('#stepCard .showAnswer').click(); await math('#stepCard .selfAssessment .courseContent');
  assert.equal(await page.locator('#stepCard .referenceAnswer').count(), 0);
  assert.equal(await page.locator('#stepCard .exampleExplanationHeader').count(), 1);
  await shot('lesson-text-explanation'); pass('Lesson text Show Answer mounts one explanation and no reference-answer field');
  await go('/subjects/english/topic/assessment-atomic');
  await page.locator('.atomicMain .primaryButton').first().click(); await page.locator('.atomicMain .primaryButton').first().click();
  await page.locator('.atomicMain .showAnswer').click(); await math('.atomicMain .selfAssessment .courseContent');
  assert.equal(await page.locator('.atomicMain .referenceAnswer').count(), 0);
  await shot('atomic-text-explanation'); pass('Atomic text cards share the explanation-only renderer');
  await page.setViewportSize({width: 390, height: 844}); await go(bank('semantic-blank'));
  await page.locator('#trainingStepTitle').waitFor(); await page.keyboard.press('Escape');
  if (await page.locator('#trainingSubmit').count()) await page.locator('#trainingSubmit').click();
  await math('.referenceAnswerBody'); await math('.trainingExplanation');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false);
  await shot('mobile-blank-math'); pass('Mobile reference and explanation math remain visible without horizontal overflow');
  assert.deepEqual(report.errors, []);
} catch (error) { report.failure = String(error.stack || error); await shot('failure'); throw error; }
finally { await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); await browser.close(); }
