import { t, getLanguage, setLanguage, locale, translateMessage, learningTitle } from "./i18n.js?v=f0de4845259eae4b";
import { createReviewView } from "./review.js?v=f0de4845259eae4b";
import { renderCourseGraph } from "./course-graph.js?v=f0de4845259eae4b";
import {questionInput} from './question-input.js?v=f0de4845259eae4b';
import {reportableContent, installContentReporting} from './content-report.js?v=f0de4845259eae4b';
import {createCatalogPicker} from './catalog-picker.js?v=f0de4845259eae4b';
import {createAtomicView} from './atomic.js?v=f0de4845259eae4b';
import { subjectHref, parsePlatformRoute, renderSubjectHome, renderSubjectEmpty, applySubjectTheme, subjectLabel, subjectLogo } from "./subjects.js?v=f0de4845259eae4b";

const $ = (id) => document.getElementById(id);
const node = (tag, className = "", text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = text;
  return item;
};
const control = (text, className, action) => { const item = node("button", className, text); item.type = "button"; item.addEventListener("click", action); return item; };
const percent = (value) => Number.isFinite(Number(value)) && value !== null ? Math.max(0, Math.min(100, Number(value))) : null;
const percentLabel = (value) => percent(value) === null ? "—" : `${Math.round(percent(value)).toLocaleString(locale())}%`;
const encode = encodeURIComponent;
const typeNames = { Lesson: t("portal.lesson"), Review: t("portal.review"), Placement: t("portal.diagnostic.0"), Supplemental: t("portal.supplemental.diagnostic.1"), Quiz: t("portal.assessment.2"), Exam: t("portal.assessment.2") };
const statusNames = { not_started: t("portal.not.started.3"), in_progress: t("portal.in.progress.4"), paused: t("portal.paused.5"), completed: t("portal.completed.6") };
const roundButton = (text, action) => control(text, "portalStart", action);

