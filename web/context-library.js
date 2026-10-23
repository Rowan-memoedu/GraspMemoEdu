import {ct, contextError} from './context-library-messages.js?v=d65b246d5f013ecd';
import {sourceAttributions} from './memory-chunk.js?v=d65b246d5f013ecd';
/* Shared reading component. Content is rendered as text, including dictionary output. */
const node = (tag, cls = '', text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};
const button = (label, action, cls = 'secondaryButton') => {
  const el = node('button', cls, label); el.type = 'button'; el.addEventListener('click', action); return el;
};

export function readingQuote(text, start, end) {
  const chars = [...text];
  return {exact: chars.slice(start, end).join(''), prefix: chars.slice(Math.max(0, start - 50), start).join(''),
    suffix: chars.slice(end, end + 50).join(''), start, end};
}

export function readingContextExport(data) {
  return {schema: 1, kind: 'reading-context-export', records: data.records.map(record => ({
    topic_id: data.topic_id, material_id: data.material.id, content_version: record.content_version || data.content_version,
    paragraph_id: record.paragraph_id, quote: record.quote, context: record.context, note: record.note || '',
    definitions: record.definitions || [], point_ids: record.point_ids || [], memory_status: record.memory_status || null,
    dictionary: record.dictionary || null,
    stale: !!record.stale, selected_sense_id: record.dictionary?.selected_sense_id || null,
    contextual_sense_selected: record.dictionary?.contextual_sense_selected === true,
  }))};
}

