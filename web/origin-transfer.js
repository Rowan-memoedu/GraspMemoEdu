export const SOURCE_ORIGIN = 'https://rowan-memoedu.github.io';
export const TARGET_ORIGIN = 'https://memoedu.github.io';
export const SOURCE_URL = `${SOURCE_ORIGIN}/GraspMemoEdu-Pages/`;
export const TARGET_URL = `${TARGET_ORIGIN}/GraspMemoEdu-Pages/`;
const BACKUP_PREFIX = 'graspmemoedu:origin-transfer-backup:';
const scoped = key => typeof key === 'string' && /^(graspmemoedu|math-learning-web):/.test(key) && !key.startsWith(BACKUP_PREFIX);

export function captureStorage(storage) {
  const values = [];
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (scoped(key)) values.push([key, storage.getItem(key)]);
  }
  return values;
}

export function validateValues(values) {
  if (!Array.isArray(values) || values.length > 2000 || JSON.stringify(values).length > 4000000) throw new Error('迁移内容超出限制，原数据已保留。');
  const keys = new Set();
  for (const pair of values) {
    if (!Array.isArray(pair) || pair.length !== 2 || !scoped(pair[0]) || typeof pair[1] !== 'string' || keys.has(pair[0])) throw new Error('迁移内容无法验证，原数据已保留。');
    keys.add(pair[0]);
  }
  return values;
}

export function identityConflict(storage, values) {
  return validateValues(values).some(([key, value]) => /:(identity|guest-identity):/.test(key) && storage.getItem(key) && storage.getItem(key) !== value);
}

export function importStorage(storage, values, {replace = false, stamp = Date.now()} = {}) {
  validateValues(values);
  if (!replace && identityConflict(storage, values)) throw new Error('identity_conflict');
  const before = captureStorage(storage);
  const backup = BACKUP_PREFIX + stamp;
  // Write the recovery copy first. A quota failure must not destroy current state.
  storage.setItem(backup, JSON.stringify(before));
  const touched = [];
  try {
    for (const [key, value] of values) {
      if (!replace && storage.getItem(key) !== null) continue;
      touched.push([key, storage.getItem(key)]);
      storage.setItem(key, value);
    }
    for (const [key, value] of touched) {
      const expected = values.find(pair => pair[0] === key)[1];
      if (storage.getItem(key) !== expected) throw new Error('存储校验未通过。');
    }
  } catch (error) {
    for (const [key, value] of touched.reverse()) {
      if (value === null) storage.removeItem(key); else storage.setItem(key, value);
    }
    throw error;
  }
  return backup;
}

export function trustedMessage(event, {origin, source, nonce, type}) {
  return event.origin === origin && event.source === source && event.data?.nonce === nonce && event.data?.type === type;
}

export function startTransferPage(win, doc) {
  const status = doc.getElementById('status');
  const primary = doc.getElementById('continue');
  const alternative = doc.getElementById('alternative');
  const say = (text, state = '') => { status.textContent = text; doc.body.dataset.state = state; };
  if (doc.body.dataset.transfer === 'source') {
    if (win.location.origin !== SOURCE_ORIGIN) { say('请从原学习网站入口恢复进度。', 'error'); primary.textContent = '返回原入口'; primary.onclick = () => win.location.assign(SOURCE_URL); return; }
    let child, nonce;
    win.addEventListener('message', event => {
      if (trustedMessage(event, {origin: TARGET_ORIGIN, source: child, nonce, type: 'graspmemoedu-transfer-ready'})) {
        try { child.postMessage({type: 'graspmemoedu-transfer-data', nonce, values: captureStorage(win.localStorage), sessionValues: captureStorage(win.sessionStorage)}, TARGET_ORIGIN); }
        catch { say('无法读取浏览器存储。请保留此浏览器，原身份仍在这里。', 'error'); }
      }
      if (trustedMessage(event, {origin: TARGET_ORIGIN, source: child, nonce, type: 'graspmemoedu-transfer-complete'})) {
        say('已在新网站恢复身份。原浏览器副本继续保留。', 'success'); primary.disabled = false;
      }
    });
    primary.addEventListener('click', () => {
      nonce = win.crypto.randomUUID();
      child = win.open(`${TARGET_URL}restore.html?nonce=${encodeURIComponent(nonce)}`, '_blank');
      if (!child) { say('请允许打开新网站，然后重试。原进度没有改动。', 'error'); return; }
      say('正在连接新网站，请在新标签页继续。', 'loading');
    });
    return;
  }
  if (win.location.origin !== TARGET_ORIGIN) { say('请使用新的学习网站地址。', 'error'); return; }
  const nonce = new URL(win.location.href).searchParams.get('nonce');
  let values, sessionValues, imported = false;
  const enter = () => win.location.replace(TARGET_URL);
  const apply = replace => {
    try {
      if (!replace && (identityConflict(win.localStorage, values) || identityConflict(win.sessionStorage, sessionValues))) throw new Error('identity_conflict');
      const localBefore = captureStorage(win.localStorage);
      importStorage(win.localStorage, values, {replace});
      try { importStorage(win.sessionStorage, sessionValues, {replace}); }
      catch (error) {
        for (const [key] of values) if (!localBefore.some(pair => pair[0] === key)) win.localStorage.removeItem(key);
        for (const [key, value] of localBefore) win.localStorage.setItem(key, value);
        throw error;
      }
      imported = true;
      win.opener?.postMessage({type: 'graspmemoedu-transfer-complete', nonce}, SOURCE_ORIGIN);
      say(values.length || sessionValues.length ? '身份与浏览器中的学习草稿已恢复。可以继续学习。' : '此浏览器未找到旧站身份。已有账号可用邀请码重新登录。', 'success');
      primary.textContent = '继续学习'; primary.onclick = enter; primary.hidden = false; alternative.hidden = true;
    } catch (error) {
      if (error.message === 'identity_conflict') {
        say('此处已有另一份身份。切回原站身份前，会保留当前身份的本机恢复副本。');
        primary.textContent = '使用原站进度'; primary.onclick = () => apply(true); primary.hidden = false;
        alternative.textContent = '保留当前身份'; alternative.onclick = enter; alternative.hidden = false;
      } else say('恢复未完成，请保留原浏览器并重试；不要清除网站数据。', 'error');
    }
  };
  win.addEventListener('message', event => {
    if (imported || !win.opener || !trustedMessage(event, {origin: SOURCE_ORIGIN, source: win.opener, nonce, type: 'graspmemoedu-transfer-data'})) return;
    try { values = validateValues(event.data.values); sessionValues = validateValues(event.data.sessionValues || []); apply(false); }
    catch { say('来源内容无法验证，未改动现有身份。', 'error'); }
  });
  primary.hidden = true;
  if (win.opener && /^[a-f0-9-]{36}$/i.test(nonce || '')) {
    say('正在从原网站恢复学习身份。', 'loading');
    win.opener.postMessage({type: 'graspmemoedu-transfer-ready', nonce}, SOURCE_ORIGIN);
    win.setTimeout(() => { if (!imported && !values) say('尚未收到原站身份。请返回原入口重试，原数据仍然保留。', 'error'); }, 15000);
  } else say('请从原网站入口开始，以保留该浏览器里的学习身份。');
  alternative.textContent = '返回原入口'; alternative.onclick = () => win.location.assign(SOURCE_URL); alternative.hidden = false;
}

if (typeof window !== 'undefined' && document.body?.dataset.transfer) startTransferPage(window, document);
