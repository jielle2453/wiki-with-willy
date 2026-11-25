// willy 網頁的 service worker（#74 第 4 份，D-357、D-358）：主機沒開時手機還打得開、看得到上次的首頁與最近開過的卡。
// app.js 用 /sw.js?v=<版本> 註冊（範圍 /）；版本從 /api/status 的 version 來，換版時網址變了，瀏覽器就裝新的。
// 畫面的檔（index.html、css、js、圖示、manifest）裝的時候存進「willy-<版本>」，之後先用存著的；
// GET /api/home、/api/page?…、/api/chat（不帶 after 的）先上網、失敗或回的不是 JSON 用存著的，成功時存一份（多一個 X-Willy-Saved 標頭記時間）；
// 其他 API 不經這裡。打開網頁本身先上網：主機沒開時 Cloudflare 回的錯誤頁（5xx）不給她看，改給存著的畫面；
// 登入過期 Cloudflare 回的轉址照樣交給瀏覽器（她才登得回去）。
"use strict";

var VERSION = new URL(self.location.href).searchParams.get("v") || "0";
var SHELL = "willy-" + VERSION;
var DATA = "willy-data";
var PAGES_MAX = 60;
var FILES = ["/", "/index.html", "/app.css", "/chat.css", "/md.js", "/icons.js", "/queue.js", "/chat.js", "/app.js",
  "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(SHELL).then(function (cache) {
    return cache.addAll(FILES);
  }).then(function () {
    return self.skipWaiting();
  }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) {
      return n.indexOf("willy-") === 0 && n !== SHELL && n !== DATA;
    }).map(function (n) { return caches.delete(n); }));
  }).then(function () {
    return self.clients.claim();
  }));
});

function isJson(res) {
  return (res.headers.get("Content-Type") || "").indexOf("application/json") === 0;
}

function saved(req) {
  return caches.open(DATA).then(function (cache) { return cache.match(req.url); });
}

function trimPages(cache) {
  return cache.keys().then(function (keys) {
    var pages = keys.filter(function (k) { return new URL(k.url).pathname === "/api/page"; });
    return Promise.all(pages.slice(0, Math.max(0, pages.length - PAGES_MAX)).map(function (k) { return cache.delete(k); }));
  });
}

function keep(req, res) {
  return res.clone().blob().then(function (body) {
    return caches.open(DATA).then(function (cache) {
      var copy = new Response(body, { status: 200, headers: {
        "Content-Type": res.headers.get("Content-Type") || "application/json; charset=utf-8",
        "X-Willy-Saved": new Date().toISOString()
      } });
      return cache.delete(req.url).then(function () { return cache.put(req.url, copy); }).then(function () {
        return trimPages(cache);
      });
    });
  }).catch(function () {});
}

var OFFLINE = JSON.stringify({ error: "主機沒開", offline: true });

function networkFirst(event) {
  var req = event.request;
  return fetch(req).then(function (res) {
    if (res.ok && isJson(res)) {
      event.waitUntil(keep(req, res));
      return res;
    }
    if (res.status >= 500 || !isJson(res)) {  // Cloudflare 的錯誤頁：用存著的
      return saved(req).then(function (hit) { return hit || res; });
    }
    return res;
  }, function () {
    return saved(req).then(function (hit) {
      return hit || new Response(OFFLINE, { status: 503, headers: { "Content-Type": "application/json; charset=utf-8" } });
    });
  });
}

function shell(path) {
  return caches.open(SHELL).then(function (cache) { return cache.match(path); });
}

function navigate(event) {
  return fetch(event.request).then(function (res) {
    if (res.status >= 500) {
      return shell("/index.html").then(function (hit) { return hit || res; });
    }
    return res;
  }, function () {
    return shell("/index.html").then(function (hit) { return hit || Response.error(); });
  });
}

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === "/api/home" || url.pathname === "/api/page" || (url.pathname === "/api/chat" && !url.search)) {
    event.respondWith(networkFirst(event));
    return;
  }
  if (url.pathname.indexOf("/api/") === 0 || url.pathname === "/sw.js") return;
  if (req.mode === "navigate") {
    event.respondWith(navigate(event));
    return;
  }
  event.respondWith(shell(url.pathname).then(function (hit) { return hit || fetch(req); }));
});
