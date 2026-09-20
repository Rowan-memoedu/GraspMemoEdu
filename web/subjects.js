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
// One Math reference palette, with subject hue/chroma and emphasis strength.
// Math and violet keep matching luminance; other accents have stronger contrast.
export const subjectHues = {math: null, physics: 26, biology: 145, english: 275, chinese: 9, chemistry: 0};
// Violet needs less chroma to remain a restrained deep purple at the same luminance.
const subjectChroma = {english: .55};
const subjectEmphasis = {physics: .75, biology: .75, chinese: .75};
const monochromeInk = new Set(['--ma-navy', '--ma-blue', '--link-color', '--score-color', '--progress-complete']);
export const mathPalette = {
  "--ma-navy": "#1e194e",
  "--ma-blue": "#096bb7",
  "--soft-blue": "#f3f8fc",
  "--link-color": "#0577c7",
  "--link-hover": "#0864a6",
  "--button-hover": "#075892",
  "--button-active": "#084f85",
  "--score-color": "#0877bf",
  "--progress-complete": "#176bb5",
  "--progress-active": "#78b6ed",
  "--progress-paused": "#a5cff3",
  "--progress-ready": "#c9e4ff",
  "--selection-border": "#98c6e9",
  "--lesson-progress": "#a8cae4",
  "--muted-accent-border": "#9db9cf",
  "--control-hover-border": "#c4d9e8",
  "--control-border": "#d6e4ef",
  "--intro-border": "#d7e7f2",
  "--intro-background": "#daebf7",
  "--task-hover-border": "#c7dbe9",
  "--answer-help-background": "#f5f9fc",
  "--subtle-accent-border": "#dce6ed",
  "--subtle-accent-background": "#f7fafc",
  "--answer-background": "#f8fafb"
};
const linear = value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
const luminance = rgb => rgb.reduce((sum, value, index) => sum + linear(value) * [.2126, .7152, .0722][index], 0);
const hsl = (hue, saturation, lightness) => {
  const a = saturation * Math.min(lightness, 1 - lightness);
  return [0, 8, 4].map(n => {
    const k = (n + hue / 30) % 12;
    return lightness - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  });
};
function recolor(hex, subjectId) {
  if (subjectId === 'math') return hex;
  const rgb = [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16) / 255);
  const high = Math.max(...rgb), low = Math.min(...rgb), lightness = (high + low) / 2;
  const saturation = subjectId === 'chemistry' ? 0 : Math.min(1, (high - low) / (1 - Math.abs(2 * lightness - 1) || 1) * (subjectChroma[subjectId] ?? 1.15));
  const baseLuminance = luminance(rgb);
  const target = baseLuminance * (baseLuminance > .05 && baseLuminance < .5 ? subjectEmphasis[subjectId] ?? 1 : 1);
  let lower = 0, upper = 1;
  for (let index = 0; index < 28; index++) {
    const mid = (lower + upper) / 2;
    if (luminance(hsl(subjectHues[subjectId], saturation, mid)) < target) lower = mid;
    else upper = mid;
  }
  return '#' + hsl(subjectHues[subjectId], saturation, (lower + upper) / 2)
    .map(value => Math.round(value * 255).toString(16).padStart(2, '0')).join('');
}
export function subjectPalette(subjectId = 'math') {
  const id = Object.hasOwn(subjectHues, subjectId) ? subjectId : 'math';
  const palette = Object.fromEntries(Object.entries(mathPalette).map(([token, value]) => [token,
    id === 'chemistry' && monochromeInk.has(token) ? '#111111' :
    id === 'chemistry' && ['--link-hover', '--button-hover', '--button-active'].includes(token) ? '#000000' : recolor(value, id)]));
  if (id === 'physics') Object.assign(palette, {
    '--link-color': '#FF8C00', '--ma-blue': '#FF8C00', '--score-color': '#FF8C00',
    '--progress-complete': '#FF8C00', '--link-hover': '#ed8200',
    '--button-hover': '#ed8200', '--button-active': '#e07b00'
  });
  return palette;
}
const accent = subject => subjectPalette(subject?.id)['--link-color'];
const emblems = { math: '∑', physics: 'φ', english: 'Aa', chinese: '文', biology: '叶', chemistry: '⚗' };
// Selected together for homepage cards and the subject navbar: P2 / B3 / C2.
const subjectMarks = {"physics":"<path d=\"M9 8h22M20 8v3l9 17M10 28a19 19 0 0 0 17 4\" /><circle cx=\"29\" cy=\"28\" r=\"4\" fill=\"currentColor\" stroke=\"none\"/>","biology":"<path d=\"M33 20c0 9-5 14-14 13S6 30 6 21 9 7 18 7 33 10 33 20Z\"/><circle cx=\"20\" cy=\"20\" r=\"5\"/><path d=\"m11 16 2-2m13 13 2-2m-15 2 2 1\"/>","chemistry":"<path d=\"m20 5 13 7v16l-13 7-13-7V12ZM20 10l8 5m0 10-8 5M12 15v10\"/>"};

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
  const symbol = element('img', 'subjectEmblem');
  symbol.src = subjectLogo(subject);
  symbol.alt = '';
  symbol.setAttribute('aria-hidden', 'true');
  return symbol;
}

export function renderSubjectHome(subjects, { onRestricted } = {}) {
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
    if (subject.available === false && onRestricted) {
      card.addEventListener('click', event => { event.preventDefault(); onRestricted(subject); });
    }
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

export function applySubjectTheme(subject, { home = false } = {}) {
  const palette = subjectPalette(subject?.id);
  const color = home ? '#000000' : palette['--link-color'];
  document.documentElement.style.setProperty('--subject-accent', color);
  document.documentElement.style.setProperty('--subject-accent-soft', color + '12');
  document.documentElement.style.setProperty('--primary-text', subject?.id === 'physics' ? '#000000' : '#ffffff');
  document.documentElement.style.setProperty('--primary-weight', subject?.id === 'physics' ? '700' : '400');
  for (const [name, value] of Object.entries(palette)) document.documentElement.style.setProperty(name, value);
  document.body.classList.toggle('platformTheme', home);
  document.body.classList.toggle('subjectTemplate', Boolean(subject));
  document.body.classList.toggle('hasSubjectContext', Boolean(subject));
  document.body.dataset.subject = subject?.id || '';
  document.body.dataset.platformContext = subject ? 'subject' : 'platform';
}

export function subjectLogo(subject) {
  if (!subject) return './favicon.svg';
  const color = subjectPalette(subject.id)[subjectMarks[subject.id] ? '--link-color' : '--ma-navy'];
  const mark = subjectMarks[subject.id]
    ? '<g stroke="' + color + '" color="' + color + '" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + subjectMarks[subject.id] + '</g>'
    : subject.id === 'math'
    ? '<path d="M12 11h17M12 29h17M27 11 17 20l10 9" fill="none" stroke="' + color + '" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
    : '<text x="20" y="27" text-anchor="middle" font-family="Arial,sans-serif" font-size="22" fill="' + color + '">' + (emblems[subject.id] || '·') + '</text>';
  return 'data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><rect x="1" y="1" width="38" height="38" rx="7" fill="#fff" stroke="#e0e0e0"/>' + mark + '</svg>');
}
