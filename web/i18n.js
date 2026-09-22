// UI strings only. Course HTML, names, answers and explanations never enter this module.
import {readerMessages} from './reader-messages.js?v=f0de4845259eae4b';
import {portalMessages} from './portal-messages.js?v=f0de4845259eae4b';
import {staticMessages} from './static-messages.js?v=f0de4845259eae4b';

export const LANGUAGE_KEY = 'math-learning-web:ui-language';
export const messages = {...staticMessages, ...readerMessages, ...portalMessages};
const translatedMessages = new Map(Object.values(messages).flatMap(pair => pair.map(value => [value, pair])));
const validLanguage = value => value === 'en' ? 'en' : 'zh-CN';
let language = 'zh-CN';
try { language = validLanguage(localStorage.getItem(LANGUAGE_KEY)); } catch { /* Chinese is the default when storage is unavailable. */ }
export const getLanguage = () => language;
export const locale = () => language === 'en' ? 'en-US' : 'zh-CN';
// Imported module/question headings have a structural label followed by an
// authored title. Localize only the label; keep the title and all body HTML verbatim.
export function learningTitle(value = '') {
  return String(value).replace(/^(Example|Practice|例题|练习)(\s*\d+(?:[.-]\d+)*)(?=\s*[:：]|$)/i,
    (_, label, number) => `${t(/^(Example|例题)$/i.test(label) ? 'Example' : 'Practice')} ${number.trim()}`);
}
export function setLanguage(value) {
  language = validLanguage(value);
  try { localStorage.setItem(LANGUAGE_KEY, language); } catch { /* Keep the preference for this page session. */ }
  document.documentElement.lang = locale();
  return language;
}
export function t(key, params = {}) {
  const pair = messages[key];
  const template = pair ? pair[language === 'en' ? 1 : 0] : key;
  return String(template).replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
}

// Only API operational errors belong here. A judge's explanation is course content.
const errors = {
  '请先完成前置知识的学习和待复习内容，并解除前置知识的暂停状态。': 'Complete the prerequisite topics and their pending reviews, and resolve any paused prerequisites first.',
  '未找到此学科。': 'This subject could not be found.',
  '此课程不属于当前学科。': 'This course does not belong to the current subject.',
  '指南路径无效。': 'This guide is unavailable.',
  '请先选择游客访问或使用邀请码登录。': 'Continue as a guest or sign in with an invitation code.',
  '访问身份已失效，请重新选择访问方式。': 'Your session is no longer valid. Please sign in again or continue as a guest.',
  '账号登录已到期，请重新输入邀请码登录。': 'Your session has expired. Enter your invitation code to sign in again.',
  '当前身份没有访问此 Topic 的权限。': 'This topic is not enabled for your account.',
  '当前身份没有访问此课程的权限。': 'This course is not enabled for your account.',
  '邀请码无效，请联系管理者。': 'The invitation code is invalid. Please contact the administrator.',
  '请选择有效的 IANA 时区。': 'Please select a valid IANA time zone.',
  '历史分页位置无效，请重新加载。': 'The history page has changed. Please reload.',
  '请先选择课程。': 'Please select a course first.',
  '未找到此学习任务。': 'This learning task could not be found.',
  '反馈中的任务和 Topic 不一致。': 'The feedback task does not match its topic.',
  '反馈中的课程和 Topic 不一致。': 'The feedback course does not match its topic.',
  '题目反馈需要指定对应 Topic。': 'Please open the relevant topic before reporting this question.',
  '只能反馈已访问的题目。': 'You can only report questions you have already visited.',
  '请求字段不完整或格式错误，请刷新后重试。': 'Some request fields are missing or invalid. Refresh and try again.',
  '学习状态已更新，已为你重新同步。': 'Your progress changed. The latest state has been loaded.',
  '学习状态已更新，请刷新后重置。': 'Your progress changed. Refresh before resetting.',
  '作答轮次已变化，请刷新。': 'This attempt has changed. Please refresh.',
  '学习步骤已变化，请刷新。': 'The learning step has changed. Please refresh.',
  '只能操作当前活动步骤。': 'Only the current step is available for this action.',
  '该内容尚未解锁。': 'This page has not been unlocked yet.',
  '答案正在判定，请稍候。': 'Your answer is being checked. Please wait.',
  '当前 Topic 已暂停或完成。': 'This topic is paused or completed.',
  '直接重置仅供游客使用。': 'Direct reset is available to guests only.',
  '体验专用浏览版仅供游客使用。': 'Demo browsing is available to guests only.',
  '当前页面没有可作答的题目。': 'There is no answerable question on this page.',
  '当前判题队列已满，请稍后重试；本次未计入作答。': 'Answer checking is busy. Try again shortly; this attempt has not been counted.',
  '判题服务暂时不可用，请重试；本次未计入作答。': 'Answer checking is temporarily unavailable. Please retry; this attempt has not been counted.',
  '此身份尚无该任务的已完成作答记录。': 'There are no completed answers for this lesson under this identity.',
  '显示名称需要 1 至 100 个可显示字符。': 'Your display name must contain 1–100 visible characters.',
  '请填写反馈内容。': 'Please enter your feedback.',
  '请完成全部作答，且不要重复使用配对选项。': 'Complete every item and use each matching option only once.',
  '所选文字与正文不一致，请重新选择。': 'The selection does not match the text. Please select it again.',
  '正文版本已变化，请刷新后重新选择文字。': 'The content version has changed. Refresh and select the text again.',
  '文字纠错需要指定对应 Topic。': 'A text correction must identify its topic.',
  '当前账号没有使用此功能的权限。': 'This feature is not enabled for your account.',
  '该功能需注册账号才能使用': 'An account is required to use this feature.',
  '该功能需注册账号才能使用。': 'An account is required to use this feature.',
  '课程版本已变化，原进度已保留。请先由管理者处理版本迁移。': 'The course was updated. Your progress is preserved; please contact the administrator.',
  '同一请求标识不能对应不同操作。': 'This request conflicts with an earlier action. Refresh and try again.',
  '请求标识已用于答案提交。': 'This request was already used to submit an answer. Refresh and try again.',
};
export const errorMessages = Object.fromEntries(Object.entries(errors).map(([source, en]) =>
  [source, [source.replace(/Topic/g, '主题').replace(/管理者/g, '管理员'), en]]));
for (const pair of Object.values(errorMessages)) {
  for (const value of pair) translatedMessages.set(value, pair);
}
export function translateMessage(message) {
  if (typeof message !== 'string' || !message.trim()) return t('error.retry');
  if (messages[message]) return t(message);
  const translated = translatedMessages.get(message);
  if (translated) return translated[language === 'en' ? 1 : 0];
  if (errorMessages[message]) return errorMessages[message][language === 'en' ? 1 : 0];
  if (language === 'en') {
    return t('error.refresh');
  }
  if (/[\u3400-\u9fff]/.test(message)) return message.replace(/Topic/g, '主题').replace(/管理者/g, '管理员');
  return t('error.refresh');
}
export function applyStaticTranslations(root = document) {
  document.documentElement.lang = locale();
  for (const element of root.querySelectorAll('[data-i18n]')) element.textContent = t(element.dataset.i18n);
  for (const attribute of ['aria-label', 'title', 'placeholder', 'content']) {
    for (const element of root.querySelectorAll(`[data-i18n-${attribute}]`)) {
      element.setAttribute(attribute, t(element.getAttribute(`data-i18n-${attribute}`)));
    }
  }
}