export function initPortal(bridge) {
  const root = $("portalContent");
  let catalog = null, profile = null, identity = null, selectedCourse = null;
  let subjects = null, catalogSubject = null, activeSubject = null;
  let sequence = 0, currentHash = "", currentRoute = null, busyCourse = false;
  let expandedTask = null, paging = false, historyError = null, guideVersion = null, guideLoaded = false;
  let refreshTimer = null, menuTimer = null, popoverTimer = null, pageTimer = null;
  let guideObjectUrls = [];
  let graphScroll = 0, feedbackContext = {}, feedbackBusy = false, renderedDay = null;
  const dashboards = new Map(), scrolls = new Map(), answerCache = new Map();
  const routeKey = () => location.hash || "#/";
  const selectedDashboard = () => dashboards.get(selectedCourse);
  const timezone = () => profile?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const query = (fields) => new URLSearchParams(Object.entries(fields).filter(([, value]) => value !== null && value !== undefined && value !== "")).toString();
  const scopedRequest = (path, options, subjectId) => bridge.request(
    subjectId ? `${path}${path.includes("?") ? "&" : "?"}subject_id=${encode(subjectId)}` : path, options);
  const call = (path, options) => scopedRequest(path, options, currentRoute?.subjectId);
  const allowed = (feature) => Boolean(bridge.getAccess()?.features?.includes(feature));
  async function saveCourseSelection(courseId, subjectId) {
    if (busyCourse) throw new Error(t('portal.selecting.32'));
    busyCourse = true;
    try {
      const result = await scopedRequest('catalog/select', {method: 'POST', body: {course_id: courseId}}, subjectId);
      if (result.selected_course_id !== courseId) throw new Error(t('catalog.saveUnconfirmed'));
      return result;
    } finally { busyCourse = false; }
  }
  const catalogPicker = createCatalogPicker({
    button: $('catalogToggle'), panel: $('catalogPanel'),
    readSubjects: () => subjects ? Promise.resolve({subjects}) : bridge.request('subjects'),
    readCatalog: subjectId => scopedRequest('catalog', undefined, subjectId),
    selectCourse: saveCourseSelection, getSubject: () => currentRoute?.subjectId,
    onSelected: (courseId, subjectId) => {
      catalog = null; selectedCourse = courseId; dashboards.clear();
      const hash = subjectHref(subjectId, '/learn'); scrolls.delete(hash);
      if (location.hash === hash) void route(); else location.hash = hash;
    },
  });
  const learnHref = () => subjectHref(currentRoute?.subjectId || "math", "/learn");
  const localHref = (path) => {
    const raw = path.replace(/^#/, "");
    return /^\/(learn|courses|guide|topic|review)(\/|\?|$)/.test(raw)
      ? subjectHref(currentRoute?.subjectId || "math", raw) : `#${raw}`;
  };
  const link = (text, href, className = "") => { const item = node("a", className, text); item.href = localHref(href); return item; };
  const pageTitle = (label) => `${label}${activeSubject ? ` · ${subjectLabel(activeSubject)}` : ""} · GraspMemoEdu`;
  const apiDate = (value, withTime = false) => {
    if (!value || !Number.isFinite(Date.parse(value))) return "—";
    return new Intl.DateTimeFormat(locale(), { timeZone: timezone(), year: "numeric", month: "2-digit", day: "2-digit", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) }).format(new Date(value));
  };
  const dayKey = (value) => new Intl.DateTimeFormat("en-CA", { timeZone: timezone(), year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
  const clockTime = (value) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat(locale(), { timeZone: timezone(), hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
  const review = createReviewView({
    ...bridge, request: call, root, homeHref: learnHref, formatDate: value => apiDate(value, true), showFeedback,
    progressChanged: () => { dashboards.clear(); answerCache.clear(); },
    setSession: id => {
      history.replaceState(null, "", `${location.hash.split("?")[0]}?session=${encode(id)}`);
      currentHash = routeKey(); currentRoute = parseRoute();
    },
  });
  const atomic = createAtomicView({...bridge, request: call, root, homeHref: learnHref,
    reviewHref: () => subjectHref(currentRoute?.subjectId, '/reviews'), formatDate: value => apiDate(value, true),
    progressChanged: () => { dashboards.clear(); answerCache.clear(); }});

  function navigate(path, replace = false) {
    const hash = localHref(path);
    if (location.hash === hash) return route();
    if (replace) { history.replaceState(null, "", hash); return route(); }
    location.hash = hash;
  }
  function parseRoute() {
    return parsePlatformRoute(routeKey());
  }
  function loading(text = t("portal.loading.learning.materials.7")) {
    const box = node("section", "portalLoading");
    box.setAttribute("aria-live", "polite"); box.append(node("span", "spinner"), node("p", "", text)); return box;
  }
  function errorBox(message, retry, heading = t("portal.unable.to.load.8")) {
    const box = node("section", "portalMessage"); box.setAttribute("role", "status");
    box.append(node("h2", "", heading), node("p", "", message ? translateMessage(message) : t("portal.the.service.is.temporarily.unavailable.please.try.again.later.9")));
    if (retry) box.append(control(t("portal.retry.10"), "primaryButton", retry));
    return box;
  }
  function emptyBox(text) { const box = node("div", "portalEmpty"); box.append(node("p", "", text)); return box; }
  function mathStyle(css, id = "portalMathStyle") {
    let style = $(id); if (!style) { style = node("style"); style.id = id; document.head.append(style); }
    style.textContent = css || "";
  }
  function trustedContent(html, className = "courseContent") { const item = node("div", className); item.innerHTML = html || ""; return item; }
  function setNavigation(section) {
    for (const item of document.querySelectorAll("[data-navigation]")) {
      const active = item.dataset.navigation === section;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
    }
  }
  function updateSubjectContext(subject) {
    activeSubject = subject;
    const home = currentRoute?.path === "/" && !currentRoute.subjectId;
    applySubjectTheme(subject, {home});
    const logo = document.querySelector('.portalLogo');
    logo.hidden = !subject;
    logo.href = subject ? subjectHref(subject.id) : '#/';
    logo.querySelector('img').src = subjectLogo(subject);
    logo.querySelector('span').textContent = subject ? t('platform.learningName', {subject: getLanguage() === 'en' && subject.id === 'math' ? 'Math' : subjectLabel(subject)}) : 'GraspMemoEdu';
    logo.setAttribute('aria-label', subject ? subjectLabel(subject) + ' · ' + t('nav.learn') : t('nav.home'));
    const context = $("subjectContext");
    context.hidden = true;
    context.textContent = subject ? subjectLabel(subject) : "";
    context.href = subject ? subjectHref(subject.id) : "#/";
    for (const item of document.querySelectorAll(".mainNavigation [data-navigation]")) {
      const section = item.dataset.navigation;
      item.hidden = !subject;
      if (section === 'reviews') item.hidden = !['english', 'chinese', 'biology', 'chemistry'].includes(subject?.id);
      item.href = subjectHref(subject?.id || "math", `/${section}`);
    }
    $("topicHomeLink").href = learnHref();
  }
  function updateUser() {
    const current = bridge.getAccess();
    const expired = bridge.getIdentityProblem();
    const rawName = profile?.display_name || current?.display_name;
    const name = expired ? t("portal.not.signed.in.11") : current?.role === "guest" && (!rawName || rawName === "游客") ? t("portal.guest.12") : rawName || t("portal.guest.12");
    const parts = name.trim().split(/\s+/);
    const initials = /^[A-Za-z]/.test(name) ? (parts.length > 1 ? parts[0][0] + parts.at(-1)[0] : name.slice(0, 2)).toUpperCase() : [...name].slice(0, 2).join("");
    $("userMenuButton").textContent = initials || t("portal.g.13");
    $("userMenuButton").setAttribute("aria-label", t("portal.userMenu", { name }));
    $("menuDisplayName").textContent = name;
    $("menuRole").textContent = expired ? t("portal.session.expired.14") : current?.is_admin ? t('admin.identity') : current?.role === "account" ? t("portal.learning.account.15") : t("portal.guest.16");
    $('adminLearningNotice').hidden = expired || !current?.is_admin;
    $("menuAccountPurpose").hidden = current?.role !== "account" || current?.purpose !== "test";
    $("logoutButton").hidden = current?.role !== "account";
  }
  function showMenu(show) {
    clearTimeout(menuTimer); $("userMenu").hidden = !show; $("userMenuButton").setAttribute("aria-expanded", String(show));
  }
  function delayHideMenu() { clearTimeout(menuTimer); menuTimer = setTimeout(() => { if (!$("userMenuHost").contains(document.activeElement)) showMenu(false); }, 100); }
  $("userMenuHost").addEventListener("mouseenter", () => showMenu(true));
  $("userMenuHost").addEventListener("mouseleave", delayHideMenu);
  $("userMenuHost").addEventListener("focusin", () => showMenu(true));
  $("userMenuHost").addEventListener("focusout", delayHideMenu);
  $("userMenuButton").addEventListener("click", () => showMenu(true));
  $("identityButton").addEventListener("click", () => showMenu(false));
  $("logoutButton").addEventListener("click", async () => { showMenu(false); await bridge.returnToGuest(); });
  $("supportButton").addEventListener("click", () => { showMenu(false); showFeedback(); });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#userMenuHost")) showMenu(false);
    if (!event.target.closest(".portalTask") && expandedTask) collapseTasks();
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") { showMenu(false); hidePopovers(); collapseTasks(); } });

  async function basics(ticket, subjectId) {
    const [nextSubjects, nextProfile] = await Promise.all([
      subjects ? { subjects } : bridge.request("subjects"),
      profile || bridge.request("profile"),
    ]);
    if (ticket !== sequence) return;
    const subject = nextSubjects.subjects?.find(item => item.id === subjectId);
    const nextCatalog = subjectId && !subjectRestricted(subject)
      ? catalog && catalogSubject === subjectId ? catalog : await scopedRequest("catalog", undefined, subjectId)
      : null;
    if (ticket !== sequence) return;
    if (!Array.isArray(nextSubjects.subjects) || (subjectId && !subjectRestricted(subject) && !Array.isArray(nextCatalog?.courses))) throw new Error(t("portal.the.course.catalog.is.temporarily.unavailable.17"));
    subjects = nextSubjects.subjects; profile = nextProfile; catalog = nextCatalog; catalogSubject = subjectId;
    selectedCourse = catalog?.selected_course_id || null;
    if (!profile.timezone) {
      try {
        const next = await bridge.request("profile", { method: "POST", body: { timezone: timezone() } });
        if (ticket !== sequence) return;
        profile = next;
      } catch { /* Use browser time while a later settings save can retry. */ }
    }
    updateUser();
  }
  function subjectRestricted(subject) {
    return Boolean(subject && bridge.getAccess()?.role !== 'account' && subject.requires_account);
  }
  function restrictedNotice(subject, home = false) {
    const notice = node('section', 'subjectAccessNotice'); notice.setAttribute('role', 'status');
    notice.append(node('p', '', t('该功能需注册账号才能使用')),
      control(t('account.loginTitle'), 'primaryButton', () => bridge.openIdentity()));
    if (!home) notice.append(link(getLanguage() === 'en' ? 'All subjects' : '返回所有学科', '#/', 'textButton'));
    if (home) { root.querySelector('.subjectAccessNotice')?.remove(); root.querySelector('.subjectHomeHeading')?.append(notice); }
    else { document.title = pageTitle(subjectLabel(subject)); root.replaceChildren(node('h1', 'portalPageTitle', subjectLabel(subject)), notice); }
  }
  async function dashboard(id, cursor = null) {
    const data = await call(`dashboard?${query({ course_id: id, cursor, limit: 25 })}`);
    if (!data.course || !Array.isArray(data.tasks) || !Array.isArray(data.history)) throw new Error(t("portal.course.learning.records.are.temporarily.unavailable.18"));
    return data;
  }
  function mergeDashboard(id, data, append = false) {
    const previous = dashboards.get(id);
    const sameHistory = Boolean(data.history_revision && data.history_revision === previous?.history_revision);
    const all = append ? [...(previous?.history || []), ...data.history] : sameHistory ? [...data.history, ...(previous?.history || [])] : data.history;
    const historyRows = [...new Map(all.map((task) => [task.id, task])).values()]
      .sort((a, b) => Date.parse(b.completed_at || 0) - Date.parse(a.completed_at || 0));
    const merged = { ...data, history: historyRows };
    if (!append && sameHistory && previous?.history?.length > data.history.length && previous.history_cursor !== undefined) {
      merged.history_cursor = previous.history_cursor; merged.has_more = previous.has_more;
    }
    dashboards.set(id, merged); return merged;
  }
  function releaseGuideAssets() { for (const url of guideObjectUrls) URL.revokeObjectURL(url); guideObjectUrls = []; }
  function stopPageWork() { clearTimeout(refreshTimer); clearTimeout(pageTimer); releaseGuideAssets(); review.stop(); atomic.stop(); hidePopovers(); }
  async function route() {
    if (currentHash) scrolls.set(currentHash, window.scrollY);
    currentRoute = parseRoute();
    if (currentRoute.canonicalHash && currentRoute.canonicalHash !== location.hash) history.replaceState(null, "", currentRoute.canonicalHash);
    currentHash = routeKey();
    const ticket = ++sequence;
    stopPageWork(); showMenu(false); collapseTasks();
    if (graphDialog.open) graphDialog.close();
    root.hidden = false; bridge.leaveTopic();
    root.classList.toggle("reviewShell", currentRoute.path.startsWith("/review/"));
    if (bridge.getIdentityProblem()) { root.hidden = true; root.replaceChildren(); return; }
    updateSubjectContext(subjects?.find(subject => subject.id === currentRoute.subjectId) || null);
    setNavigation(!currentRoute.subjectId ? "subjects" : currentRoute.path.startsWith("/courses") ? "courses" : currentRoute.path === "/guide" ? "guide" : currentRoute.path === '/reviews' ? 'reviews' : "learn");
    root.replaceChildren(loading()); window.scrollTo(0, 0);
    try {
      if (profile) await bridge.refreshAccess();
      if (ticket !== sequence) return;
      await basics(ticket, currentRoute.subjectId);
      if (ticket !== sequence) return;
      const { path, params, subjectId } = currentRoute;
      const subject = subjects.find(item => item.id === subjectId);
      if (subjectId && !subject) throw new Error(t("platform.unknownSubject"));
      updateSubjectContext(subject || null);
      if (subjectRestricted(subject)) { restrictedNotice(subject); return; }
      if (path === "/" && !subjectId) {
        document.title = "GraspMemoEdu"; root.replaceChildren(renderSubjectHome(subjects.map(item => ({...item, available: subjectRestricted(item) ? false : item.available})), {onRestricted: item => restrictedNotice(item, true)}));
      } else if (path === "/learn") {
        document.title = pageTitle(t("nav.learn"));
        if (!catalog.courses.length) { root.replaceChildren(renderSubjectEmpty(subject)); await renderGraphLibrary(ticket); return; }
        let viewedCourse = selectedCourse;
        if (!viewedCourse && params.get("taskId")) {
          const result = await call(`tasks/${encode(params.get("taskId"))}/answers`);
          if (ticket !== sequence) return;
          answerCache.set(params.get("taskId"), result); viewedCourse = result.task?.course_id;
        }
        if (!viewedCourse) { navigate("/courses", true); return; }
        const data = mergeDashboard(viewedCourse, await dashboard(viewedCourse));
        if (ticket !== sequence) return;
        renderLearn(data, params.get("taskId"), ticket);
      } else if (path === "/courses") {
        document.title = pageTitle(t("nav.courses")); await renderCourses();
      } else if (path === '/reviews') {
        const data = await call('atomic/reviews');
        if (ticket !== sequence) return;
        document.title = pageTitle(t('atomic.reviewTitle'));
        const list = node('section', 'atomicReviewList');
        root.replaceChildren(node('h1', 'portalPageTitle', t('atomic.reviewTitle')), list);
        if (data.all_materials) list.append(node('p', 'inputHint', t('admin.allCards')));
        for (const item of data.items) {
          const card = node('article', 'courseChoice');
          if (item.kind === 'atomic_card') {
            card.append(node('h2', '', item.title), node('p', '', `${item.course_title} · ${item.topic_title}`),
              link(t('admin.enterCard'), `#/review/${encode(item.topic_id)}/atomic?card=${encode(item.id)}`, 'primaryButton'));
            list.append(card); continue;
          }
          card.append(node('h2', '', item.title), node('p', '', t('atomic.dueCount', {count: item.due_count})));
          if (item.paused) card.append(node('p', 'atomicPaused', t('atomic.paused')));
          else if (!item.dependency_ready) card.append(node('p', '', t('portal.prerequisitesRequired')));
          else card.append(link(t(item.active ? 'atomic.resumeReview' : 'atomic.startReview'), `#/review/${encode(item.topic_id)}/atomic`, 'primaryButton'));
          list.append(card);
        }
        if (!data.items.length) list.append(emptyBox(t('atomic.noDue')));
        if (data.next_due_at) list.append(node('p', '', t('atomic.availableAt', {time: apiDate(data.next_due_at, true)})));
        refreshTimer = setTimeout(() => { if (ticket === sequence && !document.hidden) void route(); }, 15000);
      } else if (path === "/guide") {
        document.title = pageTitle(t("nav.guide")); await renderGuide(ticket);
      } else if (path === "/help") {
        document.title = pageTitle(t("nav.help")); renderHelp();
      } else if (path === "/settings") {
        document.title = pageTitle(t("nav.settings")); renderSettings();
      } else if (/^\/courses\/[^/]+\/progress$/.test(path)) {
        const id = decodeURIComponent(path.split("/")[2]);
        const data = await dashboard(id);
        if (ticket !== sequence) return;
        dashboards.set(id, data); renderCourseProgress(data, params);
      } else if (/^\/topic\/[^/]+$/.test(path)) {
        const topicId = decodeURIComponent(path.split('/')[2]);
        const state = await call(`state?topic_id=${encode(topicId)}`);
        if (ticket !== sequence) return;
        if (state.training_mode === 'atomic_retrieval') await atomic.open(topicId, 'learn', state);
        else { root.hidden = true; await bridge.openTopic(topicId, subjectId); }
      } else if (/^\/review\/[^/]+\/[^/]+$/.test(path)) {
        if (path.split('/')[3] === 'atomic') await atomic.open(decodeURIComponent(path.split('/')[2]), 'review', null, params.get('card'));
        else await review.open(decodeURIComponent(path.split("/")[2]), decodeURIComponent(path.split("/")[3]), params.get("session"));
      } else root.replaceChildren(errorBox(t("portal.this.page.does.not.exist.24"), () => navigate("/"), t("portal.page.not.found.25")));
      if (ticket !== sequence) return;
      const target = params.get("unitId") || params.get("topicId");
      requestAnimationFrame(() => {
        if (ticket !== sequence) return;
        if (target) root.querySelector(`[data-progress-id="${CSS.escape(target)}"]`)?.scrollIntoView({ block: "start" });
        else window.scrollTo(0, scrolls.get(currentHash) || 0);
      });
    } catch (error) {
      if (ticket !== sequence) return;
      if (bridge.getIdentityProblem()) { root.hidden = true; root.replaceChildren(); return; }
      const restricted = subjects?.find(item => item.id === currentRoute.subjectId);
      if (subjectRestricted(restricted)) {
        restrictedNotice(restricted); return;
      }
      const forbidden = error.status === 403 && ["feature_forbidden", "topic_forbidden", "course_forbidden"].includes(error.code);
      root.hidden = false; root.replaceChildren(errorBox(translateMessage(error.message), forbidden ? () => navigate("/courses") : () => location.reload(), forbidden ? t("portal.content.unavailable.26") : t("portal.service.unavailable.27")));
    }
  }

  async function renderCourses() {
    const ticket = sequence;
    const heading = node("h1", "portalPageTitle", t("portal.courses.28")), grid = node("div", "courseGrid");
    root.replaceChildren(heading, grid);
    if (!catalog.courses.length) { root.replaceChildren(renderSubjectEmpty(activeSubject)); await renderGraphLibrary(ticket); return; }
    if (bridge.getAccess()?.role === "account" && !catalog.courses.some((course) => course.available)) root.insertBefore(emptyBox(t("portal.no.courses.are.enabled.for.your.account.please.contact.your.admin.29")), grid);
    for (const course of catalog.courses) {
      const card = node("article", "courseChoice"); card.dataset.courseId = course.id;
      card.append(node("h2", "", course.title), node("p", "courseDescription", course.description || ""));
      const learn = control(t("portal.learn.31"), "primaryButton courseLearn", async () => {
        if (busyCourse) return;
        const ticket = sequence, subjectId = currentRoute.subjectId;
        for (const item of root.querySelectorAll(".courseLearn")) item.disabled = true;
        learn.textContent = t("portal.selecting.32"); card.querySelector(".fieldError")?.remove();
        try {
          await saveCourseSelection(course.id, subjectId);
          if (ticket !== sequence) return;
          selectedCourse = course.id; catalog.selected_course_id = course.id; scrolls.delete(learnHref());
          navigate("/learn");
        } catch (error) {
          if (ticket !== sequence) return;
          card.append(node("p", "fieldError", translateMessage(error.message))); learn.textContent = t("portal.learn.31");
          for (const item of root.querySelectorAll(".courseLearn")) item.disabled = item.dataset.available === "false";
        } finally { busyCourse = false; }
      });
      learn.dataset.available = String(course.available !== false); learn.disabled = course.available === false;
      const tools = node('div', 'courseTools');
      const progress = link(t('platform.courseProgress'), `#/courses/${encode(course.id)}/progress`, 'textButton courseProgressLink');
      const graph = control(t('portal.knowledge.map.93'), 'textButton courseGraphButton', async () => {
        if (graph.disabled) return;
        const ticket = sequence, learner = bridge.getAccess()?.learner_id;
        graph.disabled = true; graph.setAttribute('aria-busy', 'true');
        card.querySelector('.courseGraphStatus')?.remove();
        try {
          const data = await dashboard(course.id);
          if (ticket !== sequence || learner !== bridge.getAccess()?.learner_id || !graph.isConnected) return;
          openGraph(data);
        } catch (error) {
          if (ticket !== sequence || learner !== bridge.getAccess()?.learner_id || !graph.isConnected) return;
          const failure = node('p', 'courseGraphStatus fieldError', translateMessage(error.message));
          failure.setAttribute('role', 'status'); tools.after(failure);
        } finally { graph.disabled = course.available === false; graph.removeAttribute('aria-busy'); }
      });
      if (course.available === false) {
        progress.removeAttribute('href'); progress.setAttribute('aria-disabled', 'true'); progress.tabIndex = -1;
        progress.title = t('portal.no.learning.content.is.currently.available.33');
        graph.disabled = true; graph.title = progress.title;
      }
      tools.append(progress, graph); card.append(tools);
      card.append(learn); if (!course.available) card.append(node("p", "courseAvailability", t("portal.no.learning.content.is.currently.available.33")));
      grid.append(card);
    }
    await renderGraphLibrary(ticket);
  }

  async function renderGraphLibrary(ticket) {
    try {
      const result = await call('graphs');
      if (ticket !== sequence) return;
      const separate = result.graphs.filter(graph => !catalog.courses.some(course => course.id === graph.course_id && course.available));
      if (!separate.length) return;
      const section = node('section', 'graphLibrary');
      section.append(node('h2', '', t('portal.knowledge.map.93')));
      for (const graph of separate) {
        const card = node('article', 'courseChoice');
        card.append(node('h3', '', graph.title), control(t('portal.view.knowledge.map.91'), 'textButton', () => openGraph({course: {id: graph.course_id, title: graph.title}})));
        section.append(card);
      }
      root.append(section);
    } catch (error) {
      if (ticket === sequence) root.append(errorBox(error.message, () => route()));
    }
  }

  function hidePopovers() { clearTimeout(popoverTimer); for (const item of document.querySelectorAll(".coursePopover")) item.hidden = true; }
  function holdPopover() { clearTimeout(popoverTimer); }
  function delayPopover() { clearTimeout(popoverTimer); popoverTimer = setTimeout(hidePopovers, 100); }
  function showPopover(item) { hidePopovers(); item.hidden = false; }
  function infoRow(label, value) { const row = node("div", "portalInfoRow"); row.append(node("span", "", label), node("span", "", value)); return row; }
  function unitBar(unit, maxTopics) {
    const bar = node("div", "unitProgress");
    const count = Math.max(0, Number(unit.topic_count) || 0);
    bar.style.width = `${maxTopics ? Math.max(0, Math.min(100, count / maxTopics * 100)) : 0}%`;
    bar.setAttribute("aria-label", `${unit.title} · ${percentLabel(unit.progress)}`);
    for (const status of ["completed", "in_progress", "paused", "not_started"]) {
      const part = node("span", status); part.style.width = `${count ? Math.max(0, Number(unit.status_counts?.[status]) || 0) / count * 100 : 0}%`;
      bar.append(part);
    }
    return bar;
  }
  function unitList(data) {
    const list = node("div", "unitList"), max = Math.max(0, ...(data.units || []).map((unit) => Number(unit.topic_count) || 0));
    (data.units || []).forEach((unit, index) => {
      const row = node("div", "unitRow");
      row.append(link(t("portal.unitList", { index: (index + 1).toLocaleString(locale()), title: unit.title, count: Number(unit.topic_count).toLocaleString(locale()) }), `#/courses/${encode(data.course.id)}/progress?unitId=${encode(unit.id)}`), unitBar(unit, max)); list.append(row);
    });
    if (!list.childElementCount) list.append(node("p", "", t("portal.course.units.are.not.available.yet.34")));
    return list;
  }
  function courseSidebar(data) {
    const side = node("aside", "courseSidebar"), frame = node("section", "courseOverview"), top = node("div", "courseOverviewTop");
    const name = link(data.course.title, `#/courses/${encode(data.course.id)}/progress`, "courseNameLink");
    const circle = control(percentLabel(data.course.progress), "coursePercent", () => openGraph(data)); circle.setAttribute("aria-label", t("portal.graphProgress", { progress: percentLabel(data.course.progress) }));
    const unitsPopup = node("div", "coursePopover sequenceUnits"), detailsPopup = node("div", "coursePopover progressDetails");
    unitsPopup.hidden = true; detailsPopup.hidden = true;
    const tabs = node("div", "sequenceTabs"), unitContent = node("div"); unitsPopup.append(tabs, unitContent);
    let tabSequence = 0;
    const showUnits = async (id) => {
      const tabTicket = ++tabSequence;
      for (const item of tabs.children) item.classList.toggle("selected", item.dataset.courseId === id);
      unitContent.replaceChildren(loading(t("portal.loading.units.35")));
      try {
        const chosen = dashboards.get(id) || await dashboard(id);
        if (tabTicket !== tabSequence) return; dashboards.set(id, chosen); unitContent.replaceChildren(unitList(chosen));
        const selected = tabs.querySelector(`[data-course-id="${CSS.escape(id)}"] .sequencePercent`); if (selected) selected.textContent = percentLabel(chosen.course.progress);
      } catch (error) { if (tabTicket === tabSequence) unitContent.replaceChildren(errorBox(translateMessage(error.message), () => showUnits(id))); }
    };
    for (const course of catalog.courses) {
      const tab = control("", "sequenceTab", () => showUnits(course.id)); tab.dataset.courseId = course.id;
      tab.append(node("span", "", course.title), node("span", "sequencePercent", percentLabel(dashboards.get(course.id)?.course.progress)));
      tabs.append(tab);
    }
    unitContent.append(unitList(data)); tabs.querySelector(`[data-course-id="${CSS.escape(data.course.id)}"]`)?.classList.add("selected");
    detailsPopup.append(infoRow(t("portal.progress.36"), percentLabel(data.course.progress)), infoRow(t("portal.start.date.37"), apiDate(data.course.start_date)), infoRow(t("portal.end.date.38"), apiDate(data.course.end_date)));
    for (const [trigger, popup] of [[name, unitsPopup], [circle, detailsPopup]]) {
      trigger.addEventListener("mouseenter", () => showPopover(popup)); trigger.addEventListener("focus", () => showPopover(popup));
      trigger.addEventListener("mouseleave", delayPopover); trigger.addEventListener("blur", delayPopover);
      popup.addEventListener("mouseenter", holdPopover); popup.addEventListener("mouseleave", delayPopover);
      popup.addEventListener("focusin", holdPopover); popup.addEventListener("focusout", delayPopover);
    }
    top.append(name, circle);
    const estimate = node("div", "estimatedCompletion"); estimate.append(node("span", "", t("portal.estimated.completion.39")), node("span", "", apiDate(data.course.estimated_completion)));
    frame.append(top, estimate, unitsPopup, detailsPopup); side.append(frame); return side;
  }

  function taskIcon(task, history = false) {
    const successful = ["correct", "passed", "full", "full_credit"].includes(task.result);
    const locked = task.maintenance || task.dependency_ready === false;
    const icon = node("span", `taskIcon ${history ? successful ? "passed" : "ended" : locked ? "locked" : "unlocked"}`);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 22 24"); svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", !history && locked ? "M6 10V7a5 5 0 0 1 10 0v3M4 10h14v11H4zM11 14v3" : history ? "M3 4h16v16H3zM6 12l3 3 7-7" : "M3 4h16v16H3z"); svg.append(path); icon.append(svg);
    icon.setAttribute("role", "img");
    icon.setAttribute("aria-label", history ? t("portal.finished.42") : task.dependency_ready === false ? t("portal.prerequisitesRequired") : task.maintenance ? t("portal.under.maintenance.43") : t("portal.available.44")); return icon;
  }
  function taskSummary(task, history = false) {
    const wrap = node("div", "taskSummaryContent"), heading = node("div", "taskHeading");
    heading.append(taskIcon(task, history), node("strong", "", `${typeNames[task.type] || t("portal.lesson")}${task.retake ? t("portal.retake") : ""}`));
    if (task.reason === "gravity") {
      const reason = node("span", "taskReason", "●"); reason.title = task.reason_message ? translateMessage(task.reason_message) : t("portal.this.task.was.selected.by.the.course.schedule.45"); reason.setAttribute("aria-label", reason.title); heading.append(reason);
    }
    wrap.append(heading);
    if (task.course_title) wrap.append(node("div", "taskCourse", task.course_title));
    wrap.append(node("div", "taskTitle", task.type === 'Review' ? learningTitle(task.title) : task.title));
    const p = percent(task.progress);
    if (!history && p > 0 && p < 100) { const row = node("div", "taskProgressRow"), bar = node("div", "taskProgress"), fill = node("span"); fill.style.width = `${p}%`; bar.append(fill); row.append(bar, node("span", "", percentLabel(p))); wrap.append(row); }
    if (task.maintenance) wrap.append(node("p", "maintenanceNote", task.maintenance_message ? translateMessage(task.maintenance_message) : t("portal.this.content.is.under.maintenance.and.cannot.be.started.yet.46")));
    if (task.type === "Review" && !history && task.due_at) wrap.append(node("div", "taskDueAt", t("review.dueAt", { time: apiDate(task.due_at, true) })));
    if (history) wrap.append(node("div", "taskCompletedAt", t("portal.completedAt", { time: clockTime(task.completed_at) })));
    return wrap;
  }
  function collapseTasks() {
    expandedTask = null;
    for (const item of root.querySelectorAll(".taskDetails")) item.hidden = true;
    for (const item of root.querySelectorAll(".taskToggle")) item.setAttribute("aria-expanded", "false");
  }
  function incompleteTask(task, data) {
    const card = node("article", task.dependency_ready === false ? "portalTask taskLocked" : "portalTask taskUnlocked"); card.dataset.taskId = task.id;
    const toggle = control("", "taskToggle", () => { const was = expandedTask === task.id; collapseTasks(); if (!was) { expandedTask = task.id; details.hidden = false; toggle.setAttribute("aria-expanded", "true"); } });
    toggle.append(taskSummary(task)); toggle.setAttribute("aria-expanded", "false");
    const details = node("div", "taskDetails"); details.hidden = true;
    if (task.status === "paused") details.append(node("p", "taskStatusNote", t("portal.learning.is.paused.you.can.review.previously.studied.content.47")));
    if (["Quiz", "Exam"].includes(task.type)) {
      const info = node("div", "taskPrerequisites"); info.append(infoRow(t("portal.time.limit.48"), task.time_limit_minutes != null ? t("portal.minutes", { count: Number(task.time_limit_minutes).toLocaleString(locale()) }) : task.time_limit_seconds != null ? t("portal.minutes", { count: Math.round(task.time_limit_seconds / 60).toLocaleString(locale()) }) : "—"), infoRow(t("portal.questions.49"), task.question_count == null ? "—" : Number(task.question_count).toLocaleString(locale()))); details.append(info);
    } else {
      const requirements = node("div", "taskPrerequisites"); requirements.append(node("h3", "", t("portal.prerequisites.50")));
      if (!task.prerequisites?.length) requirements.append(node("p", "", t("portal.no.prerequisites.are.required.51")));
      for (const item of task.prerequisites || []) {
        const id = typeof item === "string" ? item : item.id;
        const known = { ...(typeof item === "object" ? item : {}), ...(data.topics || []).find((topic) => topic.id === id), ...(task.prerequisite_details || []).find((topic) => topic.id === id) };
        const mastery = typeof known.mastery === "number" && Number.isFinite(known.mastery) ? known.mastery.toLocaleString(locale(), { maximumFractionDigits: 2 }) : "—";
        const row = node("div", "prerequisiteRow"); row.append(node("span", known.status === "completed" ? "prerequisiteCheck complete" : "prerequisiteCheck", known.status === "completed" ? "✓" : "·"), link(known.title || id, `#/courses/${encode(task.course_id || data.course.id)}/progress?topicId=${encode(id)}`), node("span", "prerequisiteMetrics", t("portal.prerequisiteMetrics", { progress: percentLabel(known.progress), mastery }))); requirements.append(row);
      }
      details.append(requirements);
    }
    if (!task.maintenance) {
      const actions = node("div", "taskStartRow");
      const explicit = task.start_href || task.start_url;
      const target = (task.type === "Lesson" || !task.type) && task.topic_id ? `#/topic/${encode(task.topic_id)}` : typeof explicit === "string" && /^#\/(topic|review|learn|courses)\//.test(explicit) ? explicit : null;
      if (task.dependency_ready === false) {
        const blocked = roundButton(t("portal.prerequisitesRequired"), () => {}); blocked.disabled = true; blocked.classList.add("prerequisiteBlocked"); actions.append(blocked);
      } else if (target && allowed("learn")) actions.append(roundButton(percent(task.progress) > 0 || task.started ? t("portal.resume.52") : t("portal.start.53"), () => navigate(target)));
      else if (target && task.started && allowed("review_history")) actions.append(roundButton(t("portal.review.54"), () => navigate(target)));
      else if (target) actions.append(node("p", "", t("portal.learning.is.not.enabled.for.your.account.please.contact.your.admi.55")));
      else actions.append(node("p", "", t("portal.this.task.is.not.available.to.start.yet.56")));
      appendGuestReset(actions, task);
      details.append(actions);
    }
    card.append(toggle, details); return card;
  }
  function historyCard(task) {
    const card = node("article", "portalTask taskCompleted"); card.dataset.taskId = task.id;
    const summary = control("", "taskToggle", () => navigate(`/learn?taskId=${encode(task.id)}`)); summary.append(taskSummary(task, true)); card.append(summary);
    if (bridge.getAccess()?.role === "guest") {
      const actions = node("div", "taskStartRow completedTaskActions");
      actions.append(roundButton(t("portal.review.54"), () => navigate(`/learn?taskId=${encode(task.id)}`)));
      appendGuestReset(actions, task); card.append(actions);
    }
    return card;
  }
  function appendGuestReset(actions, task) {
    if (bridge.getAccess()?.role !== "guest" || !allowed("learn") || !task.topic_id || (task.type && task.type !== "Lesson")) return;
    const reset = control(t("portal.reset.57"), "portalStart guestReset", async () => {
      if (reset.disabled) return;
      const learner = bridge.getAccess()?.learner_id, ticket = sequence;
      reset.disabled = true; reset.textContent = t("portal.resetting.58");
      actions.parentElement?.querySelector(".resetStatus")?.remove();
      try {
        const state = await call(`state?topic_id=${encode(task.topic_id)}`);
        if (bridge.getAccess()?.learner_id !== learner || ticket !== sequence) return;
        await call(`guest/reset?topic_id=${encode(task.topic_id)}`, {method:"POST", body:{
          request_id:crypto.randomUUID(), expected_revision:state.revision,
          attempt_id:state.attempt_id, step_id:state.active_step_id,
        }});
        if (bridge.getAccess()?.learner_id !== learner || ticket !== sequence) return;
        dashboards.clear(); answerCache.clear();
        await route();
        const status = node("p", "resetStatus", t("portal.progress.has.been.reset.you.can.start.again.59")); status.setAttribute("role", "status");
        root.prepend(status);
      } catch (error) {
        if (bridge.getAccess()?.learner_id !== learner || ticket !== sequence) return;
        const status = node("p", "resetStatus fieldError", translateMessage(error.message)); status.setAttribute("role", "alert"); actions.after(status);
      } finally { reset.disabled = false; reset.textContent = t("portal.reset.57"); }
    });
    reset.title = t("portal.reset.this.lesson.s.progress.and.start.again.60");
    actions.append(reset);
  }
  function renderHistory(data, target) {
    target.replaceChildren(); let date = null; renderedDay = dayKey(new Date());
    if (!allowed("review_history")) { target.append(emptyBox(t("portal.review.is.not.enabled.for.your.account.please.contact.your.admini.61"))); return; }
    for (const task of data.history) {
      const valid = task.completed_at && Number.isFinite(Date.parse(task.completed_at));
      const key = valid ? dayKey(task.completed_at) : "unknown";
      if (key !== date) { target.append(node("h2", "completedTasksDate", valid ? key === dayKey(new Date()) ? t("portal.today.62") : apiDate(task.completed_at) : t("portal.completion.date.unknown.63"))); date = key; }
      target.append(historyCard(task));
    }
  }
  function renderLearn(data, taskId, ticket) {
    historyError = null;
    const layout = node("div", "dashboardLayout"), tasks = node("div", "dashboardTasks"); tasks.id = "dashboardTasks";
    layout.append(courseSidebar(data), tasks); root.replaceChildren(layout);
    if (taskId) { renderAnswers(tasks, taskId, ticket); return; }
    const pending = node("section", "incompleteTasks"); pending.setAttribute("aria-label", t("portal.pending.tasks.64"));
    for (const task of data.tasks) pending.append(incompleteTask(task, data));
    if (!data.tasks.length) {
      const empty = emptyBox(percent(data.course.progress) === 100 ? t("portal.all.current.tasks.in.this.course.are.complete.65") : t("portal.no.tasks.are.available.to.start.we.will.check.again.shortly.66") );
      empty.append(control(t("portal.check.again.67"), "textButton", () => route())); pending.append(empty);
    }
    const historyList = node("section", "completedTasks"); historyList.id = "completedTasks"; historyList.setAttribute("aria-label", t("portal.completed.tasks.68")); renderHistory(data, historyList);
    const more = node("div", "historyMore"); more.id = "historyMore";
    tasks.append(pending, historyList, more); renderMore();
    if (!data.tasks.length && !data.history.length && percent(data.course.progress) !== 100) historyList.append(node("p", "historyEmpty", t("portal.completed.learning.records.will.appear.here.69")));
    if (data.next_review_due_at && !data.tasks.some(task => task.type === "Review")) pending.append(node("p", "reviewNextDue", t("review.nextDue", {time: apiDate(data.next_review_due_at, true)})));
    waitForTasks(ticket, 0);
  }
  function renderMore(error = null) {
    const target = $("historyMore"), data = selectedDashboard(); if (!target || !data) return;
    target.replaceChildren(); historyError = error;
    if (paging) { target.append(loading(t("portal.loading.earlier.records.70"))); return; }
    if (error) target.append(node("p", "fieldError", error));
    if (data.has_more) target.append(control(error ? t("portal.retry.loading.71") : t("portal.load.earlier.records.72"), "textButton", loadMore));
  }
  async function loadMore() {
    const data = selectedDashboard(), id = selectedCourse, ticket = sequence;
    if (paging || !data?.has_more || currentRoute?.path !== "/learn" || currentRoute.params.has("taskId")) return;
    paging = true; renderMore(); let failure = null;
    try {
      const next = await dashboard(id, data.history_cursor);
      if (ticket !== sequence) return;
      if (next.history_revision !== data.history_revision) { dashboards.delete(id); await route(); return; }
      const merged = mergeDashboard(id, next, true); renderHistory(merged, $("completedTasks"));
    } catch (error) { failure = translateMessage(error.message); }
    finally { paging = false; if (ticket === sequence) renderMore(failure); }
  }
  window.addEventListener("scroll", () => {
    clearTimeout(pageTimer); pageTimer = setTimeout(() => { if (!historyError && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 1000) loadMore(); }, 100);
  }, { passive: true });
  setInterval(() => {
    if (document.hidden || currentRoute?.path !== "/learn" || currentRoute.params.has("taskId")) return;
    if (renderedDay !== dayKey(new Date()) && $("completedTasks") && selectedDashboard()) renderHistory(selectedDashboard(), $("completedTasks"));
  }, 30000);
  function waitForTasks(ticket, attempt) {
    const delta = Date.parse(selectedDashboard()?.next_review_due_at) - Date.now();
    const delay = Number.isFinite(delta) && delta > 0 ? Math.max(500, Math.min(15000, delta + 150)) : 15000;
    clearTimeout(refreshTimer); refreshTimer = setTimeout(async () => {
      if (ticket !== sequence || document.hidden) { if (ticket === sequence) waitForTasks(ticket, attempt); return; }
      try {
        const previous = selectedDashboard();
        const fresh = await dashboard(selectedCourse);
        if (ticket !== sequence) return;
        const data = mergeDashboard(selectedCourse, fresh);
        if (root.querySelector(".taskRefreshError") || JSON.stringify([previous?.tasks, previous?.history, previous?.course]) !== JSON.stringify([data.tasks, data.history, data.course])) {
          const y = window.scrollY, expanded = expandedTask;
          renderLearn(data, null, ticket);
          if (expanded) root.querySelector(`[data-task-id="${CSS.escape(expanded)}"] .taskToggle`)?.click();
          window.scrollTo(0, y);
        } else waitForTasks(ticket, attempt + 1);
      } catch (error) {
        if (ticket !== sequence) return;
        const target = root.querySelector(".incompleteTasks"); target?.querySelector(".taskRefreshError")?.remove();
        const message = errorBox(translateMessage(error.message), () => route(), t("portal.unable.to.check.for.new.tasks.73"));
        message.classList.add("taskRefreshError"); target?.prepend(message);
        waitForTasks(ticket, attempt + 1);
      }
    }, delay);
  }

  async function renderAnswers(target, taskId, ticket) {
    const back = control(t("portal.back.to.learning.records.74"), "taskBackButton", () => { if (scrolls.has(learnHref())) history.back(); else navigate("/learn"); });
    if (!allowed("review_history")) { target.replaceChildren(back, emptyBox(t("portal.review.is.not.enabled.for.your.account.please.contact.your.admini.61"))); return; }
    target.replaceChildren(back, loading(t("portal.loading.answers.75")));
    try {
      const result = answerCache.get(taskId) || await call(`tasks/${encode(taskId)}/answers`);
      if (ticket !== sequence) return;
      answerCache.set(taskId, result);
      mathStyle(result.math_css);
      const summary = node("article", "portalTask taskCompleted answerTaskSummary"); summary.append(taskSummary(result.task, true));
      target.replaceChildren(back, summary);
      if (!result.groups?.some((group) => group.answers?.length)) target.append(emptyBox(t("portal.no.answer.records.are.available.76")));
      for (const group of result.groups || []) {
        const section = node("section", "answerGroup"); section.append(node("h2", "answerGroupTitle", learningTitle(group.title)));
        for (const [index, answer] of (group.answers || []).entries()) section.append(answerCard(answer, index, result.task));
        target.append(section);
      }
    } catch (error) { if (ticket === sequence) target.replaceChildren(back, errorBox(translateMessage(error.message), () => renderAnswers(target, taskId, ticket), t("portal.unable.to.load.answers.77"))); }
  }
  function answerCard(answer, index, task) {
    const wrapper = node("article", "answerRecord"), head = node("div", "answerRecordHeader"), question = node("div", "answerQuestion");
    head.append(node("span", "", t("portal.questionIndex", { index: (index + 1).toLocaleString(locale()) })));
    const helpHost = node("div", "answerHelpHost"), helpMenu = node("div", "answerHelpMenu"); helpMenu.hidden = true;
    const help = control("?", "answerHelp", () => { helpMenu.hidden = !helpMenu.hidden; });
    help.title = t("portal.question.help.78"); help.setAttribute("aria-label", t("portal.question.help.78")); help.setAttribute("aria-haspopup", "true");
    helpMenu.append(control(t("portal.report.a.content.error.79"), "", () => { helpMenu.hidden = true; showFeedback({ course_id: task.course_id, topic_id: task.topic_id, task_id: task.id, question_id: answer.question_id }); }));
    helpHost.addEventListener("mouseenter", () => { helpMenu.hidden = false; });
    helpHost.addEventListener("mouseleave", () => { helpMenu.hidden = true; });
    helpHost.append(help, helpMenu); head.append(helpHost);
    const stem = trustedContent(answer.html);
    const reportContext = {course_id: task.course_id, topic_id: task.topic_id, task_id: task.id, question_id: answer.question_id};
    reportableContent(stem, reportContext, `question:${answer.question_id}`, answer.content_version);
    question.append(stem);
    if (answer.interaction && answer.interaction.type !== 'text') question.append(questionInput(answer.interaction,
      {id: `historyInteraction-${index}`, stem, value: answer.answer || '', disabled: true}).element);
    const meta = node("div", "answerMetadata");
    const difficulty = answer.difficulty == null ? "—" : ({ easy: t("portal.easy"), medium: t("portal.medium"), hard: t("portal.hard") }[answer.difficulty] || String(answer.difficulty));
    const elapsed = answer.elapsed_seconds == null ? "—" : t("portal.seconds", { count: Math.round(answer.elapsed_seconds).toLocaleString(locale()) });
    meta.append(node("span", "", t("portal.difficulty", { value: difficulty })), node("span", "", apiDate(answer.answered_at, true)), node("span", "", t("portal.elapsed", { value: elapsed })));
    const outcomes = { correct: t("portal.correct.80"), incorrect: t("portal.incorrect.81"), full: t("portal.full.credit.82"), full_credit: t("portal.full.credit.82"), partial: t("portal.partial.credit.83"), partial_credit: t("portal.partial.credit.83"), none: t("portal.no.credit.84"), no_credit: t("portal.no.credit.84"), unanswered: t("portal.unanswered.85") };
    const result = outcomes[answer.result] || (answer.correct === true ? t("portal.correct.80") : answer.correct === false ? t("portal.incorrect.81") : "—");
    const outcome = node("span", `answerOutcome ${answer.correct === true ? "correct" : answer.correct === false ? "incorrect" : ""}`, result); meta.append(outcome);
    const explanation = node("div", "answerExplanation"); explanation.hidden = true;
    if (answer.explanation_html) explanation.append(reportableContent(trustedContent(answer.explanation_html), reportContext,
      `explanation:${answer.question_id}`, answer.content_version)); else explanation.append(node("p", "", t("portal.no.explanation.is.available.for.this.question.86")));
    if (answer.reason) explanation.append(node('p', 'feedbackReason', answer.reason));
    if (answer.correct !== true) { explanation.append(node("h3", "", t("portal.your.answer.87")), node("pre", "historyYourAnswer", answer.answer == null || answer.answer === "" ? t("portal.unanswered.85") : answer.answer_display ?? answer.answer)); }
    const toggle = control(t("portal.show.explanation.88"), "answerExplanationToggle textButton", () => { explanation.hidden = !explanation.hidden; toggle.textContent = explanation.hidden ? t("portal.show.explanation.88") : t("portal.hide.explanation.89"); toggle.setAttribute("aria-expanded", String(!explanation.hidden)); }); toggle.setAttribute("aria-expanded", "false");
    question.addEventListener("click", (event) => { if (!event.target.closest("a,button")) toggle.click(); });
    wrapper.append(head, question, meta, toggle, explanation); return wrapper;
  }

  function renderCourseProgress(data, params) {
    document.title = pageTitle(data.course.title);
    const header = node("div", "progressPageHeading"); header.append(link(t("portal.back.to.learning.home.90"), learnHref(), "textButton"), node("h1", "portalPageTitle", data.course.title));
    const details = node("section", "courseProgressSummary"); details.append(infoRow(t("portal.progress.36"), percentLabel(data.course.progress)), infoRow(t("portal.start.date.37"), apiDate(data.course.start_date)), infoRow(t("portal.end.date.38"), apiDate(data.course.end_date)), infoRow(t("portal.estimated.completion.39"), apiDate(data.course.estimated_completion)), control(t("portal.view.knowledge.map.91"), "textButton", () => openGraph(data)));
    root.replaceChildren(header, details);
    const max = Math.max(0, ...(data.units || []).map((unit) => Number(unit.topic_count) || 0));
    for (const unit of data.units || []) {
      const section = node("section", "progressUnit"); section.dataset.progressId = unit.id;
      section.append(node("h2", "", t("portal.unitCount", { title: unit.title, count: Number(unit.topic_count).toLocaleString(locale()) })), unitBar(unit, max));
      for (const topic of (data.topics || []).filter((topic) => topic.unit_id === unit.id || unit.topic_ids?.includes(topic.id))) {
        const row = node("div", "progressTopic"); row.dataset.progressId = topic.id;
        row.append(node("span", "", topic.title), node("span", "", statusNames[topic.status] || "—"), node("span", "", percentLabel(topic.progress)));
        if (topic.id === params.get("topicId")) row.classList.add("highlighted"); section.append(row);
      }
      root.append(section);
    }
    if (!data.units?.length) root.append(emptyBox(t("portal.course.units.are.not.available.yet.34")));
  }

  const graphDialog = node("dialog", "graphDialog"); graphDialog.setAttribute("aria-label", t("portal.course.knowledge.map.92"));
  const graphHeading = node("div", "dialogHeading"), graphTitle = node("h2", "", t("portal.knowledge.map.93")), graphContent = node("div", "graphContent");
  const graphClose = control("×", "iconButton", () => graphDialog.close()); graphClose.setAttribute("aria-label", t("portal.close.knowledge.map.94"));
  graphHeading.append(graphTitle, graphClose); graphDialog.append(graphHeading, graphContent); document.body.append(graphDialog);
  graphDialog.addEventListener("close", () => { document.body.classList.remove("portalModalOpen"); window.scrollTo(0, graphScroll); });
  let graphRequest = 0;
  async function openGraph(data) {
    const request = ++graphRequest, ticket = sequence, learner = bridge.getAccess()?.learner_id;
    hidePopovers(); graphScroll = window.scrollY; graphTitle.textContent = t("portal.graphTitle", { title: data.course.title });
    graphContent.replaceChildren(loading(t("portal.initializing.95"))); graphDialog.showModal(); document.body.classList.add("portalModalOpen");
    try {
      const graph = await call(`graphs/${encode(data.course.id)}`);
      if (request !== graphRequest || ticket !== sequence || learner !== bridge.getAccess()?.learner_id || !graphDialog.open) return;
      renderCourseGraph(graphContent, graph, {language: getLanguage(), onOpenTopic: id => {
        graphDialog.close(); navigate(`/topic/${encode(id)}`);
      }});
    } catch (error) {
      if (request === graphRequest && ticket === sequence && graphDialog.open) graphContent.replaceChildren(errorBox(error.message, () => { graphDialog.close(); openGraph(data); }));
    }
  }

  function renderHelp() {
    const article = node("article", "guidePage"), body = node("div", "guideBody courseContent");
    const questions = [
      [t("portal.is.my.progress.saved.99"), t("portal.progress.is.saved.automatically.guests.should.continue.in.the.sam.100")],
      [t("portal.how.do.i.use.demo.browsing.101"), t("portal.as.a.guest.select.demo.browsing.beside.your.avatar.to.fill.answer.102")],
      [t("portal.what.does.reset.clear.103"), t("portal.as.a.guest.click.the.yellow.reset.button.beside.a.lesson.to.clear.104")],
      [t("portal.what.if.i.find.a.problem.or.need.help.105"), t("portal.see.guide.for.learning.instructions.to.report.a.question.or.expla.106")],
    ];
    for (const [question, answer] of questions) body.append(node("h2", "", question), node("p", "", answer));
    body.append(link(t("portal.view.guide.107"), "#/guide"));
    article.append(node("h1", "portalPageTitle", t("portal.q.a.108")), body); root.replaceChildren(article);
  }
  async function renderGuide(ticket) {
    guideLoaded = false; guideVersion = null;
    const article = node("article", "guidePage"), heading = node("h1", "portalPageTitle", t("portal.guide.109")), notice = node("div", "guideNotice"), body = node("div", "guideBody courseContent"), updated = node("p", "guideUpdated");
    notice.id = "guideNotice"; body.id = "guideBody"; updated.id = "guideUpdated";
    body.append(loading(t("portal.loading.guide.110"))); article.append(heading, notice, body, updated); root.replaceChildren(article);
    await updateGuide(ticket);
  }
  async function guideContent(html, ticket, subjectId) {
    const template = document.createElement('template'); template.innerHTML = html || '';
    const urls = [], assets = new Map();
    try {
      if (subjectId && subjectId !== 'math') {
        const origin = new URL(bridge.getApiOrigin()).origin;
        const prefix = `/subject-guide-assets/${encode(subjectId)}/`;
        await Promise.all([...template.content.querySelectorAll('img[src],a[href]')].map(async item => {
          const attribute = item.tagName === 'IMG' ? 'src' : 'href';
          const url = new URL(item.getAttribute(attribute), origin);
          // External images/links retain their normal URL. Credentials are only
          // sent through the exact, subject-scoped trusted asset transport.
          if (url.origin !== origin || !url.pathname.startsWith(prefix)) return;
          if (!assets.has(url.href)) assets.set(url.href, bridge.fetchGuideAsset(url.href, subjectId).then(blob => {
            const local = URL.createObjectURL(blob); urls.push(local); return local;
          }));
          item.setAttribute(attribute, await assets.get(url.href));
          if (attribute === 'href') item.setAttribute('download', decodeURIComponent(url.pathname.split('/').at(-1)));
        }));
      }
      if (ticket !== sequence) throw new Error('stale guide');
      releaseGuideAssets(); guideObjectUrls = urls;
      return template.content;
    } catch (error) {
      // Wait for all started downloads before revoking their temporary URLs.
      await Promise.allSettled(assets.values());
      for (const url of urls) URL.revokeObjectURL(url);
      throw error;
    }
  }
  async function updateGuide(ticket) {
    clearTimeout(refreshTimer);
    try {
      const result = await call("guide"); if (ticket !== sequence || currentRoute?.path !== "/guide") return;
      const body = $("guideBody"), notice = $("guideNotice");
      if (result.status === "unavailable") throw new Error(result.message ? translateMessage(result.message) : t("portal.the.guide.is.temporarily.unavailable.111"));
      notice.replaceChildren();
      if (!guideLoaded || guideVersion !== result.version) {
        const y = window.scrollY;
        if (result.status === "empty") { releaseGuideAssets(); body.replaceChildren(emptyBox(t("portal.the.guide.has.not.been.written.yet.112"))); }
        else if (result.status === "ready") {
          const fragment = await guideContent(result.html, ticket, currentRoute.subjectId);
          if (ticket !== sequence) return;
          body.replaceChildren(fragment); mathStyle(result.math_css, "guideMathStyle");
        }
        else throw new Error(t("portal.the.guide.returned.an.unrecognized.status.113"));
        guideVersion = result.version; guideLoaded = true; window.scrollTo(0, y);
      }
      $("guideUpdated").textContent = result.modified_at ? t("portal.updatedAt", { date: apiDate(result.modified_at, true) }) : "";
    } catch (error) {
      if (ticket !== sequence) return;
      if (error.status === 401 || error.status === 403) { releaseGuideAssets(); guideLoaded = false; }
      if (!guideLoaded) $("guideBody")?.replaceChildren();
      $("guideNotice")?.replaceChildren(errorBox(translateMessage(error.message), () => updateGuide(ticket), t("portal.unable.to.update.the.guide.114")));
    } finally { if (ticket === sequence && currentRoute?.path === "/guide") refreshTimer = setTimeout(() => { if (document.hidden) updateGuideLater(ticket); else updateGuide(ticket); }, 5000); }
  }
  function updateGuideLater(ticket) { if (ticket === sequence) refreshTimer = setTimeout(() => updateGuide(ticket), 5000); }

  function renderSettings() {
    const page = node("section", "settingsPage"), form = node("form", "profileForm");
    page.append(node("h1", "portalPageTitle", t("portal.settings.115")));
    const displayLabel = node("label", "", t("portal.display.name.116")), display = node("input"); display.id = "profileDisplayName"; displayLabel.htmlFor = display.id; const defaultGuestName = bridge.getAccess()?.role === "guest" && (!profile.display_name || profile.display_name === "游客"); display.value = defaultGuestName ? t("portal.guest.12") : profile.display_name || ""; display.maxLength = 100; display.required = true;
    const zoneLabel = node("label", "", t("portal.time.zone.117")), zone = node("input"); zone.id = "profileTimezone"; zoneLabel.htmlFor = zone.id; zone.value = timezone(); zone.required = true; zone.setAttribute("list", "timezoneOptions");
    const zones = node("datalist"); zones.id = "timezoneOptions";
    for (const value of Intl.supportedValuesOf?.("timeZone") || [timezone(), "UTC"]) { const option = node("option"); option.value = value; zones.append(option); }
    const hint = node("p", "fieldHint", t("portal.history.dates.completion.times.and.answer.times.use.this.time.zon.118")), status = node("p", "profileStatus"); status.setAttribute("role", "status");
    const submit = node("button", "primaryButton", t("portal.save.119")); submit.type = "submit";
    const languageLabel = node("label", "", t("portal.language")), language = node("select");
    language.id = "profileLanguage"; languageLabel.htmlFor = language.id;
    for (const [value, key] of [["zh-CN", "portal.languageZh"], ["en", "portal.languageEn"]]) {
      const option = node("option", "", t(key)); option.value = value; language.append(option);
    }
    language.value = getLanguage();
    language.addEventListener("change", () => { setLanguage(language.value); location.reload(); });
    const languageHint = node("p", "fieldHint", t("portal.languageHint"));
    form.append(languageLabel, language, languageHint, displayLabel, display, zoneLabel, zone, zones, hint, status, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (submit.disabled) return; submit.disabled = true; status.textContent = t("portal.saving.120");
      try { profile = await call("profile", { method: "POST", body: { display_name: defaultGuestName && display.value.trim() === t("portal.guest.12") ? profile.display_name || "游客" : display.value.trim(), timezone: zone.value.trim() } }); updateUser(); status.textContent = t("portal.settings.saved.121"); }
      catch (error) { status.textContent = translateMessage(error.message); }
      finally { submit.disabled = false; }
    });
    page.append(form); root.replaceChildren(page);
  }

  const feedbackDialog = node("dialog", "feedbackDialog"), feedbackForm = node("form"), feedbackHeading = node("div", "dialogHeading"), feedbackTitle = node("h2", "", t("portal.feedback.122"));
  const feedbackClose = control("×", "iconButton", () => feedbackDialog.close()); feedbackClose.setAttribute("aria-label", t("portal.close.feedback.123")); feedbackHeading.append(feedbackTitle, feedbackClose);
  const feedbackLabel = node("label", "", t("portal.describe.the.issue.you.encountered.124")), feedbackInput = node("textarea"); feedbackInput.id = "feedbackMessage"; feedbackLabel.htmlFor = feedbackInput.id; feedbackInput.maxLength = 4000; feedbackInput.required = true; feedbackInput.rows = 7;
  const feedbackStatus = node("p", "feedbackStatus"); feedbackStatus.setAttribute("role", "status");
  const correctionFields = node('div', 'correctionFields'); correctionFields.hidden = true;
  const originalPreview = node('p', 'correctionPreview'), replacementPreview = node('p', 'correctionPreview');
  const replacementLabel = node('label', '', t('correction.replacement')), replacementInput = node('textarea');
  replacementInput.id = 'correctionReplacement'; replacementInput.maxLength = 2000; replacementInput.rows = 2;
  replacementLabel.htmlFor = replacementInput.id;
  correctionFields.append(node('h3', '', t('correction.original')), originalPreview,
    node('h3', '', t('correction.preview')), replacementPreview, replacementLabel, replacementInput);
  function previewCorrection() {
    const quote = feedbackContext.correction?.quote;
    if (!quote) return;
    for (const [preview, value] of [[originalPreview, quote.exact], [replacementPreview, replacementInput.value]]) {
      preview.replaceChildren(document.createTextNode(quote.prefix), node('strong', '', value), document.createTextNode(quote.suffix));
    }
  }
  replacementInput.addEventListener('input', previewCorrection);
  const feedbackActions = node("div", "dialogActions"), feedbackCancel = control(t("portal.cancel.125"), "secondaryButton", () => feedbackDialog.close()), feedbackSubmit = node("button", "primaryButton", t("portal.submit.126")); feedbackSubmit.type = "submit";
  feedbackActions.append(feedbackCancel, feedbackSubmit); feedbackForm.append(feedbackHeading, correctionFields, feedbackLabel, feedbackInput, feedbackStatus, feedbackActions); feedbackDialog.append(feedbackForm); document.body.append(feedbackDialog);
  function showFeedback(context = {}) {
    if (feedbackBusy || feedbackDialog.open) return;
    const matchesSelected = !context.topic_id || selectedDashboard()?.topics?.some((topic) => topic.id === context.topic_id);
    feedbackContext = { ...(selectedCourse && matchesSelected ? { course_id: selectedCourse } : {}), ...context };
    const correction = Boolean(context.correction);
    feedbackTitle.textContent = correction ? t('correction.title') : context.question_id ? t("portal.report.a.content.error.127") : t("portal.feedback.122");
    correctionFields.hidden = !correction; replacementInput.value = ''; replacementInput.disabled = false;
    feedbackLabel.textContent = t(correction ? 'correction.comment' : 'portal.describe.the.issue.you.encountered.124');
    feedbackInput.required = !correction; feedbackInput.rows = correction ? 2 : 7; feedbackInput.maxLength = correction ? 2000 : 4000;
    previewCorrection();
    feedbackStatus.textContent = ""; feedbackInput.value = ""; feedbackInput.hidden = false; feedbackLabel.hidden = false; feedbackSubmit.hidden = false; feedbackCancel.textContent = t("portal.cancel.125");
    if (!feedbackDialog.open) feedbackDialog.showModal();
    (correction ? replacementInput : feedbackInput).focus();
  }
  feedbackForm.addEventListener("submit", async (event) => {
    event.preventDefault(); if (feedbackBusy || (!feedbackContext.correction && !feedbackInput.value.trim())) return;
    feedbackBusy = true; feedbackSubmit.disabled = true; feedbackClose.disabled = true; feedbackCancel.disabled = true; feedbackStatus.textContent = t("portal.saving.feedback.128");
    try {
      feedbackInput.disabled = true; replacementInput.disabled = true;
      const correction = feedbackContext.correction ? {...feedbackContext.correction,
        operation: replacementInput.value === '' ? 'delete' : 'replace', replacement: replacementInput.value, comment: feedbackInput.value.trim()} : null;
      const result = await call("feedback", { method: "POST", body: { message: feedbackInput.value.trim() || t('correction.title'),
        ...feedbackContext, ...(correction ? {correction} : {}) } });
      if (result.status !== "saved") throw new Error(t("portal.feedback.was.not.confirmed.as.saved.please.try.again.129"));
      feedbackStatus.textContent = t("portal.feedback.saved.thank.you.130"); correctionFields.hidden = true; feedbackInput.hidden = true; feedbackLabel.hidden = true; feedbackSubmit.hidden = true; feedbackCancel.textContent = t("portal.close.131");
    } catch (error) { feedbackStatus.textContent = translateMessage(error.message); }
    finally { feedbackBusy = false; feedbackSubmit.disabled = false; feedbackClose.disabled = false; feedbackCancel.disabled = false; feedbackInput.disabled = false; replacementInput.disabled = false; }
  });
  feedbackDialog.addEventListener("cancel", (event) => { if (feedbackBusy) event.preventDefault(); });
  feedbackDialog.addEventListener('close', () => { feedbackContext = {}; feedbackInput.value = ''; replacementInput.value = ''; });
  const contentReporting = installContentReporting(showFeedback);

  window.addEventListener("hashchange", route);
  history.scrollRestoration = "manual";
  return {
    start: route,
    setIdentity(next) {
      if (JSON.stringify(identity) !== JSON.stringify(next)) catalogPicker.reset();
      if (identity?.learner_id !== next?.learner_id || identity?.role !== next?.role) subjects = null;
      if (identity?.learner_id && next?.learner_id !== identity.learner_id) profile = null;
      if (identity && (JSON.stringify(identity.features) !== JSON.stringify(next?.features) || JSON.stringify(identity.topics) !== JSON.stringify(next?.topics))) {
        catalog = null; dashboards.clear(); answerCache.clear();
      }
      identity = next; updateUser();
    },
    suspendIdentity() {
      catalogPicker.reset();
      contentReporting.clear();
      sequence += 1; stopPageWork(); showMenu(false); bridge.leaveTopic();
      subjects = null; catalog = null; profile = null; selectedCourse = null; dashboards.clear(); answerCache.clear(); scrolls.clear();
      paging = false; busyCourse = false; expandedTask = null; root.replaceChildren(); root.hidden = true;
      if (graphDialog.open) graphDialog.close();
      if (feedbackDialog.open) feedbackDialog.close();
      feedbackInput.value = "";
    },
    permissionsChanged() { catalogPicker.reset(); catalog = null; dashboards.clear(); answerCache.clear(); },
    async identityChanged() { review.reset(); atomic.reset(); subjects = null; catalog = null; profile = null; dashboards.clear(); answerCache.clear(); scrolls.clear(); await route(); },
    progressChanged() { /* Returning to Learn reads current server progress without changing it. */ },
    showFeedback,
  };
}
