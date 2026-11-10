import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createRequire} from 'node:module';
import {before, after, beforeEach, afterEach, test} from 'node:test';

const require = createRequire(path.join(process.env.PLAYWRIGHT_NODE_MODULES || path.join(os.homedir(),
  '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'), '_entry.cjs'));
const {chromium} = require('playwright');
const web = new URL('../web/', import.meta.url);
const evidence = process.env.PASSAGE_LAYOUT_EVIDENCE;
let browser, page;
before(async () => {
  browser = await chromium.launch({headless: true, ignoreDefaultArgs:['--hide-scrollbars'], executablePath: process.env.CHROME_EXECUTABLE
    || path.join(os.homedir(), 'AppData/Local/Google/Chrome/Application/chrome.exe')});
  if (evidence) await fs.mkdir(evidence, {recursive: true});
});
after(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage({viewport: {width: 1365, height: 1000}, reducedMotion: 'reduce'});
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, 'http://passage-layout.test', 'Acceptance must remain offline');
    if (url.pathname === '/') return route.fulfill({contentType: 'text/html', body: `<!doctype html><html lang="zh-CN">
      <meta charset="utf-8"><link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/portal.css">
      <link rel="stylesheet" href="/topic-content.css"><link rel="stylesheet" href="/training.css">
      <body><main class="pageShell"><article id="question" class="step"></article></main></body></html>`});
    if (url.pathname.includes('..')) return route.abort();
    try { return route.fulfill({body: await fs.readFile(new URL('.'+url.pathname, web)),
      contentType: /\.m?js$/.test(url.pathname) ? 'text/javascript' : url.pathname.endsWith('.css') ? 'text/css' : undefined}); }
    catch { return route.abort(); }
  });
  await page.goto('http://passage-layout.test/');
});
afterEach(async () => { await page?.close(); });

const options = Array.from({length: 4}, (_, i) => ({id: 'o'+i, text: ['prepared', 'remembered', 'explained', 'noticed'][i]}));
const controls = Array.from({length: 12}, (_, i) => ({id: 'gap'+i, label: String(41+i), type: 'select', options}));
const cloze = {html: controls.map((c, i) => `<p>The visitor read the notice and {{blank:${c.id}}} the changes before continuing along the path ${i+1}.</p>`).join(''),
  interaction: {version: 2, type: 'composite', grading: 'exact', controls,
    sections: [{id: 'passage', complete: true, control_ids: controls.map(c => c.id), html: '<p>Choose the best option for each gap.</p>'}]}};
async function mount(question = cloze, {subject = 'english', disabled = false, value = '', showExplanation = false, referenceSource = question} = {}) {
  await page.evaluate(async ({question, subject, disabled, value, showExplanation, referenceSource}) => {
    document.body.dataset.subject = subject;
    const {applySubjectTheme} = await import('/subjects.js'); applySubjectTheme({id: subject});
    const {renderReaderStep, answerEditor} = await import('/learning-ui.js');
    const {answerReady} = await import('/question-input.js');
    const main = document.getElementById('question');
    const {stem} = renderReaderStep(main, {...question, answer: null}, {title: '篇章练习', showSubmittedInteraction: disabled,
      submittedValue: value, showAnswer: false, showExplanation, referenceSource, decorate: (body, kind) => {
        if (kind === 'question') {
          const actions = document.createElement('div'); actions.className = 'contentReportActions';
          actions.innerHTML = '<button type="button">加入错题本</button><button type="button">答疑反馈</button>'; body.prepend(actions);
        }
        return body;
      }});
    window.submits = 0;
    if (!disabled) {
      const editor = answerEditor(question, stem, value, {formId: 'answers', inputId: 'saved'});
      editor.form.append(editor.bottom); main.append(editor.form); window.widget = editor;
      editor.input.addEventListener('input', () => editor.submit.disabled = !answerReady(editor.input));
      editor.form.addEventListener('submit', event => {event.preventDefault(); window.submits++;});
    }
  }, {question, subject, disabled, value, showExplanation, referenceSource});
}

