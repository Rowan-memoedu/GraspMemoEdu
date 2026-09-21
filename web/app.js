import { t, translateMessage, applyStaticTranslations, learningTitle } from "./i18n.js?v=aa1b73113c05cc38";

applyStaticTranslations();

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
let identityGeneration = 0;
let identityProblem = null;
let identitySyncTimer = null;
let identitySyncPromise = null;
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
let portal = null;
let topicId = null;
let topicSubjectId = null;
let pageActive = document.hasFocus();
let answerClock = { key: null, total: 0, started: null };
let historyOpen = true;
const narrowLayout = window.matchMedia("(max-width: 1000px)");
const drafts = new Map();

const scope = () => apiBase || window.location.origin;
const identityKey = () => `identity:${scope()}`;
const guestIdentityKey = () => `guest-identity:${scope()}`;
const identityRoleKey = () => `identity-role:${scope()}`;
const identityProblemKey = () => `identity-problem:${scope()}`;
const learnerScope = () => access?.learner_id || state?.learner_id || "pending";
const recordKey = () => `submission:${scope()}:${learnerScope()}:${topicId}`;
const draftKey = (step) => `draft:${scope()}:${learnerScope()}:${topicId}:${state.course_version}:${step.attempt_id || state.attempt_id}:${step.id}`;
const can = (feature) => Boolean(access?.features?.includes(feature));
const identityFailure = (error) => error.status === 401 && ["identity_required", "invalid_identity", "identity_expired"].includes(error.code);
const permissionFailure = (error) => error.status === 403 && ["feature_forbidden", "topic_forbidden", "course_forbidden"].includes(error.code);
const selectedStep = () => state?.steps?.find((step) => step.id === state.current_step_id)
  || state?.steps?.find((step) => step.current)
  || state?.steps?.find((step) => step.id === state.active_step_id);
const activeStep = () => state?.steps?.find((step) => step.id === state.active_step_id);
// Translate system names and structural heading prefixes; preserve authored title text.
function readerStepTitle(step) {
  if (step?.kind === "introduction") return t("Introduction");
  if (step?.kind === "completion") return t("学习结果");
  return learningTitle(step?.title || "");
}
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
    super(translateMessage(message));
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
        const savedProblem = storageRead(localStorage, identityProblemKey());
        identityProblem = ["invalid_identity", "identity_expired"].includes(savedProblem) ? savedProblem : null;
        deploymentReady = true;
        renderIdentity();
      } catch (error) {
        throw new ApiError(t("服务暂不可用，请稍后重试。"), 0, error.message === "deployment_invalid" ? "deployment_invalid" : "deployment_unavailable");
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

async function request(path, { method = "GET", body, bootstrap = false, timeout = REQUEST_TIMEOUT, reauthenticated = false, skipIdentity = false, suppressIdentity = false, identityOverride, identityOperation = false } = {}) {
  const identityEpoch = identityGeneration;
  if (!deploymentReady) await ensureDeployment();
  if (!bootstrap && !skipIdentity) await ensureIdentity();
  const generation = connectionGeneration;
  const storedIdentity = storageRead(localStorage, identityKey(), "");
  const base = apiBase;
  const local = !publicMode;
  if (!bootstrap && local && !sessionToken) await ensureSession();
  const changed = () => identityEpoch !== identityGeneration || (!identityOperation && generation !== connectionGeneration)
    || (!bootstrap && storedIdentity !== storageRead(localStorage, identityKey(), ""));
  if (changed()) throw new ApiError(t("身份或学习页面已更新，请重试。"), 0, "connection_changed");
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
      throw new ApiError(t("服务暂时无法返回学习内容，请稍后重试。"), response.status, "invalid_response");
    }
    if (changed()) throw new ApiError(t("身份或学习页面已更新。"), 0, "connection_changed");
    if (!response.ok) {
      const detail = result?.detail;
      if (local && !bootstrap && !reauthenticated && response.status === 401 && ["authentication_required", "invalid_session"].includes(detail?.code)) {
        sessionToken = null;
        await ensureSession();
        return request(path, { method, body, timeout, skipIdentity, suppressIdentity, identityOverride, identityOperation, reauthenticated: true });
      }
      const message = ["authentication_required", "invalid_session"].includes(detail?.code) ? t("服务暂不可用，请稍后重试。") : typeof detail === "string" ? detail : detail?.message || t("操作未完成，请刷新学习进度后重试。");
      throw new ApiError(message, response.status, detail?.code || "request_error");
    }
    if (!bootstrap && !skipIdentity) identityRecoveryAllowed = true;
    return result;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error.name === "AbortError") throw new ApiError(t("等待服务响应超时。已保存的进度不会丢失，请重试。"), 0, "timeout");
    throw new ApiError(t("服务暂不可用，请稍后重试。"), 0, "network_error");
  } finally { clearTimeout(timer); }
}

