const $ = (id) => document.getElementById(id);
const STORAGE_PREFIX = "math-learning-web:";
const REQUEST_TIMEOUT = 18000;
const STATE_TIMEOUT = 9000;

function storageRead(storage, key, fallback = null) {
  try { return storage.getItem(STORAGE_PREFIX + key) ?? fallback; } catch { return fallback; }
}
function storageWrite(storage, key, value) {
  try {
    if (value === null) storage.removeItem(STORAGE_PREFIX + key);
    else storage.setItem(STORAGE_PREFIX + key, value);
  } catch { /* The server remains the source of saved learning progress. */ }
}
function readJSON(key) {
  try { return JSON.parse(storageRead(sessionStorage, key)); } catch { return null; }
}
function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const value = [...bytes].map((n) => n.toString(16).padStart(2, "0")).join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
function button(text, className, action, disabled = false) {
  const node = el("button", className, text);
  node.type = "button";
  node.disabled = disabled;
  node.addEventListener("click", action);
  return node;
}
function content(html) {
  const node = el("div", "courseContent");
  // Course HTML is generated and sanitized by the trusted local course importer.
  node.innerHTML = html || "";
  return node;
}

let apiBase = "";
let publicMode = false;
let deploymentReady = false;
let deploymentPromise = null;
let legacyScopes = [];
let legacyCurrentIdentities = [];
let legacyGuestIdentities = [];
let sessionToken = null;
let sessionPromise = null;
let identityToken = "";
let access = null;
let identityPromise = null;
let identityBusy = false;
let identityRecoveryAllowed = true;
let identityRecoveryPromise = null;
let state = null;
let actionBusy = false;
let pauseBusy = false;
let stateBusy = false;
let connectionGeneration = 0;
let pollTimer = null;
let pollBusy = false;
let pollTarget = null;
let connectionIssue = null;
let submissionError = null;
let retryAction = null;
let pageMessage = null;
let renderedKey = null;
let mathStyleVersion = null;
let channel = null;
let historyOpen = true;
const narrowLayout = window.matchMedia("(max-width: 1000px)");
const drafts = new Map();

const scope = () => apiBase || window.location.origin;
const identityKey = () => `identity:${scope()}`;
const guestIdentityKey = () => `guest-identity:${scope()}`;
const learnerScope = () => access?.learner_id || state?.learner_id || "pending";
const recordKey = () => `submission:${scope()}:${learnerScope()}`;
const draftKey = (step) => `draft:${scope()}:${learnerScope()}:${state.course_version}:${step.attempt_id || state.attempt_id}:${step.id}`;
const can = (feature) => Boolean(access?.features?.includes(feature));
const identityFailure = (error) => error.status === 401 && ["identity_required", "invalid_identity"].includes(error.code);
const selectedStep = () => state?.steps?.find((step) => step.id === state.current_step_id)
  || state?.steps?.find((step) => step.current)
  || state?.steps?.find((step) => step.id === state.active_step_id);
const activeStep = () => state?.steps?.find((step) => step.id === state.active_step_id);
function readingSteps() {
  const visited = (state?.steps || []).filter((step) => step.visited && step.unlocked);
  const introductions = visited.filter((step) => step.kind === "introduction");
  const modules = (state?.modules || []).flatMap((module) => visited.filter((step) => step.module_id === module.id && !["introduction", "completion"].includes(step.kind)));
  const completions = visited.filter((step) => step.kind === "completion");
  return [...introductions, ...modules, ...completions];
}
const mutationPayload = (extra = {}) => ({
  request_id: uuid(),
  expected_revision: state.revision,
  attempt_id: state.attempt_id,
  step_id: state.active_step_id,
  ...extra,
});

class ApiError extends Error {
  constructor(message, status = 0, code = "network_error") {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function ensureDeployment() {
  if (deploymentReady) return;
  if (!deploymentPromise) {
    const pending = (async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), STATE_TIMEOUT);
      try {
        const response = await fetch(new URL("./deployment.json", import.meta.url), { cache: "no-store", credentials: "same-origin", redirect: "error", signal: controller.signal });
        if (!response.ok) throw new Error("deployment_unavailable");
        const deployment = await response.json();
        if (typeof deployment?.api_origin !== "string") throw new Error("deployment_invalid");
        const configured = deployment.api_origin.trim();
        let origin = "";
        if (configured) {
          const url = new URL(configured);
          if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("deployment_invalid");
          origin = url.origin;
        }
        // Previous connection values are migration sources only, never API routing.
        const oldAddress = storageRead(localStorage, "api-base", "");
        const lastOrigin = storageRead(localStorage, "last-api-origin", "");
        const candidates = [window.location.origin];
        for (const source of [oldAddress, lastOrigin].filter(Boolean)) {
          try {
            const old = new URL(source);
            if (["http:", "https:"].includes(old.protocol) && !old.username && !old.password) candidates.unshift(old.origin);
          } catch { /* Invalid old configuration is never used for a request. */ }
        }
        legacyScopes = [...new Set(candidates)];
        legacyCurrentIdentities = [...new Set(legacyScopes.map((source) => storageRead(localStorage, `identity:${source}`, "")).filter(Boolean))];
        legacyGuestIdentities = [...new Set(legacyScopes.map((source) => storageRead(localStorage, `guest-identity:${source}`, "")).filter(Boolean))];
        apiBase = origin;
        publicMode = Boolean(configured);
        identityToken = storageRead(localStorage, identityKey(), "");
        deploymentReady = true;
      } catch (error) {
        throw new ApiError("服务暂不可用，请稍后重试。", 0, error.message === "deployment_invalid" ? "deployment_invalid" : "deployment_unavailable");
      } finally { clearTimeout(timer); }
    })();
    deploymentPromise = pending;
    pending.finally(() => { if (deploymentPromise === pending) deploymentPromise = null; }).catch(() => {});
  }
  await deploymentPromise;
}

function retireLegacyConnection() {
  // Remember the scope only after this service has verified the learning identity.
  storageWrite(localStorage, "last-api-origin", scope());
  storageWrite(localStorage, "api-base", null);
  for (const source of legacyScopes) storageWrite(sessionStorage, `pairing:${source}`, null);
  legacyCurrentIdentities = [];
  legacyGuestIdentities = [];
}