test('filled selections show only wrapping content and hide the gap number', async () => {
  const sentence = 'The visitor returned to the workshop because the familiar view reminded her of a quiet summer spent with her family.';
  const question = structuredClone(cloze);
  question.interaction.controls[0].options[0].text = sentence;
  await mount(question);
  await page.locator('[data-passage-id="gap0"] select').selectOption('o0');
  const gap = page.locator('[data-passage-id="gap0"]');
  assert.equal(await gap.locator('.passageBlankDisplay').textContent(), sentence);
  assert.equal(await gap.locator('.passageBlankNumber').isVisible(), false);
  assert((await gap.locator('.passageBlankDisplay').evaluate(el => el.getBoundingClientRect().height)) > 30);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.locator('[data-answer-control="gap0"] input[value="o2"]').check();
  assert.equal(await gap.locator('.passageBlankDisplay').textContent(), 'explained');
  if (evidence) await page.screenshot({path:path.join(evidence,'filled-cloze.png'),fullPage:true});
});

test('grammar fields grow for words, phrases and sentences and shrink after deletion', async () => {
  await page.setViewportSize({width:768,height:1000});
  await mount({html:'<p>Before the answer {{blank:g}} and after the answer.</p><ol start="3"><li>Indented <em>Markdown</em><ul><li>Nested detail</li></ul></li></ol><p>\tA tabbed line.</p>',
    interaction:{type:'fill_blank',blanks:[{id:'g',label:'61'}]}});
  const field=page.locator('.passageGrowingText');
  const sizes=[];
  for(const value of ['word','a longer phrase','A complete sentence with many words that must remain visible inside the passage without clipping or hiding its ending. '.repeat(3)]) {
    await field.fill(value);
    sizes.push(await field.evaluate(el=>({width:el.clientWidth,height:el.clientHeight,scroll:el.scrollHeight,overflow:el.scrollWidth>el.clientWidth})));
    assert.equal(await page.locator('.passageBlankNumber').isVisible(),false);
  }
  assert(sizes[1].width>sizes[0].width);
  assert(sizes[2].height>sizes[1].height && sizes[2].scroll<=sizes[2].height+2 && !sizes[2].overflow,JSON.stringify(sizes));
  assert((await page.locator('#question').textContent()).includes('and after the answer.'));
  assert.equal(await page.locator('ol').getAttribute('start'),'3');
  assert.equal(await page.locator('ol ul li').textContent(),'Nested detail');
  assert((await field.evaluate(el=>getComputedStyle(el).fontFamily)).includes('Georgia'));
  if(evidence)await page.screenshot({path:path.join(evidence,'grammar-long-sentence.png'),fullPage:true});
  await field.fill('');assert.equal(await page.locator('.passageBlankNumber').isVisible(),true);
  assert((await field.evaluate(el=>el.clientHeight))<sizes[2].height);
});

test('seven-choice and grammar references use server feedback without inventing letter prefixes', async () => {
  const seven=structuredClone(cloze);seven.interaction.controls=controls.slice(0,2).map(c=>({...c,options:Array.from({length:7},(_,i)=>({id:'s'+i,text:'A complete sentence for option '+(i+1)+'.'}))}));
  seven.interaction.sections[0].control_ids=['gap0','gap1'];seven.html='<p>Opening {{blank:gap0}} continuation {{blank:gap1}} ending.</p>';
  let source={question:seven,reference_answer:'source',choice_feedback:{items:[{id:'gap0',correct_ids:['s5'],correct:true},{id:'gap1',correct_ids:['s5'],correct:false}]}};
  await mount(seven,{disabled:true,value:JSON.stringify({section:0,values:{gap0:'s5',gap1:'s1'}}),showExplanation:true,referenceSource:source});
  assert.deepEqual(await page.locator('.referenceItemAnswer').allTextContents(),['F','F · A complete sentence for option 6.']);
  assert.equal(await page.locator('.assessmentCorrect').count(),1);assert.equal(await page.locator('.assessmentIncorrect').count(),1);
  const grammar={html:'<p>He {{blank:a}} there {{blank:b}}.</p>',interaction:{type:'fill_blank',blanks:[{id:'a',label:'61'},{id:'b',label:'62'}]}};
  source={question:grammar,reference_answer:'source',choice_feedback:{items:[{id:'a',answer:'went',correct:true},{id:'b',answer:'yesterday',correct:false}]}};
  await page.reload();await mount(grammar,{disabled:true,value:JSON.stringify({a:'went',b:'today'}),showExplanation:true,referenceSource:source});
  assert.deepEqual(await page.locator('.referenceItemAnswer').allTextContents(),['went','yesterday']);
  assert.equal(await page.locator('.referenceLetter').count(),0);
  assert.equal(await page.locator('.passageBlankNumber:visible').count(),0);
  assert.deepEqual(await page.locator('.passageBlank').evaluateAll(nodes=>nodes.map(el=>el.dataset.answerTruth)),['true','false']);
});

