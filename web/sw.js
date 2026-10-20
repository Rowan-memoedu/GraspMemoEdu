'use strict';
// Network-only by design. Never cache authenticated pages, APIs, or answers.
const APP_ROOT = new URL('./', self.location.href);
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || request.mode !== 'navigate' ||
      url.origin !== APP_ROOT.origin || !url.pathname.startsWith(APP_ROOT.pathname)) return;
  event.respondWith(fetch(request).catch(() => new Response(
    '<!doctype html><html lang="zh-CN"><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>GraspMemoEdu · 等待连接</title>' +
    '<body><main><h1>暂时无法连接</h1><p>请联网后重新打开 GraspMemoEdu。</p>' +
    '<p>离线期间无法读取课程或提交答案。已保存在云端的学习进度会在连接恢复后读取。</p>' +
    '<p><a href="' + APP_ROOT.href + '">重新连接</a></p></main></body></html>',
    { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } }
  )));
});
