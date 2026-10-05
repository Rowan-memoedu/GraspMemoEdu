// Optional timer for any reader content block. A server deadline survives refresh.
// Submission and timeout policy belong to the caller; inactive blocks create no timer.
export function createContentTimer({element, render, onExpire, now = () => performance.now()}) {
  let timer = null, state = null, receivedAt = 0, expired = false;
  const clear = () => { if (timer !== null) clearTimeout(timer); timer = null; };
  function tick() {
    clear();
    if (!state) { element.hidden = true; return; }
    element.hidden = false;
    const elapsed = state.finished_at || state.paused ? 0 : Math.max(0, now() - receivedAt) / 1000;
    const remaining = Math.max(0, Math.ceil(state.remaining_seconds - elapsed));
    const timedOut = state.timed_out || remaining === 0;
    render(element, {...state, remaining_seconds: remaining, timed_out: timedOut});
    if (timedOut && !expired && !state.finished_at) { expired = true; onExpire?.(state); }
    if (!state.finished_at) timer = setTimeout(tick, 250);
  }
  return {
    update(next) {
      if (next?.deadline_at !== state?.deadline_at) expired = false;
      if (!state || next?.server_now !== state?.server_now || next?.deadline_at !== state?.deadline_at) receivedAt = now();
      state = next; tick();
    },
    destroy() { clear(); state = null; element.hidden = true; },
  };
}