test('native English selection remains keyboard-editable after its text is shown', async () => {
  await mount();const field=page.locator('[data-passage-id="gap0"] select');
  await field.selectOption('o0');await field.focus();await field.press('ArrowDown');
  assert.equal(await field.inputValue(),'o1');
  assert.equal(await page.locator('[data-passage-id="gap0"] .passageBlankDisplay').textContent(),'remembered');
});

test('relocated content actions keep handlers and refresh permissions without duplication', async () => {
  await page.evaluate(async()=>{
    const moduleSource=await (await fetch('/learning-ui.js')).text();
    const actionModule=moduleSource.match(/from '([^']*content-report[^']*)'/)[1];
    const {installHelpRequests,helpableContent}=await import(new URL(actionModule,location.href));
    const {renderReaderStep}=await import('/learning-ui.js');
    window.allowed=true;window.helpCalls=[];
    window.helpUI=installHelpRequests(()=>({features:allowed?['request_help']:[]}),value=>helpCalls.push(value));
    renderReaderStep(document.getElementById('question'),{html:'<p>Passage</p>'},{title:'Test',showAnswer:false,showExplanation:false,
      decorate:body=>helpableContent(body,{topic_id:'training.test'},'question:q','version')});
    helpUI.refresh();helpUI.refresh();
  });
  assert.equal(await page.locator('.questionActions [data-content-actions]').count(),1);
  assert.equal(await page.locator('.courseContent [data-content-actions]').count(),0);
  await page.locator('.questionActions [data-help-action="help"]').click();
  assert.equal(await page.evaluate(()=>helpCalls[0].content_block_id),'question:q');
  await page.evaluate(()=>{allowed=false;helpUI.refresh();});assert.equal(await page.locator('[data-content-actions]').count(),0);
  await page.evaluate(()=>{allowed=true;helpUI.refresh();helpUI.refresh();});assert.equal(await page.locator('.questionActions [data-content-actions]').count(),1);
});

test('shared feedback colors options and English references according to server item results', async () => {
  const values=Object.fromEntries(controls.map((c,i)=>[c.id,i===0?'o0':'o1']));
  const source={question:cloze,reference_answer:'source answer',choice_feedback:{items:controls.map(c=>({id:c.id,correct_ids:['o0']}))},
    result:{assessment_result:{items:controls.map((c,i)=>({id:c.id,correct:i===0}))}}};
  await mount(cloze,{disabled:true,value:JSON.stringify({section:0,values}),showExplanation:true,referenceSource:source});
  assert.equal(await page.locator('.referenceItemAnswer').first().textContent(),'A');
  assert.equal(await page.locator('.referenceItemAnswer').nth(1).textContent(),'A · prepared');
  assert.equal(await page.locator('.referenceItemAnswer[data-answer-truth="true"]').count(),1);
  assert.equal(await page.locator('[data-answer-control="gap1"] .choiceOption[data-answer-truth="false"]').textContent(),'Bremembered');
  assert.equal(await page.locator('[data-answer-control="gap1"] .choiceOption[data-answer-truth="true"]').textContent(),'Aprepared');
  await page.evaluate(async ({result,spec})=>{
    const {pointAssessment}=await import('/point-assessment.js');document.getElementById('question').append(pointAssessment(result,spec));
  },{result:source.result.assessment_result,spec:cloze.interaction});
  const colors=await page.evaluate(()=>['.assessmentItemLabel','.assessmentCorrect','.assessmentIncorrect','.referenceItemNumber','.referenceItemAnswer[data-answer-truth="true"]','.referenceItemAnswer[data-answer-truth="false"]'].map(s=>getComputedStyle(document.querySelector(s)).color));
  assert.equal(colors[0],colors[3]);assert.equal(colors[1],colors[4]);assert.equal(colors[2],colors[5]);assert.notEqual(colors[1],colors[2]);assert.notEqual(colors[0],colors[1]);
  if(evidence)await page.screenshot({path:path.join(evidence,'mixed-feedback.png'),fullPage:true});
  for(const subject of ['math','physics','biology','chemistry','chinese','cs']) {
    await page.reload();await mount(cloze,{subject,disabled:true,value:JSON.stringify({section:0,values}),showExplanation:true,referenceSource:source});
    assert.equal(await page.locator('[data-answer-control="gap1"] .choiceOption[data-answer-truth="false"]').count(),1);
  }
});

