/** Read-only Chrome screenshots of the real isolated native-course renderer. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {randomUUID, createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

const [base, root, selectedTopicId] = process.argv.slice(2);
assert.equal(new URL(base).hostname, '127.0.0.1');
const metadata = JSON.parse(await fs.readFile(path.join(root, 'preview-catalog.json'), 'utf8'));
const nativeBytes = await fs.readFile(path.join(root, 'preview-native.json'));
assert.equal(createHash('sha256').update(nativeBytes).digest('hex'), metadata.source_binding.native_snapshot_sha256);
const native = JSON.parse(nativeBytes.toString('utf8'));
const nativeTopics = new Map(native.topics.map(topic => [topic.id, topic]));
const selectedTopics = metadata.topics.filter(topic => !selectedTopicId || topic.id === selectedTopicId);
assert.ok(selectedTopics.length, 'Unknown selected native Topic');
const identity = JSON.parse(await fs.readFile(path.join(root, 'preview-identity.private.json'), 'utf8'));
const captureId = randomUUID();
const out = path.join(root, 'ui-evidence', captureId.slice(0, 8)); await fs.mkdir(out, {recursive: true});
const profile = await fs.mkdtemp(path.join(path.resolve(root), 'chrome-profile-'));
const executablePath = path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe');
const execFileAsync = promisify(execFile);
const processReceipt = {run_id: captureId, pid: process.pid, parent_pid: process.ppid,
  started_at: new Date().toISOString(), script: 'capture_source_course.mjs',
  preview: path.resolve(root), origin: base, profile, browser_executable: executablePath};
const saveProcessReceipt = () => fs.writeFile(path.join(out, 'capture-process.json'), JSON.stringify(processReceipt, null, 2));
// The exclusive profile and creation fingerprint identify only this capture.
// Never infer ownership from a process name or a shared application parent.
const processes = async (known = []) => {
  const script = String.raw`
$rows = @(Get-CimInstance Win32_Process)
$captureId = [int]$env:GRASPMEMOEDU_CAPTURE_PID
$profilePattern = '--user-data-dir=(?:"' + [regex]::Escape($env:GRASPMEMOEDU_PREVIEW_PROFILE) + '"|' + [regex]::Escape($env:GRASPMEMOEDU_PREVIEW_PROFILE) + '(?=\s|$))'
$roots = @($rows | Where-Object { $_.ParentProcessId -eq $captureId -and $_.CommandLine -match $profilePattern -and $_.ExecutablePath -eq $env:GRASPMEMOEDU_PREVIEW_EXE })
$owned = @($roots)
do {
  $parents = @($owned.ProcessId)
  $extra = @($rows | Where-Object { $_.ParentProcessId -in $parents -and $_.ProcessId -notin $parents -and $_.CreationDate -ge ($roots | Sort-Object CreationDate | Select-Object -First 1).CreationDate })
  $owned += $extra
} while ($extra.Count -gt 0)
function Fingerprint($items) { @($items | ForEach-Object { [pscustomobject]@{pid=$_.ProcessId; parent_pid=$_.ParentProcessId; created_at=$_.CreationDate.ToString('o'); executable=$_.ExecutablePath; private_bytes=[long]$_.PrivatePageCount} }) }
$previous = @($env:GRASPMEMOEDU_CAPTURE_KNOWN | ConvertFrom-Json)
$alive = @($rows | Where-Object { $current = $_; @($previous | Where-Object { $_.pid -eq $current.ProcessId -and [datetime]$_.created_at -eq $current.CreationDate -and $_.executable -eq $current.ExecutablePath }).Count -gt 0 })
[pscustomobject]@{node=@(Fingerprint @($rows | Where-Object ProcessId -eq $captureId)); roots=@(Fingerprint $roots); owned=@(Fingerprint $owned); known_alive=@(Fingerprint $alive)} | ConvertTo-Json -Depth 5 -Compress
`;
  const result = await execFileAsync('pwsh', ['-NoLogo', '-NoProfile', '-Command', script], {encoding: 'utf8', timeout: 30000,
    env: {...process.env, GRASPMEMOEDU_CAPTURE_PID: String(process.pid), GRASPMEMOEDU_PREVIEW_PROFILE: profile,
      GRASPMEMOEDU_PREVIEW_EXE: executablePath, GRASPMEMOEDU_CAPTURE_KNOWN: JSON.stringify(known)}});
  return JSON.parse(result.stdout);
};
processReceipt.before_launch = await processes(); await saveProcessReceipt();
const require = createRequire(path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/_entry.cjs'));
const {chromium} = require('playwright');
let context;
try { context = await chromium.launchPersistentContext(profile, {headless: true, executablePath, viewport: {width: 1440, height: 1100}}); }
catch (error) {
  processReceipt.launch_error = String(error); processReceipt.after_failed_launch = await processes();
  await saveProcessReceipt(); throw error;
}
const report = {status: 'running', actual_native_content: true, network_mocked: false, production_opened: false,
  answers_submitted: false, real_judge_validation: false, learning_completion_verified: false,
  installed_topic_ids: metadata.topics.map(topic => topic.id), sampled_topic_ids: selectedTopics.map(topic => topic.id),
  source_binding: metadata.source_binding,
  validation_scope: 'native_content_order_and_rendering', screenshots: [], topics: [], errors: []};
let token, session;
try {
  processReceipt.after_launch = await processes(); await saveProcessReceipt();
  assert.equal(processReceipt.after_launch.roots.length, 1, 'Capture must identify its exclusive Chrome process');
  const local = await context.request.get(base + '/api/session', {headers: {Origin: base}});
  assert.equal(local.status(), 200); session = (await local.json()).session_token;
  const response = await context.request.post(base + '/api/access/login', {headers: {Origin: base, 'X-Learning-Session': session}, data: identity});
  assert.equal(response.status(), 200); const login = await response.json();
  assert.equal(login.access.purpose, 'test'); assert.equal(login.access.is_admin, true); token = login.identity_token;
  await context.addInitScript(({token, base}) => {
    localStorage.setItem('graspmemoedu:identity:' + base, token);
    localStorage.setItem('graspmemoedu:identity-role:' + base, 'account');
  }, {token, base});
  const page = await context.newPage(); page.on('pageerror', e => report.errors.push(e.message));
  const api = async (route, body) => {
    const result = await context.request.fetch(base + '/api/' + route, {method: body ? 'POST' : 'GET',
      headers: {Origin: base, 'X-Learning-Session': session, 'X-Learning-Identity': token}, ...(body ? {data: body} : {})});
    assert.equal(result.status(), 200, await result.text()); return result.json();
  };
  const shot = async (name, element = null) => {
    const file = path.join(out, name + '.png');
    if (element) await element.screenshot({path: file});
    else await page.screenshot({path: file, fullPage: false});
    report.screenshots.push(file);
  };
  for (const topic of selectedTopics) {
    const scope = '?subject_id=' + metadata.subject_id + '&topic_id=' + topic.id;
    const expected = nativeTopics.get(topic.id); assert.ok(expected);
    const content = await api('materials/content' + scope);
    const introductions = new Map(expected.introductions.map(item => [item.id, item]));
    const chunks = expected.memory_chunks || [], cards = expected.flashcards || [];
    const introOrder = (expected.content_order || []).filter(item => item.kind === 'introduction').map(item => item.id);
    const modules = new Map((expected.modules || []).map(module => [module.id, module]));
    const questionOwners = new Map(), orderedModuleQuestions = [];
    let sourceIntroduction;
    for (const entry of expected.content_order || []) {
      if (entry.kind === 'introduction') sourceIntroduction = entry.id;
      else if (entry.kind === 'example') {
        const module = modules.get(entry.id); assert.ok(module);
        assert.ok(sourceIntroduction && introductions.has(sourceIntroduction));
        assert.ok(module.practices.length >= 7, 'The native adaptive queue needs seven Practice per Example');
        for (const question of [module.example, ...module.practices]) {
          questionOwners.set(question.id, sourceIntroduction); orderedModuleQuestions.push(question.id);
        }
      }
    }
    assert.deepEqual(content.blocks.filter(block => block.kind === 'introduction').map(block => block.id),
      introOrder.map(id => 'introduction:' + id));
    let owningIntroduction;
    const visibleChunks = [];
    for (const block of content.blocks) {
      if (block.kind === 'introduction') {
        owningIntroduction = block.id.slice('introduction:'.length);
        assert.equal(block.html, introductions.get(owningIntroduction).html);
      } else if (block.kind === 'chunk') {
        const chunk = chunks.find(item => 'chunk:' + item.id === block.id); assert.ok(chunk);
        assert.equal(chunk.introduction_id, owningIntroduction); assert.equal(block.html, chunk.html);
        visibleChunks.push(chunk.id);
      } else if (block.kind === 'question' && questionOwners.has(block.id.slice('question:'.length))) {
        assert.equal(owningIntroduction, questionOwners.get(block.id.slice('question:'.length)));
      }
    }
    assert.deepEqual(visibleChunks.sort(), chunks.map(item => item.id).sort());
    const questions = [...cards, ...(expected.modules || []).flatMap(module => [module.example, ...module.practices])];
    assert.deepEqual(content.blocks.filter(block => block.kind === 'question').map(block => block.id).sort(),
      questions.map(item => 'question:' + item.id).sort());
    for (const question of questions) {
      assert.equal(content.blocks.find(block => block.id === 'question:' + question.id)?.html, question.html);
      assert.equal(content.blocks.find(block => block.id === 'explanation:' + question.id)?.html, question.explanation_html);
    }
    assert.deepEqual(content.blocks.filter(block => block.kind === 'question'
      && questionOwners.has(block.id.slice('question:'.length))).map(block => block.id.slice('question:'.length)),
    orderedModuleQuestions);
    const inventory = (await api('materials' + scope)).items.filter(item => item.kind === 'atomic_card');
    assert.deepEqual(inventory.map(item => ({id: item.id, point_ids: item.point_ids})),
      cards.map(item => ({id: item.id, point_ids: item.point_ids})));
    await page.goto(base + '/#/subjects/' + metadata.subject_id + '/topic/' + topic.id + '/content', {waitUntil: 'networkidle'});
    await page.waitForFunction(count => document.querySelectorAll('.materialBlock').length === count, content.blocks.length);
    assert.deepEqual(await page.locator('.materialBlock').evaluateAll(items => items.map(item => item.dataset.contentBlock)),
      content.blocks.map(block => block.id));
    await shot(topic.id + '-content-directory');
    for (const id of introOrder) {
      await shot(topic.id + '-introduction-' + id, page.locator('[data-content-block=' + JSON.stringify('introduction:' + id) + ']'));
    }
    const sampledParadigms = [];
    for (const module of modules.values()) {
      for (const [label, block] of [['example', 'question:' + module.example.id],
        ['explanation', 'explanation:' + module.example.id],
        ['practice', 'question:' + module.practices[0].id],
        ['practice-explanation', 'explanation:' + module.practices[0].id]]) {
        await shot(topic.id + '-' + module.id + '-' + label,
          page.locator('[data-content-block=' + JSON.stringify(block) + ']'));
      }
      sampledParadigms.push({module_id: module.id, introduction_id: questionOwners.get(module.example.id),
        example_id: module.example.id, practice_id: module.practices[0].id,
        complete_practice_count: module.practices.length});
    }
    // This initial learning state is captured before administrator card previews.
    await page.goto(base + '/#/subjects/' + metadata.subject_id + '/topic/' + topic.id, {waitUntil: 'networkidle'});
    await page.waitForFunction(() => document.querySelector('.atomicMain, #stepCard'));
    assert.ok((await page.locator('body').innerText()).includes(topic.title));
    await shot(topic.id + '-initial');
    let state = await api('state' + scope);
    let firstParadigmFlow = null;
    if (modules.size && !cards.length) {
      const active = () => state.steps.find(step => step.id === state.active_step_id);
      assert.equal(active().kind, 'example'); assert.equal(active().phase, 'choice');
      const exampleId = active().question_id, moduleId = active().module_id;
      const action = async (route, extra = {}) => {
        state = await api(route + scope, {request_id: randomUUID(), expected_revision: state.revision,
          attempt_id: state.attempt_id, step_id: state.active_step_id, ...extra});
      };
      await action('choose', {choice: 'unmastered'});
      await action('continue');
      const guidance = [];
      while (active().kind === 'introduction') {
        assert.ok(guidance.length < introOrder.length);
        guidance.push(active().introduction_id);
        assert.equal(active().html, introductions.get(active().introduction_id).html);
        await page.reload({waitUntil: 'networkidle'});
        await shot(topic.id + '-learning-guidance-' + active().introduction_id, page.locator('#stepCard'));
        await action('continue');
      }
      assert.ok(guidance.includes(questionOwners.get(exampleId)));
      assert.equal(active().question_id, exampleId); assert.equal(active().phase, 'reading');
      assert.equal(active().explanation_html, modules.get(moduleId).example.explanation_html);
      await page.reload({waitUntil: 'networkidle'});
      await shot(topic.id + '-learning-example', page.locator('#stepCard'));
      await action('continue');
      assert.equal(active().kind, 'practice'); assert.equal(active().phase, 'answer');
      assert.ok(modules.get(moduleId).practices.some(question => question.id === active().question_id));
      await page.reload({waitUntil: 'networkidle'});
      await shot(topic.id + '-learning-practice', page.locator('#stepCard'));
      firstParadigmFlow = {module_id: moduleId, example_id: exampleId, introduction_ids: guidance,
        practice_id: active().question_id, unmastered_guidance_example_practice_verified: true,
        administrator_preview: true, prerequisite_gate_verified: false,
        answer_submitted: false, complete_learning_verified: false};
    }
    if (state.phase === 'introduction' || (topic.chunks && !state.chunk)) {
      const directAtomic = state.phase === 'introduction' && !state.steps;
      state = await api((directAtomic ? 'choose' : 'continue') + scope, {request_id: randomUUID(), expected_revision: state.revision,
        attempt_id: state.attempt_id, step_id: state.active_step_id, ...(directAtomic ? {choice: 'learn'} : {})});
      await page.reload({waitUntil: 'networkidle'});
    }
    if (topic.chunks) {
      await page.locator('.atomicChunkNote').waitFor();
      assert.ok(state.chunk?.anchors?.length || state.chunk?.introduction?.html || state.chunk);
      await shot(topic.id + '-chunk');
      state = await api('continue' + scope, {request_id: randomUUID(), expected_revision: state.revision,
        attempt_id: state.attempt_id, step_id: state.active_step_id});
      assert.ok(state.current?.card_id); assert.equal(state.chunk, null);
      await page.reload({waitUntil: 'networkidle'});
      await page.locator('.atomicAnswerLayout').waitFor();
      await shot(topic.id + '-flashcard');
    }
    const cardChunks = card => chunks.filter(chunk => chunk.points.some(point => card.point_ids.includes(point.id)));
    const selected = new Map();
    const single = cards.find(card => card.point_ids.length === 1);
    if (single) selected.set(single.id, single);
    const bundle = cards.filter(card => card.point_ids.length > 1).sort((a, b) => cardChunks(b).length - cardChunks(a).length)[0];
    if (bundle) selected.set(bundle.id, bundle);
    const missing = new Set(chunks.map(chunk => chunk.id));
    for (const card of selected.values()) for (const chunk of cardChunks(card)) missing.delete(chunk.id);
    while (missing.size) {
      const candidates = cards.filter(card => !selected.has(card.id)).map(card => ({card,
        gain: cardChunks(card).filter(chunk => missing.has(chunk.id)).length})).sort((a, b) => b.gain - a.gain);
      assert.ok(candidates[0]?.gain, 'Every chunk needs a reachable native card');
      const card = candidates[0].card; selected.set(card.id, card);
      for (const chunk of cardChunks(card)) missing.delete(chunk.id);
    }
    const renderedChunks = new Set(), sampledCards = [];
    for (const card of selected.values()) {
      // A hash navigation does not reload the page. The previous card can remain
      // visible while the new materials/open request is pending.
      const opened = page.waitForResponse(response => {
        const request = response.request();
        return new URL(response.url()).pathname === '/api/materials/open'
          && request.method() === 'POST' && request.postDataJSON()?.id === card.id;
      });
      await page.goto(base + '/#/subjects/' + metadata.subject_id + '/review/' + topic.id + '/atomic?card=' + encodeURIComponent(card.id),
        {waitUntil: 'networkidle'});
      const openedResponse = await opened;
      assert.equal(openedResponse.status(), 200);
      await page.locator('[data-help-block=' + JSON.stringify('question:' + card.id) + ']').waitFor();
      const preview = (await api('state' + scope)).current;
      assert.equal(preview.card_id, card.id); assert.deepEqual(preview.point_ids, card.point_ids);
      const expectedChunks = cardChunks(card);
      assert.deepEqual(preview.memory_chunks.map(item => item.id), expectedChunks.map(item => item.id));
      await shot(topic.id + '-card-' + card.id);
      await page.locator('.atomicPeekButton').click();
      for (const [index, chunk] of expectedChunks.entries()) {
        const actual = preview.memory_chunks[index], intro = introductions.get(chunk.introduction_id);
        assert.equal(actual.introduction.id, intro.id); assert.equal(actual.introduction.html, intro.html);
        assert.deepEqual(actual.anchors, chunk.anchors);
        if (expectedChunks.length > 1) await page.locator('.memoryChunkTab').nth(index).click();
        const displayed = page.locator('.memoryChunkDialog .atomicChunk');
        assert.equal(await displayed.getAttribute('data-chunk-id'), chunk.id);
        for (const [anchorIndex, anchor] of chunk.anchors.entries()) {
          const marks = displayed.locator('.atomicSourceMark[data-anchor-index="' + anchorIndex + '"]');
          assert.equal(await marks.evaluateAll(items => items.map(item => item.textContent).join('')), anchor.quote);
        }
        if (!renderedChunks.has(chunk.id)) {
          await shot(topic.id + '-anchor-' + chunk.id, page.locator('.memoryChunkDialog'));
          renderedChunks.add(chunk.id);
        }
      }
      await page.locator('.memoryChunkClose').click();
      sampledCards.push({id: card.id, point_ids: card.point_ids, chunk_ids: expectedChunks.map(chunk => chunk.id)});
    }
    assert.equal(renderedChunks.size, chunks.length);
    report.topics.push({id: topic.id, title: topic.title, initial_learning_phase: state.phase,
      content_blocks: content.blocks.length, introduction_order: introOrder, all_native_cards_verified: inventory.length,
      rendered_chunk_ids: [...renderedChunks], sampled_cards: sampledCards,
      sampled_paradigms: sampledParadigms, first_paradigm_flow: firstParadigmFlow,
      administrator_previews_are_not_learning_sequence: true});
  }
  assert.deepEqual(report.errors, []); report.status = 'pending_cleanup';
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
} catch (error) {
  report.status = 'failed'; report.failure = String(error);
  await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2)); throw error;
} finally {
  try {
    if (token) await context.request.post(base + '/api/access/logout', {headers: {Origin: base,
      'X-Learning-Session': session, 'X-Learning-Identity': token}, data: {}, timeout: 10000});
  } finally {
    try {
      processReceipt.before_close = await processes(); await saveProcessReceipt();
    } finally {
      let closeError;
      try { await context.close(); } catch (error) { closeError = String(error); }
      processReceipt.closed_at = new Date().toISOString();
      const fingerprints = [...new Map([...(processReceipt.after_launch?.owned || []), ...(processReceipt.before_close?.owned || [])]
        .map(item => [[item.pid, item.created_at, item.executable].join('|'), item])).values()];
      processReceipt.after_close = await processes(fingerprints);
      processReceipt.owned_processes_exited = processReceipt.after_close.known_alive.length === 0
        && processReceipt.after_close.roots.length === 0 && processReceipt.after_close.owned.length === 0;
      if (closeError) processReceipt.close_error = closeError;
      await saveProcessReceipt();
      if (closeError || !processReceipt.owned_processes_exited) {
        report.status = 'failed'; report.cleanup_failure = closeError || 'Owned browser fingerprints still running after context.close';
        await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
        throw new Error(report.cleanup_failure);
      }
    }
  }
}
report.status = 'passed';
await fs.writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
