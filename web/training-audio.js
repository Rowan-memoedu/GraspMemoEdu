import {ct, contextError} from './context-library-messages.js?v=c9b135ee2b396ff5';
/* Audio is an input/content capability; text still uses the shared answer path. */
const node = (tag, cls = '', text) => {
  const el = document.createElement(tag); el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
};
const button = (text, action) => {
  const el = node('button', 'secondaryButton', text); el.type = 'button'; el.addEventListener('click', action); return el;
};

export function encodeWave(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2), view = new DataView(buffer);
  const string = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  string(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); string(8, 'WAVE');
  string(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  string(36, 'data'); view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => { const value = Math.max(-1, Math.min(1, sample)); view.setInt16(44 + i * 2, value < 0 ? value * 32768 : value * 32767, true); });
  return buffer;
}

function bytesFromBase64(value) {
  const decoded = atob(value), bytes = new Uint8Array(decoded.length);
  for (let i = 0; i < decoded.length; i++) bytes[i] = decoded.charCodeAt(i);
  return bytes;
}
function base64FromBytes(bytes) {
  let text = '';
  for (let i = 0; i < bytes.length; i += 32768) text += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(text);
}

export function trainingAudio(question, {request, contentVersion, onTranscript, disabled = false} = {}) {
  const media = question?.audio;
  if (!media || media.version !== 1 || !/^training-audio\?/.test(media.url || '')) return null;
  const root = node('section', 'trainingAudio'); root.setAttribute('aria-label', ct('听说练习'));
  const message = node('p', 'trainingAudioStatus'); message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
  const audio = node('audio'); audio.controls = true; audio.preload = 'none'; audio.hidden = true;
  const actions = node('div', 'trainingAudioActions');
  let disposed = false, busy = false, originalUrl = null, recordingUrl = null, recording = null;
  let stream = null, audioContext = null, source = null, processor = null, mute = null, chunks = [], frames = 0, interval = null, observer = null;
  let recordingActive = false, opening = false, generation = 0, asrAvailable = false;
  const status = (text, kind = '') => { message.textContent = text; message.dataset.state = kind; root.dataset.state = kind; };
  const apiPath = operation => media.url.replace('training-audio?', `training-audio/${operation}?`);
  const preview = node('audio'); preview.controls = true; preview.hidden = true; preview.preload = 'none';
  const load = button(ct('加载题目音频'), async () => {
    if (busy || disposed) return;
    busy = true; update(); status(ct('正在读取音频…'), 'loading');
    try {
      const result = await request(media.url, {timeout: 60000});
      if (disposed) return;
      if (result.mime_type !== media.mime_type || typeof result.data_base64 !== 'string' || result.data_base64.length > 14 * 1024 * 1024) throw new Error(ct('音频格式不正确。'));
      originalUrl = URL.createObjectURL(new Blob([bytesFromBase64(result.data_base64)], {type: result.mime_type}));
      audio.src = originalUrl; audio.hidden = false; load.hidden = true; status(ct('音频已就绪。'), 'success');
    } catch (error) { if (!disposed) status(contextError(error.message) || ct('音频读取失败，请重试。'), 'error'); }
    finally { busy = false; update(); }
  });
  audio.addEventListener('error', () => { if (!disposed) { status(ct('音频无法播放，请重新加载。'), 'error'); load.hidden = false; } });
  const record = button(ct('开始录音'), async () => {
    if (recordingActive) { await finishRecording(); return; }
    if (busy || opening || disposed || disabled) return;
    if (!navigator.mediaDevices?.getUserMedia || !(window.AudioContext || window.webkitAudioContext)) {
      status(ct('当前浏览器无法录音。请直接填写文字答案。'), 'error'); return;
    }
    const ticket = ++generation;
    opening = true; update(); status(ct('请允许浏览器使用麦克风。'), 'loading');
    try {
      const opened = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true}, video: false});
      if (disposed || ticket !== generation) { opened.getTracks().forEach(track => track.stop()); return; }
      stream = opened;
      audioContext = new (window.AudioContext || window.webkitAudioContext)(); await audioContext.resume();
      if (disposed || ticket !== generation) { releaseMicrophone(); return; }
      chunks = []; frames = 0; recording = null; preview.hidden = true;
      if (recordingUrl) { URL.revokeObjectURL(recordingUrl); recordingUrl = null; }
      source = audioContext.createMediaStreamSource(stream);
      processor = audioContext.createScriptProcessor(4096, 1, 1);
      mute = audioContext.createGain(); mute.gain.value = 0;
      source.connect(processor); processor.connect(mute); mute.connect(audioContext.destination);
      recordingActive = true;
      processor.onaudioprocess = event => {
        if (!recordingActive) return;
        const remaining = Math.floor(audioContext.sampleRate * 120) - frames;
        const sample = event.inputBuffer.getChannelData(0).slice(0, Math.max(0, remaining));
        chunks.push(sample); frames += sample.length;
        if (remaining <= sample.length) void finishRecording();
      };
      interval = setInterval(() => { if (recordingActive) status(ct('正在录音：{seconds} / 120 秒', {seconds: Math.floor(frames / audioContext.sampleRate)})); }, 500);
      observer = new MutationObserver(() => { if (!root.isConnected) dispose(); });
      observer.observe(document.body, {childList: true, subtree: true});
      status(ct('正在录音，完成后点击停止。'));
    } catch {
      releaseMicrophone(); if (!disposed) status(ct('无法访问麦克风。请检查浏览器权限，或直接填写文字答案。'), 'error');
    } finally { opening = false; update(); }
  });
  const transcribe = button(ct('转写到答案'), async () => {
    if (!recording || busy || recordingActive || disposed || !asrAvailable) return;
    busy = true; update(); status(ct('正在转写；完成后可在答案框修正。'), 'loading');
    try {
      const result = await request(apiPath('transcribe'), {method: 'POST', timeout: 180000,
        body: {content_version: contentVersion, mime_type: 'audio/wav', data_base64: base64FromBytes(new Uint8Array(recording))}});
      if (disposed) return;
      if (!result?.text) throw new Error(ct('未获得有效转写，可重新录音或直接填写答案。'));
      onTranscript?.(result.text); status(ct('转写已放入答案框，请核对后提交。'), 'success');
    } catch (error) { if (!disposed) status(contextError(error.message) || ct('转写失败；录音仍可回听，也可直接填写文字。'), 'error'); }
    finally { busy = false; update(); }
  });
  function update() {
    load.disabled = busy;
    record.disabled = disabled || busy || opening;
    record.textContent = recordingActive ? ct('停止录音') : recording ? ct('重新录音') : ct('开始录音');
    transcribe.disabled = disabled || busy || opening || recordingActive || !recording || !asrAvailable;
  }
  function releaseMicrophone() {
    recordingActive = false; clearInterval(interval); interval = null;
    if (processor) { processor.onaudioprocess = null; processor.disconnect(); }
    source?.disconnect(); mute?.disconnect(); stream?.getTracks().forEach(track => track.stop());
    void audioContext?.close().catch(() => {});
    processor = null; source = null; mute = null; stream = null; audioContext = null;
    observer?.disconnect(); observer = null;
  }
  async function finishRecording() {
    if (!recordingActive) return;
    const rate = audioContext.sampleRate, samples = chunks, length = frames; chunks = [];
    releaseMicrophone(); busy = true; update();
    try {
      if (!length) throw new Error(ct('录音为空，请再试一次。'));
      const offline = new OfflineAudioContext(1, Math.ceil(length * 16000 / rate), 16000);
      const buffer = offline.createBuffer(1, length, rate), values = buffer.getChannelData(0);
      let offset = 0; for (const chunk of samples) { values.set(chunk, offset); offset += chunk.length; }
      const input = offline.createBufferSource(); input.buffer = buffer; input.connect(offline.destination); input.start();
      const result = await offline.startRendering();
      if (disposed) return;
      recording = encodeWave(result.getChannelData(0));
      recordingUrl = URL.createObjectURL(new Blob([recording], {type: 'audio/wav'})); preview.src = recordingUrl; preview.hidden = false;
      status(asrAvailable ? ct('录音已保留在当前页面。可回听，或转写到答案后修正。') : ct('录音可回听；转写暂不可用，请直接填写答案。'), 'success');
    } catch (error) { if (!disposed) status(contextError(error.message), 'error'); }
    finally { busy = false; update(); }
  }
  function onVisibility() { if (document.hidden && recordingActive) void finishRecording(); }
  function dispose() {
    if (disposed) return;
    disposed = true; generation++; releaseMicrophone(); chunks = []; recording = null;
    audio.pause(); preview.pause(); if (originalUrl) URL.revokeObjectURL(originalUrl); if (recordingUrl) URL.revokeObjectURL(recordingUrl);
    document.removeEventListener('visibilitychange', onVisibility); window.removeEventListener('hashchange', dispose);
  }
  actions.append(load); root.append(actions, audio);
  if (['read_aloud', 'retell'].includes(media.mode) && onTranscript && !disabled) {
    actions.append(record, transcribe); root.append(preview);
    root.append(node('p', 'inputHint', ct('录音只用于转写。请核对答案框中的文字后提交，结果评价表达内容。')));
    void request(apiPath('status')).then(result => {
      if (disposed) return;
      asrAvailable = !!result.transcription_available; update();
      if (!asrAvailable) status(result.message ? contextError(result.message) : ct('转写暂不可用，可直接填写文字答案。'));
    }).catch(error => { if (!disposed) status(contextError(error.message), 'error'); });
  }
  if (media.transcript) {
    const details = node('details'); details.append(node('summary', '', ct('音频原文')), node('p', 'trainingAudioTranscript', media.transcript)); root.append(details);
  }
  root.append(message); update();
  document.addEventListener('visibilitychange', onVisibility); window.addEventListener('hashchange', dispose);
  root.dispose = dispose;
  return root;
}