test('empty native dropdowns contain only real options and preserve one shared answer payload', async () => {
  await mount();
  assert.deepEqual(await page.locator('.passageBlankSelect').first().evaluate(field => ({index: field.selectedIndex,
    values: [...field.options].map(o => o.value), text: field.selectedOptions[0]?.textContent || ''})),
  {index: -1, values: options.map(o => o.id), text: ''});
  await page.locator('[data-passage-id="gap0"] select').selectOption('o2');
  assert.equal(await page.locator('[data-answer-control="gap0"] input:checked').inputValue(), 'o2');
  await page.locator('[data-answer-control="gap0"] input[value="o1"]').check();
  assert.equal(await page.locator('[data-passage-id="gap0"] select').inputValue(), 'o1');
  assert.equal(await page.evaluate(() => new FormData(widget.form).getAll('answer').length), 1);
  assert.equal(await page.locator('#answers .contentReportActions').count(), 0);
  assert.equal(await page.locator('.passageQuestionPane .contentReportActions').count(), 0);
  assert.equal(await page.locator('.stepHeader .contentReportActions').count(), 1);
  const saved = await page.evaluate(() => widget.input.value);
  await page.reload(); await mount(cloze, {value: saved});
  assert.equal(await page.locator('[data-passage-id="gap0"] select').inputValue(), 'o1');
  for (const control of controls) await page.locator(`[data-passage-id="${control.id}"] select`).selectOption('o1');
  await page.locator('[data-passage-id="gap0"] select').press('Control+Enter');
  assert.equal(await page.evaluate(() => submits), 1, 'The whole passage submits once');
});

test('literal reading prompts move beside their options without adding dropdown blanks or jump links', async () => {
  const question={...cloze,html:'<p>A passage about a workshop.</p>'+controls.map(c=>`<p>${c.label}. What does the passage say?</p>`).join('')};
  await mount(question);
  assert.equal(await page.locator('.passageQuestionPane .passageNumberLink').count(),0);
  assert.equal(await page.locator('.passageQuestionNav').count(),0);
  assert.equal(await page.locator('.passageBlankSelect').count(),0);
  assert.equal(await page.locator('#answers legend').first().textContent(),'41. What does the passage say?');
  assert(!(await page.locator('.passageQuestionPane').textContent()).includes('What does'));
});

test('ambiguous numbered prose stays literal without invented navigation links', async () => {
  await mount({...cloze,html:'<p>41. Original statement.</p><p>41. Repeated reference.</p>'});
  assert.equal(await page.locator('.passageQuestionPane p .passageNumberLink').count(),0);
  assert.equal(await page.locator('[data-passage-target="gap0"]').count(),0);
});

test('compiled ordered-list questions retain their original numbers beside options', async () => {
  await mount({...cloze,html:'<p>Reading passage.</p><ol start="41">'+controls.map(()=>'<li><p>Which statement is supported?</p></li>').join('')+'</ol>'});
  assert.equal(await page.locator('.passageQuestionPane ol').count(),0);
  assert.equal(await page.locator('.passageQuestionNav').count(),0);
  assert.equal(await page.locator('#answers legend').first().textContent(),'41. Which statement is supported?');
  assert.equal(await page.locator('#answers legend').last().textContent(),'52. Which statement is supported?');
});

