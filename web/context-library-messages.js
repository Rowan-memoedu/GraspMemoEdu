import {getLanguage, translateMessage} from './i18n.js?v=031263bdf36bd1a1';

const pairs = [
  ['旧版语境：保留原文与笔记，不定位到当前正文。', 'Earlier version: original text and notes are preserved without locating them in the current text.'],
  ['查看保留的原文', 'View saved original text'],
  ['词典来源', 'Dictionary source'], ['数据许可', 'Data license'], ['基础数据许可', 'Underlying data license'],
  ['导出本篇语境', 'Export contexts'],
  ['导出仅包含本人的当前材料记录。字典义项尚未自动选定。', 'The export contains only your records for this material. Dictionary senses are not automatically selected.'],
  ['已关联现有记忆，原有复习时间保持不变。', 'Linked to your existing memory. Its review schedule is unchanged.'],
  ['已加入初学队列；完成对应练习后进入复习。', 'Added to initial learning. Complete the exercises before scheduled review.'],
  ['已保存为待审核候选，保留原文语境；尚未进入复习。', 'Saved with its context for review. It is not scheduled for practice yet.'],
  ['已标记疑惑。', 'Question marked.'], ['已取消疑惑标记。', 'Question mark removed.'],
  ['正在查找释义…', 'Looking up meanings…'],
  ['本地词典暂不可用，仍可保留语境。', 'The local dictionary is temporarily unavailable. You can still save the context.'],
  ['当前材料和本地词库没有这个词义。可以记录疑惑，保留原文等待补充。', 'No meaning is available in this material or the local dictionary. Save the context for later clarification.'],
  ['我的疑惑或语境笔记', 'My question or context note'], ['取消疑惑标记', 'Unmark question'], ['标记疑惑', 'Mark question'],
  ['保存语境笔记', 'Save context note'], ['加入记忆', 'Add to memory'], ['保存候选', 'Save candidate'],
  ['查看学习内容', 'View lesson'], ['开始对应学习', 'Start learning'],
  ['候选需先关联或补充知识点与练习，再进入原有学习和复习流程。', 'A candidate needs a linked or new knowledge point and exercises before it enters learning and review.'],
  ['保留的语境', 'Saved contexts'], ['还没有标记。点击带下划线的词，或选中一段文字查词。', 'No marks yet. Select an underlined word or highlight text to look it up.'],
  [' · 疑惑', ' · Question'], ['返回阅读材料', 'Reading library'],
  ['点击带下划线的词查看释义；也可以选中文字后查词并保留疑惑。', 'Select an underlined word for its meaning, or highlight text to look it up and save a question.'],
  ['管理员临时预览：阅读标记不写入正式学习记录。', 'Temporary admin preview: marks are not saved to learner records.'],
  ['查词：{word}', 'Look up: {word}'], ['查所选文字', 'Look up selection'], ['词义与语境', 'Meaning and context'],
  ['选择词语后，释义和记录操作会显示在这里。', 'Select a word to see meanings and save its context.'],
  ['正在读取阅读材料…', 'Loading reading materials…'], ['阅读材料', 'Reading library'],
  ['当前课程还没有阅读材料。可以先从学习页进入课程。', 'This course has no reading materials yet. Open a lesson from the Learning page.'],
  ['重新读取', 'Reload'], ['听说练习', 'Listening and speaking'], ['加载题目音频', 'Load audio'],
  ['正在读取音频…', 'Loading audio…'], ['音频格式不正确。', 'The audio format is invalid.'], ['音频已就绪。', 'Audio is ready.'],
  ['音频读取失败，请重试。', 'Audio could not be loaded. Try again.'], ['音频无法播放，请重新加载。', 'Audio could not play. Reload it.'],
  ['开始录音', 'Record'], ['当前浏览器无法录音。请直接填写文字答案。', 'This browser cannot record. Type your answer instead.'],
  ['请允许浏览器使用麦克风。', 'Allow this browser to use your microphone.'],
  ['正在录音：{seconds} / 120 秒', 'Recording: {seconds} / 120 seconds'],
  ['正在录音，完成后点击停止。', 'Recording. Select Stop when finished.'],
  ['无法访问麦克风。请检查浏览器权限，或直接填写文字答案。', 'The microphone is unavailable. Check browser permissions or type your answer.'],
  ['转写到答案', 'Transcribe'], ['正在转写；完成后可在答案框修正。', 'Transcribing. You can correct the text in your answer.'],
  ['未获得有效转写，可重新录音或直接填写答案。', 'No transcript was returned. Record again or type your answer.'],
  ['转写已放入答案框，请核对后提交。', 'The transcript is in your answer. Check it before submitting.'],
  ['转写失败；录音仍可回听，也可直接填写文字。', 'Transcription failed. You can replay the recording or type your answer.'],
  ['停止录音', 'Stop'], ['重新录音', 'Record again'], ['录音为空，请再试一次。', 'The recording is empty. Try again.'],
  ['录音已保留在当前页面。可回听，或转写到答案后修正。', 'Recording ready on this page. Replay it or transcribe and edit your answer.'],
  ['录音可回听；转写暂不可用，请直接填写答案。', 'You can replay the recording. Transcription is unavailable; type your answer.'],
  ['录音只用于转写。请核对答案框中的文字后提交，结果评价表达内容。', 'Recording provides a transcript. Check the text before submitting; feedback evaluates the content of your answer.'],
  ['转写暂不可用，可直接填写文字答案。', 'Transcription is unavailable. You can type your answer.'], ['音频原文', 'Audio transcript'],
  ['语音转写暂不可用，可直接填写文字答案。', 'Transcription is unavailable. You can type your answer.'],
  ['语音转写尚未配置；可直接填写文字答案。', 'Transcription is not configured. You can type your answer.'],
  ['所选文字与正文不一致，请重新选择。', 'The selected text no longer matches. Select it again.'],
  ['正文版本已变化，请刷新后重新选择。', 'The material has changed. Refresh and select the text again.'],
  ['记忆入口暂不可用；可先标记疑惑并保留原文。', 'The memory link is unavailable. You can save the question and context.'],
  ['音频资源暂不可用。', 'The audio resource is unavailable.'],
  ['音频资源校验未通过。', 'The audio resource could not be verified.'],
  ['请使用不超过 120 秒的单声道 PCM WAV 录音。', 'Use a mono PCM WAV recording no longer than 120 seconds.'],
  ['转写未完成，请重试或直接填写答案；本次未计入作答。', 'Transcription did not finish. Retry or type your answer. No answer was graded.'],
  ['语音转写正在处理其他录音，请稍后重试。', 'Transcription is busy. Try again shortly.'],
  ['请登录账号后使用语音转写；也可直接填写文字答案。', 'Sign in to use transcription, or type your answer.'],
];
export const contextMessages = Object.fromEntries(pairs.map(pair => [pair[0], pair]));
export function ct(source, params = {}) {
  const pair = contextMessages[source];
  const text = pair ? pair[getLanguage() === 'en' ? 1 : 0] : source;
  return String(text).replace(/\{([A-Za-z0-9_]+)\}/g, (match, key) => Object.hasOwn(params, key) ? String(params[key]) : match);
}
export function contextError(message) {
  const pair = pairs.find(item => item.includes(message));
  return pair ? pair[getLanguage() === 'en' ? 1 : 0] : translateMessage(message);
}