async function fetchGuideAsset(value, subjectId, retried = false) {
  await ensureDeployment();
  await ensureIdentity();
  if (!publicMode) await ensureSession();
  const base = new URL(apiBase || window.location.origin);
  const url = new URL(value, base);
  const prefix = `/subject-guide-assets/${encodeURIComponent(subjectId)}/`;
  if (!/^[a-z][a-z0-9-]*$/.test(subjectId) || subjectId === 'math' || url.origin !== base.origin
      || url.username || url.password || !url.pathname.startsWith(prefix)
      || url.pathname.slice(prefix.length).split('/').some(part => {
        try { const decoded = decodeURIComponent(part); return !decoded || ['.', '..'].includes(decoded) || /[\\/\u0000]/.test(decoded); }
        catch { return true; }
      })) throw new ApiError(t('portal.content.unavailable.26'), 403, 'invalid_guide_asset');
  const epoch = identityGeneration, generation = connectionGeneration, token = identityToken;
  const headers = {'X-Learning-Identity': token};
  if (!publicMode) headers['X-Learning-Session'] = sessionToken;
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);
  const changed = () => epoch !== identityGeneration || generation !== connectionGeneration || token !== identityToken;
  try {
    const response = await fetch(url, {headers, signal:controller.signal, credentials:publicMode ? 'omit' : 'same-origin', redirect:'error', cache:'no-store'});
    if (changed()) throw new ApiError(t('身份或学习页面已更新。'), 0, 'connection_changed');
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      const detail = data?.detail;
      if (!publicMode && !retried && response.status === 401 && ['authentication_required','invalid_session'].includes(detail?.code)) {
        sessionToken = null; await ensureSession();
        if (changed()) throw new ApiError(t('身份或学习页面已更新。'), 0, 'connection_changed');
        return fetchGuideAsset(value, subjectId, true);
      }
      throw new ApiError(detail?.message || t('portal.content.unavailable.26'), response.status, detail?.code || 'guide_asset_unavailable');
    }
    const blob = await response.blob();
    if (changed()) throw new ApiError(t('身份或学习页面已更新。'), 0, 'connection_changed');
    if (blob.size > 20 * 1024 * 1024) throw new ApiError(t('portal.content.unavailable.26'), 413, 'guide_asset_too_large');
    return blob;
  } finally { clearTimeout(timer); }
}

async function ensureSession() {
  if (sessionToken) return;
  if (!sessionPromise) {
    sessionPromise = request("session", { bootstrap: true, timeout: STATE_TIMEOUT })
      .then((result) => {
        if (!result.session_token) throw new ApiError(t("服务未提供有效的会话信息。"), 0, "invalid_session");
        sessionToken = result.session_token;
      })
      .finally(() => { sessionPromise = null; });
  }
  await sessionPromise;
}

function rememberIdentity(result) {
  if (!result?.identity_token || !["guest", "account"].includes(result.access?.role) || !Array.isArray(result.access.features) || !Array.isArray(result.access.topics)) throw new ApiError(t("服务暂不可用，请稍后重试。"), 0, "invalid_access");
  identityToken = result.identity_token;
  access = result.access;
  identityProblem = null;
  storageWrite(localStorage, identityProblemKey(), null);
  storageWrite(localStorage, identityRoleKey(), access.role);
  if (access.role === "guest") storageWrite(localStorage, guestIdentityKey(), identityToken);
  storageWrite(localStorage, identityKey(), identityToken);
  renderIdentity();
}

async function obtainGuestIdentity({ createIfMissing = true } = {}) {
  if (!deploymentReady) await ensureDeployment();
  const savedGuest = storageRead(localStorage, guestIdentityKey(), "");
  const candidates = [...new Set([savedGuest, ...legacyGuestIdentities].filter(Boolean))];
  for (const token of candidates) {
    try {
      const guestAccess = await request("access", { skipIdentity: true, identityOverride: token, timeout: STATE_TIMEOUT, identityOperation: true });
      if (guestAccess.role === "guest") return { identity_token: token, access: guestAccess };
    } catch (error) {
      if (!identityFailure(error)) throw error;
    }
    if (storageRead(localStorage, guestIdentityKey()) === token) storageWrite(localStorage, guestIdentityKey(), null);
    legacyGuestIdentities = legacyGuestIdentities.filter((candidate) => candidate !== token);
  }
  if (!createIfMissing) return null;
  return request("access/guest", { method: "POST", body: {}, skipIdentity: true, suppressIdentity: true, identityOperation: true });
}

