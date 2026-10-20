// Only application installation lives here; learning state remains server-owned.
let installPrompt;
const installed = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
function renderInstall() {
  document.querySelectorAll('[data-pwa-install]').forEach(button => {
    button.hidden = !installPrompt || installed();
    button.disabled = false;
  });
  document.querySelectorAll('[data-pwa-installed]').forEach(node => { node.hidden = !installed(); });
}
addEventListener('beforeinstallprompt', event => {
  event.preventDefault(); installPrompt = event; renderInstall();
});
addEventListener('appinstalled', () => { installPrompt = null; renderInstall(); });
document.querySelectorAll('[data-pwa-install]').forEach(button => button.addEventListener('click', async () => {
  if (!installPrompt) return;
  const pending = installPrompt; installPrompt = null; button.disabled = true;
  try { await pending.prompt(); await pending.userChoice; }
  catch { /* Manual installation instructions remain visible. */ }
  finally { renderInstall(); }
}));
renderInstall();
if ('serviceWorker' in navigator && isSecureContext) {
  // Activation never reloads a page: an unfinished answer must stay on screen.
  navigator.serviceWorker.register(new URL('./sw.js?v=e95839540a8bb6dd', import.meta.url), { scope: './', updateViaCache: 'none' })
    .then(registration => {
      const update = () => registration.update().catch(() => {});
      addEventListener('online', update);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) update(); });
    }).catch(error => { console.warn('应用安装组件暂不可用', error.name); });
}
