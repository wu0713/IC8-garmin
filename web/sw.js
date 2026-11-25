// 只快取 App 外殼，讓主畫面圖示在網路不穩時也打得開；API 一律走網路。
const CACHE = "ic8-shell-v30";
const SHELL = ["/", "/style.css", "/js/core.js", "/js/summary.js", "/js/activity.js", "/js/analysis.js",
  "/js/me.js", "/js/brief.js", "/js/apple.js", "/js/strength.js", "/js/learn.js", "/js/runner.js", "/js/share.js", "/js/main.js", "/manifest.webmanifest", "/characters/manifest.json", "/icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname.startsWith("/cal/")) return;
  // 網路優先，失敗才用快取 → 更新後立刻生效
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});

// 晨報、晚報推播
self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || "跑課", {
    body: d.body || "", icon: "/icons/apple-touch-icon.png", badge: "/icons/apple-touch-icon.png", data: { url: d.url || "/" },
  }));
});
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((cs) => {
    for (const c of cs) { if ("focus" in c) { c.navigate(url).catch(() => {}); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