for (const width of [320, 375, 414, 768, 1365]) test(`two independent columns and centered navigation at ${width}px`, async () => {
  await page.setViewportSize({width, height: 1000}); await mount();
  const geometry = await page.evaluate(() => {
    const left = document.querySelector('.passageQuestionPane'), right = document.getElementById('answers');
    const a = left.getBoundingClientRect(), b = right.getBoundingClientRect();
    return {a: {x: a.x, right: a.right, y: a.y}, b: {x: b.x, y: b.y},
      leftScroll: left.scrollHeight > left.clientHeight, rightScroll: right.scrollHeight > right.clientHeight,
      overflow: document.documentElement.scrollWidth > innerWidth};
  });
  assert(geometry.a.right <= geometry.b.x && Math.abs(geometry.a.y - geometry.b.y) < 1);
  assert(geometry.leftScroll && geometry.rightScroll); assert(!geometry.overflow);
  for (const id of ['gap0', 'gap6', 'gap11']) {
    const button = page.locator(`[data-passage-target="${id}"]`);
    await button.scrollIntoViewIfNeeded();
    const before = await page.evaluate(() => ({left: document.querySelector('.passageQuestionPane').scrollTop, outer: scrollY,
      value: widget.input.value}));
    await button.click();
    const after = await page.evaluate(id => {
      const target = document.querySelector(`[data-answer-control="${id}"]`).getBoundingClientRect();
      const pane = document.getElementById('answers').getBoundingClientRect();
      return {left: document.querySelector('.passageQuestionPane').scrollTop, outer: scrollY,
        value: widget.input.value, center: Math.abs(target.y + target.height/2 - pane.y - pane.height/2)};
    }, id);
    assert.equal(after.left, before.left); assert.equal(after.outer, before.outer); assert.equal(after.value, before.value);
    assert(after.center < 3, JSON.stringify({width, id, ...after}));
  }
  if (evidence) await page.screenshot({path: path.join(evidence, `cloze-${width}.png`), fullPage: true});
});

test('reading section prose stays left while numbered comprehension questions and all options stay right', async () => {
  const reading = structuredClone(cloze); reading.html = '<p>Read the following passage.</p>';
  reading.interaction.controls = controls.slice(0, 4).map((c, i) => ({...c, label: `${21+i}. Why did the visitor return?`}));
  reading.interaction.sections[0] = {id: 'reading', complete: true, control_ids: controls.slice(0,4).map(c => c.id),
    html: '<p>The museum reopened after the conservation team repaired its windows.</p>'.repeat(20)};
  await mount(reading);
  assert((await page.locator('.passageQuestionPane').textContent()).includes('conservation team'));
  assert(!(await page.locator('#answers').textContent()).includes('conservation team'));
  assert.equal(await page.locator('.passageQuestionPane .passageNumberLink,.passageQuestionNav').count(), 0);
  assert(!(await page.locator('.passageQuestionPane').textContent()).includes('Why did the visitor'));
  assert.equal(await page.locator('#answers legend').first().textContent(), '21. Why did the visitor return?');
  assert.equal(await page.locator('#answers fieldset').count(), 4);
  if (evidence) await page.screenshot({path: path.join(evidence, 'reading.png'), fullPage: true});
});

test('seven-choice passage uses the same split and number links without altering option IDs', async () => {
  const seven = structuredClone(cloze);
  seven.interaction.controls = controls.slice(0,5).map((c, i) => ({...c, label: String(36+i),
    options: Array.from({length:7}, (_, k) => ({id: 'sentence'+k, text: `Sentence option ${k+1}.`}))}));
  seven.interaction.sections[0].control_ids = seven.interaction.controls.map(c => c.id);
  seven.html = seven.interaction.controls.map(c => `<p>The paragraph continues here. {{blank:${c.id}}} It then gives another detail.</p>`).join('');
  await mount(seven);
  assert.equal(await page.locator('.passageNumberLink').count(), 5);
  assert.equal(await page.locator('#answers input[type="radio"]').count(), 35);
  await page.locator('[data-passage-id="gap0"] select').selectOption('sentence6');
  assert.equal(await page.locator('[data-answer-control="gap0"] input:checked').inputValue(), 'sentence6');
});

test('legacy seven-choice matching gaps are blank and navigate to their right-hand pairing rows', async () => {
  const matching = {html:'<p>Choose a sentence for each gap.</p><p>We left home. (36) The road was quiet.</p><p>We arrived. (37) The garden was open.</p>',
    interaction:{version:1,type:'matching',fixed_side:'left',row_count:2,fixed:['a','b'],direction:'right',
      left:[{id:'a',text:'(36)'},{id:'b',text:'(37)'}],right:options}};
  await mount(matching);
  assert.deepEqual(await page.locator('.passageBlankValue').allTextContents(),['','']);
  await page.locator('[data-passage-target="b"]').click();
  assert.equal(await page.locator('#answers .passageAnswerCurrent').getAttribute('data-answer-control'),'b');
  assert.equal(await page.locator('#answers .contentReportActions').count(),0);
});