async function ensureIdentity() {
  if (!deploymentReady) await ensureDeployment();
  if (identityProblem) throw new ApiError(identityProblemMessage(), 401, identityProblem);
  if (identityToken && access) return;
  if (!identityPromise) {
    const pending = (async () => {
      const candidates = [...new Set([identityToken, ...legacyCurrentIdentities].filter(Boolean))];
      for (const token of candidates) {
        try {
          const currentAccess = await request("access", { skipIdentity: true, identityOverride: token, timeout: STATE_TIMEOUT, identityOperation: true });
          if (currentAccess.role === "account") {
            const guest = await obtainGuestIdentity({ createIfMissing: false });
            if (guest) storageWrite(localStorage, guestIdentityKey(), guest.identity_token);
          }
          rememberIdentity({ identity_token: token, access: currentAccess });
          retireLegacyConnection();
          return;
        } catch (error) {
          if (!identityFailure(error)) throw error;
          if (accountIdentity(token) || error.code === "identity_expired") {
            blockAccountIdentity(error);
            throw error;
          }
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

function accountIdentity(token = identityToken) {
  if (!token) return false;
  if (token === identityToken && access?.role === "account") return true;
  for (const source of new Set([scope(), ...legacyScopes])) {
    if (storageRead(localStorage, `identity:${source}`) === token && storageRead(localStorage, `identity-role:${source}`) === "account") return true;
  }
  const guests = [storageRead(localStorage, guestIdentityKey()), ...legacyGuestIdentities].filter(Boolean);
  return guests.length > 0 && !guests.includes(token);
}

function identityProblemMessage() {
  return identityProblem === "identity_expired" ? t("账号登录已到期，请重新登录。原游客记录仍可使用。") : t("账号登录已失效，请重新登录。原游客记录仍可使用。");
}

function renderIdentityNotice() {
  const notice = $("identityNotice");
  notice.hidden = !identityProblem;
  if (!identityProblem) { notice.replaceChildren(); return; }
  const actions = el("div", "noticeActions");
  actions.append(button(t("重新登录"), "primaryButton", openIdentity, identityBusy), button(t("使用原游客身份"), "secondaryButton", returnToGuest, identityBusy));
  notice.replaceChildren(el("h2", "", t("账号需要重新登录")), el("p", "", identityProblemMessage()), actions);
}

function invalidateIdentityView() {
  syncAnswerClock(true);
  identityGeneration += 1;
  connectionGeneration += 1;
  demoFillGeneration += 1;
  identityPromise = null;
  clearLearningView();
  drafts.clear();
  portal?.suspendIdentity();
}

function blockAccountIdentity(error) {
  if (!identityProblem) {
    identityProblem = error.code === "identity_expired" ? "identity_expired" : "invalid_identity";
    storageWrite(localStorage, identityProblemKey(), identityProblem);
    forgetInvalidIdentity();
    storageWrite(localStorage, identityRoleKey(), null);
    invalidateIdentityView();
    channel?.postMessage({ type: "identity-changed", scope: scope() });
  }
  renderIdentity();
}

function scheduleIdentitySync() {
  clearTimeout(identitySyncTimer);
  identitySyncTimer = setTimeout(() => { void syncExternalIdentity(); }, 0);
}

async function syncExternalIdentity() {
  if (!deploymentReady || identitySyncPromise) return;
  const savedToken = storageRead(localStorage, identityKey(), "");
  const savedProblem = storageRead(localStorage, identityProblemKey());
  const nextProblem = ["invalid_identity", "identity_expired"].includes(savedProblem) ? savedProblem : null;
  if (savedToken === identityToken && nextProblem === identityProblem) return;
  invalidateIdentityView();
  identityToken = savedToken;
  access = null;
  identityProblem = nextProblem;
  identityBusy = false;
  identityRecoveryAllowed = true;
  $("identityDialog").close();
  renderIdentity();
  if (identityProblem) return;
  const pending = (async () => { if (portal) await portal.identityChanged(); else if (topicId) await start(); })();
  identitySyncPromise = pending;
  try { await pending; }
  finally {
    if (identitySyncPromise === pending) identitySyncPromise = null;
    if (storageRead(localStorage, identityKey(), "") !== identityToken) scheduleIdentitySync();
  }
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
  $("stepCard").replaceChildren();
  $("moduleResult").replaceChildren();
  $("lessonTitle").textContent = t("site.title");
  $("lessonProgress").hidden = true;
  $("progressCaption").hidden = true;
  $("historyPanel").replaceChildren(el("p", "historyHint", t("正在读取学习记录…")));
  $("loadingState").replaceChildren(el("span", "spinner"), el("h2", "", t("正在读取学习进度")), el("p", "", t("稍等片刻，课程将在这里打开。")));
  $("loadingState").hidden = false;
  renderConnectionNotice();
  renderPageNotice();
}

async function switchIdentity(result) {
  invalidateIdentityView();
  rememberIdentity(result);
  retireLegacyConnection();
  channel?.postMessage({ type: "identity-changed", scope: scope() });
  $("identityDialog").close();
  $("invitationCode").value = "";
  if (portal) await portal.identityChanged();
  else if (topicId) await start();
}

async function recoverIdentity(error) {
  if (!identityFailure(error)) return false;
  if (identityProblem) return true;
  if (accountIdentity() || error.code === "identity_expired") { blockAccountIdentity(error); return true; }
  if (identityRecoveryPromise) return false;
  if (!identityRecoveryAllowed) return false;
  identityRecoveryAllowed = false;
  forgetInvalidIdentity();
  invalidateIdentityView();
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
  const guest = access?.role === "guest";
  const name = identityProblem ? t("未登录") : !access || guest ? t("游客") : access.display_name || t("学习账号");
  $("identityName").textContent = name;
  const action = identityProblem ? t("重新登录") : !access || guest ? t("邀请码登录") : t("切换账号");
  $("identityAction").textContent = action;
  $("identityButton").setAttribute("aria-label", t("reader.identityAria", { action, name }));
  $("identityButton").disabled = identityBusy;
  $("logoutButton").disabled = identityBusy;
  $("identityDialogStatus").textContent = identityProblem ? identityProblemMessage() : guest || !access ? t("当前使用游客身份。") : t("reader.currentAccount", { name, purpose: access.purpose === "test" ? t("（测试账号）") : "" });
  $("guestIdentityButton").hidden = guest || (!access && !identityProblem);
  $("guestIdentityButton").textContent = identityProblem ? t("使用原游客身份") : t("退出并切回游客");
  $("identitySubmitButton").disabled = identityBusy;
  $("guestIdentityButton").disabled = identityBusy;
  $("invitationCode").disabled = identityBusy;
  $("closeIdentityButton").disabled = identityBusy;
  $("cancelIdentityButton").disabled = identityBusy;
  $("guestDemoOption").hidden = access?.role !== "guest";
  $("guestDemoCheckbox").checked = access?.role === "guest" && storageRead(localStorage, demoPreferenceKey(), "false") === "true";
  renderIdentityNotice();
  portal?.setIdentity(access);
}

function featureMessage(feature) {
  if (feature === "pause_topic" && access?.role === "guest") return t("该功能需注册账号才能使用");
  const names = { learn: t("继续学习"), submit_answer: t("作答"), review_history: t("回看"), pause_topic: t("暂时终止学习") };
  return t("reader.featureUnavailable", { feature: names[feature] || t("此") });
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
  target.replaceChildren(el("h2", "", empty ? t("暂时没有可学习的课程") : t("当前课程尚未开放")), el("p", "", access?.role === "account" ? t("请联系管理员，为你的账号开放课程。") : t("当前没有可进入的课程，请稍后再试。")));
  target.append(button(t("邀请码登录"), "primaryButton", openIdentity));
  $("historyPanel").replaceChildren(el("p", "historyHint", t("暂无学习记录。")));
  renderIdentity();
}

async function refreshAccess() {
  const next = await request("access", { skipIdentity: true, timeout: STATE_TIMEOUT });
  if (next.learner_id !== access?.learner_id && access) throw new ApiError(t("当前身份已更新，请重新读取。"), 0, "connection_changed");
  access = next;
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
function demoPreferenceKey() { return `guest-demo:${scope()}:${learnerScope()}`; }
let demoFillGeneration = 0;
async function prefillDemoAnswer(step) {
  const input = $("answerInput");
  if (access?.role !== "guest" || !$("guestDemoCheckbox").checked || !input || !step?.actions?.includes("submit")) return;
  const key = draftKey(step);
  if (input.value || drafts.has(key) || storageRead(sessionStorage, key) !== null) return;
  const generation = ++demoFillGeneration, token = identityToken, topic = topicId;
  try {
    const result = await topicRequest("demo-answer", { timeout: STATE_TIMEOUT });
    if (generation !== demoFillGeneration || !input.isConnected || identityToken !== token || topicId !== topic
        || access?.role !== "guest" || !$("guestDemoCheckbox").checked || result.step_id !== step.id
        || result.question_id !== step.question_id || input.value || drafts.has(key)
        || storageRead(sessionStorage, key) !== null || typeof result.answer !== "string") return;
    input.value = result.answer;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  } catch { if (input.isConnected && generation === demoFillGeneration && $("guestDemoCheckbox").checked) announce(t("标准答案暂时无法填入，可重新勾选或自行作答。")); }
}
function announce(message) { $("announcer").textContent = message; }
function notifyOtherTabs() {
  channel?.postMessage({ type: "state-changed", scope: scope(), learner_id: learnerScope(), topic_id: topicId, revision: state?.revision });
  portal?.progressChanged();
}

function topicRequest(path, options) {
  if (!topicId) throw new ApiError(t("请先选择学习内容。"), 0, "topic_required");
  const subject = topicSubjectId ? `&subject_id=${encodeURIComponent(topicSubjectId)}` : "";
  return request(`${path}${path.includes("?") ? "&" : "?"}topic_id=${encodeURIComponent(topicId)}${subject}`, options);
}

function syncAnswerClock(forcePause = false) {
  const now = performance.now();
  if (answerClock.started !== null) answerClock.total += Math.max(0, now - answerClock.started);
  if (answerClock.key) storageWrite(sessionStorage, answerClock.key, String(Math.round(answerClock.total)));
  const step = selectedStep();
  const key = topicId && step && ["example", "practice"].includes(step.kind)
    ? `elapsed:${scope()}:${learnerScope()}:${topicId}:${step.attempt_id || state.attempt_id}:${step.id}` : null;
  if (key !== answerClock.key) {
    const saved = Number(key && storageRead(sessionStorage, key, "0"));
    answerClock = { key, total: Number.isFinite(saved) ? Math.max(0, saved) : 0, started: null };
  }
  const running = !forcePause && key && pageActive && !document.hidden && !$("appLayout").hidden
    && state.status === "in_progress" && step.id === state.active_step_id && ["choice", "answer"].includes(step.phase)
    && !state.pending_submission_id && !actionBusy && !pauseBusy;
  answerClock.started = running ? now : null;
  return Math.min(86400000, Math.round(answerClock.total));
}

function applyState(next, { force = false, announceChange = false } = {}) {
  if (!next || !Array.isArray(next.steps) || !Array.isArray(next.modules)) throw new ApiError(t("课程数据不完整，请重新连接。"), 0, "invalid_state");
  if (access?.learner_id && next.learner_id !== access.learner_id) throw new ApiError(t("此学习记录不属于当前身份，请刷新。"), 0, "identity_mismatch");
  if (state && next.course_version === state.course_version && next.revision < state.revision) return;
  const accessChanged = next.access && JSON.stringify(next.access) !== JSON.stringify(access);
  if (next.access) access = next.access;
  const changed = accessChanged || !state || next.course_version !== state.course_version || next.revision !== state.revision || next.current_step_id !== state.current_step_id || next.dependency_ready !== state.dependency_ready;
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
  if (announceChange && changed && previousStep !== state.current_step_id) announce(readerStepTitle(selectedStep()) || t("学习进度已更新"));
}

async function refreshState({ quiet = true, force = false } = {}) {
  if (!topicId || stateBusy || identityProblem) return;
  const generation = connectionGeneration;
  stateBusy = true;
  try {
    const next = await topicRequest("state", { timeout: STATE_TIMEOUT });
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
  if (state && !connectionIssue) $("saveStatus").textContent = state.pending_submission_id ? t("答案已提交，正在判题") : t("学习进度已保存");
  if (!connectionIssue || !state) return;
  const p = el("p");
  p.append(el("strong", "", t("服务暂不可用")));
  notice.append(p, el("p", "", connectionIssue.message));
  if (state) notice.append(el("p", "", t("已显示的内容仍可阅读，恢复后会同步已保存的进度。")));
  const actions = el("div", "noticeActions");
  actions.append(button(t("重试"), "textButton", () => window.location.reload()));
  notice.append(actions);
  if (state) $("saveStatus").textContent = t("等待服务恢复");
}

function renderUnavailable() {
  const target = $("loadingState");
  target.replaceChildren(el("h2", "", t("服务暂不可用")), el("p", "", t("请稍后重试，已保存的学习进度会为你保留。")));
  target.append(button(t("重试"), "primaryButton", () => window.location.reload()));
  $("historyPanel").replaceChildren(el("p", "historyHint", t("学习记录将在服务恢复后显示。")));
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
    const next = await topicRequest(path, { method: "POST", body: payload });
    applyState(next, { force: true, announceChange: true });
    notifyOtherTabs();
    if (path === "pause") announce(t("已暂时终止学习，等待管理者解锁。"));
    if (["continue", "select"].includes(path)) focusStep();
  } catch (error) {
    if (generation !== connectionGeneration) return;
    if (await recoverIdentity(error)) return;
    if (error.status === 409) {
      pageMessage = t("学习进度已在另一个窗口更新，已同步到最新步骤。");
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
    ? existing.payload : mutationPayload({ answer, elapsed_ms: syncAnswerClock() });
  await sendSubmission(payload);
}

async function sendSubmission(payload) {
  if (actionBusy || !state || state.status !== "in_progress") return;
  if (!allowFeature("submit_answer")) return;
  const generation = connectionGeneration;
  actionBusy = true;
  syncAnswerClock();
  submissionError = null;
  pageMessage = null;
  retryAction = null;
  const record = { payload, submission_id: null, status: "sending" };
  persistSubmission(record);
  renderBusy();
  try {
    const result = await topicRequest("submit", { method: "POST", body: payload });
    if (generation !== connectionGeneration) return;
    handleSubmission(result, record);
    notifyOtherTabs();
  } catch (error) {
    if (generation !== connectionGeneration) return;
    if (await recoverIdentity(error)) return;
    if (error.status === 409) {
      persistSubmission(null);
      pageMessage = t("作答状态已更新，请以当前页面为准。");
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
      submissionError = { ...record.payload, reason: result.reason ? translateMessage(result.reason) : t("判题暂时没有完成，请重试。") };
      persistSubmission({ ...record, status: "error", submission_id: result.submission_id, reason: submissionError.reason });
    } else {
      pageMessage = t("判题服务暂时没有完成，当前作答未计错，可以重新提交。");
    }
    announce(t("判题服务暂时不可用，本次未计错。可以重试。"));
  } else {
    stopPolling();
    persistSubmission(null);
    submissionError = null;
    if (result.status === "judged") announce(result.correct ? t("回答正确，请阅读反馈后继续。") : t("本题回答有误，请阅读反馈后继续。"));
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
    const result = await topicRequest(`submissions/${encodeURIComponent(id)}`, { timeout: STATE_TIMEOUT });
    if (generation !== connectionGeneration) return;
    handleSubmission(result);
  } catch (error) {
    if (generation !== connectionGeneration) return;
    if (await recoverIdentity(error)) return;
    if (error.status === 404) {
      stopPolling();
      persistSubmission(null);
      pageMessage = t("未找到刚才的判题记录，已重新读取学习进度。");
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
  document.title = t("reader.pageTitle", { title: state.title });
  renderProgress();
  renderPause();
  renderHistory();
  const step = selectedStep();
  renderReview(step);
  renderStep(step);
  renderResult(step);
  renderBusy();
  $("saveStatus").textContent = connectionIssue ? t("等待重新连接") : state.pending_submission_id ? t("答案已提交，正在判题") : t("学习进度已保存");
  const module = state.modules.find((item) => item.id === step?.module_id);
  const moduleIndex = state.modules.indexOf(module);
  $("footerPosition").textContent = moduleIndex >= 0 ? t("reader.modulePosition", { current: moduleIndex + 1, total: state.modules.length }) : "";
  syncAnswerClock();
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
    actions.append(button(t("重试刚才的操作"), "textButton", () => mutate(saved.path, saved.payload, { pause: saved.pause })));
  }
  actions.append(button(t("关闭提示"), "textButton", () => { pageMessage = null; retryAction = null; renderPageNotice(); }));
  target.append(actions);
}

function renderProgress() {
  const completed = state.modules.filter((module) => module.status === "completed").length;
  const caption = $("progressCaption");
  caption.hidden = false;
  caption.replaceChildren(el("span", "", t("reader.modulesCompleted", { completed, total: state.modules.length })), el("span", "", state.status === "paused" ? t("已暂停") : state.status === "completed" ? t("学习完成") : t("学习中")));
  const target = $("lessonProgress");
  target.hidden = false;
  target.setAttribute("aria-valuenow", String(Math.round(100 * completed / (state.modules.length || 1))));
  target.setAttribute("aria-valuetext", t("reader.modulesCompleted", { completed, total: state.modules.length }));
  target.replaceChildren(...state.modules.map((module) => {
    const segment = el("span", `progressSegment ${module.status}`);
    segment.title = learningTitle(module.title);
    return segment;
  }));
}

function renderPause() {
  const target = $("pauseNotice");
  target.hidden = state.status !== "paused";
  if (target.hidden) return;
  const automatic = state.pause?.reason === "practice_error_limit";
  target.replaceChildren(
    el("h2", "", automatic ? t("检测到状态不佳，已暂停作答") : t("已暂时终止学习")),
    el("p", "", t("请等待管理者解锁。已完成模块的评分和学习记录已保留，仍可回看已学内容。")),
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
  if (introductions.length) nodes.push(historyGroup(t("Introduction"), state.introduction_read ? t("已读") : t("阅读中"), introductions));
  for (const module of state.modules) {
    const steps = visited.filter((step) => step.module_id === module.id && !["introduction", "completion"].includes(step.kind));
    const status = module.status === "completed" ? t("reader.completedMastery", { score: module.mastery })
      : module.status === "paused" ? t("已暂停") : steps.length ? t("学习中") : t("尚未学习");
    nodes.push(historyGroup(learningTitle(module.title), status, steps, module.status, module.attempt_id));
  }
  const completion = visited.filter((step) => step.kind === "completion");
  if (completion.length) nodes.push(historyGroup(t("Topic 完成"), t("已完成"), completion, "completed"));
  nodes.push(el("p", "historyHint", t("已学内容可以随时回看。后续内容随学习进度开放。")));
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
      let label = step.kind === "introduction" ? t("Introduction") : step.kind === "completion" ? t("学习结果") : step.kind === "example" ? t("Example") : learningTitle(step.title);
      if (step.kind === "example" && step.phase === "reading") label += t(" · 讲解");
      const item = button(label, "historyItem", () => selectStep(step.id), actionBusy);
      if (step.id === state.current_step_id) item.setAttribute("aria-current", "step");
      if (step.id === state.active_step_id && state.status !== "completed") item.append(el("span", "historyMark", t("当前")));
      else if (currentAttempt && step.attempt_id !== currentAttempt && step.kind !== "introduction") item.append(el("span", "historyMark", t("reader.historyAttempt", { attempt: attempts.indexOf(step.attempt_id) + 1 })));
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
  $("historyButton").setAttribute("aria-label", open ? t("收起学习记录") : t("展开学习记录"));
  $("historyDrawerToggle").setAttribute("aria-expanded", String(open));
  $("historyPanel").hidden = !open;
  $("historyScrim").hidden = !open || !narrowLayout.matches;
  document.body.classList.toggle("historyDrawerOpen", !$("appLayout").hidden && open && narrowLayout.matches);
  if (focus && narrowLayout.matches) (open ? $("historyButton") : $("historyDrawerToggle")).focus({ preventScroll: true });
}

function renderReview(step) {
  const target = $("reviewNotice");
  target.hidden = !step || step.id === state.active_step_id;
  target.replaceChildren();
  if (!target.hidden) target.append(el("span", "", t("正在回看已学内容")), button(t("返回当前步骤"), "textButton", () => selectStep(state.active_step_id), actionBusy));
}

function renderStep(step) {
  const key = JSON.stringify([state.course_version, state.revision, state.current_step_id, state.pending_submission_id, state.dependency_ready, submissionError?.request_id, submissionError?.reason, access?.features]);
  if (key === renderedKey) return;
  renderedKey = key;
  const target = $("stepCard");
  const oldInput = $("answerInput");
  const focus = oldInput && document.activeElement === oldInput ? { start: oldInput.selectionStart, end: oldInput.selectionEnd, scroll: oldInput.scrollTop } : null;
  const previousId = target.dataset.stepId;
  target.dataset.stepId = step?.id || "";
  target.replaceChildren();
  if (!step) {
    const title = el("h2", "stepTitle", t("正在同步学习内容"));
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
  const title = el("h2", "stepTitle", readerStepTitle(step));
  title.id = "stepTitle";
  title.tabIndex = -1;
  heading.append(title);
  const module = state.modules.find((item) => item.id === step.module_id);
  if (step.kind === "practice" && module) heading.append(el("span", "stepCounter", module.attempt_id && module.attempt_id !== step.attempt_id ? t("历史作答") : t("reader.practiceCompleted", { completed: module.practice_answered_count, total: module.practice_target_count })));
  else if (step.kind === "introduction") heading.append(el("span", "stepCounter", state.introduction_read ? t("已读") : t("阅读")));
  target.append(heading, content(step.html));
  if (step.answer !== null && step.answer !== undefined && step.answer !== "") {
    const answer = el("details", "submittedAnswer");
    answer.append(el("summary", "", t("查看已提交答案")), el("pre", "", step.answer));
    target.append(answer);
  }
  if (step.feedback) {
    const feedback = el("div", `feedback ${step.feedback.correct ? "correct" : "incorrect"}`);
    feedback.setAttribute("role", "status");
    feedback.append(el("span", "feedbackIcon", step.feedback.correct ? "✓" : "!"));
    const body = el("div");
    body.append(el("span", "feedbackTitle", step.feedback.correct ? t("回答正确") : t("本题回答有误")));
    if (step.feedback.reason) body.append(el("div", "feedbackReason", step.feedback.reason));
    feedback.append(body);
    target.append(feedback);
  }
  if (step.explanation_html) {
    target.append(el("h3", "exampleExplanationHeader", t("Explanation · 解析")), content(step.explanation_html));
  }
  const actions = Array.isArray(step.actions) ? step.actions : [];
  if (state.dependency_ready === false && !actions.length) target.append(el("p", "featureNotice", translateMessage("请先完成前置知识的学习和待复习内容，并解除前置知识的暂停状态。")));
  if (state.status === "in_progress" && step.id === state.active_step_id) {
    if (!can("learn")) target.append(el("p", "featureNotice", featureMessage("learn")));
    else if (step.phase === "answer" && !can("submit_answer")) target.append(el("p", "featureNotice", featureMessage("submit_answer")));
  }
  if (actions.includes("mastered") || actions.includes("unmastered")) {
    const area = el("div", "learningActions");
    area.append(el("p", "choicePrompt", t("这道题，你已经掌握了吗？")));
    const choices = el("div", "choiceActions");
    if (actions.includes("mastered")) choices.append(button(t("已掌握，试着作答"), "primaryButton", () => mutate("choose", mutationPayload({ choice: "mastered" }))));
    if (actions.includes("unmastered")) choices.append(button(t("未掌握，先学习"), "secondaryButton", () => mutate("choose", mutationPayload({ choice: "unmastered" }))));
    area.append(choices);
    target.append(area);
  }
  if (step.phase === "unmastered") {
    const area = el("div", "learningActions");
    area.append(el("p", "choiceNotice", t("已选择未掌握。点击下一页，开始阅读相关讲解。")));
    target.append(area);
  }
  if (actions.includes("submit")) target.append(answerForm(step));
  if (state.pending_submission_id && step.id === state.active_step_id) {
    const wait = el("div", "waiting");
    wait.setAttribute("role", "status");
    wait.append(el("span", "spinner"), el("span", "", can("pause_topic") ? t("正在判题，请稍候。你仍可暂时终止学习或回看已学内容。") : t("正在判题，请稍候。你仍可回看已学内容。")));
    target.append(wait);
  }
  renderStepNavigation(target, step, actions);
  void prefillDemoAnswer(step);
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
  navigation.setAttribute("aria-label", t("已学内容翻页"));
  function pageControl(label, id, page) {
    const control = button(label, "secondaryButton pageButton", () => selectStep(page.id));
    control.id = id;
    control.dataset.navAvailable = "true";
    control.title = t("reader.pageControlTitle", { label, title: readerStepTitle(page) });
    return control;
  }
  if (previous && can("review_history")) {
    navigation.append(pageControl(t("上一页"), "previousPageButton", previous));
    footer.append(navigation);
  }
  const area = el("div", "continueRow");
  if (actions.includes("continue")) {
    const continueButton = button(t("下一页"), "primaryButton", () => mutate("continue", mutationPayload()));
    continueButton.setAttribute("aria-label", t("Continue，继续学习"));
    area.append(continueButton);
  } else if (actions.includes("submit")) {
    area.append(target.querySelector("#submitButton"));
  } else if (next && can("review_history")) {
    const forward = el("nav", "historyPagination");
    forward.setAttribute("aria-label", t("已学内容翻页"));
    forward.append(pageControl(t("下一页"), "nextPageButton", next));
    area.append(forward);
  }
  if (area.childElementCount) footer.append(area);
  if (footer.childElementCount) target.append(footer);
}

function answerForm(step) {
  const form = el("form", "learningActions answerForm");
  form.id = "answerForm";
  form.addEventListener("submit", submitAnswer);
  const label = el("label", "", t("你的答案"));
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
    if (submissionError && input.value.trim() !== submissionError.answer) $("submitButton").textContent = t("Submit");
    else if (submissionError) $("submitButton").textContent = t("重试判题");
  });
  input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  const error = el("p", "inputError", t("请先填写答案。"));
  error.id = "answerError";
  error.hidden = true;
  const bottom = el("div", "answerBottom");
  const hint = el("p", "inputHint", t("只要描述清楚正确答案的形式即可，表达方式不限，夹杂口语也没关系。Ctrl + Enter 提交。"));
  hint.id = "answerHint";
  const submit = el("button", "primaryButton", submissionError && input.value.trim() === submissionError.answer ? t("重试判题") : t("Submit"));
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
    text.append(el("span", "feedbackTitle", t("判题未完成，本次未计错")), el("div", "feedbackReason", translateMessage(submissionError.reason)));
    failed.append(el("span", "feedbackIcon", "!"), text);
    form.append(failed);
  }
  form.append(bottom);
  return form;
}

function masteryBadge(score) {
  const badge = el("div", "mastery");
  badge.setAttribute("aria-label", t("reader.masteryScore", { score }));
  badge.append(el("span", "", t("熟练度")), el("strong", "", score), el("span", "", "/ 4"));
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
  text.append(el("p", "resultTitle", t("本模块已完成")), el("p", "resultDetail", module.practice_answered_count === 0 ? t("Example 作答正确，已跳过本模块练习。") : t("reader.practiceResult", { completed: module.practice_answered_count, errors: module.practice_error_count })));
  target.append(text, masteryBadge(module.mastery));
}
function renderCompletion(target) {
  const heading = el("h2", "completionHeading", t("本 Topic 已完成"));
  heading.id = "stepTitle";
  heading.tabIndex = -1;
  target.append(heading, el("p", "completionIntro", t("各模块的学习结果已保存。你可以通过学习记录回看题目与讲解。")));
  const list = el("div", "completionList");
  for (const module of state.modules) {
    const row = el("div", "completionRow");
    row.append(el("span", "completionRowTitle", learningTitle(module.title)), masteryBadge(module.mastery));
    list.append(row);
  }
  target.append(list);
}

function renderBusy() {
  if (!state) return;
  syncAnswerClock();
  const disabled = actionBusy || pauseBusy || identityBusy;
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
  $("pauseButton").title = can("pause_topic") ? t("暂时终止整个 Topic 的作答，等待管理者解锁") : featureMessage("pause_topic");
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
  if (!$("identityDialog").open) $("identityDialog").showModal();
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
    const result = await request("access/login", { method: "POST", body: { invitation_code: invitation }, skipIdentity: true, identityOperation: true });
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
  if (!$("identityDialog").open) openIdentity();
  identityBusy = true;
  $("identityError").hidden = true;
  renderIdentity();
  renderBusy();
  try {
    const guest = await obtainGuestIdentity();
    if (access?.role === "account") {
      try {
        const result = await request("access/logout", { method: "POST", body: {}, skipIdentity: true, identityOperation: true });
        if (result.status !== "logged_out") throw new ApiError(t("退出尚未确认，请重试。"), 0, "logout_unconfirmed");
      } catch (error) {
        if (!identityFailure(error)) throw error;
        // The server has confirmed this session is already unusable.
      }
    }
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
      submissionError = { ...record.payload, reason: record.reason ? translateMessage(record.reason) : t("上次判题未完成，可以重试。") };
      render();
    } else if (record.submission_id && state.pending_submission_id) schedulePoll(record.submission_id);
    else if (record.status === "sending" && activeStep()?.actions?.includes("submit")) {
      submissionError = { ...record.payload, reason: t("上次提交尚未确认，请点击重试。重复提交不会重复计分。"), uncertain: true };
      render();
    }
  }
}

$("identityButton").addEventListener("click", openIdentity);
$("guestDemoCheckbox").addEventListener("change", () => {
  demoFillGeneration += 1;
  if (access?.role !== "guest") return;
  storageWrite(localStorage, demoPreferenceKey(), String($("guestDemoCheckbox").checked));
  if ($("guestDemoCheckbox").checked) void prefillDemoAnswer(selectedStep());
});
$("identityForm").addEventListener("submit", loginIdentity);
$("guestIdentityButton").addEventListener("click", returnToGuest);
$("closeIdentityButton").addEventListener("click", () => $("identityDialog").close());
$("cancelIdentityButton").addEventListener("click", () => $("identityDialog").close());
$("identityDialog").addEventListener("close", () => { $("invitationCode").value = ""; });
$("identityDialog").addEventListener("cancel", (event) => { if (identityBusy) event.preventDefault(); });
$("historyButton").addEventListener("click", () => {
  setHistoryOpen(!historyOpen, { focus: true });
});
$("historyDrawerToggle").addEventListener("click", () => setHistoryOpen(true, { focus: true }));
$("historyScrim").addEventListener("click", () => setHistoryOpen(false, { focus: true }));
narrowLayout.addEventListener("change", () => setHistoryOpen(historyOpen));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && narrowLayout.matches && historyOpen && !document.querySelector("dialog[open]")) setHistoryOpen(false, { focus: true });
});
setHistoryOpen(true);
$("pauseButton").addEventListener("click", () => {
  if (state?.status === "in_progress" && !pauseBusy) mutate("pause", { request_id: uuid() }, { pause: true });
});
window.addEventListener("focus", () => { pageActive = true; syncAnswerClock(); scheduleIdentitySync(); if (!actionBusy && !pauseBusy) refreshState(); });
window.addEventListener("blur", () => { pageActive = false; syncAnswerClock(); });
window.addEventListener("pagehide", () => syncAnswerClock(true));
window.addEventListener("online", () => refreshState({ force: true }));
document.addEventListener("visibilitychange", () => { syncAnswerClock(); if (!document.hidden && !actionBusy && !pauseBusy) refreshState(); });
try {
  channel = new BroadcastChannel("math-learning-web-progress");
  channel.onmessage = (event) => {
    if (event.data?.type === "identity-changed" && event.data.scope === scope()) scheduleIdentitySync();
    if (event.data?.type === "state-changed" && event.data.scope === scope() && event.data.learner_id === learnerScope()) {
      portal?.progressChanged();
      if (event.data.topic_id === topicId && event.data.revision > (state?.revision ?? -1) && !actionBusy && !pauseBusy) refreshState();
    }
  };
} catch { /* Periodic state reads also keep separate windows synchronized. */ }
window.addEventListener("storage", (event) => {
  if (deploymentReady && [identityKey(), identityProblemKey()].some((key) => event.key === STORAGE_PREFIX + key)) scheduleIdentitySync();
});
setInterval(() => {
  if (!document.hidden && !actionBusy && !pauseBusy && !state?.pending_submission_id) refreshState();
}, 5000);
$("skipContent").addEventListener("click", (event) => { event.preventDefault(); (topicId ? $("lessonContent") : $("portalContent")).focus(); });
$("topicFeedbackButton").addEventListener("click", () => portal?.showFeedback({ topic_id: topicId, question_id: selectedStep()?.question_id }));

function leaveTopic() {
  syncAnswerClock(true);
  connectionGeneration += 1;
  topicId = null;
  topicSubjectId = null;
  clearLearningView();
  $("appLayout").hidden = true;
  document.body.classList.remove("historyDrawerOpen", "topicPage");
}

async function openTopic(id, subjectId) {
  leaveTopic();
  topicId = id;
  topicSubjectId = subjectId;
  $("appLayout").hidden = false;
  document.body.classList.add("topicPage");
  setHistoryOpen(historyOpen);
  await start();
}

const { initPortal } = await import("./portal.js?v=aa1b73113c05cc38");
portal = initPortal({
  fetchGuideAsset: async (url, subjectId) => {
    try { return await fetchGuideAsset(url, subjectId); }
    catch (error) { if (identityFailure(error)) await recoverIdentity(error); throw error; }
  },
  request: async (path, options) => {
    try { return await request(path, options); }
    catch (error) {
      if (identityFailure(error)) await recoverIdentity(error);
      else if (permissionFailure(error)) {
        try { await refreshAccess(); portal?.permissionsChanged(); }
        catch (accessError) { if (identityFailure(accessError)) await recoverIdentity(accessError); }
      }
      throw error;
    }
  },
  getAccess: () => access,
  getIdentityProblem: () => identityProblem,
  refreshAccess: async () => {
    try { await refreshAccess(); }
    catch (error) { if (identityFailure(error)) await recoverIdentity(error); throw error; }
  },
  getApiOrigin: () => apiBase || window.location.origin,
  setAccess: next => { access = next; renderIdentity(); },
  demoEnabled: () => $("guestDemoCheckbox").checked,
  openIdentity, returnToGuest, openTopic, leaveTopic,
});
await portal.start();
