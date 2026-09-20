const $ = (id) => document.getElementById(id);
const node = (tag, className = "", text) => {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = text;
  return item;
};
const link = (text, href, className = "") => { const item = node("a", className, text); item.href = href; return item; };
const control = (text, className, action) => { const item = node("button", className, text); item.type = "button"; item.addEventListener("click", action); return item; };
const percent = (value) => Number.isFinite(Number(value)) && value !== null ? Math.max(0, Math.min(100, Number(value))) : null;
const percentLabel = (value) => percent(value) === null ? "—" : `${Math.round(percent(value))}%`;
const encode = encodeURIComponent;
const typeNames = { Placement: "Diagnostic", Supplemental: "Supplemental Diagnostic", Quiz: "Assessment", Exam: "Assessment" };
const statusNames = { not_started: "尚未学习", in_progress: "学习中", paused: "已暂时终止", completed: "已完成" };
const roundButton = (text, action) => control(text, "portalStart", action);

export function initPortal(bridge) {
  const root = $("portalContent");
  let catalog = null, profile = null, identity = null, selectedCourse = null;
  let sequence = 0, currentHash = "", currentRoute = null, busyCourse = false;
  let expandedTask = null, paging = false, historyError = null, guideVersion = null, guideLoaded = false;
  let refreshTimer = null, menuTimer = null, popoverTimer = null, pageTimer = null;
  let graphScroll = 0, feedbackContext = {}, feedbackBusy = false, renderedDay = null;
  const dashboards = new Map(), scrolls = new Map(), answerCache = new Map();
  const routeKey = () => location.hash || "#/learn";
  const selectedDashboard = () => dashboards.get(selectedCourse);
  const timezone = () => profile?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const query = (fields) => new URLSearchParams(Object.entries(fields).filter(([, value]) => value !== null && value !== undefined && value !== "")).toString();
  const call = (path, options) => bridge.request(path, options);
  const allowed = (feature) => Boolean(bridge.getAccess()?.features?.includes(feature));
  const learnHref = () => "#/learn";
  const apiDate = (value, withTime = false) => {
    if (!value || !Number.isFinite(Date.parse(value))) return "—";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: timezone(), year: "numeric", month: "2-digit", day: "2-digit", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}) }).format(new Date(value));
  };
  const dayKey = (value) => new Intl.DateTimeFormat("en-CA", { timeZone: timezone(), year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
  const clockTime = (value) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat("zh-CN", { timeZone: timezone(), hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";

  function navigate(path, replace = false) {
    const hash = path.startsWith("#") ? path : `#${path}`;
    if (location.hash === hash) return route();
    if (replace) { history.replaceState(null, "", hash); return route(); }
    location.hash = hash;
  }
  function parseRoute() {
    const raw = routeKey().replace(/^#/, "");
    const [path, search = ""] = raw.split("?");
    return { path, params: new URLSearchParams(search) };
  }
  function loading(text = "正在读取学习内容…") {
    const box = node("section", "portalLoading");
    box.setAttribute("aria-live", "polite"); box.append(node("span", "spinner"), node("p", "", text)); return box;
  }
  function errorBox(message, retry, heading = "暂时无法读取") {
    const box = node("section", "portalMessage"); box.setAttribute("role", "status");
    box.append(node("h2", "", heading), node("p", "", message || "服务暂不可用，请稍后重试。"));
    if (retry) box.append(control("重试", "primaryButton", retry));
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
  function updateUser() {
    const current = bridge.getAccess();
    const expired = bridge.getIdentityProblem();
    const name = expired ? "未登录" : profile?.display_name || current?.display_name || "游客";
    const parts = name.trim().split(/\s+/);
    const initials = /^[A-Za-z]/.test(name) ? (parts.length > 1 ? parts[0][0] + parts.at(-1)[0] : name.slice(0, 2)).toUpperCase() : [...name].slice(0, 2).join("");
    $("userMenuButton").textContent = initials || "游";
    $("userMenuButton").setAttribute("aria-label", `用户菜单，${name}`);
    $("menuDisplayName").textContent = name;
    $("menuRole").textContent = expired ? "登录已失效" : current?.role === "account" ? "学习账号" : "游客身份";
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

  async function basics() {
    const [nextCatalog, nextProfile] = await Promise.all([call("catalog"), call("profile")]);
    if (!Array.isArray(nextCatalog.courses)) throw new Error("课程目录暂时无法读取。");
    catalog = nextCatalog; profile = nextProfile; selectedCourse = catalog.selected_course_id || null;
    if (!profile.timezone) {
      try { profile = await call("profile", { method: "POST", body: { timezone: timezone() } }); } catch { /* Use browser time while a later settings save can retry. */ }
    }
    updateUser();
  }
  async function dashboard(id, cursor = null) {
    const data = await call(`dashboard?${query({ course_id: id, cursor, limit: 25 })}`);
    if (!data.course || !Array.isArray(data.tasks) || !Array.isArray(data.history)) throw new Error("课程学习记录暂时无法读取。");
    return data;
  }
  function mergeDashboard(id, data, append = false) {
    const previous = dashboards.get(id);
    const all = append ? [...(previous?.history || []), ...data.history] : [...data.history, ...(previous?.history || [])];
    const historyRows = [...new Map(all.map((task) => [task.id, task])).values()]
      .sort((a, b) => Date.parse(b.completed_at || 0) - Date.parse(a.completed_at || 0));
    const merged = { ...data, history: historyRows };
    if (!append && previous?.history?.length > data.history.length && previous.history_cursor !== undefined) {
      merged.history_cursor = previous.history_cursor; merged.has_more = previous.has_more;
    }
    dashboards.set(id, merged); return merged;
  }
  function stopPageWork() { clearTimeout(refreshTimer); clearTimeout(pageTimer); hidePopovers(); }
  async function route() {
    if (currentHash) scrolls.set(currentHash, window.scrollY);
    currentHash = routeKey(); currentRoute = parseRoute();
    const ticket = ++sequence;
    stopPageWork(); showMenu(false); collapseTasks();
    if (graphDialog.open) graphDialog.close();
    root.hidden = false; bridge.leaveTopic();
    if (bridge.getIdentityProblem()) { root.hidden = true; root.replaceChildren(); return; }
    setNavigation(currentRoute.path.startsWith("/courses") ? "courses" : currentRoute.path === "/guide" ? "guide" : "learn");
    root.replaceChildren(loading()); window.scrollTo(0, 0);
    try {
      if (catalog && profile) await bridge.refreshAccess();
      if (!catalog || !profile) await basics();
      if (ticket !== sequence) return;
      const { path, params } = currentRoute;
      if (path === "/learn" || path === "/") {
        let viewedCourse = selectedCourse;
        if (!viewedCourse && params.get("taskId")) {
          const result = await call(`tasks/${encode(params.get("taskId"))}/answers`);
          if (ticket !== sequence) return;
          answerCache.set(params.get("taskId"), result); viewedCourse = result.task?.course_id;
        }
        if (!viewedCourse) { navigate("/courses", true); return; }
        document.title = "LEARN · 数学学习";
        const data = mergeDashboard(viewedCourse, await dashboard(viewedCourse));
        if (ticket !== sequence) return;
        renderLearn(data, params.get("taskId"), ticket);
      } else if (path === "/courses") {
        document.title = "COURSES · 数学学习"; renderCourses();
      } else if (path === "/guide") {
        document.title = "GUIDE · 数学学习"; await renderGuide(ticket);
      } else if (path === "/help") {
        document.title = "Q&A · 数学学习"; renderHelp();
      } else if (path === "/settings") {
        document.title = "个人设置 · 数学学习"; renderSettings();
      } else if (/^\/courses\/[^/]+\/progress$/.test(path)) {
        const id = decodeURIComponent(path.split("/")[2]);
        const data = await dashboard(id);
        if (ticket !== sequence) return;
        dashboards.set(id, data); renderCourseProgress(data, params);
      } else if (/^\/topic\/[^/]+$/.test(path)) {
        root.hidden = true;
        await bridge.openTopic(decodeURIComponent(path.split("/")[2]));
      } else root.replaceChildren(errorBox("这个页面不存在。", () => navigate("/learn"), "未找到页面"));
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
      const forbidden = error.status === 403 && ["feature_forbidden", "topic_forbidden", "course_forbidden"].includes(error.code);
      root.hidden = false; root.replaceChildren(errorBox(error.message, forbidden ? () => navigate("/courses") : () => location.reload(), forbidden ? "当前内容尚未开放" : "服务暂不可用"));
    }
  }

  function renderCourses() {
    const heading = node("h1", "portalPageTitle", "COURSES"), grid = node("div", "courseGrid");
    root.replaceChildren(heading, grid);
    if (!catalog.courses.length) { grid.append(emptyBox(bridge.getAccess()?.role === "account" ? "管理员尚未为你的账号开放课程，请联系管理员。" : "暂时没有可学习的课程。")); return; }
    if (bridge.getAccess()?.role === "account" && !catalog.courses.some((course) => course.available)) root.insertBefore(emptyBox("管理员尚未为你的账号开放课程，请联系管理员。"), grid);
    for (const course of catalog.courses) {
      const card = node("article", "courseChoice"); card.dataset.courseId = course.id;
      card.append(node("h2", "", course.title), node("p", "courseDescription", course.description || ""));
      const learn = control("Learn", "primaryButton courseLearn", async () => {
        if (busyCourse) return; busyCourse = true;
        for (const item of root.querySelectorAll(".courseLearn")) item.disabled = true;
        learn.textContent = "正在选择…"; card.querySelector(".fieldError")?.remove();
        try {
          await call("catalog/select", { method: "POST", body: { course_id: course.id } });
          selectedCourse = course.id; catalog.selected_course_id = course.id; scrolls.delete("#/learn");
          navigate("/learn");
        } catch (error) {
          card.append(node("p", "fieldError", error.message)); learn.textContent = "Learn";
          for (const item of root.querySelectorAll(".courseLearn")) item.disabled = item.dataset.available === "false";
        } finally { busyCourse = false; }
      });
      learn.dataset.available = String(course.available !== false); learn.disabled = course.available === false;
      card.append(learn); if (!course.available) card.append(node("p", "courseAvailability", "当前暂无可进入的学习内容。"));
      grid.append(card);
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
    bar.setAttribute("aria-label", `${unit.title}，${percentLabel(unit.progress)}`);
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
      row.append(link(`${index + 1}. ${unit.title} (${unit.topic_count} topics)`, `#/courses/${encode(data.course.id)}/progress?unitId=${encode(unit.id)}`), unitBar(unit, max)); list.append(row);
    });
    if (!list.childElementCount) list.append(node("p", "", "课程单元尚未提供。"));
    return list;
  }
  function courseSidebar(data) {
    const side = node("aside", "courseSidebar"), frame = node("section", "courseOverview"), top = node("div", "courseOverviewTop");
    const name = link(data.course.title, `#/courses/${encode(data.course.id)}/progress`, "courseNameLink");
    const circle = control(percentLabel(data.course.progress), "coursePercent", () => openGraph(data)); circle.setAttribute("aria-label", `课程完成 ${percentLabel(data.course.progress)}，查看知识图谱`);
    const unitsPopup = node("div", "coursePopover sequenceUnits"), detailsPopup = node("div", "coursePopover progressDetails");
    unitsPopup.hidden = true; detailsPopup.hidden = true;
    const tabs = node("div", "sequenceTabs"), unitContent = node("div"); unitsPopup.append(tabs, unitContent);
    let tabSequence = 0;
    const showUnits = async (id) => {
      const tabTicket = ++tabSequence;
      for (const item of tabs.children) item.classList.toggle("selected", item.dataset.courseId === id);
      unitContent.replaceChildren(loading("正在读取单元…"));
      try {
        const chosen = dashboards.get(id) || await dashboard(id);
        if (tabTicket !== tabSequence) return; dashboards.set(id, chosen); unitContent.replaceChildren(unitList(chosen));
        const selected = tabs.querySelector(`[data-course-id="${CSS.escape(id)}"] .sequencePercent`); if (selected) selected.textContent = percentLabel(chosen.course.progress);
      } catch (error) { if (tabTicket === tabSequence) unitContent.replaceChildren(errorBox(error.message, () => showUnits(id))); }
    };
    for (const course of catalog.courses) {
      const tab = control("", "sequenceTab", () => showUnits(course.id)); tab.dataset.courseId = course.id;
      tab.append(node("span", "", course.title), node("span", "sequencePercent", percentLabel(dashboards.get(course.id)?.course.progress)));
      tabs.append(tab);
    }
    unitContent.append(unitList(data)); tabs.querySelector(`[data-course-id="${CSS.escape(data.course.id)}"]`)?.classList.add("selected");
    detailsPopup.append(infoRow("Progress", percentLabel(data.course.progress)), infoRow("Start Date", apiDate(data.course.start_date)), infoRow("End Date", apiDate(data.course.end_date)));
    for (const [trigger, popup] of [[name, unitsPopup], [circle, detailsPopup]]) {
      trigger.addEventListener("mouseenter", () => showPopover(popup)); trigger.addEventListener("focus", () => showPopover(popup));
      trigger.addEventListener("mouseleave", delayPopover); trigger.addEventListener("blur", delayPopover);
      popup.addEventListener("mouseenter", holdPopover); popup.addEventListener("mouseleave", delayPopover);
      popup.addEventListener("focusin", holdPopover); popup.addEventListener("focusout", delayPopover);
    }
    top.append(name, circle);
    const estimate = node("div", "estimatedCompletion"); estimate.append(node("span", "", "Estimated completion"), node("span", "", apiDate(data.course.estimated_completion)));
    frame.append(top, estimate, unitsPopup, detailsPopup); side.append(frame); return side;
  }

  function taskIcon(task, history = false) {
    const successful = ["correct", "passed", "full", "full_credit"].includes(task.result);
    const icon = node("span", `taskIcon ${history ? successful ? "passed" : "ended" : task.maintenance ? "locked" : "unlocked"}`, history ? successful ? "✓" : task.result === "incorrect" || task.result === "failed" ? "×" : "✓" : "");
    if (!history) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 22 24");
      const path = document.createElementNS(svg.namespaceURI, "path");
      path.setAttribute("d", task.maintenance ? "M6 10V7a5 5 0 0 1 10 0v3M4 10h14v11H4zM11 14v3" : "M7 10V7a5 5 0 0 1 10 0M4 10h14v11H4zM11 14v3"); svg.append(path); icon.append(svg);
    }
    icon.setAttribute("aria-label", history ? successful ? "已通过" : task.result === "incorrect" || task.result === "failed" ? "未通过" : "已结束" : task.maintenance ? "维护中" : "可查看"); return icon;
  }
  function taskSummary(task, history = false) {
    const wrap = node("div", "taskSummaryContent"), heading = node("div", "taskHeading");
    heading.append(taskIcon(task, history), node("strong", "", `${typeNames[task.type] || task.type || "Lesson"}${task.retake ? " (Retake)" : ""}`));
    if (task.reason === "gravity") {
      const reason = node("span", "taskReason", "●"); reason.title = task.reason_message || "这项任务由课程学习安排选出。"; reason.setAttribute("aria-label", reason.title); heading.append(reason);
    }
    wrap.append(heading);
    if (task.course_title) wrap.append(node("div", "taskCourse", task.course_title));
    wrap.append(node("div", "taskTitle", task.title));
    const p = percent(task.progress);
    if (!history && p > 0 && p < 100) { const row = node("div", "taskProgressRow"), bar = node("div", "taskProgress"), fill = node("span"); fill.style.width = `${p}%`; bar.append(fill); row.append(bar, node("span", "", percentLabel(p))); wrap.append(row); }
    if (task.maintenance) wrap.append(node("p", "maintenanceNote", task.maintenance_message || "此内容正在维护，暂时无法开始。"));
    if (history) wrap.append(node("div", "taskCompletedAt", `Completed @ ${clockTime(task.completed_at)}`));
    return wrap;
  }
  function collapseTasks() {
    expandedTask = null;
    for (const item of root.querySelectorAll(".taskDetails")) item.hidden = true;
    for (const item of root.querySelectorAll(".taskToggle")) item.setAttribute("aria-expanded", "false");
  }
  function incompleteTask(task, data) {
    const card = node("article", "portalTask taskUnlocked"); card.dataset.taskId = task.id;
    const toggle = control("", "taskToggle", () => { const was = expandedTask === task.id; collapseTasks(); if (!was) { expandedTask = task.id; details.hidden = false; toggle.setAttribute("aria-expanded", "true"); } });
    toggle.append(taskSummary(task)); toggle.setAttribute("aria-expanded", "false");
    const details = node("div", "taskDetails"); details.hidden = true;
    if (task.status === "paused") details.append(node("p", "taskStatusNote", "学习已暂时终止，可以回看已学内容。"));
    if (["Quiz", "Exam"].includes(task.type)) {
      const info = node("div", "taskPrerequisites"); info.append(infoRow("Time Limit", task.time_limit_minutes != null ? `${task.time_limit_minutes} min` : task.time_limit_seconds != null ? `${Math.round(task.time_limit_seconds / 60)} min` : "—"), infoRow("Questions", task.question_count ?? "—")); details.append(info);
    } else {
      const requirements = node("div", "taskPrerequisites"); requirements.append(node("h3", "", "Prerequisites"));
      if (!task.prerequisites?.length) requirements.append(node("p", "", "没有前置知识要求。"));
      for (const item of task.prerequisites || []) {
        const id = typeof item === "string" ? item : item.id;
        const known = (data.topics || []).find((topic) => topic.id === id) || (typeof item === "object" ? item : {});
        const row = node("div", "prerequisiteRow"); row.append(node("span", known.status === "completed" ? "prerequisiteCheck complete" : "prerequisiteCheck", known.status === "completed" ? "✓" : "·"), link(known.title || id, `#/courses/${encode(task.course_id || data.course.id)}/progress?topicId=${encode(id)}`)); requirements.append(row);
      }
      details.append(requirements);
    }
    if (!task.maintenance) {
      const actions = node("div", "taskStartRow");
      const explicit = task.start_href || task.start_url;
      const target = (task.type === "Lesson" || !task.type) && task.topic_id ? `#/topic/${encode(task.topic_id)}` : typeof explicit === "string" && /^#\/(topic|learn|courses)\//.test(explicit) ? explicit : null;
      if (target && allowed("learn")) actions.append(roundButton(percent(task.progress) > 0 || task.started ? "Resume" : "Start", () => navigate(target)));
      else if (target && task.started && allowed("review_history")) actions.append(roundButton("回看", () => navigate(target)));
      else if (target) actions.append(node("p", "", "管理员尚未开放学习功能，请联系管理员。"));
      else actions.append(node("p", "", "此任务的学习入口尚未开放。"));
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
      actions.append(roundButton("回看", () => navigate(`/learn?taskId=${encode(task.id)}`)));
      appendGuestReset(actions, task); card.append(actions);
    }
    return card;
  }
  function appendGuestReset(actions, task) {
    if (bridge.getAccess()?.role !== "guest" || !allowed("learn") || !task.topic_id || (task.type && task.type !== "Lesson")) return;
    const reset = control("reset", "portalStart guestReset", async () => {
      if (reset.disabled) return;
      const learner = bridge.getAccess()?.learner_id, ticket = sequence;
      reset.disabled = true; reset.textContent = "重置中…";
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
        const status = node("p", "resetStatus", "进度已重置，可以重新开始。"); status.setAttribute("role", "status");
        root.prepend(status);
      } catch (error) {
        if (bridge.getAccess()?.learner_id !== learner || ticket !== sequence) return;
        const status = node("p", "resetStatus fieldError", error.message); status.setAttribute("role", "alert"); actions.after(status);
      } finally { reset.disabled = false; reset.textContent = "reset"; }
    });
    reset.title = "重置这个 lesson 的学习进度，从头开始";
    actions.append(reset);
  }
  function renderHistory(data, target) {
    target.replaceChildren(); let date = null; renderedDay = dayKey(new Date());
    if (!allowed("review_history")) { target.append(emptyBox("管理员尚未开放回看功能，请联系管理员。")); return; }
    for (const task of data.history) {
      const valid = task.completed_at && Number.isFinite(Date.parse(task.completed_at));
      const key = valid ? dayKey(task.completed_at) : "unknown";
      if (key !== date) { target.append(node("h2", "completedTasksDate", valid ? key === dayKey(new Date()) ? "Today" : apiDate(task.completed_at) : "完成日期未知")); date = key; }
      target.append(historyCard(task));
    }
  }
  function renderLearn(data, taskId, ticket) {
    historyError = null;
    const layout = node("div", "dashboardLayout"), tasks = node("div", "dashboardTasks"); tasks.id = "dashboardTasks";
    layout.append(courseSidebar(data), tasks); root.replaceChildren(layout);
    if (taskId) { renderAnswers(tasks, taskId, ticket); return; }
    const pending = node("section", "incompleteTasks"); pending.setAttribute("aria-label", "待完成任务");
    for (const task of data.tasks) pending.append(incompleteTask(task, data));
    if (!data.tasks.length) {
      const empty = emptyBox(percent(data.course.progress) === 100 ? "本课程的现有学习任务已完成。" : "暂时没有可开始的任务，稍后会再次检查。" );
      empty.append(control("重新检查", "textButton", () => route())); pending.append(empty);
      if (percent(data.course.progress) !== 100 && data.course.start_date) waitForTasks(ticket, 0);
    }
    const historyList = node("section", "completedTasks"); historyList.id = "completedTasks"; historyList.setAttribute("aria-label", "已完成任务"); renderHistory(data, historyList);
    const more = node("div", "historyMore"); more.id = "historyMore";
    tasks.append(pending, historyList, more); renderMore();
    if (!data.tasks.length && !data.history.length && percent(data.course.progress) !== 100) historyList.append(node("p", "historyEmpty", "完成后的学习记录会显示在这里。"));
  }
  function renderMore(error = null) {
    const target = $("historyMore"), data = selectedDashboard(); if (!target || !data) return;
    target.replaceChildren(); historyError = error;
    if (paging) { target.append(loading("正在读取更早记录…")); return; }
    if (error) target.append(node("p", "fieldError", error));
    if (data.has_more) target.append(control(error ? "重试加载" : "加载更早记录", "textButton", loadMore));
  }
  async function loadMore() {
    const data = selectedDashboard(), id = selectedCourse, ticket = sequence;
    if (paging || !data?.has_more || currentRoute?.path !== "/learn" || currentRoute.params.has("taskId")) return;
    paging = true; renderMore(); let failure = null;
    try {
      const next = await dashboard(id, data.history_cursor);
      if (ticket !== sequence) return;
      const merged = mergeDashboard(id, next, true); renderHistory(merged, $("completedTasks"));
    } catch (error) { failure = error.message; }
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
    clearTimeout(refreshTimer); refreshTimer = setTimeout(async () => {
      if (ticket !== sequence || document.hidden) { if (ticket === sequence) waitForTasks(ticket, attempt); return; }
      try {
        const data = mergeDashboard(selectedCourse, await dashboard(selectedCourse));
        if (ticket !== sequence) return;
        if (data.tasks.length || percent(data.course.progress) === 100) { const y = window.scrollY; renderLearn(data, null, ticket); window.scrollTo(0, y); }
        else waitForTasks(ticket, attempt + 1);
      } catch (error) {
        if (ticket !== sequence) return;
        const target = root.querySelector(".incompleteTasks"); target?.replaceChildren(errorBox(error.message, () => route(), "暂时无法检查新任务"));
      }
    }, [5000, 10000, 15000][Math.min(attempt, 2)]);
  }

  async function renderAnswers(target, taskId, ticket) {
    const back = control("← 返回学习记录", "taskBackButton", () => { if (scrolls.has("#/learn")) history.back(); else navigate("/learn"); });
    if (!allowed("review_history")) { target.replaceChildren(back, emptyBox("管理员尚未开放回看功能，请联系管理员。")); return; }
    target.replaceChildren(back, loading("正在读取作答记录…"));
    try {
      const result = answerCache.get(taskId) || await call(`tasks/${encode(taskId)}/answers`);
      if (ticket !== sequence) return;
      answerCache.set(taskId, result);
      mathStyle(result.math_css);
      const summary = node("article", "portalTask taskCompleted answerTaskSummary"); summary.append(taskSummary(result.task, true));
      target.replaceChildren(back, summary);
      if (!result.groups?.some((group) => group.answers?.length)) target.append(emptyBox("暂无可查看的作答记录。"));
      for (const group of result.groups || []) {
        const section = node("section", "answerGroup"); section.append(node("h2", "answerGroupTitle", group.title));
        for (const [index, answer] of (group.answers || []).entries()) section.append(answerCard(answer, index, result.task));
        target.append(section);
      }
    } catch (error) { if (ticket === sequence) target.replaceChildren(back, errorBox(error.message, () => renderAnswers(target, taskId, ticket), "暂时无法读取作答记录")); }
  }
  function answerCard(answer, index, task) {
    const wrapper = node("article", "answerRecord"), head = node("div", "answerRecordHeader"), question = node("div", "answerQuestion");
    head.append(node("span", "", `Question ${index + 1}`));
    const helpHost = node("div", "answerHelpHost"), helpMenu = node("div", "answerHelpMenu"); helpMenu.hidden = true;
    const help = control("?", "answerHelp", () => { helpMenu.hidden = !helpMenu.hidden; });
    help.title = "题目帮助"; help.setAttribute("aria-label", "题目帮助"); help.setAttribute("aria-haspopup", "true");
    helpMenu.append(control("Report a content error", "", () => { helpMenu.hidden = true; showFeedback({ course_id: task.course_id, topic_id: task.topic_id, task_id: task.id, question_id: answer.question_id }); }));
    helpHost.addEventListener("mouseenter", () => { helpMenu.hidden = false; });
    helpHost.addEventListener("mouseleave", () => { helpMenu.hidden = true; });
    helpHost.append(help, helpMenu); head.append(helpHost);
    question.append(trustedContent(answer.html));
    const meta = node("div", "answerMetadata");
    const difficulty = answer.difficulty == null ? "—" : ({ easy: "E", medium: "M", hard: "H" }[answer.difficulty] || String(answer.difficulty));
    const elapsed = answer.elapsed_seconds == null ? "—" : `${Math.round(answer.elapsed_seconds)} s`;
    meta.append(node("span", "", `Difficulty ${difficulty}`), node("span", "", apiDate(answer.answered_at, true)), node("span", "", `Elapsed ${elapsed}`));
    const outcomes = { correct: "Correct", incorrect: "Incorrect", full: "Full Credit", full_credit: "Full Credit", partial: "Partial Credit", partial_credit: "Partial Credit", none: "No Credit", no_credit: "No Credit", unanswered: "未作答" };
    const result = outcomes[answer.result] || (answer.correct === true ? "Correct" : answer.correct === false ? "Incorrect" : "—");
    const outcome = node("span", `answerOutcome ${answer.correct === true ? "correct" : answer.correct === false ? "incorrect" : ""}`, result); meta.append(outcome);
    const explanation = node("div", "answerExplanation"); explanation.hidden = true;
    if (answer.explanation_html) explanation.append(trustedContent(answer.explanation_html)); else explanation.append(node("p", "", "此题暂未提供解析。"));
    if (answer.correct !== true) { explanation.append(node("h3", "", "Your Answer"), node("pre", "historyYourAnswer", answer.answer == null || answer.answer === "" ? "未作答" : answer.answer)); }
    const toggle = control("查看解析", "answerExplanationToggle textButton", () => { explanation.hidden = !explanation.hidden; toggle.textContent = explanation.hidden ? "查看解析" : "收起解析"; toggle.setAttribute("aria-expanded", String(!explanation.hidden)); }); toggle.setAttribute("aria-expanded", "false");
    question.addEventListener("click", (event) => { if (!event.target.closest("a,button")) toggle.click(); });
    wrapper.append(head, question, meta, toggle, explanation); return wrapper;
  }

  function renderCourseProgress(data, params) {
    document.title = `${data.course.title} · 课程进度`;
    const header = node("div", "progressPageHeading"); header.append(link("← 返回学习主页", learnHref(), "textButton"), node("h1", "portalPageTitle", data.course.title));
    const details = node("section", "courseProgressSummary"); details.append(infoRow("Progress", percentLabel(data.course.progress)), infoRow("Start Date", apiDate(data.course.start_date)), infoRow("End Date", apiDate(data.course.end_date)), infoRow("Estimated completion", apiDate(data.course.estimated_completion)), control("查看知识图谱", "textButton", () => openGraph(data)));
    root.replaceChildren(header, details);
    const max = Math.max(0, ...(data.units || []).map((unit) => Number(unit.topic_count) || 0));
    for (const unit of data.units || []) {
      const section = node("section", "progressUnit"); section.dataset.progressId = unit.id;
      section.append(node("h2", "", `${unit.title} (${unit.topic_count} topics)`), unitBar(unit, max));
      for (const topic of (data.topics || []).filter((topic) => topic.unit_id === unit.id || unit.topic_ids?.includes(topic.id))) {
        const row = node("div", "progressTopic"); row.dataset.progressId = topic.id;
        row.append(node("span", "", topic.title), node("span", "", statusNames[topic.status] || "—"), node("span", "", percentLabel(topic.progress)));
        if (topic.id === params.get("topicId")) row.classList.add("highlighted"); section.append(row);
      }
      root.append(section);
    }
    if (!data.units?.length) root.append(emptyBox("课程单元尚未提供。"));
  }

  const graphDialog = node("dialog", "graphDialog"); graphDialog.setAttribute("aria-label", "课程知识图谱");
  const graphHeading = node("div", "dialogHeading"), graphTitle = node("h2", "", "知识图谱"), graphContent = node("div", "graphContent");
  const graphClose = control("×", "iconButton", () => graphDialog.close()); graphClose.setAttribute("aria-label", "关闭知识图谱");
  graphHeading.append(graphTitle, graphClose); graphDialog.append(graphHeading, graphContent); document.body.append(graphDialog);
  graphDialog.addEventListener("close", () => { document.body.classList.remove("portalModalOpen"); window.scrollTo(0, graphScroll); });
  function openGraph(data) {
    hidePopovers(); graphScroll = window.scrollY; graphTitle.textContent = `${data.course.title} · 知识图谱`;
    graphContent.replaceChildren(loading("Initializing ...")); graphDialog.showModal(); document.body.classList.add("portalModalOpen");
    requestAnimationFrame(() => drawGraph(data.topics || []));
  }
  function drawGraph(topics) {
    if (!topics.length) { graphContent.replaceChildren(emptyBox("当前课程尚无知识图谱数据。")); return; }
    const byId = new Map(topics.map((topic) => [topic.id, topic])), levels = new Map();
    function level(id, seen = new Set()) {
      if (levels.has(id)) return levels.get(id); if (seen.has(id)) return 0;
      const nextSeen = new Set(seen).add(id);
      const parents = (byId.get(id)?.prerequisites || []).map((p) => typeof p === "string" ? p : p.id).filter((p) => byId.has(p));
      const result = parents.length ? Math.max(...parents.map((p) => level(p, nextSeen))) + 1 : 0; levels.set(id, result); return result;
    }
    topics.forEach((topic) => level(topic.id));
    const maxLevel = Math.max(...levels.values()), rows = Array.from({ length: maxLevel + 1 }, () => []); topics.forEach((topic) => rows[levels.get(topic.id)].push(topic));
    const width = Math.max(680, Math.max(...rows.map((row) => row.length)) * 210), height = Math.max(280, rows.length * 115 + 70), positions = new Map();
    rows.forEach((row, rank) => row.forEach((topic, index) => positions.set(topic.id, { x: width / (row.length + 1) * (index + 1), y: height - 65 - rank * 115 })));
    const ns = "http://www.w3.org/2000/svg", make = (tag, attrs = {}) => { const item = document.createElementNS(ns, tag); for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, String(value)); return item; };
    const svg = make("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "主题及其先修关系，自下向上排列" });
    const defs = make("defs"), marker = make("marker", { id: "portalGraphArrow", markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto" }); marker.append(make("path", { d: "M0,0 L8,4 L0,8 Z", fill: "#a9b4be" })); defs.append(marker); svg.append(defs);
    for (const topic of topics) for (const parent of topic.prerequisites || []) {
      const from = positions.get(typeof parent === "string" ? parent : parent.id), to = positions.get(topic.id); if (!from) continue;
      svg.append(make("path", { d: `M${from.x},${from.y - 24} C${from.x},${from.y - 70} ${to.x},${to.y + 70} ${to.x},${to.y + 24}`, fill: "none", stroke: "#b6c2cc", "stroke-width": 1.5, "marker-end": "url(#portalGraphArrow)" }));
    }
    for (const topic of topics) {
      const p = positions.get(topic.id), group = make("g"), completed = topic.status === "completed";
      const fill = completed ? "#176bb5" : topic.status === "in_progress" || topic.status === "paused" ? "#a5cff3" : topic.frontier ? "#c9e4ff" : "#f2f2f2";
      group.append(make("rect", { x: p.x - 86, y: p.y - 24, width: 172, height: 48, rx: 3, fill, stroke: "#c9d2da" }));
      const title = make("title"); title.textContent = `${topic.title} · ${statusNames[topic.status] || "尚未学习"}`; group.append(title);
      const label = make("text", { x: p.x, y: p.y + 5, "text-anchor": "middle", fill: completed ? "#fff" : "#1e194e", "font-size": 13 }); label.textContent = [...topic.title].length > 13 ? [...topic.title].slice(0, 12).join("") + "…" : topic.title; group.append(label); svg.append(group);
    }
    const viewport = node("div", "graphViewport"); viewport.append(svg);
    graphContent.replaceChildren(viewport, node("p", "graphLegend", "深蓝：已完成　浅蓝：学习中　淡蓝：可开始　灰色：尚未学习"));
  }

  function renderHelp() {
    const article = node("article", "guidePage"), body = node("div", "guideBody courseContent");
    const questions = [
      ["学习进度会保存吗？", "进度会自动保存。游客请使用同一浏览器继续学习；更换设备或清除浏览器数据后，可能无法找回原游客身份。使用邀请码登录的账号可在其他设备登录后继续。"],
      ["“体验专用浏览版”怎么用？", "游客勾选右上角的选项后，作答框会自动填入标准答案，点击 Submit 即可提交。你也可以修改答案，系统仍会正常判对错；取消勾选后，后续题目需要自行作答。"],
      ["reset 会重置什么？", "游客点击某个 Lesson 旁的黄色 reset，会立即清空该 Lesson 当前显示的学习进度和作答记录，让你从头开始。其他 Lesson 和其他人的进度不受影响。"],
      ["题目有问题，或者不知道怎么操作怎么办？", "操作流程可以查看 GUIDE。发现题目或解析有误，可在学习页点击“内容反馈”，或在作答记录中点击问号选择 Report a content error；其他问题可通过头像菜单中的 Support 反馈。"],
    ];
    for (const [question, answer] of questions) body.append(node("h2", "", question), node("p", "", answer));
    body.append(link("查看 GUIDE →", "#/guide"));
    article.append(node("h1", "portalPageTitle", "Q&A"), body); root.replaceChildren(article);
  }
  async function renderGuide(ticket) {
    guideLoaded = false; guideVersion = null;
    const article = node("article", "guidePage"), heading = node("h1", "portalPageTitle", "GUIDE"), notice = node("div", "guideNotice"), body = node("div", "guideBody courseContent"), updated = node("p", "guideUpdated");
    notice.id = "guideNotice"; body.id = "guideBody"; updated.id = "guideUpdated";
    body.append(loading("正在读取指南…")); article.append(heading, notice, body, updated); root.replaceChildren(article);
    await updateGuide(ticket);
  }
  async function updateGuide(ticket) {
    clearTimeout(refreshTimer);
    try {
      const result = await call("guide"); if (ticket !== sequence || currentRoute?.path !== "/guide") return;
      const body = $("guideBody"), notice = $("guideNotice");
      if (result.status === "unavailable") throw new Error(result.message || "指南暂时无法读取。");
      notice.replaceChildren();
      if (!guideLoaded || guideVersion !== result.version) {
        const y = window.scrollY;
        if (result.status === "empty") body.replaceChildren(emptyBox("指南内容尚未填写。"));
        else if (result.status === "ready") { body.innerHTML = result.html || ""; mathStyle(result.math_css, "guideMathStyle"); }
        else throw new Error("指南返回了无法识别的状态。");
        guideVersion = result.version; guideLoaded = true; window.scrollTo(0, y);
      }
      $("guideUpdated").textContent = result.modified_at ? `更新于 ${apiDate(result.modified_at, true)}` : "";
    } catch (error) {
      if (ticket !== sequence) return;
      if (!guideLoaded) $("guideBody")?.replaceChildren();
      $("guideNotice")?.replaceChildren(errorBox(error.message, () => updateGuide(ticket), "暂时无法更新指南"));
    } finally { if (ticket === sequence && currentRoute?.path === "/guide") refreshTimer = setTimeout(() => { if (document.hidden) updateGuideLater(ticket); else updateGuide(ticket); }, 5000); }
  }
  function updateGuideLater(ticket) { if (ticket === sequence) refreshTimer = setTimeout(() => updateGuide(ticket), 5000); }

  function renderSettings() {
    const page = node("section", "settingsPage"), form = node("form", "profileForm");
    page.append(node("h1", "portalPageTitle", "个人设置"));
    const displayLabel = node("label", "", "显示名称"), display = node("input"); display.id = "profileDisplayName"; displayLabel.htmlFor = display.id; display.value = profile.display_name || ""; display.maxLength = 100; display.required = true;
    const zoneLabel = node("label", "", "时区"), zone = node("input"); zone.id = "profileTimezone"; zoneLabel.htmlFor = zone.id; zone.value = timezone(); zone.required = true; zone.setAttribute("list", "timezoneOptions");
    const zones = node("datalist"); zones.id = "timezoneOptions";
    for (const value of Intl.supportedValuesOf?.("timeZone") || [timezone(), "UTC"]) { const option = node("option"); option.value = value; zones.append(option); }
    const hint = node("p", "fieldHint", "历史日期、完成时间与作答时间按此时区显示。"), status = node("p", "profileStatus"); status.setAttribute("role", "status");
    const submit = node("button", "primaryButton", "保存"); submit.type = "submit";
    form.append(displayLabel, display, zoneLabel, zone, zones, hint, status, submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (submit.disabled) return; submit.disabled = true; status.textContent = "正在保存…";
      try { profile = await call("profile", { method: "POST", body: { display_name: display.value.trim(), timezone: zone.value.trim() } }); updateUser(); status.textContent = "个人设置已保存。"; }
      catch (error) { status.textContent = error.message; }
      finally { submit.disabled = false; }
    });
    page.append(form); root.replaceChildren(page);
  }

  const feedbackDialog = node("dialog", "feedbackDialog"), feedbackForm = node("form"), feedbackHeading = node("div", "dialogHeading"), feedbackTitle = node("h2", "", "Support · 站内反馈");
  const feedbackClose = control("×", "iconButton", () => feedbackDialog.close()); feedbackClose.setAttribute("aria-label", "关闭反馈"); feedbackHeading.append(feedbackTitle, feedbackClose);
  const feedbackLabel = node("label", "", "请描述你遇到的问题"), feedbackInput = node("textarea"); feedbackInput.id = "feedbackMessage"; feedbackLabel.htmlFor = feedbackInput.id; feedbackInput.maxLength = 4000; feedbackInput.required = true; feedbackInput.rows = 7;
  const feedbackStatus = node("p", "feedbackStatus"); feedbackStatus.setAttribute("role", "status");
  const feedbackActions = node("div", "dialogActions"), feedbackCancel = control("Cancel", "secondaryButton", () => feedbackDialog.close()), feedbackSubmit = node("button", "primaryButton", "Submit"); feedbackSubmit.type = "submit";
  feedbackActions.append(feedbackCancel, feedbackSubmit); feedbackForm.append(feedbackHeading, feedbackLabel, feedbackInput, feedbackStatus, feedbackActions); feedbackDialog.append(feedbackForm); document.body.append(feedbackDialog);
  function showFeedback(context = {}) {
    if (feedbackBusy) return;
    const matchesSelected = !context.topic_id || selectedDashboard()?.topics?.some((topic) => topic.id === context.topic_id);
    feedbackContext = { ...(selectedCourse && matchesSelected ? { course_id: selectedCourse } : {}), ...context };
    feedbackTitle.textContent = context.question_id ? "Report a content error · 内容纠错" : "Support · 站内反馈";
    feedbackStatus.textContent = ""; feedbackInput.value = ""; feedbackInput.hidden = false; feedbackLabel.hidden = false; feedbackSubmit.hidden = false; feedbackCancel.textContent = "Cancel";
    if (!feedbackDialog.open) feedbackDialog.showModal();
  }
  feedbackForm.addEventListener("submit", async (event) => {
    event.preventDefault(); if (feedbackBusy || !feedbackInput.value.trim()) return;
    feedbackBusy = true; feedbackSubmit.disabled = true; feedbackClose.disabled = true; feedbackCancel.disabled = true; feedbackStatus.textContent = "正在保存反馈…";
    try {
      const result = await call("feedback", { method: "POST", body: { message: feedbackInput.value.trim(), ...feedbackContext } });
      if (result.status !== "saved") throw new Error("反馈尚未确认保存，请重试。");
      feedbackStatus.textContent = "反馈已保存，谢谢。"; feedbackInput.hidden = true; feedbackLabel.hidden = true; feedbackSubmit.hidden = true; feedbackCancel.textContent = "Close";
    } catch (error) { feedbackStatus.textContent = error.message; }
    finally { feedbackBusy = false; feedbackSubmit.disabled = false; feedbackClose.disabled = false; feedbackCancel.disabled = false; }
  });
  feedbackDialog.addEventListener("cancel", (event) => { if (feedbackBusy) event.preventDefault(); });

  window.addEventListener("hashchange", route);
  history.scrollRestoration = "manual";
  return {
    start: route,
    setIdentity(next) {
      if (identity?.learner_id && next?.learner_id !== identity.learner_id) profile = null;
      if (identity && (JSON.stringify(identity.features) !== JSON.stringify(next?.features) || JSON.stringify(identity.topics) !== JSON.stringify(next?.topics))) {
        catalog = null; dashboards.clear(); answerCache.clear();
      }
      identity = next; updateUser();
    },
    suspendIdentity() {
      sequence += 1; stopPageWork(); showMenu(false); bridge.leaveTopic();
      catalog = null; profile = null; selectedCourse = null; dashboards.clear(); answerCache.clear(); scrolls.clear();
      paging = false; busyCourse = false; expandedTask = null; root.replaceChildren(); root.hidden = true;
      if (graphDialog.open) graphDialog.close();
      if (feedbackDialog.open) feedbackDialog.close();
      feedbackInput.value = "";
    },
    permissionsChanged() { catalog = null; dashboards.clear(); answerCache.clear(); },
    async identityChanged() { catalog = null; profile = null; dashboards.clear(); answerCache.clear(); scrolls.clear(); await route(); },
    progressChanged() { /* Returning to Learn reads current server progress without changing it. */ },
    showFeedback,
  };
}
