const CACHE_NAME = 'dawncast-v1';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/favicon.svg',
  '/manifest.json',
  '/apple-touch-icon.png',
  '/icon-192.png',
  '/icon-512.png'
];

// 只快取瀏覽器原生資源請求（HTML 導航/CSS/JS/圖片/字型），
// 用 request.destination 判斷而非猜測 URL 路徑或網域——
// 這樣無論 API 走同網域 /api 前綴、裸路徑、或跨網域，都不會被誤判為靜態資源。
// fetch() 呼叫的 API 請求 destination 恆為空字串，天生被排除。
const CACHEABLE_DESTINATIONS = new Set(['style', 'script', 'image', 'font', 'manifest']);

function isCacheableRequest(request) {
  return request.mode === 'navigate' || CACHEABLE_DESTINATIONS.has(request.destination);
}

function isCacheableResponse(response) {
  return response.status === 200 && response.type === 'basic';
}

async function putInCache(request, response) {
  const cache = await caches.open(CACHE_NAME);
  await cache.put(request, response);
}

// 離線且無快取時：導航請求退回 app shell，其餘請求交回瀏覽器產生網路錯誤。
function respondWhenOffline(request) {
  if (request.mode === 'navigate') {
    return caches.match('/index.html');
  }
  return undefined;
}

async function fetchAndRevalidate(request) {
  const networkResponse = await fetch(request);
  if (isCacheableResponse(networkResponse)) {
    // 背景寫入快取，不阻塞回應；寫入失敗（例如配額不足）不影響本次結果。
    putInCache(request, networkResponse.clone()).catch(() => undefined);
  }
  return networkResponse;
}

// Stale-while-revalidate：先回快取，同時在背景更新。
async function respondFromCache(request) {
  const cachedResponse = await caches.match(request);
  const networkPromise = fetchAndRevalidate(request).catch(() => respondWhenOffline(request));
  return cachedResponse ?? networkPromise;
}

async function precacheStaticShell() {
  const cache = await caches.open(CACHE_NAME);
  await cache.addAll(STATIC_ASSETS);
  await self.skipWaiting();
}

async function deleteOutdatedCaches() {
  const cacheNames = await caches.keys();
  const outdated = cacheNames.filter((name) => name !== CACHE_NAME);
  await Promise.all(outdated.map((name) => caches.delete(name)));
  await self.clients.claim();
}

// 1. Install：預先快取核心靜態外殼
self.addEventListener('install', (event) => {
  event.waitUntil(precacheStaticShell());
});

// 2. Activate：清掉舊版快取
self.addEventListener('activate', (event) => {
  event.waitUntil(deleteOutdatedCaches());
});

// 3. Fetch：靜態外殼走 stale-while-revalidate，其餘（含 API）一律 network-only
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // 忽略非 HTTP/HTTPS 請求（chrome-extension 等）
  if (!url.protocol.startsWith('http')) return;
  if (request.method !== 'GET') return;

  if (!isCacheableRequest(request)) {
    event.respondWith(fetch(request));
    return;
  }

  event.respondWith(respondFromCache(request));
});