export function createContextLibraryView(bridge) {
  const root = bridge.root;
  let epoch = 0, selectionEpoch = 0, data = null, selection = null, detail = null, topic = null;
  let panel = null, records = null, busy = false, destroyed = false;
  const call = (path, options) => bridge.request(path, options);
  const suffix = () => new URLSearchParams({topic_id: topic, material_id: data.material.id}).toString();
  const findRecord = () => data.records.find(r => !r.stale && r.paragraph_id === selection.paragraph_id &&
    r.quote.start === selection.quote.start && r.quote.end === selection.quote.end);
  const actionStatus = record => record?.memory_status === 'learned' ? ct('已关联现有记忆，原有复习时间保持不变。')
    : record?.memory_status === 'pending' ? ct('已加入初学队列；完成对应练习后进入复习。')
    : record?.memory_status === 'candidate' ? ct('已保存为待审核候选，保留原文语境；尚未进入复习。') : '';
  function stop() {
    epoch++; selectionEpoch++; destroyed = true; data = null; selection = null; detail = null; panel = null;
  }
  function status(message, error = false) {
    const el = panel?.querySelector('[data-reading-status]');
    if (el) { el.textContent = message; el.dataset.state = error ? 'error' : 'success'; }
  }
  async function save(action) {
    if (!selection || busy) return;
    const ticket = selectionEpoch, page = epoch;
    const note = panel.querySelector('textarea')?.value || '';
    busy = true; panel.dataset.state = 'loading';
    panel.querySelectorAll('button').forEach(el => { el.disabled = true; });
    try {
      const record = await call(`context-library/marks?${suffix()}`, {method: 'POST', body: {...selection, action, note}});
      if (page !== epoch || ticket !== selectionEpoch) return;
      data.records = [...data.records.filter(r => r.id !== record.id), record];
      busy = false; renderDetail(); renderRecords();
      status(action === 'memory' ? actionStatus(record) : action === 'mark' ? ct('已标记疑惑。') : ct('已取消疑惑标记。'));
      bridge.progressChanged?.();
    } catch (error) {
      if (page === epoch && ticket === selectionEpoch) { busy = false; renderDetail(); status(contextError(error.message), true); }
    }
  }
  function renderDetail() {
    if (!panel || !selection) return;
    panel.replaceChildren(); panel.dataset.state = detail ? 'default' : 'loading';
    panel.append(node('h3', '', selection.quote.exact));
    if (!detail) { panel.append(node('p', 'inputHint', ct('正在查找释义…'))); return; }
    if (detail.definitions.length) {
      const list = node('ol', 'readingDefinitions');
      for (const definition of detail.definitions) {
        const item = node('li'); item.append(node('p', '', definition.meaning));
        if (definition.note) item.append(node('p', 'inputHint', definition.note));
        if (definition.lemmas?.length) item.append(node('p', 'inputHint', definition.lemmas.join(' · ')));
        if (definition.examples?.length) item.append(node('p', '', definition.examples.join('\n')));
        if (definition.relations?.length) {
          const relations = node('ul', 'readingRelations');
          for (const relation of definition.relations.slice(0, 30)) {
            const description = typeof relation === 'string' ? relation :
              [relation.kind || relation.type, relation.text || relation.lemma || relation.lemmas?.join(' / ')].filter(Boolean).join('：');
            if (description) relations.append(node('li', '', description));
          }
          item.append(relations);
        }
        list.append(item);
      }
      panel.append(list);
    } else panel.append(node('p', 'inputHint', ct(detail.dictionary?.status === 'not_available'
      ? '本地词典暂不可用，仍可保留语境。'
      : '当前材料和本地词库没有这个词义。可以记录疑惑，保留原文等待补充。')));
    const dictionarySource = ['available', 'not_found'].includes(detail.dictionary?.status) ? detail.dictionary.source : null;
    if (dictionarySource?.attribution) {
      const credit = node('details', 'readingAttribution');
      credit.append(node('summary', '', [dictionarySource.name, dictionarySource.version].filter(Boolean).join(' ')),
        node('p', 'inputHint', dictionarySource.attribution));
      if (dictionarySource.license) credit.append(node('p', 'inputHint', dictionarySource.license));
      if (dictionarySource.changes) credit.append(node('p', 'inputHint', Array.isArray(dictionarySource.changes)
        ? dictionarySource.changes.join(' ') : String(dictionarySource.changes)));
      const sources = node('p', 'readingSourceLinks');
      for (const [key, label] of [['url', ct('词典来源')], ['license_url', ct('数据许可')], ['underlying_license_url', ct('基础数据许可')]]) {
        try {
          const url = new URL(dictionarySource[key]);
          if (!['https:', 'http:'].includes(url.protocol)) continue;
          const link = node('a', '', label); link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
          sources.append(link, document.createTextNode(' '));
        } catch { /* Unavailable source links do not create a navigable URL. */ }
      }
      credit.append(sources);
      panel.append(credit);
    }
    const record = findRecord();
    const label = node('label', 'readingNoteLabel', ct('我的疑惑或语境笔记'));
    const note = node('textarea', 'answerInput'); note.rows = 2; note.maxLength = 2000;
    note.value = record?.note || ''; label.append(note); panel.append(label);
    const actions = node('div', 'readingActions');
    actions.append(button(record?.marked ? ct('取消疑惑标记') : ct('标记疑惑'), () => save(record?.marked ? 'unmark' : 'mark')));
    const memoryLabel = record?.memory_status ? ct('保存语境笔记') : detail.point_ids.length ? ct('加入记忆') : ct('保存候选');
    actions.append(button(memoryLabel, () => save('memory')));
    const learningUrl = record?.memory?.learning_url;
    if (['pending', 'learned'].includes(record?.memory_status) && typeof learningUrl === 'string' && learningUrl.startsWith('#/')) {
      const link = node('a', 'secondaryButton', record.memory_status === 'learned' ? ct('查看学习内容') : ct('开始对应学习'));
      link.href = learningUrl; actions.append(link);
    }
    panel.append(actions);
    const message = node('p', 'readingStatus', actionStatus(record)); message.dataset.readingStatus = 'true';
    message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite'); panel.append(message);
    if (!detail.point_ids.length) panel.append(node('p', 'inputHint', ct('候选需先关联或补充知识点与练习，再进入原有学习和复习流程。')));
  }
  async function select(values) {
    selection = values; detail = null; busy = false;
    const ticket = ++selectionEpoch, page = epoch;
    renderDetail();
    try {
      const result = await call(`context-library/lookup?${suffix()}`, {method: 'POST', body: values});
      if (ticket !== selectionEpoch || page !== epoch) return;
      detail = result; renderDetail();
    } catch (error) {
      if (ticket === selectionEpoch && page === epoch) {
        detail = {definitions: [], point_ids: []}; renderDetail(); status(contextError(error.message), true);
      }
    }
  }
  function renderRecords() {
    if (!records) return;
    records.replaceChildren(node('h3', '', ct('保留的语境')));
    if (!data.records.length) { records.append(node('p', 'inputHint', ct('还没有标记。点击带下划线的词，或选中一段文字查词。'))); return; }
    records.append(button(ct('导出本篇语境'), () => {
      const blob = new Blob([JSON.stringify(readingContextExport(data), null, 2)], {type: 'application/json'});
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = `reading-context-${data.topic_id}-${data.material.id}.json`;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }));
    records.append(node('p', 'inputHint', ct('导出仅包含本人的当前材料记录。字典义项尚未自动选定。')));
    const list = node('ul', 'readingRecords');
    for (const record of data.records) {
      const item = node('li');
      if (record.stale) {
        item.append(node('strong', '', record.quote.exact), node('p', 'inputHint', ct('旧版语境：保留原文与笔记，不定位到当前正文。')));
        const original = node('details'); original.append(node('summary', '', ct('查看保留的原文')),
          node('p', 'readingOriginalContext', record.context)); item.append(original);
      } else item.append(button(record.quote.exact, () => select({content_version: data.content_version,
        paragraph_id: record.paragraph_id, quote: record.quote}), 'textButton'));
      item.append(node('span', 'inputHint', record.marked ? ct(' · 疑惑') : ''));
      if (record.memory_status && !record.stale) item.append(node('p', 'inputHint', actionStatus(record)));
      if (record.note) item.append(node('p', '', record.note));
      list.append(item);
    }
    records.append(list);
  }
  function renderMaterial() {
    root.replaceChildren();
    const view = node('section', 'contextLibrary');
    view.append(button(ct('返回阅读材料'), () => show(), 'textButton'));
    view.append(node('h1', 'lessonTitle', data.material.title));
    view.append(node('p', 'inputHint', ct('点击带下划线的词查看释义；也可以选中文字后查词并保留疑惑。')));
    if (data.preview) view.append(node('p', 'inputHint', ct('管理员临时预览：阅读标记不写入正式学习记录。')));
    const columns = node('div', 'readingColumns'), article = node('article', 'readingArticle');
    for (const paragraph of data.material.paragraphs) {
      const el = node('p'); el.dataset.paragraph = paragraph.id;
      const chars = [...paragraph.text]; let start = 0;
      for (const annotation of data.material.annotations.filter(a => a.paragraph_id === paragraph.id).sort((a, b) => a.start - b.start)) {
        el.append(document.createTextNode(chars.slice(start, annotation.start).join('')));
        const word = button(chars.slice(annotation.start, annotation.end).join(''), () => select({
          content_version: data.content_version, paragraph_id: paragraph.id,
          quote: readingQuote(paragraph.text, annotation.start, annotation.end)}), 'readingWord');
        word.setAttribute('aria-label', ct('查词：{word}', {word: word.textContent})); el.append(word); start = annotation.end;
      }
      el.append(document.createTextNode(chars.slice(start).join('')));
      article.append(el);
    }
    let captured = null;
    const lookup = button(ct('查所选文字'), () => { if (captured) select(captured); }); lookup.disabled = true;
    lookup.addEventListener('pointerdown', event => event.preventDefault());
    function capture() {
      const current = document.getSelection(); captured = null;
      if (current?.rangeCount && !current.isCollapsed) {
        const range = current.getRangeAt(0);
        const blockOf = n => (n.nodeType === Node.ELEMENT_NODE ? n : n.parentElement)?.closest('[data-paragraph]');
        const block = blockOf(range.startContainer);
        if (block && article.contains(block) && block === blockOf(range.endContainer)) {
          const exact = range.cloneContents().textContent;
          const before = document.createRange(); before.selectNodeContents(block); before.setEnd(range.startContainer, range.startOffset);
          const start = [...before.cloneContents().textContent].length, end = start + [...exact].length;
          const paragraph = data.material.paragraphs.find(p => p.id === block.dataset.paragraph);
          if (exact.trim() && [...exact].length <= 2000) captured = {content_version: data.content_version,
            paragraph_id: paragraph.id, quote: readingQuote(paragraph.text, start, end)};
        }
      }
      lookup.disabled = !captured;
    }
    article.addEventListener('pointerup', capture); article.addEventListener('keyup', capture);
    panel = node('aside', 'readingPanel'); panel.setAttribute('aria-label', ct('词义与语境'));
    panel.append(node('p', 'inputHint', ct('选择词语后，释义和记录操作会显示在这里。')));
    const main = node('div'); main.append(article, lookup); columns.append(main, panel); view.append(columns);
    const sources = sourceAttributions(data.material.source ? [{text: data.material.source}] : []);
    if (sources) view.append(sources);
    records = node('section', 'readingSaved'); view.append(records); root.append(view); renderRecords();
  }
  async function show(topicId = null, materialId = null) {
    stop(); destroyed = false; topic = topicId; const ticket = epoch;
    root.replaceChildren(node('p', 'inputHint', ct('正在读取阅读材料…')));
    try {
      if (topicId && materialId) {
        data = await call('context-library/material?' + new URLSearchParams({topic_id: topicId, material_id: materialId}));
        if (ticket !== epoch || destroyed) return;
        renderMaterial();
      } else {
        const result = await call('context-library' + (topicId ? '?topic_id=' + encodeURIComponent(topicId) : ''));
        if (ticket !== epoch || destroyed) return;
        const view = node('section', 'contextLibrary'); view.append(node('h1', 'lessonTitle', ct('阅读材料')));
        if (!result.items.length) view.append(node('p', 'inputHint', ct('当前课程还没有阅读材料。可以先从学习页进入课程。')));
        const list = node('ul', 'readingMaterialList');
        for (const item of result.items) {
          const row = node('li'); row.append(button(item.title, () => show(item.topic_id, item.id), 'textButton'));
          row.append(node('p', 'inputHint', item.topic_title)); list.append(row);
        }
        view.append(list); root.replaceChildren(view);
      }
    } catch (error) {
      if (ticket === epoch && !destroyed) {
        const failure = node('section', 'contextLibrary'); failure.append(node('p', 'readingStatus', contextError(error.message)),
          button(ct('重新读取'), () => show(topicId, materialId))); root.replaceChildren(failure);
      }
    }
  }
  return {show, stop};
}
