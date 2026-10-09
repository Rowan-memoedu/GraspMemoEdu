// Only authenticated students report presence; failures never interrupt learning.
export function startPresence({getAccess, request, getTopics}) {
  let stream = crypto.randomUUID(), sequence = 0, learner = null, active = false;
  let topicKey = '', busy = false, pending = false, suspended = false;
  const topicsFor = access => getTopics().filter(id => !id.startsWith('training.') && access?.topics?.some(topic => topic.id === id)).slice(0, 100);
  async function pulse(final = false) {
    const access = getAccess();
    const eligible = access?.role === 'account' && access.is_student && !access.is_admin;
    if (!eligible) { learner = null; active = false; busy = false; pending = false; return; }
    if (learner !== access.learner_id) {
      learner = access.learner_id; stream = crypto.randomUUID(); sequence = 0;
      active = false; topicKey = ''; busy = false; pending = false;
    }
    const visible = !final && !suspended && document.visibilityState === 'visible';
    if (!visible && !active) return;
    if (busy && !final) { pending = true; return; }
    const topics = visible ? topicsFor(access) : [];
    const owner = learner, key = topics.slice().sort().join('\0');
    active = visible; topicKey = key; busy = true;
    const body = {stream_id: stream, sequence: ++sequence, visible, topic_ids: topics};
    try { await request('presence', {method: 'POST', body, keepalive: final, timeout: 8000}); }
    catch { /* A missing heartbeat is a gap, never fabricated online time. */ }
    finally { if (owner === learner) { busy = false; if (pending) { pending = false; void pulse(); } } }
  }
  // Route/render/identity changes are detected without coupling to learning views.
  const observer = new MutationObserver(() => {
    const access = getAccess(), key = topicsFor(access).sort().join('\0');
    if (key !== topicKey || access?.learner_id !== learner) void pulse();
  });
  observer.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['hidden']});
  document.addEventListener('visibilitychange', () => void pulse(document.visibilityState !== 'visible'));
  window.addEventListener('pagehide', () => { suspended = true; void pulse(true); });
  window.addEventListener('pageshow', () => { suspended = false; void pulse(); });
  window.setInterval(() => void pulse(), 30000);
  void pulse();
}