for (const subject of ['math', 'physics', 'biology', 'chemistry', 'chinese', 'cs']) test(`${subject} ordinary text and select blanks never acquire numbers or split layout`, async () => {
  await mount({html: '<p>The result is {{blank:x}}.</p>', interaction: {type: 'fill_blank', blanks: [{id:'x', label:'1'}]}}, {subject});
  assert.equal(await page.locator('.passageBlankNumber').count(), 0); assert.equal(await page.locator('#question input').count(), 1);
  await page.reload(); await mount(cloze, {subject});
  assert.equal(await page.locator('.passageBlankNumber,.passageQuestionPane').count(), 0);
  assert.equal(await page.locator('.passageBlankSelect').count(), 12);
});

test('English grammar keeps authored gap numbers; ordinary English blanks have none; submitted controls stay disabled', async () => {
  const grammar = {html: '<p>He {{blank:g61}} the display {{blank:g62}}.</p>',
    interaction: {type:'fill_blank', blanks:[{id:'g61',label:'61'},{id:'g62',label:'62'}]}};
  await mount(grammar);
  assert.deepEqual(await page.locator('.passageBlankNumber').allTextContents(), ['61','62']);
  assert.equal(await page.locator('.passageQuestionPane').count(), 0);
  await page.reload(); await mount({html:'<p>Result: {{blank:x}}</p>', interaction:{type:'fill_blank',blanks:[{id:'x'}]}});
  assert.equal(await page.locator('.passageBlankNumber').count(), 0);
  await page.reload(); await mount(cloze, {disabled:true, value:JSON.stringify({section:0,values:{gap0:'o1'}})});
  assert.equal(await page.locator('input:not(:disabled),select:not(:disabled)').count(), 0);
  assert.equal(await page.locator('[data-passage-id="gap0"] select').inputValue(), 'o1');
});

test('existing field states preserve geometry and number links have keyboard focus and sufficient contrast', async () => {
  await mount();
  const checks = await page.locator('.passageBlank').first().evaluate(wrapper => {
    const states = ['default','hover','focus','active','disabled','loading','error','success'];
    const sizes = states.map(state => {
      wrapper.className = 'passageBlank' + (state === 'default' ? '' : ' is-'+state);
      const rect = wrapper.getBoundingClientRect(); return [rect.width, rect.height];
    });
    wrapper.className = 'passageBlank';
    const button = wrapper.querySelector('button'); button.focus();
    const rgb = getComputedStyle(button).color.match(/[\d.]+/g).slice(0,3).map(Number);
    const light = rgb.map(n => {n/=255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4;})
      .reduce((sum,n,i)=>sum+n*[.2126,.7152,.0722][i],0);
    return {sizes, contrast:1.05/(light+.05), width:button.getBoundingClientRect().width,
      height:button.getBoundingClientRect().height, focused:document.activeElement===button};
  });
  assert(checks.sizes.every(size => size.every((value,index) => value===checks.sizes[0][index])));
  assert(checks.contrast>=4.5 && checks.width>=44 && checks.height>=44 && checks.focused);
  if (evidence) {
    const css = await fs.readFile(new URL('styles.css', web), 'utf8');
    await fs.writeFile(path.join(evidence,'passage-states.preview.html'), `<!doctype html><meta charset="utf-8"><title>填空控件状态</title><style>${css}</style>`
      + ['default','hover','focus','active','disabled','loading','error','success'].map(state => `<p>${state}: <span class="passageBlank is-${state}"><button class="passageNumberLink" type="button">41</button><select class="passageBlankField passageBlankSelect" ${state==='disabled'?'disabled':''}><option value="a">A · prepared</option></select></span></p>`).join(''));
  }
});

if (process.env.PASSAGE_SOURCE_BATCH) test('real imported cloze and grammar use the shared reader without editing source content', async () => {
  for (const kind of ['cloze','grammar']) {
    const bank = JSON.parse(await fs.readFile(path.join(process.env.PASSAGE_SOURCE_BATCH, kind, 'bank.compiled.json'), 'utf8'));
    for (const index of [0, bank.groups.length-1]) {
      const q = bank.groups[index].questions[0];
      await page.reload(); await mount(q);
      assert.equal(await page.locator('.passageBlankNumber').count(), kind === 'cloze' ? q.interaction.controls.length : q.interaction.blanks.length);
      assert.equal(await page.locator('.passageQuestionPane').count(), kind === 'cloze' ? 1 : 0);
      if (evidence) await page.screenshot({path:path.join(evidence, `${kind}-source-${index}.png`),fullPage:true});
    }
  }
});
