/* StockPilot service worker: lets the till open and sell with no internet.
 *
 * - Pages: network first, fall back to the last copy saved on the device.
 * - App files (/_next/static, fonts, icons): saved the first time they load.
 * - /api/*: never cached here. The till keeps its own copy of products and
 *   queues sales in IndexedDB (see src/lib/pos.ts).
 */
const VERSION = "sp-till-v1";
const PAGES = `${VERSION}-pages`;
const ASSETS = `${VERSION}-assets`;
const SHELL = ["/pos", "/manifest.webmanifest", "/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(PAGES).then((c) => c.addAll(SHELL).catch(() => undefined)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isAsset(url) {
  return url.pathname.startsWith("/_next/static/") || url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com"
    || /\.(png|svg|ico|webmanifest|woff2?)$/.test(url.pathname);
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin && url.pathname.startsWith("/api/")) return;

  if (isAsset(url)) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok || res.type === "opaque") { const copy = res.clone(); caches.open(ASSETS).then((c) => c.put(req, copy)); }
        return res;
      })),
    );
    return;
  }

  if (url.origin !== self.location.origin) return;

  // Pages and the data Next.js loads when moving between pages (RSC requests).
  const rsc = req.headers.get("RSC") === "1" || url.searchParams.has("_rsc");
  if (req.mode === "navigate" || rsc) {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(PAGES).then((c) => c.put(req, copy)); }
        return res;
      }).catch(async () => {
        const hit = await caches.match(req, { ignoreSearch: !rsc });
        if (hit) return hit;
        if (req.mode === "navigate") return (await caches.match("/pos")) || Response.error();
        return Response.error();
      }),
    );
  }
});
