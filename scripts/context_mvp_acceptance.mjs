/** Real-site English MVP acceptance; fresh isolated Chrome profile, no mocked APIs. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';

const [base, fixture, mode] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const identities = JSON.parse(await fs.readFile(path.join(fixture, 'identities.json'), 'utf8'));
const output = path.join(fixture, 'browser'); await fs.mkdir(output, {recursive: true});
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
const captureFile = process.argv.find(arg => arg.startsWith('--capture-wave='))?.split('=').slice(1).join('=');
if (captureFile) await fs.access(captureFile);
const browser = await chromium.launch({headless: true,
  executablePath: path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe'),
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    ...(captureFile ? ['--use-file-for-fake-audio-capture=' + captureFile] : [])]});
const context = await browser.newContext({viewport: {width: 1365, height: 960}, locale: 'zh-CN', permissions: ['microphone']});
const page = await context.newPage(); page.setDefaultTimeout(30000);
const report = {checks: [], errors: [], screenshots: [], cards: [], real_judge: [], production_data: false, mocked_endpoints: false};
let token, session;
page.on('pageerror', error => report.errors.push(error.message));
const pass = name => {report.checks.push(name); console.log('PASS ' + name);};
const go = route => page.goto(base + '/#' + route, {waitUntil: 'networkidle'});
const topicId = 'english-mvp-context', materialId = 'repair-cafe';
const reading = `/subjects/english/reading?topic=${topicId}&material=${materialId}`;
const query = `topic_id=${topicId}&material_id=${materialId}&subject_id=english`;
async function api(route, body, expected = 200, qa = false) {
  session ||= (await (await fetch(base + '/api/session', {headers: {Origin: base}})).json()).session_token;
  const response = await fetch(base + (qa ? '/__qa/' : '/api/') + route, {method: body === undefined ? 'GET' : 'POST',
    headers: {Origin: base, 'Content-Type': 'application/json', 'X-Learning-Session': session,
      ...(token ? {'X-Learning-Identity': token} : {})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const result = await response.json(); assert.equal(response.status, expected, JSON.stringify(result)); return result;
}
async function login(who) {
  await page.locator('#userMenuButton').click(); await page.locator('#identityButton').click();
  await page.locator('#invitationCode').fill(identities[who].code);
  const response = page.waitForResponse(r => r.url().includes('/api/access/login') && r.request().method() === 'POST');
  await page.locator('#identitySubmitButton').click();
  const logged = await (await response).json(); token = logged.identity_token;
  assert.equal(logged.access.learner_id, identities[who].id);
  await page.locator('#identityDialog').waitFor({state: 'hidden'});
}
async function shot(name) {
  const filename = path.join(output, name + '.png');
  await page.evaluate(async () => {window.scrollTo(0, 0); await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);});
  await page.screenshot({path: filename, fullPage: true}); report.screenshots.push(filename);
}
async function language(value) {
  await go('/subjects/english/settings');
  await Promise.all([page.waitForEvent('load'), page.locator('#profileLanguage').selectOption(value)]);
  await page.locator('#profileLanguage').waitFor();
  await page.waitForLoadState('networkidle');
}
async function selectWord(word) {
  await page.locator('.readingArticle').evaluate((article, word) => {
    const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT); let text;
    while ((text = walker.nextNode())) {
      const start = text.textContent.indexOf(word);
      if (start < 0) continue;
      const range = document.createRange(); range.setStart(text, start); range.setEnd(text, start + word.length);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      text.parentElement.dispatchEvent(new Event('pointerup', {bubbles: true})); return;
    }
    throw new Error('Word not present in reading text');
  }, word);
  await page.getByRole('button', {name: '查所选文字', exact: true}).click();
  await page.locator('.readingPanel h3').filter({hasText: word}).waitFor();
  await page.getByRole('button', {name: /^(保存候选|保存语境笔记)$/}).waitFor();
}
async function openCard(topic, card) {
  const route = `/subjects/english/review/${topic}/atomic?card=${card}`;
  if (page.url() === base + '/#' + route) await go('/subjects/english/reviews?materials=1');
  const opened = page.waitForResponse(r => r.url().includes('/api/materials/open') && r.request().method() === 'POST');
  await go(route); await opened;
  await page.locator(`.atomicQuestion[data-card-id="${card}"]`).waitFor();
  return api('state?subject_id=english&topic_id=' + topic);
}
async function submitText(text, expected) {
  await page.locator('#atomicAnswer').fill(text);
  await page.locator('#atomicAnswerForm button[type="submit"]').click();
  await page.locator('.atomicQuestion .feedback').waitFor({timeout: 180000});
  const current = (await api(`state?subject_id=english&topic_id=${topicId}`)).current;
  assert.equal(current.correct, expected, current.reason);
  report.real_judge.push({correct: current.correct, reason: current.reason, card_id: current.card_id});
  console.log('REAL JUDGE ' + (expected ? 'correct' : 'incorrect'));
  await shot('feedback-' + current.card_id + '-' + (expected ? 'correct' : 'incorrect') + '-' + page.viewportSize().width);
}
async function checkAudioInput() {
  assert(captureFile, 'Pass --capture-wave=<PCM WAV> to exercise real browser recording');
  await go('/subjects/english/courses'); await login('admin1');
  await openCard(topicId, 'context-41ffc517a138f2f6060e9e8b');
  await page.setViewportSize({width: 375, height: 900});
  await page.getByRole('button', {name: '开始录音', exact: true}).click();
  await page.getByRole('button', {name: '停止录音', exact: true}).waitFor();
  await shot('recording-mobile-375');
  // Real-time capture of the 22-second authored speech through Chrome's test microphone.
  await new Promise(resolve => setTimeout(resolve, 23000));
  await page.getByRole('button', {name: '停止录音', exact: true}).click();
  const transcribe = page.getByRole('button', {name: '转写到答案', exact: true});
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent === '转写到答案' && !b.disabled));
  const recording = page.locator('.trainingAudio audio').nth(1);
  await recording.waitFor({state: 'visible'});
  await recording.evaluate(async element => {await element.play();});
  await page.waitForFunction(() => document.querySelectorAll('.trainingAudio audio')[1]?.currentTime > .1);
  await recording.evaluate(element => element.pause());
  const response = page.waitForResponse(r => r.url().includes('/api/training-audio/transcribe'), {timeout: 180000});
  await transcribe.click();
  const transcribed = await response;
  assert.equal(transcribed.status(), 200, await transcribed.text());
  const request = transcribed.request().postDataJSON();
  assert.equal(request.mime_type, 'audio/wav');
  const wave = Buffer.from(request.data_base64, 'base64'); assert.equal(wave.toString('ascii', 0, 4), 'RIFF');
  assert.equal(wave.toString('ascii', 8, 12), 'WAVE');
  const result = await transcribed.json();
  assert.equal(result.editable, true); assert.match(result.text, /museum|Friday|north gate/i);
  await page.locator('.trainingAudioStatus').filter({hasText: '转写已放入答案框'}).waitFor();
  assert.equal(await page.locator('#atomicAnswer').inputValue(), result.text);
  assert.equal((await api(`state?subject_id=english&topic_id=${topicId}`)).current.phase, 'answer');
  report.transcription = {provider: result.provider, text: result.text, wave_bytes: wave.length};
  const sizes = await page.evaluate(() => ({viewport: innerWidth, document: document.documentElement.scrollWidth}));
  assert(sizes.document <= sizes.viewport + 1, JSON.stringify(sizes));
  await shot('transcribed-mobile-375');
  await submitText('The bus leaves at 8:30 on Friday. Meet at the north gate. Bring lunch and a raincoat, but no money. Ask Mr. Li before 4:00 today if you have questions.', true);
  pass('Real browser PCM recording, replay and Fun-ASR transcript remain editable and submit through existing content judging');
}
async function checkAudioScreens() {
  await go('/subjects/english/courses'); await login('admin1');
  await openCard(topicId, 'context-41ffc517a138f2f6060e9e8b');
  await page.getByRole('button', {name: '加载题目音频', exact: true}).click();
  await page.locator('.trainingAudio audio').first().waitFor({state: 'visible'});
  for (const width of [320, 375, 414, 768]) {
    await page.setViewportSize({width, height: 900});
    const sizes = await page.evaluate(() => ({viewport: innerWidth, document: document.documentElement.scrollWidth}));
    assert(sizes.document <= sizes.viewport + 1, JSON.stringify(sizes)); await shot('audio-mobile-' + width);
  }
  pass('Real audio question and recorder controls fit 320/375/414/768 widths');
}
async function checkFeedback() {
  await go('/subjects/english/courses'); await login('admin1');
  for (const [answer, correct] of [['We decided to reuse these glass bottles to reduce waste.', true],
    ['We decided to smash every glass bottle and create more waste.', false]]) {
    await page.setViewportSize({width: 1365, height: 960});
    await openCard(topicId, 'seed-ctx-translate'); await submitText(answer, correct);
    for (const width of [1365, 375]) {
      await page.setViewportSize({width, height: 960});
      const layout = await page.locator('.atomicFeedback').evaluate(element => ({display: getComputedStyle(element).display,
        title: element.querySelector('strong').getBoundingClientRect().toJSON(),
        viewport: innerWidth, document: document.documentElement.scrollWidth}));
      assert.equal(layout.display, 'block'); assert(layout.title.width > layout.title.height * 2);
      assert(layout.document <= layout.viewport + 1, JSON.stringify(layout));
      await shot(`feedback-${correct ? 'correct' : 'incorrect'}-${width}`);
    }
  }
  pass('Real correct/incorrect atomic feedback flows vertically with readable headings at desktop and 375px widths');
}
try {
  if (mode === '--feedback-only') {
    await checkFeedback();
  } else if (mode === '--screens-only') {
    await checkAudioScreens();
  } else if (mode === '--audio-only') {
    await checkAudioInput();
  } else {
  await go('/subjects/english/courses'); await login('student1'); await go(reading);
  await page.locator('.readingArticle').waitFor();
  await selectWord('volunteers');
  assert.match(await page.locator('.readingPanel').innerText(), /Open English WordNet/);
  await page.locator('.readingPanel textarea').fill('这里 volunteers 指哪些人？希望补充可区分词义的练习。');
  await page.getByRole('button', {name: /^(保存候选|保存语境笔记)$/}).click();
  await page.locator('[data-reading-status]').filter({hasText: '尚未进入复习'}).waitFor();
  const download = page.waitForEvent('download');
  await page.getByRole('button', {name: '导出本篇语境', exact: true}).click();
  const exportedFile = path.join(output, 'reading-context-export.json'); await (await download).saveAs(exportedFile);
  const exported = JSON.parse(await fs.readFile(exportedFile, 'utf8'));
  assert.equal(exported.kind, 'reading-context-export');
  const candidate = exported.records.find(r => r.quote.exact === 'volunteers');
  assert(candidate && candidate.memory_status === 'candidate');
  assert.equal(candidate.contextual_sense_selected, false); assert.equal(candidate.selected_sense_id, null);
  assert(candidate.dictionary?.senses?.[0]?.sense_id); assert(candidate.dictionary.source.license);
  assert.equal(candidate.point_ids.length, 0); assert(candidate.definitions.length);
  assert(!JSON.stringify(exported).includes(identities.student1.id));
  await shot('reading-candidate-export');
  pass('Real dictionary lookup, private candidate and browser JSON export preserve context without selecting a sense');

  if (mode !== '--candidate-only') {
    await page.locator('.readingWord').filter({hasText: /^plant$/}).click();
    await page.getByRole('button', {name: /^(加入记忆|保存语境笔记)$/}).waitFor();
    await api(`state?subject_id=english&topic_id=${topicId}`);
    const before = await api(`audit?topic_id=${topicId}`, undefined, 200, true);
    await page.locator('.readingPanel textarea').fill('结合 workers 和 closed 判断 plant 的语境义。');
    if (await page.getByRole('button', {name: '标记疑惑', exact: true}).count()) {
      await page.getByRole('button', {name: '标记疑惑', exact: true}).click();
      await page.locator('[data-reading-status]').filter({hasText: '已标记疑惑'}).waitFor();
    }
    await page.getByRole('button', {name: /^(加入记忆|保存语境笔记)$/}).click();
    await page.locator('[data-reading-status]').filter({hasText: '已加入初学队列'}).waitFor();
    const after = await api(`audit?topic_id=${topicId}`, undefined, 200, true);
    assert.deepEqual(after.state.points, before.state.points); assert(after.state.requested_points.includes('ctx-plant'));
    await page.getByRole('link', {name: '开始对应学习', exact: true}).click();
    await page.waitForURL(/\/topic\/english-mvp-context/);
    const state = await api(`state?subject_id=english&topic_id=${topicId}`);
    assert(state.unified_topic); assert.equal(state.introduction_id, 'intro-ctx-ask');
    pass('Reading marks persist and linked points enter the priority queue without mastery, FSRS or Introduction-order changes');

    await go(reading); await page.locator('.readingArticle').waitFor();
    for (const width of [320, 375, 414, 768]) {
      await page.setViewportSize({width, height: 900});
      const sizes = await page.evaluate(() => ({viewport: innerWidth, document: document.documentElement.scrollWidth}));
      assert(sizes.document <= sizes.viewport + 1, JSON.stringify(sizes)); await shot('reading-mobile-' + width);
    }
    await page.setViewportSize({width: 1365, height: 960});
    await language('en'); await go(reading);
    await page.getByRole('button', {name: 'Export contexts', exact: true}).waitFor();
    assert.match(await page.locator('.readingArticle').innerText(), /When the old bicycle plant closed/);
    await shot('reading-english');
    await language('zh-CN');
    pass('Actual reader fits 320/375/414/768 widths and switches interface language while preserving authored text');

    await login('student2'); await go(reading); await page.locator('.readingArticle').waitFor();
    assert.equal((await api('context-library/material?' + query)).records.length, 0);
    assert.equal(await page.getByRole('button', {name: '导出本篇语境', exact: true}).count(), 0);
    pass('A second learner cannot see or export the first learner’s contexts');
    for (const who of ['standard', 'ordinary_student', 'advanced_nonstudent', 'formal_student', 'formal_admin']) {
      await login(who); assert.equal((await api('catalog?subject_id=english')).courses.length, 0);
      await api('context-library/material?' + query, undefined, 403);
      await api(`training-audio?subject_id=english&topic_id=${topicId}&question_id=seed-ctx-listen`, undefined, 403);
    }
    pass('Test-material policy excludes ordinary, advanced nonstudent and formal identities from reading and audio');

    await login('admin1'); await go('/subjects/english/reviews?materials=1');
    const topics = [];
    for (const id of ['english-mvp-skywells', topicId]) {
      topics.push(JSON.parse(await fs.readFile(path.join(fixture, 'data/topics', id + '.json'), 'utf8')));
    }
    const cards = topics.flatMap(t => t.flashcards.map(card => ({topic: t.id, card})));
    assert.equal(cards.length, 37);
    for (const [index, {topic, card}] of cards.entries()) {
      const state = await openCard(topic, card.id);
      assert.equal(state.current.card_id, card.id); assert.equal(state.current.phase, 'answer');
      assert(!('explanation_html' in state.current));
      assert(!state.current.audio || !('transcript' in state.current.audio));
      assert((await page.locator('.atomicQuestion .courseContent').first().innerText()).trim().length > 10);
      const type = card.interaction?.type || 'text';
      assert.equal(state.current.interaction.type, type);
      if (card.audio) {
        await page.getByRole('button', {name: '加载题目音频', exact: true}).click();
        const audio = page.locator('.trainingAudio audio').first();
        await audio.waitFor({state: 'visible'});
        await audio.evaluate(async element => {await element.play();});
        await page.waitForFunction(() => document.querySelector('.trainingAudio audio')?.currentTime > .15);
        assert(await audio.evaluate(element => Number.isFinite(element.duration) && element.duration > 0));
        await audio.evaluate(element => element.pause());
        await shot('audio-' + card.audio.mode + '-' + index);
      }
      report.cards.push({id: card.id, topic, type, audio: card.audio?.mode || null});
      if ((index + 1) % 5 === 0) console.log(`CARDS ${index + 1}/37`);
    }
    pass('All 37 real compiled cards render on normal routes; all five real audio files decode and play without answer transcripts');
    await openCard(topicId, 'seed-ctx-translate');
    await submitText('We decided to reuse these glass bottles in order to reduce waste.', true);
    await openCard(topicId, 'seed-ctx-translate');
    await submitText('We decided to smash every glass bottle and create more waste.', false);
    pass('ExistingJudge produces real correct and incorrect semantic judgments through the normal submit workflow');

    await go(reading); await page.locator('.readingWord').filter({hasText: /^plant$/}).click();
    await page.getByRole('button', {name: '标记疑惑', exact: true}).click();
    await page.locator('[data-reading-status]').filter({hasText: '已标记疑惑'}).waitFor();
    const adminAudit = await api(`audit?topic_id=${topicId}`, undefined, 200, true);
    assert(Object.values(adminAudit.persistent_rows).every(value => value === 0));
    assert.equal((await api('context-library/material?' + query)).records.length, 1);
    await login('admin2'); await go(reading);
    assert.equal((await api('context-library/material?' + query)).records.length, 0);
    await login('admin1'); await go(reading);
    assert.equal((await api('context-library/material?' + query)).records.length, 0);
    pass('Administrator preview stores no persistent learning/context rows and expires when changing identity');
  }
  }
  assert.deepEqual(report.errors, []);
} catch (error) {
  report.failure = {message: error.message, stack: error.stack};
  await shot('failure').catch(() => {}); throw error;
} finally {
  await fs.writeFile(path.join(output, mode === '--candidate-only' ? 'candidate-report.json' : mode === '--audio-only' ? 'audio-report.json' : mode === '--screens-only' ? 'audio-screens-report.json' : mode === '--feedback-only' ? 'feedback-report.json' : 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