async function request(path, { method = "GET", body, bootstrap = false, timeout = REQUEST_TIMEOUT, reauthenticated = false, skipIdentity = false, suppressIdentity = false, identityOverride } = {}) {
  if (!deploymentReady) await ensureDeployment();
  if (!bootstrap && !skipIdentity) await ensureIdentity();
  const generation = connectionGeneration;
  const base = apiBase;
  const local = !publicMode;
  if (!bootstrap && local && !sessionToken) await ensureSession();
  if (generation !== connectionGeneration) throw new ApiError("服务连接已更新，请重试。", 0, "connection_changed");
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!bootstrap) {
    if (local) headers["X-Learning-Session"] = sessionToken;
    const currentIdentity = identityOverride ?? identityToken;
    if (!suppressIdentity && currentIdentity) headers["X-Learning-Identity"] = currentIdentity;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(`${base}/api/${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      credentials: local ? "same-origin" : "omit",
      cache: "no-store",
      redirect: "error",
    });
    let result;
    try { result = await response.json(); } catch {
      throw new ApiError("服务暂时无法返回学习内容，请稍后重试。", response.status, "invalid_response");
    }
    if (!response.ok) {
      const detail = result?.detail;
      if (local && !bootstrap && !reauthenticated && response.status === 401 && ["authentication_required", "invalid_session"].includes(detail?.code)) {
        sessionToken = null;
        await ensureSession();
        return request(path, { method, body, timeout, skipIdentity, suppressIdentity, identityOverride, reauthenticated: true });
      }
      const message = ["authentication_required", "invalid_session"].includes(detail?.code) ? "服务暂不可用，请稍后重试。" : typeof detail === "string" ? detail : detail?.message || "操作未完成，请刷新学习进度后重试。";
      throw new ApiError(message, response.status, detail?.code || "request_error");
    }
    if (generation !== connectionGeneration) throw new ApiError("服务连接已更新。", 0, "connection_changed");
    return result;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === "AbortError") throw new ApiError("等待服务响应超时。已保存的进度不会丢失，请重试。", 0, "timeout");
    throw new ApiError("服务暂不可用，请稍后重试。", 0, "network_error");
  } finally { clearTimeout(timer); }
}

async function ensureSession() {
  if (sessionToken) return;
  if (!sessionPromise) {
    sessionPromise = request("session", { bootstrap: true, timeout: STATE_TIMEOUT })
      .then((result) => {
        if (!result.session_token) throw new ApiError("服务未提供有效的会话信息。", 0, "invalid_session");
        sessionToken = result.session_token;
      })
      .finally(() => { sessionPromise = null; });
  }
  await sessionPromise;
}

function rememberIdentity(result) {
  if (!result?.identity_token || !["guest", "account"].includes(result.access?.role) || !Array.isArray(result.access.features) || !Array.isArray(result.access.topics)) throw new ApiError("服务暂不可用，请稍后重试。", 0, "invalid_access");
  identityToken = result.identity_token;
  access = result.access;
  storageWrite(localStorage, identityKey(), identityToken);
  if (access.role === "guest") storageWrite(localStorage, guestIdentityKey(), identityToken);
  renderIdentity();
}

async function obtainGuestIdentity({ createIfMissing = true } = {}) {
  if (!deploymentReady) await ensureDeployment();
  const savedGuest = storageRead(localStorage, guestIdentityKey(), "");
  const candidates = [...new Set([savedGuest, ...legacyGuestIdentities].filter(Boolean))];
  for (const token of candidates) {
    try {
      const guestAccess = await request("access", { skipIdentity: true, identityOverride: token, timeout: STATE_TIMEOUT });
      if (guestAccess.role === "guest") return { identity_token: token, access: guestAccess };
    } catch (error) {
      if (!identityFailure(error)) throw error;
    }
    if (storageRead(localStorage, guestIdentityKey()) === token) storageWrite(localStorage, guestIdentityKey(), null);
    legacyGuestIdentities = legacyGuestIdentities.filter((candidate) => candidate !== token);
  }
  if (!createIfMissing) return null;
  return request("access/guest", { method: "POST", body: {}, skipIdentity: true, suppressIdentity: true });
}

async function ensureIdentity() {
  if (!deploymentReady) await ensureDeployment();
  if (identityToken && access) return;
  if (!identityPromise) {
    const pending = (async () => {
      const candidates = [...new Set([identityToken, ...legacyCurrentIdentities].filter(Boolean))];
      for (const token of candidates) {
        try {
          const currentAccess = await request("access", { skipIdentity: true, identityOverride: token, timeout: STATE_TIMEOUT });
          if (currentAccess.role === "account") {
            const guest = await obtainGuestIdentity({ createIfMissing: false });
            if (guest) storageWrite(localStorage, guestIdentityKey(), guest.identity_token);
          }
          rememberIdentity({ identity_token: token, access: currentAccess });
          retireLegacyConnection();
          return;
        } catch (error) {
          if (!identityFailure(error)) throw error;
          if (identityToken === token) forgetInvalidIdentity();
          legacyCurrentIdentities = legacyCurrentIdentities.filter((candidate) => candidate !== token);
          legacyGuestIdentities = legacyGuestIdentities.filter((candidate) => candidate !== token);
        }
      }
      rememberIdentity(await obtainGuestIdentity());
      retireLegacyConnection();
    })();
    identityPromise = pending;
    pending.finally(() => { if (identityPromise === pending) identityPromise = null; }).catch(() => {});
  }
  await identityPromise;
}

function forgetInvalidIdentity() {
  if (storageRead(localStorage, identityKey()) === identityToken) storageWrite(localStorage, identityKey(), null);
  if (storageRead(localStorage, guestIdentityKey()) === identityToken) storageWrite(localStorage, guestIdentityKey(), null);
  legacyCurrentIdentities = legacyCurrentIdentities.filter((candidate) => candidate !== identityToken);
  legacyGuestIdentities = legacyGuestIdentities.filter((candidate) => candidate !== identityToken);
  identityToken = "";
  access = null;
}

function clearLearningView() {
  stopPolling();
  state = null;
  renderedKey = null;
  submissionError = null;
  retryAction = null;
  connectionIssue = null;
  pageMessage = null;
  actionBusy = false;
  pauseBusy = false;
  stateBusy = false;
  pollBusy = false;
  $("courseShell").hidden = true;
  $("lessonTitle").textContent = "数学学习";
  $("lessonProgress").hidden = true;
  $("progressCaption").hidden = true;
  $("historyPanel").replaceChildren(el("p", "historyHint", "正在读取学习记录…"));
  $("loadingState").replaceChildren(el("span", "spinner"), el("h2", "", "正在读取学习进度"), el("p", "", "稍等片刻，课程将在这里打开。"));
  $("loadingState").hidden = false;
  renderConnectionNotice();
  renderPageNotice();
}

async function switchIdentity(result) {
  connectionGeneration += 1;
  clearLearningView();
  rememberIdentity(result);
  $("identityDialog").close();
  $("invitationCode").value = "";
  await start();
}

async function recoverIdentity(error) {
  if (!identityFailure(error)) return false;
  if (identityRecoveryPromise) return false;
  if (!identityRecoveryAllowed) return false;
  identityRecoveryAllowed = false;
  forgetInvalidIdentity();
  identityPromise = null;
  connectionGeneration += 1;
  clearLearningView();
  const pending = (async () => {
    const guest = await obtainGuestIdentity();
    await switchIdentity(guest);
  })();
  identityRecoveryPromise = pending;
  try { await pending; return true; }
  catch (recoveryError) {
    connectionIssue = recoveryError;
    renderConnectionNotice();
    if (!state) renderUnavailable();
    return true;
  }
  finally { if (identityRecoveryPromise === pending) identityRecoveryPromise = null; }
}

function renderIdentity() {
  const guest = !access || access.role === "guest";
  const name = guest ? "游客" : access.display_name || "学习账号";
  $("identityName").textContent = name;
  $("identityAction").textContent = guest ? "邀请码登录" : "切换账号";
  $("identityButton").setAttribute("aria-label", `${guest ? "邀请码登录" : "切换账号"}，当前${name}`);
  $("identityDialogStatus").textContent = guest ? "当前使用游客身份。" : `当前账号：${name}`;
  $("guestIdentityButton").hidden = guest;
  $("identitySubmitButton").disabled = identityBusy;
  $("guestIdentityButton").disabled = identityBusy;
  $("invitationCode").disabled = identityBusy;
  $("closeIdentityButton").disabled = identityBusy;
  $("cancelIdentityButton").disabled = identityBusy;
}

function featureMessage(feature) {
  if (feature === "pause_topic" && access?.role === "guest") return "该功能需注册账号才能使用";
  const names = { learn: "继续学习", submit_answer: "作答", review_history: "回看", pause_topic: "暂时终止学习" };
  return `管理员尚未开放${names[feature] || "此"}功能，请联系管理员。`;
}
function allowFeature(feature) {
  if (can(feature)) return true;
  pageMessage = featureMessage(feature);
  retryAction = null;
  renderPageNotice();
  announce(pageMessage);
  return false;
}

function renderAccessUnavailable() {
  clearLearningView();
  const target = $("loadingState");
  const empty = !access?.topics?.length;
  target.replaceChildren(el("h2", "", empty ? "暂时没有可学习的课程" : "当前课程尚未开放"), el("p", "", access?.role === "account" ? "请联系管理员，为你的账号开放课程。" : "当前没有可进入的课程，请稍后再试。"));
  target.append(button("邀请码登录", "primaryButton", openIdentity));
  $("historyPanel").replaceChildren(el("p", "historyHint", "暂无学习记录。"));
  renderIdentity();
}

async function refreshAccess() {
  access = await request("access", { skipIdentity: true, timeout: STATE_TIMEOUT });
  renderIdentity();
}

function persistSubmission(record) {
  storageWrite(sessionStorage, recordKey(), record ? JSON.stringify(record) : null);
}
function draftValue(step) {
  const key = draftKey(step);
  return drafts.get(key) ?? storageRead(sessionStorage, key, "");
}
function saveDraft(step, answer) {
  const key = draftKey(step);
  drafts.set(key, answer);
  storageWrite(sessionStorage, key, answer);
}
function announce(message) { $("announcer").textContent = message; }
function notifyOtherTabs() {
  channel?.postMessage({ type: "state-changed", scope: scope(), learner_id: learnerScope(), revision: state?.revision });
}

function applyState(next, { force = false, announceChange = false } = {}) {
  if (!next || !Array.isArray(next.steps) || !Array.isArray(next.modules)) throw new ApiError("课程数据不完整，请重新连接。", 0, "invalid_state");
  if (state && next.course_version === state.course_version && next.revision < state.revision) return;
  const accessChanged = next.access && JSON.stringify(next.access) !== JSON.stringify(access);
  if (next.access) access = next.access;
  const changed = accessChanged || !state || next.course_version !== state.course_version || next.revision !== state.revision || next.current_step_id !== state.current_step_id;
  const previousStep = state?.current_step_id;
  state = next;
  connectionIssue = null;
  identityRecoveryAllowed = true;
  renderIdentity();
  if (state.status === "paused" || state.status === "completed") {
    submissionError = null;
    stopPolling();
    persistSubmission(null);
  }
  if (submissionError && (submissionError.step_id !== state.active_step_id || submissionError.attempt_id !== state.attempt_id)) submissionError = null;
  if (state.pending_submission_id) schedulePoll(state.pending_submission_id);
  else if (!pollBusy) stopPolling();
  if (changed || force) render();
  else renderConnectionNotice();
  if (announceChange && changed && previousStep !== state.current_step_id) announce(selectedStep()?.title || "学习进度已更新");
}

async function refreshState({ quiet = true, force = false } = {}) {
  if (stateBusy) return;
  const generation = connectionGeneration;
  stateBusy = true;
  try {
    const next = await request("state", { timeout: STATE_TIMEOUT });
    if (!quiet) pageMessage = null;
    applyState(next, { force });
    renderConnectionNotice();
  } catch (error) {
    if (generation !== connectionGeneration) return;
    if (await recoverIdentity(error)) return;
    if (error.code === "topic_forbidden") {
      try { await refreshAccess(); } catch { /* Preserve the last verified identity. */ }
      renderAccessUnavailable();
      return;
    }
    if (error.code !== "connection_changed") {
      connectionIssue = error;
      renderConnectionNotice();
      if (!state) renderUnavailable();
    }
  } finally { if (generation === connectionGeneration) stateBusy = false; }
}

function renderConnectionNotice() {
  const notice = $("connectionNotice");
  notice.replaceChildren();
  notice.hidden = !connectionIssue || !state;
  if (state && !connectionIssue) $("saveStatus").textContent = state.pending_submission_id ? "答案已提交，正在判题" : "学习进度已保存";
  if (!connectionIssue || !state) return;
  const p = el("p");
  p.append(el("strong", "", "服务暂不可用"));
  notice.append(p, el("p", "", connectionIssue.message));
  if (state) notice.append(el("p", "", "已显示的内容仍可阅读，恢复后会同步已保存的进度。"));
  const actions = el("div", "noticeActions");
  actions.append(button("重试", "textButton", () => window.location.reload()));
  notice.append(actions);
  if (state) $("saveStatus").textContent = "等待服务恢复";
}

function renderUnavailable() {
  const target = $("loadingState");
  target.replaceChildren(el("h2", "", "服务暂不可用"), el("p", "", "请稍后重试，已保存的学习进度会为你保留。"));
  target.append(button("重试", "primaryButton", () => window.location.reload()));
  $("historyPanel").replaceChildren(el("p", "historyHint", "学习记录将在服务恢复后显示。"));
  target.hidden = false;
}

async function mutate(path, payload, { pause = false } = {}) {
  if ((pause ? pauseBusy : actionBusy) || !state) return;
  if (!allowFeature(pause ? "pause_topic" : path === "select" ? "review_history" : "learn")) return;
  const generation = connectionGeneration;
  if (pause) pauseBusy = true;
  else actionBusy = true;
  retryAction = null;
  pageMessage = null;
  renderBusy();
  try {
    const next = await request(path, { method: "POST", body: payload });
    applyState(next, { force: true, announceChange: true });
    notifyOtherTabs();
    if (path === "pause") announce("已暂时终止学习，等待管理者解锁。");
    if (["continue", "select"].includes(path)) focusStep();
  } catch (error) {
    if (generation !== connectionGeneration) return;
    if (await recoverIdentity(error)) return;
    if (error.status === 409) {
      pageMessage = "学习进度已在另一个窗口更新，已同步到最新步骤。";
      await refreshState({ force: true });
    } else if (error.code === "feature_forbidden" || error.code === "topic_forbidden") {
      pageMessage = error.message;
      retryAction = null;
      await refreshState({ force: true });
    } else {
      pageMessage = error.message;
      retryAction = { path, payload, pause };
      if (!error.status || [401, 403].includes(error.status)) connectionIssue = error;
    }
  } finally {
    if (generation === connectionGeneration) {
      if (pause) pauseBusy = false;
      else actionBusy = false;
      render();
    }
  }
}

async function submitAnswer(event) {
  event?.preventDefault();
  const step = selectedStep();
  if (actionBusy || !step?.actions?.includes("submit") || state.status !== "in_progress") return;
  if (!allowFeature("submit_answer")) return;
  const input = $("answerInput");
  const answer = input.value.trim();
  if (!answer) {
    $("answerError").hidden = false;
    input.setAttribute("aria-invalid", "true");
    input.focus();
    return;
  }
  saveDraft(step, input.value);
  const existing = readJSON(recordKey());
  const payload = existing?.payload?.step_id === step.id && existing.payload.attempt_id === state.attempt_id && existing.payload.answer === answer
    ? existing.payload : mutationPayload({ answer });
  await sendSubmission(payload);
}

async function sendSubmission(payload) {
  if (actionBusy || !state || state.status !== "in_progress") return;
  if (!allowFeature("submit_answer")) return;
  const generation = connectionGeneration;
  actionBusy = true;
  submissionError = null;
  pageMessage = null;
  retryAction = null;
  const record = { payload, submission_id: null, status: "sending" };
  persistSubmission(record);
  renderBusy();
  try {
    const result = await request("submit", { method: "POST", body: payload });
    if (generation !== connectionGeneration) return;
    handleSubmission(result, record);
    notifyOtherTabs();
  } catch (error) {
    if (generation !== connectionGeneration) return;
    if (await recoverIdentity(error)) return;
    if (error.status === 409) {
      persistSubmission(null);
      pageMessage = "作答状态已更新，请以当前页面为准。";
      await refreshState({ force: true });
    } else if (error.code === "feature_forbidden" || error.code === "topic_forbidden") {
      persistSubmission(null);
      pageMessage = error.message;
      await refreshState({ force: true });
    } else {
      submissionError = { ...payload, reason: error.message, uncertain: true };
      persistSubmission({ ...record, status: "error", reason: error.message });
      if (!error.status || [401, 403].includes(error.status)) connectionIssue = error;
      await refreshState();
    }
  } finally {
    if (generation === connectionGeneration) {
      actionBusy = false;
      render();
    }
  }
}

function handleSubmission(result, record = readJSON(recordKey())) {
  if (result.state) applyState(result.state);
  if (state?.status === "paused" || state?.status === "completed") return;
  // A late response from an older attempt must not replace polling for a newer one.
  if (state?.pending_submission_id && state.pending_submission_id !== result.submission_id) {
    schedulePoll(state.pending_submission_id);
    return;
  }
  if (result.state && state && result.state.revision < state.revision) return;
  const latestRecord = readJSON(recordKey());
  if (record?.payload && latestRecord?.payload && record.payload.request_id !== latestRecord.payload.request_id) return;
  if (result.status === "pending") {
    if (record) persistSubmission({ ...record, status: "pending", submission_id: result.submission_id });
    submissionError = null;
    schedulePoll(result.submission_id);
  } else if (result.status === "error") {
    stopPolling();
    if (record?.payload && record.payload.step_id === state?.active_step_id && record.payload.attempt_id === state?.attempt_id) {
      submissionError = { ...record.payload, reason: result.reason || "判题暂时没有完成，请重试。" };
      persistSubmission({ ...record, status: "error", submission_id: result.submission_id, reason: submissionError.reason });
    } else {
      pageMessage = "判题服务暂时没有完成，当前作答未计错，可以重新提交。";
    }
    announce("判题服务暂时不可用，本次未计错。可以重试。");
  } else {
    stopPolling();
    persistSubmission(null);
    submissionError = null;
    if (result.status === "judged") announce(result.correct ? "回答正确，请阅读反馈后继续。" : "本题回答有误，请阅读反馈后继续。");
  }
  render();
}

function schedulePoll(id, delay = 1000) {
  if (!id || state?.status !== "in_progress") return;
  if (pollTarget === id && (pollTimer || pollBusy)) return;
  if (pollTimer) clearTimeout(pollTimer);
  pollTarget = id;
  pollTimer = setTimeout(() => pollSubmission(id), delay);
}
function stopPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
  pollTarget = null;
}
async function pollSubmission(id) {
  if (pollBusy || pollTarget !== id) return;
  pollTimer = null;
  pollBusy = true;
  const generation = connectionGeneration;
  let delay = 1000;
  try {
    const result = await request(`submissions/${encodeURIComponent(id)}`, { timeout: STATE_TIMEOUT });
    if (generation !== connectionGeneration) return;
    handleSubmission(result);
  } catch (error) {
    if (generation !== connectionGeneration) return;
    if (await recoverIdentity(error)) return;
    if (error.status === 404) {
      stopPolling();
      persistSubmission(null);
      pageMessage = "未找到刚才的判题记录，已重新读取学习进度。";
      await refreshState({ force: true });
    } else {
      connectionIssue = error;
      renderConnectionNotice();
      delay = 4000;
    }
  } finally {
    if (generation === connectionGeneration) {
      pollBusy = false;
      if (pollTarget === id && state?.status === "in_progress") schedulePoll(id, delay);
    }
  }
}

function render() {
  renderIdentity();
  renderConnectionNotice();
  renderPageNotice();
  if (!state) return;
  if (mathStyleVersion !== state.course_version) {
    let style = $("courseMathStyle");
    if (!style) {
      style = document.createElement("style");
      style.id = "courseMathStyle";
      document.head.append(style);
    }
    style.textContent = state.math_css || "";
    mathStyleVersion = state.course_version;
  }
  $("loadingState").hidden = true;
  $("courseShell").hidden = false;
  $("lessonTitle").textContent = state.title;
  document.title = `${state.title} · 数学学习`;
  renderProgress();
  renderPause();
  renderHistory();
  const step = selectedStep();
  renderReview(step);
  renderStep(step);
  renderResult(step);
  renderBusy();
  $("saveStatus").textContent = connectionIssue ? "等待重新连接" : state.pending_submission_id ? "答案已提交，正在判题" : "学习进度已保存";
  const module = state.modules.find((item) => item.id === step?.module_id);
  const moduleIndex = state.modules.indexOf(module);
  $("footerPosition").textContent = moduleIndex >= 0 ? `模块 ${moduleIndex + 1} / ${state.modules.length}` : "";
}

function renderPageNotice() {
  const target = $("pageNotice");
  target.replaceChildren();
  target.hidden = !pageMessage;
  if (!pageMessage) return;
  target.append(el("p", "", pageMessage));
  const actions = el("div", "noticeActions");
  if (retryAction) {
    const saved = retryAction;
    actions.append(button("重试刚才的操作", "textButton", () => mutate(saved.path, saved.payload, { pause: saved.pause })));
  }
  actions.append(button("关闭提示", "textButton", () => { pageMessage = null; retryAction = null; renderPageNotice(); }));
  target.append(actions);
}

function renderProgress() {
  const completed = state.modules.filter((module) => module.status === "completed").length;
  const caption = $("progressCaption");
  caption.hidden = false;
  caption.replaceChildren(el("span", "", `已完成 ${completed} / ${state.modules.length} 个模块`), el("span", "", state.status === "paused" ? "已暂停" : state.status === "completed" ? "学习完成" : "学习中"));
  const target = $("lessonProgress");
  target.hidden = false;
  target.setAttribute("aria-valuenow", String(Math.round(100 * completed / (state.modules.length || 1))));
  target.setAttribute("aria-valuetext", `已完成 ${completed} / ${state.modules.length} 个模块`);
  target.replaceChildren(...state.modules.map((module) => {
    const segment = el("span", `progressSegment ${module.status}`);
    segment.title = module.title;
    return segment;
  }));
}

function renderPause() {
  const target = $("pauseNotice");
  target.hidden = state.status !== "paused";
  if (target.hidden) return;
  const automatic = state.pause?.reason === "practice_error_limit";
  target.replaceChildren(
    el("h2", "", automatic ? "检测到状态不佳，已暂停作答" : "已暂时终止学习"),
    el("p", "", "请等待管理者解锁。已完成模块的评分和学习记录已保留，仍可回看已学内容。"),
  );
}

function renderHistory() {
  const target = $("historyPanel");
  if (!can("review_history")) {
    target.replaceChildren(el("p", "historyHint", featureMessage("review_history")));
    return;
  }
  const nodes = [];
  const visited = readingSteps();
  const introductions = visited.filter((step) => step.kind === "introduction");
  if (introductions.length) nodes.push(historyGroup("Introduction", state.introduction_read ? "已读" : "阅读中", introductions));
  for (const module of state.modules) {
    const steps = visited.filter((step) => step.module_id === module.id && !["introduction", "completion"].includes(step.kind));
    const status = module.status === "completed" ? `已完成 · 熟练度 ${module.mastery}`
      : module.status === "paused" ? "已暂停" : steps.length ? "学习中" : "尚未学习";
    nodes.push(historyGroup(module.title, status, steps, module.status, module.attempt_id));
  }
  const completion = visited.filter((step) => step.kind === "completion");
  if (completion.length) nodes.push(historyGroup("Topic 完成", "已完成", completion, "completed"));
  nodes.push(el("p", "historyHint", "已学内容可以随时回看。后续内容随学习进度开放。"));
  target.replaceChildren(...nodes);
}
function historyGroup(title, status, steps, className = "", currentAttempt = null) {
  const group = el("section", "historyGroup");
  const heading = el("div", "historyGroupHeader");
  heading.append(el("span", "moduleName", title), el("span", `historyStatus ${className}`, status));
  group.append(heading);
  if (steps.length) {
    const items = el("div", "historyItems");
    const attempts = [...new Set(steps.map((step) => step.attempt_id).filter(Boolean))];
    for (const step of steps) {
      let label = step.kind === "introduction" ? "Introduction" : step.kind === "completion" ? "学习结果" : step.kind === "example" ? "Example" : step.title;
      if (step.kind === "example" && step.phase === "reading") label += " · 讲解";
      const item = button(label, "historyItem", () => selectStep(step.id), actionBusy);
      if (step.id === state.current_step_id) item.setAttribute("aria-current", "step");
      if (step.id === state.active_step_id && state.status !== "completed") item.append(el("span", "historyMark", "当前"));
      else if (currentAttempt && step.attempt_id !== currentAttempt && step.kind !== "introduction") item.append(el("span", "historyMark", `历史 · 第 ${attempts.indexOf(step.attempt_id) + 1} 轮`));
      items.append(item);
    }
    group.append(items);
  }
  return group;
}
function selectStep(id) {
  if (actionBusy || pauseBusy || !readingSteps().some((step) => step.id === id)) return;
  if (!allowFeature("review_history")) return;
  if (narrowLayout.matches) setHistoryOpen(false);
  if (id === state.current_step_id) return;
  mutate("select", mutationPayload({ step_id: id }));
}

function setHistoryOpen(open, { focus = false } = {}) {
  historyOpen = open;
  $("appLayout").classList.toggle("historyCollapsed", !open);
  $("historyButton").setAttribute("aria-expanded", String(open));
  $("historyButton").setAttribute("aria-label", open ? "收起学习记录" : "展开学习记录");
  $("historyDrawerToggle").setAttribute("aria-expanded", String(open));
  $("historyPanel").hidden = !open;
  $("historyScrim").hidden = !open || !narrowLayout.matches;
  document.body.classList.toggle("historyDrawerOpen", open && narrowLayout.matches);
  if (focus && narrowLayout.matches) (open ? $("historyButton") : $("historyDrawerToggle")).focus({ preventScroll: true });
}

function renderReview(step) {
  const target = $("reviewNotice");
  target.hidden = !step || step.id === state.active_step_id;
  target.replaceChildren();
  if (!target.hidden) target.append(el("span", "", "正在回看已学内容"), button("返回当前步骤", "textButton", () => selectStep(state.active_step_id), actionBusy));
}

function renderStep(step) {
  const key = JSON.stringify([state.course_version, state.revision, state.current_step_id, state.pending_submission_id, submissionError?.request_id, submissionError?.reason, access?.features]);
  if (key === renderedKey) return;
  renderedKey = key;
  const target = $("stepCard");
  const oldInput = $("answerInput");
  const focus = oldInput && document.activeElement === oldInput ? { start: oldInput.selectionStart, end: oldInput.selectionEnd, scroll: oldInput.scrollTop } : null;
  const previousId = target.dataset.stepId;
  target.dataset.stepId = step?.id || "";
  target.replaceChildren();
  if (!step) {
    const title = el("h2", "stepTitle", "正在同步学习内容");
    title.id = "stepTitle";
    target.append(title);
    return;
  }
  if (step.kind === "completion") {
    renderCompletion(target);
    renderStepNavigation(target, step, []);
    return;
  }
  const heading = el("div", "stepHeader");
  const title = el("h2", "stepTitle", step.title);
  title.id = "stepTitle";
  title.tabIndex = -1;
  heading.append(title);
  const module = state.modules.find((item) => item.id === step.module_id);
  if (step.kind === "practice" && module) heading.append(el("span", "stepCounter", module.attempt_id && module.attempt_id !== step.attempt_id ? "历史作答" : `已完成 ${module.practice_answered_count} / ${module.practice_target_count} 道练习`));
  else if (step.kind === "introduction") heading.append(el("span", "stepCounter", state.introduction_read ? "已读" : "阅读"));
  target.append(heading, content(step.html));
  if (step.answer !== null && step.answer !== undefined && step.answer !== "") {
    const answer = el("details", "submittedAnswer");
    answer.append(el("summary", "", "查看已提交答案"), el("pre", "", step.answer));
    target.append(answer);
  }
  if (step.feedback) {
    const feedback = el("div", `feedback ${step.feedback.correct ? "correct" : "incorrect"}`);
    feedback.setAttribute("role", "status");
    feedback.append(el("span", "feedbackIcon", step.feedback.correct ? "✓" : "!"));
    const body = el("div");
    body.append(el("span", "feedbackTitle", step.feedback.correct ? "回答正确" : "本题回答有误"));
    if (step.feedback.reason) body.append(el("div", "feedbackReason", step.feedback.reason));
    feedback.append(body);
    target.append(feedback);
  }
  if (step.explanation_html) {
    target.append(el("h3", "exampleExplanationHeader", "Explanation · 解析"), content(step.explanation_html));
  }
  const actions = Array.isArray(step.actions) ? step.actions : [];
  if (state.status === "in_progress" && step.id === state.active_step_id) {
    if (!can("learn")) target.append(el("p", "featureNotice", featureMessage("learn")));
    else if (step.phase === "answer" && !can("submit_answer")) target.append(el("p", "featureNotice", featureMessage("submit_answer")));
  }
  if (actions.includes("mastered") || actions.includes("unmastered")) {
    const area = el("div", "learningActions");
    area.append(el("p", "choicePrompt", "这道题，你已经掌握了吗？"));
    const choices = el("div", "choiceActions");
    if (actions.includes("mastered")) choices.append(button("已掌握，试着作答", "primaryButton", () => mutate("choose", mutationPayload({ choice: "mastered" }))));
    if (actions.includes("unmastered")) choices.append(button("未掌握，先学习", "secondaryButton", () => mutate("choose", mutationPayload({ choice: "unmastered" }))));
    area.append(choices);
    target.append(area);
  }
  if (step.phase === "unmastered") {
    const area = el("div", "learningActions");
    area.append(el("p", "choiceNotice", "已选择未掌握。点击 Continue，开始阅读相关讲解。"));
    target.append(area);
  }
  if (actions.includes("submit")) target.append(answerForm(step));
  if (state.pending_submission_id && step.id === state.active_step_id) {
    const wait = el("div", "waiting");
    wait.setAttribute("role", "status");
    wait.append(el("span", "spinner"), el("span", "", can("pause_topic") ? "正在判题，请稍候。你仍可暂时终止学习或回看已学内容。" : "正在判题，请稍候。你仍可回看已学内容。"));
    target.append(wait);
  }
  renderStepNavigation(target, step, actions);
  if (focus && previousId === step.id && $("answerInput")) {
    const input = $("answerInput");
    input.focus({ preventScroll: true });
    input.setSelectionRange(focus.start, focus.end);
    input.scrollTop = focus.scroll;
  }
}

function renderStepNavigation(target, step, actions) {
  const pages = readingSteps();
  const index = pages.findIndex((item) => item.id === step.id);
  const previous = index > 0 ? pages[index - 1] : null;
  const next = index >= 0 ? pages[index + 1] : null;
  const footer = el("div", "stepNavigation");
  const navigation = el("nav", "historyPagination");
  navigation.setAttribute("aria-label", "已学内容翻页");
  function pageControl(label, id, page) {
    const control = button(label, "secondaryButton pageButton", () => selectStep(page.id));
    control.id = id;
    control.dataset.navAvailable = "true";
    control.title = `${label}：${page.title}`;
    return control;
  }
  if (previous && can("review_history")) {
    navigation.append(pageControl("上一页", "previousPageButton", previous));
    footer.append(navigation);
  }
  const area = el("div", "continueRow");
  if (actions.includes("continue")) {
    const continueButton = button("Continue", "primaryButton", () => mutate("continue", mutationPayload()));
    continueButton.setAttribute("aria-label", "Continue，继续学习");
    area.append(continueButton);
  } else if (actions.includes("submit")) {
    area.append(target.querySelector("#submitButton"));
  } else if (next && can("review_history")) {
    const forward = el("nav", "historyPagination");
    forward.setAttribute("aria-label", "已学内容翻页");
    forward.append(pageControl("下一页", "nextPageButton", next));
    area.append(forward);
  }
  if (area.childElementCount) footer.append(area);
  if (footer.childElementCount) target.append(footer);
}

function answerForm(step) {
  const form = el("form", "learningActions answerForm");
  form.id = "answerForm";
  form.addEventListener("submit", submitAnswer);
  const label = el("label", "", "你的答案");
  label.htmlFor = "answerInput";
  const input = el("textarea", "answerInput");
  input.id = "answerInput";
  input.name = "answer";
  input.rows = 3;
  input.maxLength = 2000;
  input.spellcheck = false;
  input.autocomplete = "off";
  input.setAttribute("aria-describedby", "answerHint answerError");
  input.value = draftValue(step);
  input.addEventListener("input", () => {
    saveDraft(step, input.value);
    $("answerError").hidden = true;
    input.removeAttribute("aria-invalid");
    $("submitButton").disabled = !input.value.trim() || actionBusy || pauseBusy || !can("submit_answer");
    if (submissionError && input.value.trim() !== submissionError.answer) $("submitButton").textContent = "Submit";
    else if (submissionError) $("submitButton").textContent = "重试判题";
  });
  input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  const error = el("p", "inputError", "请先填写答案。");
  error.id = "answerError";
  error.hidden = true;
  const bottom = el("div", "answerBottom");
  const hint = el("p", "inputHint", "可输入数值、分数或数学表达式，例如 3/4、x^2。Ctrl + Enter 提交。");
  hint.id = "answerHint";
  const submit = el("button", "primaryButton", submissionError && input.value.trim() === submissionError.answer ? "重试判题" : "Submit");
  submit.id = "submitButton";
  submit.type = "submit";
  submit.setAttribute("form", "answerForm");
  submit.disabled = !input.value.trim();
  bottom.append(hint, submit);
  form.append(label, input, error);
  if (submissionError && submissionError.step_id === step.id) {
    const failed = el("div", "feedback incorrect");
    failed.setAttribute("role", "alert");
    const text = el("div");
    text.append(el("span", "feedbackTitle", "判题未完成，本次未计错"), el("div", "feedbackReason", submissionError.reason));
    failed.append(el("span", "feedbackIcon", "!"), text);
    form.append(failed);
  }
  form.append(bottom);
  return form;
}

function masteryBadge(score) {
  const badge = el("div", "mastery");
  badge.setAttribute("aria-label", `熟练度 ${score}，满分 4`);
  badge.append(el("span", "", "熟练度"), el("strong", "", score), el("span", "", "/ 4"));
  const bars = el("span", "masteryBars");
  bars.setAttribute("aria-hidden", "true");
  for (let i = 1; i <= 4; i += 1) bars.append(el("i", i <= score ? "filled" : ""));
  badge.append(bars);
  return badge;
}
function renderResult(step) {
  const target = $("moduleResult");
  const module = state.modules.find((item) => item.id === step?.module_id);
  target.hidden = !["example", "practice"].includes(step?.kind) || !module || module.status !== "completed" || module.mastery == null || (module.attempt_id && module.attempt_id !== step.attempt_id);
  target.replaceChildren();
  if (target.hidden) return;
  const text = el("div");
  text.append(el("p", "resultTitle", "本模块已完成"), el("p", "resultDetail", module.practice_answered_count === 0 ? "Example 作答正确，已跳过本模块练习。" : `已完成 ${module.practice_answered_count} 道练习，其中 ${module.practice_error_count} 道有误。`));
  target.append(text, masteryBadge(module.mastery));
}
function renderCompletion(target) {
  const heading = el("h2", "completionHeading", "本 Topic 已完成");
  heading.id = "stepTitle";
  heading.tabIndex = -1;
  target.append(heading, el("p", "completionIntro", "各模块的学习结果已保存。你可以通过学习记录回看题目与讲解。"));
  const list = el("div", "completionList");
  for (const module of state.modules) {
    const row = el("div", "completionRow");
    row.append(el("span", "completionRowTitle", module.title), masteryBadge(module.mastery));
    list.append(row);
  }
  target.append(list);
}

function renderBusy() {
  if (!state) return;
  const disabled = actionBusy || pauseBusy;
  for (const element of $("stepCard").querySelectorAll("button, textarea")) {
    const feature = element.closest(".historyPagination") ? "review_history" : element.id === "submitButton" || element.closest(".answerForm") ? "submit_answer" : "learn";
    element.disabled = disabled || element.dataset.navAvailable === "false" || !can(feature);
    if (!can(feature)) element.title = featureMessage(feature);
  }
  if ($("submitButton") && !disabled) $("submitButton").disabled = !$("answerInput").value.trim() || !can("submit_answer");
  for (const element of $("historyPanel").querySelectorAll("button")) element.disabled = disabled || !can("review_history");
  for (const element of $("reviewNotice").querySelectorAll("button")) element.disabled = disabled || !can("review_history");
  $("pauseButton").hidden = state.status !== "in_progress";
  $("pauseControl").hidden = state.status !== "in_progress";
  $("pauseButton").disabled = pauseBusy || identityBusy || (access?.role === "account" && !can("pause_topic"));
  $("pauseButton").title = can("pause_topic") ? "暂时终止整个 Topic 的作答，等待管理者解锁" : featureMessage("pause_topic");
  $("pauseButton").setAttribute("aria-busy", String(pauseBusy));
  $("stepCard").setAttribute("aria-busy", String(actionBusy));
}
function focusStep() {
  requestAnimationFrame(() => {
    $("stepTitle")?.focus({ preventScroll: true });
    const top = $("stepCard").getBoundingClientRect().top;
    if (top < 0 || top > window.innerHeight * .5) $("stepCard").scrollIntoView({ block: "start", behavior: "auto" });
  });
}

function openIdentity() {
  renderIdentity();
  $("invitationCode").value = "";
  $("identityError").hidden = true;
  $("identityDialog").showModal();
}

async function loginIdentity(event) {
  event.preventDefault();
  if (identityBusy) return;
  const invitation = $("invitationCode").value.trim();
  if (!invitation) { $("invitationCode").focus(); return; }
  identityBusy = true;
  $("identityError").hidden = true;
  renderIdentity();
  renderBusy();
  try {
    const result = await request("access/login", { method: "POST", body: { invitation_code: invitation }, skipIdentity: true, suppressIdentity: true });
    identityRecoveryAllowed = true;
    await switchIdentity(result);
  } catch (error) {
    if (error.code !== "connection_changed") {
      $("identityError").textContent = error.message;
      $("identityError").hidden = false;
    }
  } finally {
    identityBusy = false;
    renderIdentity();
    renderBusy();
  }
}

async function returnToGuest() {
  if (identityBusy) return;
  identityBusy = true;
  $("identityError").hidden = true;
  renderIdentity();
  renderBusy();
  try {
    const guest = await obtainGuestIdentity();
    identityRecoveryAllowed = true;
    await switchIdentity(guest);
  } catch (error) {
    if (error.code !== "connection_changed") {
      $("identityError").textContent = error.message;
      $("identityError").hidden = false;
    }
  } finally {
    identityBusy = false;
    renderIdentity();
    renderBusy();
  }
}

async function start() {
  await refreshState({ quiet: false, force: true });
  if (!state) return;
  const record = readJSON(recordKey());
  if (record?.payload && record.payload.step_id === state.active_step_id && record.payload.attempt_id === state.attempt_id && state.status === "in_progress") {
    if (record.status === "error" && !state.pending_submission_id && activeStep()?.actions?.includes("submit")) {
      submissionError = { ...record.payload, reason: record.reason || "上次判题未完成，可以重试。" };
      render();
    } else if (record.submission_id && state.pending_submission_id) schedulePoll(record.submission_id);
    else if (record.status === "sending" && activeStep()?.actions?.includes("submit")) {
      submissionError = { ...record.payload, reason: "上次提交尚未确认，请点击重试。重复提交不会重复计分。", uncertain: true };
      render();
    }
  }
}

$("identityButton").addEventListener("click", openIdentity);
$("identityForm").addEventListener("submit", loginIdentity);
$("guestIdentityButton").addEventListener("click", returnToGuest);
$("closeIdentityButton").addEventListener("click", () => $("identityDialog").close());
$("cancelIdentityButton").addEventListener("click", () => $("identityDialog").close());
$("identityDialog").addEventListener("close", () => { $("invitationCode").value = ""; });
$("historyButton").addEventListener("click", () => {
  setHistoryOpen(!historyOpen, { focus: true });
});
$("historyDrawerToggle").addEventListener("click", () => setHistoryOpen(true, { focus: true }));
$("historyScrim").addEventListener("click", () => setHistoryOpen(false, { focus: true }));
narrowLayout.addEventListener("change", () => setHistoryOpen(historyOpen));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && narrowLayout.matches && historyOpen && !$("identityDialog").open) setHistoryOpen(false, { focus: true });
});
setHistoryOpen(true);
$("pauseButton").addEventListener("click", () => {
  if (state?.status === "in_progress" && !pauseBusy) mutate("pause", { request_id: uuid() }, { pause: true });
});
window.addEventListener("focus", () => { if (!actionBusy && !pauseBusy) refreshState(); });
window.addEventListener("online", () => refreshState({ force: true }));
document.addEventListener("visibilitychange", () => { if (!document.hidden && !actionBusy && !pauseBusy) refreshState(); });
try {
  channel = new BroadcastChannel("math-learning-web-progress");
  channel.onmessage = (event) => {
    if (event.data?.type === "state-changed" && event.data.scope === scope() && event.data.learner_id === learnerScope() && event.data.revision > (state?.revision ?? -1) && !actionBusy && !pauseBusy) refreshState();
  };
} catch { /* Periodic state reads also keep separate windows synchronized. */ }
setInterval(() => {
  if (!document.hidden && !actionBusy && !pauseBusy && !state?.pending_submission_id) refreshState();
}, 5000);
start();
