import { getLanguage } from './i18n.js';

const copy = (zh, en) => getLanguage() === 'en' ? en : zh;
const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const link = (text, href, className) => {
  const node = element('a', className, text); node.href = href; return node;
};
const decode = value => { try { return decodeURIComponent(value); } catch { return value; } };
const accent = subject => {
  const value = /^#[\da-f]{3}(?:[\da-f]{3})?$/i.test(subject?.accent || '') ? subject.accent : '#785744';
  return value.length === 4 ? '#' + [...value.slice(1)].map(letter => letter + letter).join('') : value;
};
const emblems = { math: '∑', physics: 'φ', english: 'Aa', chinese: '文', biology: '叶', chemistry: '⚗' };

export const subjectLabel = subject => (getLanguage() === 'en' && subject.title_en) || subject.title;

export function subjectHref(subjectId, path = '/learn') {
  const suffix = path.startsWith('/') ? path : '/' + path;
  return '#/subjects/' + encodeURIComponent(subjectId) + suffix;
}

export function parsePlatformRoute(hash = '#/') {
  const raw = (hash || '#/').replace(/^#/, '');
  const queryIndex = raw.indexOf('?');
  const path = (queryIndex < 0 ? raw : raw.slice(0, queryIndex)) || '/';
  const search = queryIndex < 0 ? '' : raw.slice(queryIndex);
  const params = new URLSearchParams(search);
  const match = path.match(/^\/subjects\/([^/]+)(\/.*)?$/);
  if (match) {
    const subjectId = decode(match[1]);
    const localPath = !match[2] || match[2] === '/' ? '/learn' : match[2];
    return { subjectId, path: localPath, params,
      ...(!match[2] || match[2] === '/' ? { canonicalHash: subjectHref(subjectId, '/learn') + search } : {}) };
  }
  if (/^\/(?:learn|courses|guide)(?:\/|$)/.test(path) || /^\/(?:topic|review)\//.test(path)) {
    return { subjectId: 'math', path, params, canonicalHash: subjectHref('math', path) + search };
  }
  return { subjectId: null, path, params };
}

function emblem(subject) {
  const symbol = element('span', 'subjectEmblem', subject.emblem || emblems[subject.id] || '·');
  symbol.setAttribute('aria-hidden', 'true');
  return symbol;
}

export function renderSubjectHome(subjects) {
  const section = element('section', 'subjectHome');
  section.setAttribute('aria-labelledby', 'subjectHomeTitle');
  const heading = element('header', 'subjectHomeHeading');
  heading.append(element('p', 'subjectKicker', copy('学科 · 学习 · 复习', 'SUBJECTS · STUDY · REVIEW')));
  const title = element('h1', 'subjectBrand', 'GraspMemoEdu'); title.id = 'subjectHomeTitle';
  heading.append(title, element('p', 'subjectWelcome', copy('欢迎回来。选择一门学科，开始今天的学习。', 'Welcome back. Choose a subject to begin.')));
  const grid = element('div', 'subjectGrid');
  for (const subject of subjects) {
    const card = link('', subjectHref(subject.id), 'subjectCard');
    card.dataset.subjectId = subject.id;
    card.style.setProperty('--card-accent', accent(subject));
    const top = element('div', 'subjectCardTop');
    top.append(emblem(subject));
    const arrow = element('span', 'subjectCardArrow', '↗'); arrow.setAttribute('aria-hidden', 'true'); top.append(arrow);
    const name = element('h2', 'subjectName', subjectLabel(subject));
    const secondary = getLanguage() === 'en' ? subject.title : subject.title_en;
    const description = (getLanguage() === 'en' && subject.description_en) || subject.description || '';
    card.append(top, name);
    if (secondary && secondary !== subjectLabel(subject)) card.append(element('p', 'subjectSecondaryName', secondary));
    if (description) card.append(element('p', 'subjectDescription', description));
    const count = Number.isInteger(subject.course_count) && subject.course_count > 0 ? subject.course_count : 0;
    card.append(element('span', 'subjectCardStatus', count
      ? copy(`${count} 门课程`, `${count} ${count === 1 ? 'course' : 'courses'}`)
      : copy('暂无课程', 'No courses yet')));
    grid.append(card);
  }
  section.append(heading, grid);
  return section;
}

export function renderSubjectEmpty(subject) {
  const section = element('section', 'subjectEmpty');
  section.style.setProperty('--card-accent', accent(subject));
  section.setAttribute('aria-labelledby', 'subjectEmptyTitle');
  const back = link(copy('← 所有学科', '← All subjects'), '#/', 'subjectBack');
  const panel = element('div', 'subjectEmptyPanel');
  panel.append(emblem(subject));
  const title = element('h1', 'subjectEmptyTitle', subjectLabel(subject)); title.id = 'subjectEmptyTitle';
  panel.append(title, element('p', 'subjectEmptyMessage', copy('这门学科暂无课程。', 'There are no courses in this subject yet.')));
  const actions = element('div', 'subjectEmptyActions');
  actions.append(link(copy('查看学科指南', 'Subject guide'), subjectHref(subject.id, '/guide'), 'subjectGuideLink'),
    link(copy('返回所有学科', 'All subjects'), '#/', 'subjectAllLink'));
  panel.append(actions); section.append(back, panel);
  return section;
}

export function applySubjectTheme(subject) {
  const color = accent(subject);
  document.documentElement.style.setProperty('--subject-accent', color);
  document.documentElement.style.setProperty('--subject-accent-soft', color + '12');
  document.body.classList.add('platformTheme');
  document.body.classList.toggle('hasSubjectContext', Boolean(subject));
  document.body.dataset.subject = subject?.id || '';
  document.body.dataset.platformContext = subject ? 'subject' : 'platform';
}
