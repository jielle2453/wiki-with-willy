// willy 網頁（#74 第 3 份「網頁首頁與看卡」）：首頁、找卡、看卡、隨手記；對話在右欄（chat.js 的 window.WillyChat）。
// 網址用 hash：#/（首頁）、#/find?q=…（找卡）、#/page/<相對路徑>（看卡）、#/note（隨手記）、#/chat（手機的對話分頁）。
// 畫面上的字一律用 textContent 放，只有 md.js 轉好的 HTML（已先跳脫）用 innerHTML。日期都用 kb 給的，網頁不自己算今天（D-252）。
// 手機（第 4 份，D-357、D-358）：/api/status 連不上、逾時 5 秒或回的不是 JSON → 離線：最上面一條「主機沒開，這是 X 的資料」、
// 首頁與看過的卡用 sw.js 存著的；打勾、改日期、新增、隨手記、傳檔、跟 AI 講的先排進 queue.js，打開、切回來、online、
// 主機又連得到時照順序送；搜尋、開原檔、「要你確認的」要主機。手機或平板不是從主畫面開的，首頁最上面提示加到主畫面。
(function () {
  "use strict";

  var STATUS_EVERY = 30000;
  var STATUS_TIMEOUT = 5000;
  var HOME_EVERY = 60000;
  var TOAST_MS = 10000;
  var UPLOAD_MAX = 50 * 1024 * 1024;
  var NOTE_DELAY = 1500;
  var WIDE = "(min-width: 900px)";
  var AGENDA_PAGE = "系統/日程.md";
  var INDEX_PAGE = "總目錄.md";
  var PENDING_PAGE = "系統/待處理.md";
  var CARDS_DIR = "檔案索引";
  var AGENDA_KEY = "willy.agendaView";
  var CARDS_TTL = 60000;
  // 待處理的種類（照 kb 的 pending.SECTIONS）：[圖示, 小標籤, 顏色]
  var PENDING_KINDS = {
    missing_file: ["search-x", "原檔不見", "red"], unreadable: ["file", "讀不到", "amber"], close: ["circle-check", "要確認結案", "green"],
    conflict: ["triangle-alert", "兩份寫法不同", "amber"], script_touched: ["shield-alert", "程式動到的檔", "gray"],
    lint: ["heart-pulse", "健檢查到的", "blue"], handedit: ["notebook-pen", "你手改過的", "purple"], tag_edit: ["tag", "改了標籤", "purple"],
    report: ["heart-pulse", "健檢報告", "blue"]
  };
  var PENDING_ORDER = ["missing_file", "unreadable", "close", "conflict", "script_touched", "lint", "handedit", "tag_edit", "report"];
  var CLAUDE_ASK = "有哪些要我跟你一起看的？";
  var COPY_TEXT = "這是副本，改了不會存回去";
  var UPLOAD_ACCEPT = "image/*,application/pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.md,.zip";
  var MENU = [
    { label: "首頁", hash: "#/", view: "home", icon: "house" },
    { label: "總目錄", page: INDEX_PAGE, icon: "book-open" },
    { label: "日程", page: AGENDA_PAGE, icon: "calendar" },
    { label: "待處理", page: PENDING_PAGE, icon: "inbox" },
    { label: "健檢報告", report: true, icon: "heart-pulse" },
    { label: "隨手記", hash: "#/note", view: "note", icon: "notebook-pen" }
  ];
  var WEEKDAY = "日一二三四五六";
  var NEED_HOST = "主機開了才能用";
  var QUEUED_TEXT = "主機開了才會送出";
  var HINT_KEY = "willy.homeHint";
  var HINT_DAYS = 30;
  var VAULT_KEY = "willy.vaultName";
  var QUEUE_ACTIONS = { done: "打勾", edit: "改日期", add: "新增" };

  var ENTER_MS = 600;
  var PROP_KEYS = [["卡型", "類型", "tag"], ["型別", "類型", "tag"], ["文件日期", "日期", "calendar"], ["實體", "相關", "folder"],
    ["別名", "別名", "user"], ["更新日", "更新", "clock"]];

  var state = { home: null, homeSeq: 0, view: null, page: null, pageCache: null, chat: false, toastTimer: null, note: null, drawn: false,
    offline: false, login: false, homeSaved: null, homeAt: null, queue: [], flushing: false, swVersion: null, bar: null, weekDay: null, agendaMonth: null, agendaDay: null, cardsP: null, cardsAt: 0 };
  var queue = window.WillyQueue ? window.WillyQueue.open() : null;

  // ---------- 小工具 ----------

  function $(id) {
    return document.getElementById(id);
  }

  /** h("div", {cls, text, attrs, on}, [子元素]) */
  function h(tag, props, kids) {
    var el = document.createElement(tag);
    var p = props || {};
    if (p.cls) el.className = p.cls;
    if (p.text !== undefined && p.text !== null) el.textContent = String(p.text);
    Object.keys(p.attrs || {}).forEach(function (k) {
      if (p.attrs[k] !== undefined && p.attrs[k] !== null && p.attrs[k] !== false) el.setAttribute(k, p.attrs[k] === true ? "" : String(p.attrs[k]));
    });
    Object.keys(p.on || {}).forEach(function (k) { el.addEventListener(k, p.on[k]); });
    (kids || []).forEach(function (k) {
      if (k !== null && k !== undefined && k !== false) el.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
    });
    return el;
  }

  function button(text, onClick, cls, iconName) {
    if (!iconName) return h("button", { cls: cls || "btn", text: text, attrs: { type: "button" }, on: { click: onClick } });
    return h("button", { cls: cls || "btn", attrs: { type: "button" }, on: { click: onClick } }, [icon(iconName), h("span", { cls: "btn-label", text: text })]);
  }

  // ---------- 畫面的小零件（圖示、空狀態、骨架） ----------

  /** 線條圖示（icons.js 的 window.WillyIcons）；沒載到就不畫。 */
  function icon(name, cls) {
    return window.WillyIcons ? window.WillyIcons.icon(name, cls) : null;
  }

  /** 只有圖示的按鈕：字放 aria-label 與 title。 */
  function iconButton(name, label, onClick, cls) {
    return h("button", { cls: cls || "icon-btn", attrs: { type: "button", "aria-label": label, title: label }, on: { click: onClick } }, [icon(name)]);
  }

  /** 空的時候：圖示＋一句話（ok＝好消息，綠的）。 */
  function emptyState(iconName, text, ok) {
    return h("div", { cls: "empty" + (ok ? " ok" : "") }, [h("span", { cls: "empty-icon" }, [icon(iconName)]), h("p", { text: text })]);
  }

  /** 載入中的灰色骨架，不寫「讀取中」。 */
  function skeleton(kinds) {
    return h("div", { cls: "skeleton", attrs: { "aria-busy": "true", "aria-label": "載入中" } }, kinds.map(function (k) {
      return h("span", { cls: "sk " + k });
    }));
  }

  function sectionHead(iconName, title, count, extra, countCls) {
    return h("div", { cls: "section-head" }, [icon(iconName), h("h2", { text: title }),
      count ? h("span", { cls: "count" + (countCls ? " " + countCls : ""), text: String(count) }) : null,
      h("span", { cls: "spacer" })].concat(extra || []));
  }

  /** 原檔副檔名：open.path 的，沒有就用 ext（/api/cards 從清冊補的）。 */
  function extOf(f) {
    var m = /\.([a-z0-9]+)$/.exec(String((f && f.open && f.open.path) || "").toLowerCase());
    return m ? m[1] : String((f && f.ext) || "").toLowerCase();
  }

  /** 檔案類型：照原檔副檔名上色（PDF 紅、Excel 綠、Word 藍、圖片紫、其他灰）；拿不到副檔名才照卡型挑灰色圖示。回 [class, 圖示名]。 */
  function fileKind(f) {
    var ext = extOf(f);
    if (ext === "pdf") return ["pdf", "file-text"];
    if (/^(png|jpe?g|gif|webp|bmp|heic|svg)$/.test(ext)) return ["img", "file-image"];
    if (/^(xlsx?|xlsm|csv|numbers)$/.test(ext)) return ["xls", "file-spreadsheet"];
    if (/^(docx?|pages|rtf)$/.test(ext)) return ["doc", "file-text"];
    if (ext) return ["", "file"];
    var t = String((f && (f.type || f.card_type)) || "");
    return ["", /報價|估價|表|帳|款|薪/.test(t) ? "file-spreadsheet" : /照片|圖/.test(t) ? "file-image" : /會議|紀錄|記錄/.test(t) ? "notebook-pen" : "file-text"];
  }

  /** 檔案類型圖示。首頁「最近」、找卡的這筆沒有原檔路徑（這台找不到原檔）時，用 /api/cards 補的副檔名補上顏色：
   *  已經抓過卡清單就用那份（不管過期沒），沒抓過才抓一次。 */
  function fileIcon(f) {
    var k = fileKind(f);
    var el = h("span", { cls: "ftype" + (k[0] ? " " + k[0] : "") }, [icon(k[1])]);
    if (!extOf(f) && f && f.card && !state.offline) {
      (state.cardsP || cardsData()).then(function (d) {
        if (!d._ext) {
          d._ext = {};
          arr(d.cards).forEach(function (c) { if (c && c.ext) d._ext[c.id] = c.ext; });
        }
        var ext = d._ext[f.card];
        if (!ext) return;
        var k2 = fileKind({ ext: ext });
        el.className = "ftype" + (k2[0] ? " " + k2[0] : "");
        el.textContent = "";
        el.appendChild(icon(k2[1]));
      }).catch(function () {});
    }
    return el;
  }

  /** 人、廠商、案子的彩色圓形頭像：第一個字，顏色照名字固定（八色輪流）。 */
  function avatar(name, cls) {
    var s = String(name || "").trim();
    var n = 0;
    Array.from(s).forEach(function (ch) { n = (n * 31 + ch.codePointAt(0)) % 9973; });
    return h("span", { cls: "avatar av" + (n % 8) + (cls ? " " + cls : ""), text: Array.from(s)[0] || "?", attrs: { title: s, "aria-hidden": "true" } });
  }

  /** 實體類型 → 圖示（人、廠商、案子、物料）。 */
  function entIcon(type) {
    var t = String(type || "");
    if (/廠商|工班|師傅/.test(t)) return "store";
    if (/案|工程/.test(t)) return "briefcase";
    if (/物料|材料/.test(t)) return "package";
    return "user";
  }

  /** 長句一行，多的變「…」；滑過看全文（title）。 */
  function line1(tag, text, cls, props) {
    var p = Object.assign({ cls: "clamp" + (cls ? " " + cls : ""), text: text || "" }, props || {});
    p.attrs = Object.assign({ title: text || "" }, p.attrs || {});
    return h(tag, p);
  }

  /** 小標籤：圖示＋短字；title 放這是什麼。 */
  function chip(iconName, text, cls, title) {
    return h("span", { cls: "tag" + (cls ? " " + cls : ""), attrs: { title: title || null } }, [iconName ? icon(iconName) : null, h("span", { text: text })]);
  }

  /** kb 的 YYYY-MM-DD 往後加幾天（只從 kb 給的今天往後排，不自己算今天）。 */
  function addDays(day, n) {
    var t = utc(day);
    return t === null ? "" : new Date(t + n * 86400000).toISOString().slice(0, 10);
  }

  function realDate(d) {
    return /^\d{4}-\d{2}-\d{2}$/.test(String(d || ""));
  }

  /** 卡所在的夾，給人看的：去掉最前面的「檔案索引」，用 / 分開。 */
  function folderText(path) {
    return dirOf(String(path || "")).split("/").filter(function (x, i) { return x && !(i === 0 && x === "檔案索引"); }).join(" / ");
  }

  function enc(s) {
    return encodeURIComponent(s);
  }

  function dec(s) {
    try {
      return decodeURIComponent(s);
    } catch (e) {
      return s;
    }
  }

  function wide() {
    return window.matchMedia ? window.matchMedia(WIDE).matches : true;
  }

  function pageHash(path) {
    return "#/page/" + enc(path);
  }

  function dirOf(path) {
    var i = path.lastIndexOf("/");
    return i >= 0 ? path.slice(0, i) : "";
  }

  /** kb 給的 YYYY-MM-DD → UTC 毫秒（只拿來排版與兩個 kb 日期相減，不算今天）。 */
  function utc(day) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ""));
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3]) : null;
  }

  function dayText(day, withYear) {
    var t = utc(day);
    if (t === null) return String(day || "");
    var d = new Date(t);
    return (withYear ? d.getUTCFullYear() + " 年 " : "") + (d.getUTCMonth() + 1) + " 月 " + d.getUTCDate() + " 日 星期" + WEEKDAY.charAt(d.getUTCDay());
  }

  function shortDay(day) {
    var t = utc(day);
    if (t === null) return String(day || "");
    var d = new Date(t);
    return (d.getUTCMonth() + 1) + "/" + d.getUTCDate() + "（" + WEEKDAY.charAt(d.getUTCDay()) + "）";
  }

  function greeting() {
    var hour = new Date().getHours();
    return hour >= 5 && hour < 12 ? "早安" : hour >= 12 && hour < 18 ? "午安" : "晚安";
  }

  function arr(x) {
    return Array.isArray(x) ? x : [];
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  /** 「11/24 14:05」；relative 時今天的寫「今天 14:05」（畫面上用；寫進檔的不用相對的）。 */
  function clock(t, relative) {
    var d = t instanceof Date ? t : new Date(t);
    if (isNaN(d.getTime())) return "";
    var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
    return relative && d.toDateString() === new Date().toDateString() ? "今天 " + hm : (d.getMonth() + 1) + "/" + d.getDate() + " " + hm;
  }

  function lsGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function lsSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (e) {
      // 私密瀏覽或關了網站資料：記不住就下次再提醒
    }
  }

  // ---------- 連主機 ----------

  /** fetch 包一層：回 {status, data, saved}（saved＝sw.js 拿存著的那份時記的時間）；POST 一律帶 X-Willy: 1。 */
  function api(path, opts) {
    var o = opts || {};
    var init = { method: o.method || "GET", cache: "no-store", headers: { "X-Willy": "1" } };
    if (o.body !== undefined) {
      init.headers["Content-Type"] = o.raw ? "application/octet-stream" : "application/json";
      init.body = o.raw ? o.body : JSON.stringify(o.body);
    }
    return fetch(path, init).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try {
          data = t ? JSON.parse(t) : null;
        } catch (e) {
          data = null;
        }
        return { status: r.status, data: data, saved: r.headers.get("X-Willy-Saved") };
      });
    });
  }

  /** 問主機在不在：回 Promise<"ok"|"offline"|"login">（login＝Cloudflare 要她重新登入，轉址到登入頁）。 */
  function probeStatus() {
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, STATUS_TIMEOUT) : null;
    return fetch("/api/status", { cache: "no-store", redirect: "manual", signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) {
        if (r.type === "opaqueredirect" || r.status === 403) return "login";
        if (!r.ok) return "offline";
        return r.json().then(function (d) {
          if (!d || d.app !== "wiki-with-willy") return "offline";
          showVault(d.vault_name);
          registerWorker(d.version);
          return "ok";
        });
      })
      .catch(function () { return "offline"; })
      .then(function (how) {
        if (timer) clearTimeout(timer);
        return how;
      });
  }

  function checkStatus() {
    return probeStatus().then(function (how) {
      var was = state.offline;
      state.login = how === "login";
      state.offline = how !== "ok";
      showConn(!state.offline);
      if (was !== state.offline && state.drawn && !busyTyping() && state.view !== "chat") drawView(parseHash());
      if (!state.offline) flushQueue();
      return how;
    });
  }

  /** 左上與手機頁首放知識庫名稱，方塊放第一個字；記在這台，主機沒開時也照樣顯示上次的名稱。 */
  function showVault(name) {
    if (!name || typeof name !== "string") return;
    lsSet(VAULT_KEY, name);
    Array.prototype.forEach.call(document.querySelectorAll(".vault-name"), function (el) { el.textContent = name; });
    Array.prototype.forEach.call(document.querySelectorAll(".brand-mark"), function (el) { el.textContent = Array.from(name)[0].toUpperCase(); });
  }

  function showConn(ok) {
    var text = ok ? "主機連線中" : state.login ? "要重新登入" : "主機沒開";
    $("dot").className = ok ? "dot on" : "dot";
    $("state").textContent = text;
    Array.prototype.forEach.call(document.querySelectorAll(".topbar .dot"), function (el) { el.className = ok ? "dot on" : "dot"; });
    Array.prototype.forEach.call(document.querySelectorAll(".conn-text"), function (el) { el.textContent = text; });
    Array.prototype.forEach.call(document.querySelectorAll(".quick-conn"), function (el) {
      el.textContent = text;
    });
    state.connText = text;
    fillBar();
  }

  // ---------- 主機沒開（第 4 份） ----------

  /** 最上面那一條：main() 每次重畫都放一個，狀態變了 fillBar 改它的字。 */
  function offlineBar() {
    state.bar = h("div", { cls: "offline-bar", attrs: { role: "status" } });
    fillBar();
    return state.bar;
  }

  function fillBar() {
    var el = state.bar;
    if (!el) return;
    el.textContent = "";
    el.hidden = !state.offline;
    if (!state.offline) return;
    el.appendChild(icon(state.login ? "circle-alert" : "cloud-off"));
    if (state.login) {
      el.appendChild(h("span", { text: "登入過期了，" }));
      el.appendChild(button("按這裡重新登入", function () { location.reload(); }, "linkish"));
      return;
    }
    var at = state.homeSaved || state.homeAt;
    el.appendChild(h("span", { text: at ? "主機沒開，這是 " + clock(at, true) + " 的資料" : "主機沒開，還沒有存著的資料" }));
  }

  function storeOk() {
    return !!(queue && window.WillyQueue);
  }

  function reloadQueue() {
    if (!storeOk()) return Promise.resolve();
    return queue.all().then(function (items) {
      state.queue = items || [];
    }).catch(function () {
      state.queue = [];
    }).then(showQueue);
  }

  function showQueue() {
    var n = state.queue.length;
    var el = $("pending");
    el.textContent = "";
    if (n) {
      el.appendChild(icon("clock"));
      el.appendChild(h("span", { text: "還有 " + n + " 件沒送出" }));
    }
    el.hidden = !n;
    chatCall("showPending", state.queue.filter(function (q) { return q.kind === "chat"; }).map(function (q) { return q.body.text; }));
  }

  /** 排進去（離線時她做的事），畫面照排著的先改。 */
  function enqueue(kind, body, label, extra) {
    if (!storeOk()) {
      toast("這台存不了，" + NEED_HOST);
      return Promise.resolve(false);
    }
    var item = Object.assign({ t: Date.now(), kind: kind, body: body, label: label }, extra || {});
    return queue.add(item).then(function () {
      return reloadQueue();
    }).then(function () {
      if (state.view === "home" && state.home) drawHome(state.home);
      return true;
    }).catch(function () {
      toast("這台存不了，" + NEED_HOST);
      return false;
    });
  }

  /** 一件排著的怎麼送；回 {status, data}，網路錯誤 reject（queue.js 判斷要不要停）。 */
  function sendItem(item) {
    if (item.kind === "act") return api("/api/act", { method: "POST", body: item.body });
    if (item.kind === "note") return api("/api/note", { method: "POST", body: item.body });
    if (item.kind === "chat") return api("/api/chat/send", { method: "POST", body: item.body });
    if (item.kind === "upload") {
      return (item.data !== undefined ? Promise.resolve(item.data) : queue.file(item.id)).then(function (data) {
        if (data === undefined) return { status: 410, data: { ok: false, message: "檔案本身不在這台了" } };
        var blob = new Blob([data], { type: item.type || "application/octet-stream" });
        return api("/api/upload?name=" + enc(item.name), { method: "POST", body: blob, raw: true });
      });
    }
    return Promise.resolve({ status: 400, data: { ok: false, message: "看不懂這件" } });
  }

  /** 兩個分頁（或主畫面的跟瀏覽器的）同時開著：有 Web Locks 就只讓一個送，另一個這次跳過。 */
  function oneAtATime(fn) {
    if (navigator.locks && typeof navigator.locks.request === "function") {
      return navigator.locks.request("willy-queue", { ifAvailable: true }, function (lock) {
        return lock ? fn() : null;
      });
    }
    return fn();
  }

  /** 主機連得到：照順序一件一件送；有沒套上的送一句到對話問 AI。 */
  function flushQueue() {
    if (!storeOk() || state.flushing || state.offline) return Promise.resolve();
    state.flushing = true;
    return reloadQueue().then(function () {
      if (!state.queue.length) return null;
      return oneAtATime(function () { return window.WillyQueue.flush(queue, sendItem); }).then(function (res) {
        if (!res) return;
        if (res.stopped) checkStatus();
        if (res.failed.length) {
          api("/api/chat/send", { method: "POST", body: { text: window.WillyQueue.failedText(res.failed) } }).catch(function () {});
          toast("離線時排的有 " + res.failed.length + " 件沒套上，已在對話裡問 AI 要怎麼處理");
        } else if (res.sent) {
          toast("離線時排的 " + res.sent + " 件都送出了");
        }
        if (res.sent || res.failed.length) loadHome();
      });
    }).catch(function () {}).then(function () {
      state.flushing = false;
      return reloadQueue();
    });
  }

  function expectOf(it) {
    return { date: it.date || "", start: it.start || "", time: it.time || "", status: it.status || "open" };
  }

  /** 離線（或送的時候斷了）可以排隊的動作：打勾、改日期、新增；其他的說要主機。 */
  function queueAct(body, it) {
    var what = QUEUE_ACTIONS[body.action];
    if (!what) {
      toast(NEED_HOST);
      return Promise.resolve();
    }
    var b = Object.assign({}, body);
    if (it && (body.action === "done" || body.action === "edit")) b.expect = expectOf(it);
    var label = what + "：" + (it ? it.text || "" : body.text || "");
    return enqueue("act", b, label).then(function (ok) {
      if (ok) toast("主機沒開，先存著，" + QUEUED_TEXT);
    });
  }

  function queuedActs() {
    var out = { done: {}, edit: {}, add: [] };
    state.queue.forEach(function (q) {
      if (q.kind !== "act" || !q.body) return;
      if (q.body.action === "done") out.done[q.body.id] = true;
      else if (q.body.action === "edit") out.edit[q.body.id] = q.body;
      else if (q.body.action === "add") out.add.push(q.body);
    });
    return out;
  }

  // ---------- 加到主畫面（第 4 份） ----------

  function isPhone() {
    var ua = navigator.userAgent || "";
    if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "ios";
    if (/Android/.test(ua)) return "android";
    return null;
  }

  function standalone() {
    var media = window.matchMedia ? window.matchMedia("(display-mode: standalone)").matches : false;
    return media || navigator.standalone === true;
  }

  function homeHint() {
    var kind = isPhone();
    if (!kind || standalone()) return null;
    var last = Number(lsGet(HINT_KEY) || 0);
    if (last && Date.now() - last < HINT_DAYS * 86400000) return null;
    var text = kind === "ios" ? "點下面的分享鈕，選「加入主畫面」，之後從主畫面打開，離線記的才不會被清掉"
      : "點右上角選單，選「加到主畫面」";
    var body = h("div", { cls: "hint-body" }, [h("p", { text: text })]);
    var card = h("div", { cls: "hint" }, [icon("smartphone"), body]);
    body.appendChild(button("知道了", function () {
      lsSet(HINT_KEY, String(Date.now()));
      card.remove();
    }, "linkish"));
    return card;
  }

  // ---------- service worker（第 4 份） ----------

  /** 用 /sw.js?v=<版本> 註冊；版本變了再註冊一次，瀏覽器就換新的。 */
  function registerWorker(version) {
    if (!("serviceWorker" in navigator) || !version || state.swVersion === version) return;
    state.swVersion = version;
    navigator.serviceWorker.register("/sw.js?v=" + enc(version), { scope: "/" }).catch(function () {
      state.swVersion = null;
    });
  }

  // ---------- 對話（網頁對話那份的 window.WillyChat） ----------

  function mountChat() {
    var el = $("chat");
    if (window.WillyChat && typeof window.WillyChat.mount === "function") {
      try {
        window.WillyChat.mount(el);
        state.chat = true;
        return;
      } catch (e) {
        state.chat = false;
      }
    }
    el.textContent = "";
    el.appendChild(h("p", { cls: "chat-missing", text: "對話還沒裝好" }));
  }

  function chatCall(name, arg) {
    if (!state.chat || typeof window.WillyChat[name] !== "function") return false;
    try {
      window.WillyChat[name](arg);
      return true;
    } catch (e) {
      return false;
    }
  }

  function setPage(path) {
    chatCall("setPage", path);
  }

  /** 把一句放進對話輸入框（不送）；手機切到對話分頁。 */
  function askAi(text) {
    var ok = chatCall("prefill", text);
    if (!ok && !state.chat) toast("對話還沒裝好");
    if (!wide()) location.hash = "#/chat";
  }

  // ---------- 提示 ----------

  /** 下方中間一行；有 undo 帶「復原」。warn：出錯或做不了（圖示換成琥珀色的驚嘆號）。 */
  function toast(text, undo, warn) {
    var el = $("toast");
    if (state.toastTimer) clearTimeout(state.toastTimer);
    el.textContent = "";
    el.className = "toast" + (warn ? " warn" : "");
    el.appendChild(icon(warn ? "circle-alert" : undo ? "circle-check" : "info"));
    el.appendChild(h("span", { text: text }));
    if (undo) {
      el.appendChild(button("復原", function () {
        el.hidden = true;
        undo();
      }, "", "undo-2"));
    }
    el.hidden = true;  // 重新放一次，動畫才會再跑
    void el.offsetWidth;
    el.hidden = false;
    state.toastTimer = setTimeout(function () { el.hidden = true; }, TOAST_MS);
  }

  // ---------- 選單 ----------

  function menuItems(cls) {
    return MENU.map(function (m) {
      var a = h("a", { cls: cls, attrs: { href: m.page ? pageHash(m.page) : m.hash || "#" } }, [icon(m.icon), h("span", { text: m.label })]);
      a.dataset.key = m.page || m.view || "report";
      if (m.report) {
        a.addEventListener("click", function (ev) {
          ev.preventDefault();
          openReport();
        });
      }
      return a;
    });
  }

  function markMenu() {
    var key = state.view === "page" ? state.page : state.view;
    Array.prototype.forEach.call(document.querySelectorAll(".menu-item"), function (a) {
      a.classList.toggle("on", a.dataset.key === key || (a.dataset.key === "report" && state.view === "page" && /^系統\/健檢報告\//.test(state.page || "")));
    });
    // 手機底下三分頁：選單頁（總目錄、日程、待處理、健檢報告、隨手記）不屬於哪一個，三個都不亮；其他的卡片頁算找卡
    var menuPage = state.view === "note" || (state.view === "page" &&
      (MENU.some(function (m) { return m.page && m.page === state.page; }) || /^系統\/健檢報告\//.test(state.page || "")));
    var tab = state.view === "chat" ? "chat" : state.view === "home" ? "home" : menuPage ? "" : "find";
    Array.prototype.forEach.call(document.querySelectorAll(".tabs a"), function (a) {
      a.classList.toggle("on", a.dataset.tab === tab);
    });
  }

  function openReport() {
    api("/api/latest-report").then(function (r) {
      var p = r.data && r.data.path;
      if (p) location.hash = pageHash(p);
      else toast("還沒有健檢報告");
    }).catch(function () { toast("連不上主機", null, true); });
  }

  // ---------- 路由 ----------

  function parseHash() {
    var s = location.hash || "#/";
    var m;
    if ((m = /^#\/find(?:\?q=(.*))?$/.exec(s))) return { view: "find", q: dec(m[1] || "") };
    if ((m = /^#\/page\/(.+)$/.exec(s))) return { view: "page", path: dec(m[1]) };
    if ((m = /^#\/index(?:\/(.*))?$/.exec(s))) return { view: "page", path: INDEX_PAGE, dir: dec(m[1] || "") };
    if (s === "#/chat") return { view: "chat" };
    if (s === "#/note") return { view: "note" };
    return { view: "home" };
  }

  function route() {
    var r = parseHash();
    flushNote();
    document.body.setAttribute("data-view", r.view);
    if (r.view === "chat") {
      if (!state.drawn) {
        state.view = "home";
        drawView({ view: "home" });
      }
      state.view = "chat";
      markMenu();
      return;
    }
    drawView(r);
  }

  function drawView(r) {
    state.view = r.view;
    state.page = r.view === "page" ? r.path : null;
    state.drawn = true;
    markMenu();
    enter();
    $("main").scrollTop = 0;
    if (r.view === "page") return specialView(r) || renderPage(r.path);
    setPage(null);
    if (r.view === "find") return renderFind(r.q);
    if (r.view === "note") return renderNote();
    return renderHome();
  }

  /** 換頁時內容輕輕浮上來（只在換頁後一小段時間，60 秒重抓首頁時不閃）。 */
  function enter() {
    var el = $("main");
    if (state.enterTimer) clearTimeout(state.enterTimer);
    el.classList.remove("enter");
    void el.offsetWidth;
    el.classList.add("enter");
    state.enterTimer = setTimeout(function () { el.classList.remove("enter"); }, ENTER_MS);
  }

  function main(kids) {
    var el = $("main");
    el.textContent = "";
    el.appendChild(offlineBar());
    kids.forEach(function (k) {
      if (k) el.appendChild(k);
    });
  }

  // ---------- 首頁 ----------

  function renderHome() {
    if (state.home) drawHome(state.home);
    else main([skeleton(["date", "title", "stats", "block", "line", "line", "short", "strip"])]);
    return loadHome();
  }

  function loadHome() {
    var seq = ++state.homeSeq;
    return api("/api/home").then(function (r) {
      if (seq !== state.homeSeq) return;  // 後來又抓了一次：舊的回來晚了不蓋新的
      if (r.status !== 200 || !r.data) throw new Error((r.data && r.data.error) || "首頁讀不到");
      state.home = r.data;
      state.homeSaved = r.saved || null;  // 有值＝sw.js 給的存著那份
      if (!r.saved) state.homeAt = new Date();
      fillBar();
      if (state.view === "home" || (state.view === "chat" && !wide())) drawHome(r.data);
    }).catch(function (e) {
      if (state.view === "home" && !state.home) main([emptyState("cloud-off", "首頁讀不到：" + (e && e.message ? e.message : "連不上主機"))]);
    });
  }

  /** 她正在首頁打字（改日期、新增、填答案）時不重畫，免得字不見。 */
  function busyTyping() {
    var a = document.activeElement;
    return !!(a && $("main").contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.type !== "checkbox" && a.type !== "file");
  }

  function refreshHome() {
    if (state.view === "home" && busyTyping()) return Promise.resolve();
    return loadHome();
  }

  /** 大搜尋框：左邊放放大鏡。 */
  function searchBox(input) {
    return h("div", { cls: "searchbox" }, [icon("search"), input]);
  }

  function drawHome(home) {
    var search = h("input", { cls: "input search", attrs: { type: "search", placeholder: state.offline ? "找卡：" + NEED_HOST : "找卡：打字按 Enter",
      "aria-label": "找卡", disabled: state.offline } });
    search.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" || ev.isComposing || ev.keyCode === 229) return;
      var q = search.value.trim();
      if (q) location.hash = "#/find?q=" + enc(q);
    });
    main([
      homeHint(),
      h("div", { cls: "hello" }, [h("p", { cls: "date" }, [icon("calendar"), h("span", { text: dayText(home.today) })]), h("h1", { text: greeting() })]),
      statsRow(home),
      searchBox(search),
      todaySection(home),
      confirmSection(home),
      weekSection(home),
      recentSection(home)
    ]);
  }

  /** 頂端一排數字小卡：大數字、小字、圖示；點了捲到那一區。 */
  function statsRow(home) {
    var todo = home.todo || {};
    var asks = confirmCount(home);
    var cells = [
      ["s-today", "sun", arr(todo.today).length, "今天", "sec-today"],
      ["s-late", "alarm-clock", arr(todo.overdue).length, "過期", "sec-today"],
      ["s-ask", "circle-help", asks, "要確認", "sec-confirm"],
      ["s-week", "calendar-days", arr(todo.week).length, "這週", "sec-week"]
    ];
    return h("div", { cls: "stats" }, cells.map(function (c) {
      return h("button", { cls: "stat " + c[0] + (c[2] ? "" : " zero"), attrs: { type: "button", "aria-label": c[3] + " " + c[2] + " 件" },
        on: { click: function () { goSection(c[4]); } } },
      [h("span", { cls: "stat-icon" }, [icon(c[1])]), h("span", { cls: "stat-num", text: String(c[2]) }), h("span", { cls: "stat-label", text: c[3] })]);
    }));
  }

  function goSection(id) {
    var el = $(id);
    if (!el) return;
    var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
  }

  function confirmCount(home) {
    var asks = arr(home.confirm).filter(function (c) { return c && arr(c.results).some(function (r) { return r && Array.isArray(r.argv); }); });
    var others = arr(home.others).filter(function (o) { return o && o.dismiss; });
    return asks.length + others.length + arr(home.claude).length;
  }

  function todaySection(home) {
    var todo = home.todo || {};
    var items = arr(todo.overdue).concat(arr(todo.today));
    var queued = queuedActs();
    var adds = queued.add.map(function (b) {
      return h("div", { cls: "todo queued-row" }, [h("span", { cls: "check" }), h("span", { cls: "todo-text", text: b.text || "" }),
        h("span", { cls: "queued" }, [icon("clock"), h("span", { text: QUEUED_TEXT })])]);
    });
    var list = h("div", { cls: "todo-list" }, items.length || adds.length ? items.map(function (it) { return todoRow(it, home, queued); }).concat(adds)
      : [emptyState("circle-check", "今天沒有要做的事", true)]);
    var late = arr(todo.overdue).length;
    return h("section", { cls: "section", attrs: { id: "sec-today" } }, [
      sectionHead("sun", "今天要做的", items.length + adds.length, [uploadButton()], late ? "late" : ""),
      h("div", { cls: "box" }, [list, addRow(home)])
    ]);
  }

  function whenText(it, home) {
    if (it.late) {
      var a = utc(it.date);
      var b = utc(home.today);
      var n = a !== null && b !== null ? Math.round((b - a) / 86400000) : null;
      return n && n > 0 ? "過期 " + n + " 天" : "過期";
    }
    if (it.date === home.today) return "今天" + (it.time ? " " + it.time : "");
    return shortDay(it.date) + (it.time ? " " + it.time : "");
  }

  function openItem(it) {
    var fid = it.origin && it.origin.card;
    if (!(it.card && it.card.path) && /^f-/.test(fid || "")) {
      cardsData().then(function (d) {
        var c = arr(d && d.cards).filter(function (x) { return x.id === fid; })[0];
        if (c && c.path) location.hash = pageHash(c.path);
        else toast("找不到這件的卡", null, true);
      });
      return;
    }
    var p = it.card && it.card.path ? it.card.path : AGENDA_PAGE;
    if (p === AGENDA_PAGE && state.page === AGENDA_PAGE) {
      toast("這件沒有對應的卡");
      return;
    }
    location.hash = pageHash(p);
  }

  function todoRow(it, home, queued) {
    var row = h("div", { cls: "todo" });
    var box = h("input", { attrs: { type: "checkbox", "aria-label": "打勾：" + (it.text || "") } });
    box.addEventListener("change", function () {
      box.disabled = true;
      act({ action: "done", id: it.id }, false, it);
    });
    var waiting = queued && (queued.done[it.id] || queued.edit[it.id]);
    var when = h("button", {
      cls: "when " + (it.late ? "late" : it.date === home.today ? "today" : ""),
      text: whenText(it, home),
      attrs: { type: "button", title: "改日期與時間" }
    });
    var form = null;
    var toggleForm = function () {
      if (form) {
        form.remove();
        form = null;
        return;
      }
      form = editForm(it, function () {
        form.remove();
        form = null;
      });
      row.insertBefore(form, row.querySelector(".guess"));
      form.querySelector("input").focus();
    };
    when.addEventListener("click", toggleForm);
    row.appendChild(h("label", { cls: "check" }, [box, icon("check")]));
    row.appendChild(line1("button", it.text || "", "linkish todo-text", { attrs: { type: "button" }, on: { click: function () { openItem(it); } } }));
    if (waiting) {  // 離線排著的：先照做（打勾的劃掉、改的日期先顯示），標淡字
      if (queued.done[it.id]) {
        box.checked = true;
        box.disabled = true;
        row.classList.add("queued-done");
      } else {
        var e = queued.edit[it.id];
        when.textContent = shortDay(e.date || it.date) + (e.time || it.time ? " " + (e.time || it.time) : "");
      }
      row.appendChild(h("span", { cls: "queued" }, [icon("clock"), h("span", { text: QUEUED_TEXT })]));
    }
    var who = arr(it.entities)[0];
    if (who) row.appendChild(avatar(who, "sm"));
    row.appendChild(when);
    // 次要動作：滑過才出現（手機沒有滑過，一直顯示）
    row.appendChild(h("span", { cls: "row-actions" }, [
      iconButton("calendar-clock", "改日期與時間", toggleForm),
      iconButton("external-link", "打開這件的卡", function () { openItem(it); })
    ]));
    if (it.inferred) row.appendChild(guessRow(it));
    return row;
  }

  function editForm(it, close) {
    var date = h("input", { cls: "input", attrs: { type: "date", value: it.date || "", "aria-label": "日期" } });
    var time = h("input", { cls: "input", attrs: { type: "time", value: it.time || "", "aria-label": "時間" } });
    return h("div", { cls: "form-row" }, [
      date,
      time,
      button("好", function () {
        var body = { action: "edit", id: it.id };
        if (date.value) body.date = date.value;
        if (time.value) body.time = time.value;
        close();
        act(body, false, it);
      }, "btn primary"),
      button("取消", close, "btn ghost")
    ]);
  }

  /** AI 推測的日程：對、改日期、不用了（照外掛）。 */
  function guessRow(it) {
    var row = h("div", { cls: "guess" }, [h("span", { cls: "guess-tag", attrs: { title: "AI 推測的，對嗎？" } }, [icon("sparkles"), h("span", { text: "AI 推測" })])]);
    row.appendChild(iconButton("check", "對，是這樣", function () { act({ action: "inferred_ok", id: it.id }); }, "icon-btn guess-btn"));
    row.appendChild(iconButton("calendar-clock", "改日期", function () {
      var date = h("input", { cls: "input", attrs: { type: "date", value: it.date || "", "aria-label": "新日期" } });
      var f = h("div", { cls: "form-row" }, [date, button("好", function () {
        if (date.value) act({ action: "inferred_move", id: it.id, date: date.value });
      }, "btn primary"), button("取消", function () { f.remove(); }, "btn ghost")]);
      row.appendChild(f);
      date.focus();
    }, "icon-btn guess-btn"));
    row.appendChild(iconButton("x", "不用了", function () { act({ action: "inferred_cancel", id: it.id }); }, "icon-btn guess-btn"));
    return row;
  }

  function addRow(home) {
    var wrap = h("div", { cls: "add" });
    var toggle = button("新增", function () {
      if (wrap.querySelector(".form-row")) return;
      var date = h("input", { cls: "input", attrs: { type: "date", value: home.today || "", "aria-label": "日期" } });
      var time = h("input", { cls: "input", attrs: { type: "time", "aria-label": "時間（可不填）" } });
      var text = h("input", { cls: "input grow", attrs: { type: "text", placeholder: "要做什麼", "aria-label": "要做什麼" } });
      var send = function () {
        if (!text.value.trim()) {
          text.focus();
          return;
        }
        var body = { action: "add", text: text.value.trim() };
        if (date.value) body.date = date.value;
        if (time.value) body.time = time.value;
        form.remove();
        act(body);
      };
      text.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" && !ev.isComposing && ev.keyCode !== 229) send();
      });
      var form = h("div", { cls: "form-row" }, [text, date, time, button("送出", send, "btn primary"), button("取消", function () { form.remove(); }, "btn ghost")]);
      wrap.appendChild(form);
      text.focus();
    }, "linkish add-toggle", "plus");
    wrap.appendChild(toggle);
    return wrap;
  }

  function confirmSection(home) {
    var asks = arr(home.confirm).filter(function (c) { return c && arr(c.results).some(function (r) { return r && Array.isArray(r.argv); }); });
    var others = arr(home.others).filter(function (o) { return o && o.dismiss; });
    // counts.claude 含 others；others 已經一張一張畫成「知道了」，這行只數還沒畫出來的（claude[]）
    var n = arr(home.claude).length;
    if (!asks.length && !others.length && !n) return null;
    var kids = asks.map(askCard).concat(others.map(otherCard));
    if (n > 0) {
      kids.push(h("button", { cls: "linkish more-line", attrs: { type: "button" }, on: { click: function () { askAi(CLAUDE_ASK); } } },
        [icon("sparkles"), h("span", { cls: "grow", text: "還有 " + n + " 件要跟 AI 一起看" }), icon("arrow-right")]));
    }
    return h("section", { cls: "section", attrs: { id: "sec-confirm" } }, [sectionHead("circle-help", "要你確認的", asks.length + others.length + n)].concat(kids));
  }

  /** 從原句抽「名字」、類型、一句短問題；抽不出名字回 null。 */
  function askParts(text) {
    var t = String(text || "");
    var m = /「([^」]{1,24})」(?:（([^，、）]{1,8}))?/.exec(t);
    if (!m) return null;
    var tail = t.indexOf("：") >= 0 ? t.slice(t.lastIndexOf("：") + 1) : t.slice(m.index + m[0].length);
    var q = /[^？?。！]+[？?]/.exec(tail);
    return { name: m[1], type: m[2] || "", q: (q ? q[0] : tail).trim() };
  }

  function askCard(c) {
    var parts = askParts(c.text);
    var body = h("div", { cls: "ask-body" });
    var card = h("div", { cls: "ask" + (c.urgent ? " urgent" : "") + (parts ? " named" : "") });
    if (parts) {
      card.appendChild(avatar(parts.name, "lg"));
      body.appendChild(h("div", { cls: "ask-head" }, [h("h3", { cls: "ask-name", text: parts.name }),
        parts.type ? chip(entIcon(parts.type), parts.type, "", "類型") : null, c.urgent ? chip("triangle-alert", "急", "amber") : null]));
      body.appendChild(line1("p", parts.q, "ask-q"));
      body.appendChild(h("details", { cls: "ask-more" }, [h("summary", { attrs: { title: "看原句" } }, [icon("chevron-down"), h("span", { cls: "sr", text: "看原句" })]),
        h("p", { text: c.text || "" })]));
    } else {
      card.appendChild(h("span", { cls: "ask-icon" }, [icon(c.urgent ? "triangle-alert" : "circle-help")]));
      body.appendChild(line1("p", c.text || "", "ask-q", { on: { click: function (ev) { ev.currentTarget.classList.toggle("clamp"); } } }));
    }
    card.appendChild(body);
    var answers = h("div", { cls: "answers" });
    var slot = h("div");
    if (state.offline) body.appendChild(chip("cloud-off", NEED_HOST, "", null));
    arr(c.results).forEach(function (r, idx) {
      if (!r || !Array.isArray(r.argv)) return;
      var b = answers.appendChild(button(r.label || "好", function () {
        if (!r.fill) {
          act({ action: "answer", key: c.key, result: idx, label: r.label });
          return;
        }
        slot.textContent = "";
        slot.appendChild(fillForm(r.fill, function (value) { act({ action: "answer", key: c.key, result: idx, label: r.label, value: value }); }));
      }));
      b.disabled = state.offline;  // 「要你確認的」不排隊：離線時按鈕停用
    });
    body.appendChild(answers);
    body.appendChild(slot);
    return card;
  }

  function fillForm(fill, done) {
    var input;
    if (fill.input === "select" && Array.isArray(fill.options)) {
      input = h("select", { cls: "input grow", attrs: { "aria-label": fill.name || "選一個" } },
        fill.options.map(function (o) { return h("option", { text: o.label || o.value, attrs: { value: o.value } }); }));
    } else {
      input = h("input", { cls: "input grow", attrs: { type: fill.input === "date" ? "date" : "text", "aria-label": fill.name || "請填", placeholder: fill.name || "" } });
    }
    var ok = function () {
      var v = String(input.value || "").trim();
      if (!v) {
        input.focus();
        return;
      }
      done(v);
    };
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.isComposing && ev.keyCode !== 229) ok();
    });
    setTimeout(function () { input.focus(); }, 0);
    return h("div", { cls: "form-row" }, [input, button("好", ok, "btn primary")]);
  }

  function otherCard(o) {
    return h("div", { cls: "ask" }, [h("span", { cls: "ask-icon" }, [icon("info")]), h("div", { cls: "ask-body" }, [line1("p", o.text || "", "ask-q", {
      on: { click: function (ev) { ev.currentTarget.classList.toggle("clamp"); } } }),
      h("div", { cls: "answers" }, [button("知道了", function () { act({ action: "dismiss", id: o.id }); }, "btn", "check")])])]);
  }

  /** 這週：今天起七天一排格子（星期、日期大字、有事的點），今天主色；點一天只看那天的，再點一次看整週。 */
  function weekSection(home) {
    var todo = home.todo || {};
    var items = arr(todo.week);
    var all = arr(todo.today).concat(items);
    if (utc(home.today) === null) return null;
    var days = [0, 1, 2, 3, 4, 5, 6].map(function (i) {
      var day = addDays(home.today, i);
      return { day: day, items: all.filter(function (it) { return it.date === day; }) };
    });
    if (!days.some(function (d) { return d.day === state.weekDay; })) state.weekDay = null;
    var strip = h("div", { cls: "week-strip", attrs: { role: "group", "aria-label": "這週七天" } });
    var list = h("div", { cls: "box week-list" });
    var draw = function () {
      strip.textContent = "";
      days.forEach(function (d, i) {
        var t = new Date(utc(d.day));
        var n = d.items.length;
        var dots = h("span", { cls: "wd-dots" }, n > 3 ? [h("span", { cls: "wd-more", text: String(n) })]
          : d.items.map(function () { return h("i"); }));
        strip.appendChild(h("button", {
          cls: "wd" + (i === 0 ? " today" : "") + (n ? " has" : "") + (state.weekDay === d.day ? " on" : ""),
          attrs: { type: "button", "aria-pressed": state.weekDay === d.day ? "true" : "false", "aria-label": shortDay(d.day) + " " + n + " 件" },
          on: { click: function () {
            state.weekDay = state.weekDay === d.day ? null : d.day;
            draw();
          } }
        }, [h("span", { cls: "wd-name", text: i === 0 ? "今天" : WEEKDAY.charAt(t.getUTCDay()) }), h("span", { cls: "wd-num", text: String(t.getUTCDate()) }), dots]));
      });
      var pick = state.weekDay ? days.filter(function (d) { return d.day === state.weekDay; })[0].items : items;
      list.textContent = "";
      if (!pick.length) list.appendChild(emptyState("calendar-days", state.weekDay ? "這天沒排事" : "這週沒排事", true));
      pick.forEach(function (it) {
        var who = arr(it.entities)[0];
        list.appendChild(h("div", { cls: "week-row" }, [
          h("span", { cls: "when", text: shortDay(it.date) + (it.time ? " " + it.time : "") }),
          line1("button", it.text || "", "linkish", { attrs: { type: "button" }, on: { click: function () { openItem(it); } } }),
          who ? avatar(who, "sm") : null
        ]));
      });
    };
    draw();
    return h("section", { cls: "section", attrs: { id: "sec-week" } }, [sectionHead("calendar-days", "這週", items.length), strip, list]);
  }

  function recentSection(home) {
    var items = arr(home.recent).slice(0, 6);
    if (!items.length) return null;
    return h("section", { cls: "section" }, [sectionHead("clock", "最近"), h("div", { cls: "cards" }, items.map(function (f) {
      return h("button", { cls: "mini", attrs: { type: "button", title: f.title || "" }, on: { click: function () { if (f.card_path) location.hash = pageHash(f.card_path); } } }, [
        fileIcon(f),
        line1("span", f.title || f.card_path || "", "mini-title"),
        h("span", { cls: "mini-meta" }, [f.type ? h("span", { cls: "tag", text: f.type }) : null, realDate(f.date) ? h("span", { text: shortDay(f.date) }) : null])
      ]);
    }))]);
  }

  /** 首頁的動作：做完提示一行（有 undo 才有「復原」）、重抓首頁。復原本身不再給復原。
   *  主機沒開（或送的時候連不上）：打勾、改日期、新增排進隊伍（it 是那件，打勾與改日期帶 expect），其他的說要主機。 */
  function act(body, isUndo, it) {
    if (state.offline) return queueAct(body, it);
    return api("/api/act", { method: "POST", body: body }).then(function (r) {
      // 沒到主機（Cloudflare 回的連不上、登入頁或要重新登入）才排；500、524 可能已經做了，不排免得做兩次
      if (window.WillyQueue.unreachable(r.status) || (r.data === null && (r.status < 400 || r.status === 403))) {
        throw new Error("offline");
      }
      var d = r.data || {};
      var msg = d.message || d.error || (r.status === 200 ? "好了" : "沒做成");
      toast(msg, !isUndo && d.ok && d.undo ? function () { act(d.undo, true); } : null);
      return loadHome().then(redrawSpecial);
    }).catch(function () {
      state.offline = true;
      showConn(false);
      checkStatus();
      return queueAct(body, it);
    });
  }

  // ---------- 傳照片或檔案 ----------

  function uploadButton() {
    var input = h("input", { attrs: { type: "file", multiple: true, accept: UPLOAD_ACCEPT, "aria-label": "傳照片或檔案" } });
    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files || []);
      input.value = "";
      if (files.length) uploadFiles(files);
    });
    return h("label", { cls: "icon-btn upload", attrs: { title: "傳照片或檔案給 AI 整理" } }, [icon("upload"), input]);
  }

  /** 離線時檔案本身存進 IndexedDB（ArrayBuffer，iPhone 舊版存 Blob 不穩），主機開了照 /api/upload 送。 */
  function queueFiles(files) {
    var chain = Promise.resolve();
    files.forEach(function (f) {
      chain = chain.then(function () {
        if (f.size > UPLOAD_MAX) {
          toast(f.name + "：檔太大了（50 MB 以內）");
          return null;
        }
        return f.arrayBuffer().then(function (data) {
          return enqueue("upload", null, "傳檔：" + f.name, { name: f.name, type: f.type, data: data });
        });
      });
    });
    return chain.then(function () { toast("主機沒開，檔先存在這台，" + QUEUED_TEXT); });
  }

  function uploadFiles(files) {
    if (state.offline) return queueFiles(files);
    var ok = 0;
    var last = "";
    var chain = Promise.resolve();
    files.forEach(function (f, i) {
      chain = chain.then(function () {
        if (f.size > UPLOAD_MAX) {
          last = f.name + "：檔太大了（50 MB 以內）";
          return null;
        }
        toast("正在傳 " + (i + 1) + "/" + files.length + "：" + f.name);
        return api("/api/upload?name=" + enc(f.name), { method: "POST", body: f, raw: true }).then(function (r) {
          var d = r.data || {};
          if (r.status === 200 && d.ok) ok++;
          else last = f.name + "：" + (d.message || d.error || (r.status === 413 ? "檔太大了（50 MB 以內）" : "沒放進去"));
        }).catch(function () {
          last = f.name + "：連不上主機";
        });
      });
    });
    chain.then(function () {
      toast(ok ? "已放進暫存區，AI 會接著整理" + (last ? "；" + last : "") : last || "沒放進去");
    });
  }

  // ---------- 找卡 ----------

  function quickRow() {
    return h("div", { cls: "quick" }, menuItems("menu-item").filter(function (a) { return a.dataset.key !== "home"; })
      .concat([h("span", { cls: "quick-conn muted small", text: state.connText || "" })]));
  }

  /** 找卡的一列：類型圖示、標題、所在的夾（淡字）、一段摘要（類型、日期、相關的人與案子）。 */
  function resultRow(iconEl, title, where, snip, path) {
    var kids = [line1("span", title, "result-title")];
    if (where) kids.push(h("span", { cls: "result-where" }, [icon("folder"), line1("span", where)]));
    if (snip) kids.push(h("span", { cls: "result-snip" }, snip));
    return h("button", { cls: "result", attrs: { type: "button", disabled: !path }, on: { click: function () { if (path) location.hash = pageHash(path); } } },
      [iconEl, h("span", { cls: "result-main" }, kids)]);
  }

  function renderFind(q) {
    var input = h("input", { cls: "input search", attrs: { type: "search", placeholder: "找卡：打字按 Enter", value: q || "", "aria-label": "找卡" } });
    input.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" || ev.isComposing || ev.keyCode === 229) return;
      var v = input.value.trim();
      location.hash = v ? "#/find?q=" + enc(v) : "#/find";
    });
    var out = h("div", { cls: "results" }, [skeleton(["block", "block", "block", "block"])]);
    main([quickRow(), h("h1", { cls: "find-title", text: "找卡" }), searchBox(input), out]);
    if (state.offline) {
      input.disabled = true;
      out.textContent = "";
      out.appendChild(emptyState("cloud-off", "主機沒開：找卡" + NEED_HOST + "；最近開過的卡在首頁的「最近」點得開"));
      return null;
    }
    if (!q) return fillIndex(out, "", function () { return state.view === "find"; });
    return api("/api/find?q=" + enc(q)).then(function (r) {
      if (state.view !== "find") return;
      out.textContent = "";
      var d = r.data || {};
      if (r.status !== 200) {
        out.appendChild(emptyState("circle-alert", d.error || "沒找成"));
        return;
      }
      var ents = arr(d.entities);
      var files = arr(d.files);
      if (ents.length) {
        out.appendChild(h("p", { cls: "result-head" }, [h("span", { text: "人、案子" }), h("span", { text: String(ents.length) })]));
        ents.forEach(function (e) {
          out.appendChild(resultRow(avatar(e.name, "md"), e.name || "", "", e.type ? [chip(entIcon(e.type), e.type)] : null, e.card_path));
        });
      }
      if (files.length) {
        out.appendChild(h("p", { cls: "result-head" }, [h("span", { text: "檔案" }), h("span", { text: String(d.more && d.total ? d.total : files.length) })]));
        files.forEach(function (f) {
          var snip = [f.type ? h("span", { cls: "tag", text: f.type }) : null, realDate(f.date) ? chip("calendar", shortDay(f.date)) : null]
            .concat(arr(f.entities).slice(0, 4).map(function (n) { return avatar(n, "sm"); }));
          out.appendChild(resultRow(fileIcon(f), f.title || f.card_path || "", folderText(f.card_path), snip, f.card_path));
        });
      }
      if (!ents.length && !files.length) out.appendChild(emptyState("search-x", "沒找到，換幾個字試試"));
      if (d.more) out.appendChild(h("p", { cls: "result-more", text: "還有 " + (d.total - files.length) + " 份，多打幾個字" }));
    }).catch(function () {
      out.textContent = "";
      out.appendChild(emptyState("cloud-off", "連不上主機"));
    });
  }

  // ---------- 看卡 ----------

  function getPage(path) {
    var c = state.pageCache;
    if (c && c.path === path) {
      state.pageCache = null;
      return Promise.resolve(c.r);
    }
    return api("/api/page?path=" + enc(path));
  }

  /** 麵包屑：卡所在的夾，一層一層；檔案卡（檔案索引底下）的夾點了到總目錄那一層，其他的夾不是頁、不能點。 */
  function crumbs(path) {
    var parts = dirOf(path).split("/").filter(Boolean);
    var el = h("nav", { cls: "crumbs", attrs: { "aria-label": "所在的夾" } });
    var cards = parts[0] === CARDS_DIR;
    if (cards) parts[0] = "總目錄";
    if (!parts.length) parts = ["知識庫"];
    parts.forEach(function (p, i) {
      if (i) el.appendChild(icon("chevron-right"));
      el.appendChild(cards ? h("a", { text: p, attrs: { href: i ? "#/index/" + enc(parts.slice(1, i + 1).join("/")) : "#/index" } }) : h("span", { text: p }));
    });
    return el;
  }

  /** 檔頭裡給人看的幾欄做成一排小標籤（滑過看是哪一欄）；人與案子帶頭像。其他欄（編號、指紋…）不顯示。 */
  function propsBlock(front) {
    var tags = [];
    var seen = {};
    PROP_KEYS.forEach(function (k) {
      var raw = String((front && front[k[0]]) || "").trim();
      if (!raw || seen[k[1]]) return;
      var vals = raw.replace(/^\[|\]$/g, "").split(/\s*,\s*/).filter(Boolean);
      if (!vals.length) return;
      seen[k[1]] = true;
      vals.forEach(function (v) {
        if (k[0] === "文件日期" && !realDate(v)) return;  // 「未載」之類不放
        if (k[0] === "實體") tags.push(h("span", { cls: "tag ent", attrs: { title: k[1] } }, [avatar(v, "xs"), h("span", { text: v })]));
        else tags.push(chip(k[2], realDate(v) ? shortDay(v) : v, k[0] === "卡型" || k[0] === "型別" ? "kind" : "", k[1]));
      });
    });
    return tags.length ? h("div", { cls: "props" }, tags) : null;
  }

  /** 長文件（三個以上的二、三級標題）上方放目錄：點了跳過去。 */
  function tocBlock(block) {
    var heads = Array.prototype.slice.call(block.querySelectorAll("h2, h3"));
    if (heads.length < 3) return null;
    return h("nav", { cls: "toc", attrs: { "aria-label": "目錄" } }, [h("span", { cls: "toc-head", attrs: { title: "目錄" } }, [icon("list")])].concat(heads.map(function (el) {
      return line1("button", el.textContent, "toc-item" + (el.tagName === "H3" ? " sub" : ""), { attrs: { type: "button" }, on: { click: function () {
        var calm = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        el.scrollIntoView({ behavior: calm ? "auto" : "smooth", block: "start" });
      } } });
    })));
  }

  function renderPage(path) {
    setPage(path);
    var title = h("h1", { text: path.split("/").pop().replace(/\.md$/i, "") });
    var head = h("div", { cls: "page-head" }, [title]);
    var actions = h("div", { cls: "page-actions" });
    var back = iconButton("arrow-left", "返回", function () {
      if (history.length > 1) history.back();
      else location.hash = "#/";
    });
    var ask = iconButton("sparkles", "問 AI", function () {
      if (wide()) {
        if (!chatCall("prefill", "")) toast("對話還沒裝好");
      } else {
        location.hash = "#/chat";
      }
    }, "btn icon-only");
    actions.appendChild(ask);
    var body = h("div", { cls: "page-body" }, [skeleton(["line", "line", "short", "line", "line", "short"])]);
    main([h("div", { cls: "page-bar" }, [back, crumbs(path), actions]), head, body]);
    return getPage(path).then(function (r) {
      if (state.view !== "page" || state.page !== path) return;
      body.textContent = "";
      if (r.status !== 200 || !r.data) {
        var off = state.offline || (r.data && r.data.offline);
        body.appendChild(emptyState(off ? "cloud-off" : "file", off ? "主機沒開，這頁沒存著（只看得到最近開過的卡）" : "找不到這頁，或這頁不給看"));
        return;
      }
      title.textContent = r.data.title || title.textContent;
      if (r.data.path && r.data.path !== path) state.page = r.data.path;
      var block = mdBlock(r.data, r.data.path || path);
      dropTitle(block, title.textContent);
      var props = propsBlock(block.front);
      if (props) head.appendChild(props);
      var open = openButton(block.front);
      if (open && !/^f-/.test(block.fileId || "")) actions.insertBefore(open, ask);
      if (/^f-/.test(block.fileId || "")) {
        fileHero(head, title, block.front, r.data.markdown, block.fileId);
      } else if (/^e-/i.test(String(block.front["編號"] || ""))) {
        head.classList.add("hero");
        head.insertBefore(h("div", { cls: "hero-row" }, [avatar(title.textContent, "xl"), title]), head.firstChild);
        sectionize(block);
      } else if (/^系統\/健檢報告\//.test(state.page || "")) {
        // 健檢報告（D-362）：照範本的固定段標題拆成卡片；標題對不上就照舊畫 md，只把每段收成一塊
        if (healthReport(head, title, block)) {
          body.appendChild(block);
          return;
        }
        sectionize(block);
      }
      var panel = panelBlock(r.data.panel);
      if (panel) body.appendChild(panel);
      var toc = tocBlock(block);
      if (toc) body.appendChild(toc);
      body.appendChild(block);
    }).catch(function () {
      body.textContent = "";
      body.appendChild(emptyState("cloud-off", "連不上主機"));
    });
  }

  /** 卡裡第一個 # 標題常跟頁標題一樣（前面可能還有一句說明）：頁首已經有了，不再重複一次。 */
  function dropTitle(block, text) {
    var first = block.querySelector("h1");
    if (first && first.tagName === "H1" && first.textContent.trim() === String(text || "").trim()) first.remove();
  }

  // ---------- 選單各頁的專屬畫面（D-361） ----------

  /** 日程、待處理、總目錄不畫 md，畫專屬畫面；健檢報告與其他頁照舊畫 md。回 undefined＝不是這幾頁。 */
  function specialView(r) {
    if (r.path === AGENDA_PAGE) {
      setPage(r.path);
      return renderAgenda() || true;
    }
    if (r.path === PENDING_PAGE) {
      setPage(r.path);
      return renderPending() || true;
    }
    if (r.path === INDEX_PAGE) {
      setPage(r.path);
      return renderIndex(r.dir || "") || true;
    }
    return undefined;
  }

  function redrawSpecial() {
    if (state.view !== "page") return;
    if (state.page === AGENDA_PAGE) renderAgenda(true);
    else if (state.page === PENDING_PAGE) renderPending(true);
  }

  /** 專屬畫面的頁首：彩色方塊圖示＋大標題，右邊放切換之類的。 */
  function viewHead(iconName, title, tone, extra) {
    return h("div", { cls: "view-head" }, [h("span", { cls: "view-icon " + tone }, [icon(iconName)]), h("h1", { text: title }),
      h("span", { cls: "spacer" })].concat(extra || []));
  }

  /** 兩三個選項的切換鈕（清單／月曆）。 */
  function segmented(items, on, pick) {
    return h("div", { cls: "seg", attrs: { role: "group" } }, items.map(function (it) {
      return h("button", { cls: "seg-btn" + (it[0] === on ? " on" : ""), attrs: { type: "button", "aria-pressed": it[0] === on ? "true" : "false" },
        on: { click: function () { if (it[0] !== on) pick(it[0]); } } }, [icon(it[1]), h("span", { text: it[2] })]);
    }));
  }

  function offlineEmpty(body, what) {
    body.textContent = "";
    body.appendChild(emptyState("cloud-off", "主機沒開，" + what + NEED_HOST));
  }

  /** 首頁的資料（拿今天的日期、要你確認的）：剛抓過就用那份。 */
  function homeData(fresh) {
    if (state.home && !fresh) return Promise.resolve(state.home);
    return api("/api/home").then(function (r) {
      if (r.status === 200 && r.data) state.home = r.data;
      return state.home;
    });
  }

  /** 全部的檔案卡（kb cards list）：一分鐘內用同一份。 */
  function cardsData(fresh) {
    if (!state.cardsP || fresh || Date.now() - state.cardsAt > CARDS_TTL) {
      state.cardsAt = Date.now();
      state.cardsP = api("/api/cards").then(function (r) {
        if (r.status !== 200 || !r.data) throw new Error((r.data && r.data.error) || "卡讀不到");
        return r.data;
      });
      state.cardsP.catch(function () { state.cardsP = null; });
    }
    return state.cardsP;
  }

  // ----- 日程：清單與月曆 -----

  /** kb agenda list 的一件 → 跟首頁一樣的欄位（late、inferred）。 */
  function agendaItem(it, today) {
    var open = (it.status || "open") === "open";
    return Object.assign({}, it, { late: open && !!it.date && it.date < today, inferred: open && it.source === "inferred" });
  }

  function renderAgenda(quiet) {
    var mode = lsGet(AGENDA_KEY) === "month" ? "month" : "list";
    var body = h("div", { cls: "view-body" }, [skeleton(["block", "line", "line", "line", "short", "line"])]);
    var seg = segmented([["list", "list", "清單"], ["month", "calendar-days", "月曆"]], mode, function (m) {
      lsSet(AGENDA_KEY, m);
      renderAgenda();
    });
    if (!quiet || !$("main").querySelector(".agenda-view")) main([h("div", { cls: "agenda-view" }, [viewHead("calendar", "日程", "blue", [seg]), body])]);
    else body = $("main").querySelector(".agenda-view .view-body");
    if (state.offline) return offlineEmpty(body, "日程");
    return Promise.all([api("/api/agenda"), homeData()]).then(function (rs) {
      if (state.view !== "page" || state.page !== AGENDA_PAGE) return;
      var a = rs[0];
      var home = rs[1];
      if (a.status !== 200 || !a.data || !home) throw new Error((a.data && a.data.error) || "日程讀不到");
      var today = home.today;
      var items = arr(a.data.items).map(function (it) { return agendaItem(it, today); });
      body.textContent = "";
      if (mode === "month") drawMonth(body, items, today);
      else drawAgendaList(body, items, today);
    }).catch(function (e) {
      body.textContent = "";
      body.appendChild(emptyState("cloud-off", e && e.message ? e.message : "連不上主機"));
    });
  }

  function byDate(a, b) {
    return String(a.date || "9").localeCompare(String(b.date || "9")) || String(a.time || "").localeCompare(String(b.time || ""));
  }

  function drawAgendaList(body, items, today) {
    var open = items.filter(function (it) { return (it.status || "open") === "open"; }).sort(byDate);
    var weekEnd = addDays(today, 6);
    var groups = [
      ["alarm-clock", "過期", "late", open.filter(function (it) { return it.late; })],
      ["sun", "今天", "", open.filter(function (it) { return it.date === today; })],
      ["calendar-days", "這週", "", open.filter(function (it) { return it.date > today && it.date <= weekEnd; })],
      ["calendar", "之後", "", open.filter(function (it) { return it.date > weekEnd; })],
      ["circle-help", "沒日期", "", open.filter(function (it) { return !it.date; })]
    ];
    var queued = queuedActs();
    var ctx = { today: today };
    body.appendChild(h("div", { cls: "box add-box" }, [addRow(ctx)]));
    var any = false;
    groups.forEach(function (g) {
      if (!g[3].length) return;
      any = true;
      body.appendChild(h("section", { cls: "section" }, [sectionHead(g[0], g[1], g[3].length, null, g[2]),
        h("div", { cls: "box" }, [h("div", { cls: "todo-list" }, g[3].map(function (it) { return todoRow(it, ctx, queued); }))])]));
    });
    if (!any) body.appendChild(emptyState("circle-check", "沒有要做的事", true));
    var done = items.filter(function (it) { return (it.status || "open") !== "open"; }).sort(byDate).reverse();
    if (done.length) {
      body.appendChild(h("details", { cls: "section fold" }, [h("summary", {}, [icon("circle-check"), h("span", { text: "做完的、取消的" }),
        h("span", { cls: "count", text: String(done.length) }), icon("chevron-down")]), h("div", { cls: "box" }, done.map(doneRow))]));
    }
  }

  function doneRow(it) {
    var cancelled = it.status === "cancelled";
    return h("div", { cls: "todo done-row" }, [h("span", { cls: "done-mark" + (cancelled ? " x" : "") }, [icon(cancelled ? "x" : "check")]),
      line1("span", it.text || "", "todo-text"), it.date ? h("span", { cls: "when", text: shortDay(it.date) }) : null,
      iconButton("undo-2", "打開回來", function () { act({ action: "reopen", id: it.id }); })]);
  }

  function drawMonth(body, items, today) {
    var open = items.filter(function (it) { return (it.status || "open") === "open" && it.date; });
    var ym = state.agendaMonth || today.slice(0, 7);
    var y = +ym.slice(0, 4);
    var m = +ym.slice(5, 7);
    var days = new Date(Date.UTC(y, m, 0)).getUTCDate();
    var lead = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
    var pick = state.agendaDay && state.agendaDay.slice(0, 7) === ym ? state.agendaDay : today.slice(0, 7) === ym ? today : null;
    var go = function (n) {
      var d = new Date(Date.UTC(y, m - 1 + n, 1));
      state.agendaMonth = d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1);
      state.agendaDay = null;
      renderAgenda(true);
    };
    var nav = h("div", { cls: "cal-nav" }, [iconButton("chevron-left", "上個月", function () { go(-1); }),
      h("h2", { text: y + " 年 " + m + " 月" }), iconButton("chevron-right", "下個月", function () { go(1); }), h("span", { cls: "spacer" }),
      button("今天", function () {
        state.agendaMonth = null;
        state.agendaDay = null;
        renderAgenda(true);
      }, "btn")]);
    var grid = h("div", { cls: "cal-grid", attrs: { role: "grid" } }, Array.from(WEEKDAY).map(function (w) { return h("span", { cls: "cal-wd", text: w }); }));
    for (var i = 0; i < lead; i++) grid.appendChild(h("span", { cls: "cal-pad" }));
    for (var d = 1; d <= days; d++) {
      (function (day) {
        var list = open.filter(function (it) { return it.date === day; });
        var late = list.some(function (it) { return it.late; });
        var marks = list.length > 3 ? [h("span", { cls: "cal-more", text: String(list.length) })]
          : list.map(function (it) { return h("i", { cls: it.late ? "late" : "" }); });
        grid.appendChild(h("button", {
          cls: "cal-day" + (day === today ? " today" : "") + (day === pick ? " on" : "") + (list.length ? " has" : "") + (late ? " late" : ""),
          attrs: { type: "button", "aria-pressed": day === pick ? "true" : "false", "aria-label": shortDay(day) + " " + list.length + " 件" },
          on: { click: function () {
            state.agendaMonth = ym;
            state.agendaDay = day;
            renderAgenda(true);
          } }
        }, [h("span", { cls: "cal-num", text: String(+day.slice(8)) }), h("span", { cls: "cal-marks" }, marks)]));
      })(ym + "-" + pad(d));
    }
    body.appendChild(h("div", { cls: "cal box" }, [nav, grid]));
    if (!pick) return;
    var dayItems = open.filter(function (it) { return it.date === pick; }).sort(byDate);
    var ctx = { today: today };
    var queued = queuedActs();
    body.appendChild(h("section", { cls: "section" }, [sectionHead(pick === today ? "sun" : "calendar", dayText(pick), dayItems.length),
      dayItems.length ? h("div", { cls: "box" }, [h("div", { cls: "todo-list" }, dayItems.map(function (it) { return todoRow(it, ctx, queued); }))])
        : emptyState("calendar-days", "這天沒排事", true)]));
  }

  // ----- 待處理：跟首頁「要你確認的」同一種卡 -----

  function renderPending(quiet) {
    var body = h("div", { cls: "view-body" }, [skeleton(["block", "block", "block"])]);
    if (!quiet || !$("main").querySelector(".pending-view")) main([h("div", { cls: "pending-view" }, [viewHead("inbox", "待處理", "amber"), body])]);
    else body = $("main").querySelector(".pending-view .view-body");
    if (state.offline) return offlineEmpty(body, "待處理");
    return Promise.all([api("/api/pending"), homeData(true)]).then(function (rs) {
      if (state.view !== "page" || state.page !== PENDING_PAGE) return;
      var r = rs[0];
      var home = rs[1] || {};
      if (r.status !== 200 || !r.data) throw new Error((r.data && r.data.error) || "待處理讀不到");
      var items = arr(r.data.items);
      var asks = arr(home.confirm).filter(function (c) { return c && arr(c.results).some(function (x) { return x && Array.isArray(x.argv); }); });
      var canDismiss = {};
      arr(home.others).forEach(function (o) { if (o && o.dismiss) canDismiss[o.id] = true; });
      body.textContent = "";
      var front = items.filter(function (it) { return !it.set_aside; });
      var aside = items.filter(function (it) { return it.set_aside; });
      body.appendChild(h("div", { cls: "stats three" }, [
        miniStat("s-ask", "circle-help", asks.length, "要問你的"), miniStat("s-late", "inbox", front.length, "要處理的"),
        miniStat("s-zero", "clock", aside.length, "先放著的")]));
      if (asks.length) body.appendChild(h("section", { cls: "section" }, [sectionHead("sparkles", "整理時要問你的", asks.length)].concat(asks.map(askCard))));
      PENDING_ORDER.forEach(function (kind) {
        var list = front.filter(function (it) { return it.kind === kind; });
        if (!list.length) return;
        var k = PENDING_KINDS[kind];
        body.appendChild(h("section", { cls: "section" }, [sectionHead(k[0], k[1], list.length)].concat(list.map(function (it) {
          return pendingCard(it, canDismiss[it.id]);
        }))));
      });
      var rest = front.filter(function (it) { return !PENDING_KINDS[it.kind]; });
      if (rest.length) body.appendChild(h("section", { cls: "section" }, [sectionHead("info", "其他", rest.length)].concat(rest.map(function (it) {
        return pendingCard(it, canDismiss[it.id]);
      }))));
      if (!asks.length && !front.length) body.appendChild(emptyState("circle-check", "沒有要你決定的事", true));
      if (aside.length) {
        body.appendChild(h("details", { cls: "section fold" }, [h("summary", {}, [icon("clock"), h("span", { text: "先放著的" }),
          h("span", { cls: "count", text: String(aside.length) }), icon("chevron-down")])].concat(aside.map(function (it) { return pendingCard(it, false); }))));
      }
    }).catch(function (e) {
      body.textContent = "";
      body.appendChild(emptyState("cloud-off", e && e.message ? e.message : "連不上主機"));
    });
  }

  /** 不能點的數字小卡（待處理頁頂端）。 */
  function miniStat(cls, iconName, n, label) {
    return h("div", { cls: "stat static " + cls + (n ? "" : " zero") }, [h("span", { cls: "stat-icon" }, [icon(iconName)]),
      h("span", { cls: "stat-num", text: String(n) }), h("span", { cls: "stat-label", text: label })]);
  }

  /** 待處理一件：「」裡的名字當大字、種類小標籤、一句；能按的走現有的路（知道了＝dismiss），其他的「問 AI」帶好那句。 */
  function pendingCard(it, canDismiss) {
    var k = PENDING_KINDS[it.kind] || ["info", "其他", "gray"];
    var text = String(it.text || "") + (it.kind === "unreadable" && it.reason ? "：" + it.reason : "");
    // 名字當大字只用在講一份檔或一件事的（原檔不見、讀不到、要確認結案）；健檢、手改這類整句才看得懂
    var m = /^(missing_file|unreadable|close)$/.test(it.kind || "") ? /「([^」]{1,40})」(?:（[^）]{1,20}）)?/.exec(text) : null;
    var rest = m ? text.slice(m.index + m[0].length).replace(/^[：:，、\s]+/, "") : "";
    var body = h("div", { cls: "ask-body" });
    var card = h("div", { cls: "ask pend" + (m ? " named" : "") }, [h("span", { cls: "ask-icon kt-" + k[2] }, [icon(k[0])]), body]);
    var when = it.created ? chip("calendar", shortDay(String(it.created).slice(0, 10)), "", "記下的日子") : null;
    if (m) {
      body.appendChild(h("div", { cls: "ask-head" }, [h("h3", { cls: "ask-name clamp", text: m[1], attrs: { title: m[1] } }), chip("", k[1], "kt-" + k[2]), when]));
      body.appendChild(line1("p", rest || text, "ask-q"));
    } else {
      body.appendChild(h("div", { cls: "ask-head" }, [chip("", k[1], "kt-" + k[2]), when]));
      body.appendChild(line1("p", text, "ask-q", { on: { click: function (ev) { ev.currentTarget.classList.toggle("clamp"); } } }));
    }
    if (m && rest) {
      body.appendChild(h("details", { cls: "ask-more" }, [h("summary", { attrs: { title: "看原句" } }, [icon("chevron-down"), h("span", { cls: "sr", text: "看原句" })]),
        h("p", { text: text })]));
    }
    var btns = [];
    if (it.kind === "report") btns.push(button("看報告", openReport, "btn primary", "heart-pulse"));
    if (canDismiss) btns.push(button("知道了", function () { act({ action: "dismiss", id: it.id }); }, "btn", "check"));
    btns.push(button("問 AI", function () { askAi("待處理這件要怎麼處理：" + text); }, "btn" + (btns.length ? "" : " primary-soft"), "sparkles"));
    btns.forEach(function (b) { if (state.offline) b.disabled = true; });
    body.appendChild(h("div", { cls: "answers" }, btns));
    return card;
  }

  // ----- 總目錄：資料夾格子，點進去往下一層 -----

  function renderIndex(dir) {
    var bar = h("nav", { cls: "crumbs index-crumbs", attrs: { "aria-label": "所在的夾" } });
    var parts = dir ? dir.split("/") : [];
    bar.appendChild(h("a", { text: "總目錄", attrs: { href: "#/index" } }));
    parts.forEach(function (p, i) {
      bar.appendChild(icon("chevron-right"));
      bar.appendChild(i === parts.length - 1 ? h("span", { text: p }) : h("a", { text: p, attrs: { href: "#/index/" + enc(parts.slice(0, i + 1).join("/")) } }));
    });
    var out = h("div", { cls: "view-body" }, [skeleton(["block", "block", "line", "line"])]);
    main([viewHead(dir ? "folder" : "book-open", dir ? parts[parts.length - 1] : "總目錄", "green"), parts.length ? bar : null, out]);
    if (state.offline) return offlineEmpty(out, "總目錄");
    return fillIndex(out, dir, function () { return state.view === "page" && state.page === INDEX_PAGE; });
  }

  /** 在 out 裡畫 dir 這一層：先資料夾格子（圖示、名字、幾張卡），再這層的卡。still()：畫面還在才畫。 */
  function fillIndex(out, dir, still) {
    return cardsData().then(function (d) {
      if (!still()) return;
      out.textContent = "";
      var prefix = CARDS_DIR + "/" + (dir ? dir + "/" : "");
      var cards = arr(d.cards).filter(function (c) { return c && typeof c.path === "string" && c.path.indexOf(prefix) === 0 && !c.archived; });
      var folders = {};
      var here = [];
      cards.forEach(function (c) {
        var rest = c.path.slice(prefix.length);
        var cut = rest.indexOf("/");
        if (cut < 0) here.push(c);
        else folders[rest.slice(0, cut)] = (folders[rest.slice(0, cut)] || 0) + 1;
      });
      var names = Object.keys(folders).sort(function (a, b) { return a.localeCompare(b, "zh-Hant"); });
      if (names.length) {
        out.appendChild(h("div", { cls: "folders" }, names.map(function (n) {
          var to = dir ? dir + "/" + n : n;
          return h("a", { cls: "folder", attrs: { href: "#/index/" + enc(to), title: n } }, [h("span", { cls: "folder-icon " + avatarTone(n) }, [icon("folder")]),
            line1("span", n, "folder-name"), h("span", { cls: "folder-count", text: folders[n] + " 張" })]);
        })));
      }
      if (here.length) {
        here.sort(function (a, b) { return String(b.date || "").localeCompare(String(a.date || "")) || String(a.title).localeCompare(String(b.title), "zh-Hant"); });
        out.appendChild(h("p", { cls: "result-head" }, [h("span", { text: "卡" }), h("span", { text: String(here.length) })]));
        here.forEach(function (c) {
          var snip = [c.card_type ? h("span", { cls: "tag", text: c.card_type }) : null, realDate(c.date) ? chip("calendar", shortDay(c.date)) : null]
            .concat(arr(c.entities).slice(0, 4).map(function (n) { return avatar(n, "sm"); }));
          out.appendChild(resultRow(fileIcon(c), c.title || c.path, "", snip, c.path));
        });
      }
      if (!names.length && !here.length) out.appendChild(emptyState("folder", dir ? "這個夾是空的" : "還沒有卡"));
    }).catch(function (e) {
      if (!still()) return;
      out.textContent = "";
      out.appendChild(emptyState("cloud-off", e && e.message ? e.message : "連不上主機"));
    });
  }

  /** 名字固定對到八色裡的一色（頭像與資料夾圖示共用）。 */
  function avatarTone(name) {
    var n = 0;
    Array.from(String(name || "")).forEach(function (ch) { n = (n * 31 + ch.codePointAt(0)) % 9973; });
    return "av" + (n % 8);
  }

  // ----- 卡片頁的頂端 -----

  /** 檔案卡的原檔名：開原檔是 file:// 的取最後一段，不然看檔頭「原檔」底下第一行。 */
  function originalName(front, markdown) {
    var link = String((front && front["開原檔"]) || "");
    if (/^file:/i.test(link)) return dec(link.split(/[\\/]/).pop());
    var m = /^原檔:\s*\n\s*-\s*(\S[^\n]*)$/m.exec(String(markdown || ""));
    return m ? dec(m[1].trim().split("/").pop()) : "";
  }

  /** 檔案卡：大的類型圖示、原檔名、開原檔；原檔是圖片就直接預覽（/api/file）。 */
  function fileHero(head, title, front, markdown, fid) {
    var name = originalName(front, markdown);
    var kind = fileKind({ open: { path: name } });
    var row = h("div", { cls: "hero-row" }, [h("span", { cls: "ftype lg" + (kind[0] ? " " + kind[0] : "") }, [icon(kind[1])]),
      h("div", { cls: "hero-text" }, [title, name ? line1("p", name, "hero-sub") : null])]);
    head.classList.add("hero");
    head.insertBefore(row, head.firstChild);
    var open = openButton(front, true);
    if (open) head.insertBefore(h("div", { cls: "hero-actions" }, [open]), row.nextSibling);
    if (kind[0] === "img" && !state.offline) {
      var img = h("img", { cls: "preview", attrs: { src: "/api/file?id=" + enc(fid), alt: name, loading: "lazy" } });
      img.addEventListener("error", function () { wrap.remove(); });
      var wrap = h("div", { cls: "preview-box" }, [img]);
      head.appendChild(wrap);
    }
  }

  // ----- 健檢報告（D-362）-----

  /** templates/health-report.md 的 ## 段標題，照字比對；範本改了這裡要一起改（tests/test_lib_web.py 守著）。 */
  var REPORT_HEADS = ["一句話", "這個月替你做了什麼", "要你決定的", "先放著超過一個月的", "建議下一步", "數字（給服務人員）"];

  /** 健檢報告拆成：頂端摘要、三格數字、要你決定的每件一張卡（長的收起來）、其他段各一塊、數字段收起來。
   *  沒內容的段報告本來就不寫（report.py），所以只要求有「一句話」、而且每個段標題都認得；對不上就不動 block、回 false（照舊畫 md）。 */
  function healthReport(head, title, block) {
    var heads = Array.prototype.filter.call(block.children, function (el) { return el.tagName === "H2"; })
      .map(function (el) { return el.textContent.trim(); });
    if (heads.indexOf(REPORT_HEADS[0]) < 0 || !heads.every(function (t) { return REPORT_HEADS.indexOf(t) >= 0; })) return false;
    var parts = {};
    var intro = [];
    var cur = null;
    Array.prototype.slice.call(block.children).forEach(function (el) {
      if (el.tagName === "H2") {
        cur = el.textContent.trim();
        parts[cur] = parts[cur] || [];
        el.remove();
        return;
      }
      (cur === null ? intro : parts[cur]).push(el);
    });
    function lis(name) {
      var out = [];
      (parts[name] || []).forEach(function (el) { out = out.concat(Array.prototype.slice.call(el.querySelectorAll("li"))); });
      return out;
    }
    function textOf(name) {
      return (parts[name] || []).map(function (el) { return el.textContent; }).join("\n");
    }
    var decide = lis("要你決定的");
    var m = /已放進待處理（(\d+) 件/.exec(textOf("要你決定的"));
    var nDecide = m ? Number(m[1]) : decide.length;
    var nDone = lis("這個月替你做了什麼").length;
    var nAside = lis("先放著超過一個月的").length;

    head.classList.add("hero");
    head.insertBefore(h("div", { cls: "hero-row" }, [h("span", { cls: "view-icon green" }, [icon("heart-pulse")]), title]), head.firstChild);
    block.textContent = "";
    block.classList.add("report");
    var sum = textOf("一句話").trim();
    if (sum) block.appendChild(h("div", { cls: "report-sum" }, [icon("sparkles"), h("p", { text: sum })]));
    block.appendChild(h("div", { cls: "stats three" }, [miniStat("s-ask", "circle-help", nDecide, "要你決定"),
      miniStat("s-done", "circle-check", nDone, "替你做了"), miniStat("s-zero", "clock", nAside, "先放著")]));

    var cards = decide.map(function (li) {
      var q = h("p", { cls: "ask-q clamp", attrs: { title: li.textContent.trim() } });
      Array.prototype.slice.call(li.childNodes).forEach(function (n) { if (!(n.tagName === "UL" || n.tagName === "OL")) q.appendChild(n); });
      q.addEventListener("click", function (ev) { if (ev.target.tagName !== "A") q.classList.toggle("clamp"); });
      return h("div", { cls: "ask pend rep-ask" }, [h("span", { cls: "ask-icon kt-amber" }, [icon("circle-help")]), h("div", { cls: "ask-body" }, [q])]);
    });
    block.appendChild(h("section", { cls: "section" }, [sectionHead("circle-help", "要你決定的", nDecide,
      [h("a", { cls: "btn", text: "看待處理", attrs: { href: pageHash(PENDING_PAGE) } })])]
      .concat(cards.length ? cards : [h("div", { cls: "md-sec" }, parts["要你決定的"] || [])])));
    [["這個月替你做了什麼", "circle-check"], ["先放著超過一個月的", "clock"], ["建議下一步", "sparkles"]].forEach(function (x) {
      var els = parts[x[0]] || [];
      if (!els.length) return;
      block.appendChild(h("section", { cls: "section" }, [sectionHead(x[1], x[0], lis(x[0]).length), h("div", { cls: "md-sec" }, els)]));
    });
    block.appendChild(h("details", { cls: "fold" }, [h("summary", {}, [icon("list"), h("span", { text: "數字（給服務人員）" }), icon("chevron-down")]),
      h("div", { cls: "md-sec" }, (parts["數字（給服務人員）"] || []).concat(intro))]));
    return true;
  }

  /** 綜合卡：每個 ## 段落收成一塊。 */
  function sectionize(block) {
    var cur = null;
    Array.prototype.slice.call(block.children).forEach(function (el) {
      if (el.tagName === "H2") {
        cur = h("section", { cls: "md-sec" });
        block.insertBefore(cur, el);
      }
      if (cur) cur.appendChild(el);
    });
  }

  /** 綜合卡底下的「接下來」（D-340，kb entity panel）。 */
  function panelBlock(panel) {
    var items = panel && arr(panel.agenda);
    if (!items || !items.length) return null;
    return h("div", { cls: "panel" }, [h("h2", {}, [icon("calendar-clock"), h("span", { text: "接下來" })]), h("ul", {}, items.map(function (a) {
      return h("li", { cls: a.late ? "late" : "" }, [h("span", { cls: "day", text: shortDay(a.date) }), line1("span", a.text || a.line || "")]);
    }))]);
  }

  function mdBlock(data, path) {
    var fid = "";
    var front = window.WillyMd ? window.WillyMd.frontMatter(String(data.markdown || "").replace(/\r\n?/g, "\n")).data : {};
    if (/^f-[0-9a-z]+$/i.test(front["編號"] || "")) fid = front["編號"];
    var el = h("div", { cls: "md" });
    if (window.WillyMd) {
      el.innerHTML = window.WillyMd.render(data.markdown, { dir: dirOf(path), fileId: fid }).html;
    } else {
      el.appendChild(h("pre", { text: data.markdown || "" }));
    }
    el.front = front;
    el.fileId = fid;
    el.addEventListener("click", onMdClick);
    return el;
  }

  /** 檔案卡的「開原檔」在檔頭（不顯示）：照它做一個按鈕。 */
  function openButton(front, labeled) {
    var link = String((front && front["開原檔"]) || "");
    var prefix = "obsidian://wiki-with-willy-open?id=";
    var id = "";
    if (link.toLowerCase().indexOf(prefix) === 0) id = dec(link.slice(prefix.length).split(/[&#]/)[0]);
    else if (/^file:/i.test(link)) id = front["編號"] || "";
    if (/^f-[0-9a-z]+$/i.test(id)) {
      return labeled ? button("開原檔", function () { openOriginal(id); }, "btn primary", "external-link")
        : iconButton("external-link", "開原檔", function () { openOriginal(id); }, "btn icon-only");
    }
    if (/^https?:\/\//i.test(link)) {
      return h("a", { cls: "btn icon-only", attrs: { href: link, target: "_blank", rel: "noopener noreferrer", "aria-label": "開原檔", title: "開原檔" } },
        [icon("external-link")]);
    }
    return null;
  }

  function onMdClick(ev) {
    var a = ev.target.closest ? ev.target.closest("a") : null;
    if (!a) return;
    if (a.classList.contains("md-open")) {
      ev.preventDefault();
      openOriginal(a.getAttribute("data-id"));
      return;
    }
    if (a.classList.contains("md-page")) {
      ev.preventDefault();
      var primary = dec(String(a.getAttribute("href") || "").replace(/^#\/page\//, ""));
      openPageLink(primary, a.getAttribute("data-alt"));
    }
  }

  /** 卡之間的連結：先照這頁的夾，404 再照知識庫根目錄試一次（D-541 之前的舊頁）。 */
  function openPageLink(primary, alt) {
    api("/api/page?path=" + enc(primary)).then(function (r) {
      if (r.status === 200) {
        goPage(primary, r);
        return null;
      }
      if (!alt) {
        toast("找不到這頁，或這頁不給看", null, true);
        return null;
      }
      return api("/api/page?path=" + enc(alt)).then(function (r2) {
        if (r2.status === 200) {
          goPage(alt, r2);
        } else {
          toast("找不到這頁，或這頁不給看", null, true);
        }
      });
    }).catch(function () { toast("連不上主機", null, true); });
  }

  function goPage(path, r) {
    state.pageCache = { path: path, r: r };
    if (location.hash === pageHash(path)) route();
    else location.hash = pageHash(path);
  }

  /** 開原檔：在主機前打開真正的檔；在別台下載一份副本。 */
  function openOriginal(id) {
    if (state.offline) {
      toast("開原檔" + NEED_HOST);
      return;
    }
    api("/api/open?id=" + enc(id)).then(function (r) {
      var d = r.data || {};
      if (d.download) {
        var a = h("a", { attrs: { href: d.download, target: "_blank", rel: "noopener" } });
        document.body.appendChild(a);
        a.click();
        a.remove();
        toast(COPY_TEXT);
      } else if (d.url && /^https?:\/\//i.test(d.url)) {
        window.open(d.url, "_blank", "noopener,noreferrer");
      } else {
        toast(d.message || d.error || "開不了這份");
      }
    }).catch(function () { toast("連不上主機", null, true); });
  }

  // ---------- 隨手記 ----------

  /** 主機沒開：隨手記讀不到，只能先記一句，主機開了接在最後（不整份覆蓋）。 */
  function renderOfflineNote() {
    var input = h("input", { cls: "input grow", attrs: { type: "text", placeholder: "離線時先記一句", "aria-label": "離線時先記一句" } });
    var list = h("div", { cls: "queued-notes" });
    function draw() {
      list.textContent = "";
      state.queue.filter(function (q) { return q.kind === "note"; }).forEach(function (q) {
        list.appendChild(h("p", { cls: "queued-row" }, [h("span", { text: q.body.append }), h("span", { cls: "queued" }, [icon("clock"), h("span", { text: QUEUED_TEXT })])]));
      });
    }
    var save = function () {
      var v = input.value.trim();
      if (!v) return;
      input.value = "";
      enqueue("note", { append: v, at: clock(new Date()) }, "隨手記：" + v).then(draw);
    };
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.isComposing && ev.keyCode !== 229) save();
    });
    main([h("h1", { cls: "note-title", text: "隨手記" }),
      h("p", { cls: "muted", text: "主機沒開，隨手記要主機開了才看得到。可以先記一句，主機開了會接在最後。" }),
      h("div", { cls: "form-row" }, [input, button("記下", save, "btn primary")]), list]);
    draw();
    return null;
  }

  function renderNote() {
    if (state.offline) return renderOfflineNote();
    var area = h("textarea", { cls: "note", attrs: { "aria-label": "隨手記", placeholder: "隨手記點什麼，停手一下就會存" } });
    var info = h("p", { cls: "note-info", attrs: { "aria-live": "polite" } });
    var note = { area: area, info: info, base: null, timer: null, saving: false, again: false };
    state.note = note;
    area.disabled = true;
    main([h("h1", { cls: "note-title", text: "隨手記" }), area, info]);
    area.addEventListener("input", function () {
      if (note.timer) clearTimeout(note.timer);
      info.textContent = "還沒存";
      note.timer = setTimeout(function () { saveNote(note); }, NOTE_DELAY);
    });
    area.addEventListener("blur", function () { flushNote(); });
    return loadNote(note);
  }

  function loadNote(note) {
    return api("/api/note").then(function (r) {
      if (r.status !== 200 || !r.data) throw new Error();
      note.area.value = r.data.text || "";
      note.base = r.data.base;
      note.area.disabled = false;
      note.info.textContent = "停止打字 1.5 秒自動存";
    }).catch(function () { note.info.textContent = "連不上主機，隨手記讀不到"; });
  }

  function saveNote(note) {
    if (note.timer) {
      clearTimeout(note.timer);
      note.timer = null;
    }
    if (note.base === null) return Promise.resolve();
    if (note.saving) {
      note.again = true;
      return Promise.resolve();
    }
    note.saving = true;
    var text = note.area.value;
    return api("/api/note", { method: "POST", body: { text: text, base: note.base } }).then(function (r) {
      if (r.status === 200 && r.data) {
        note.base = r.data.base;
        if (note.area.value === text) note.info.textContent = "已存";
      } else if (r.status === 409 && r.data) {
        toast("別的地方剛改過，已重新載入");
        keepUnsaved(note, note.area.value, r.data.text || "");
        note.area.value = r.data.text || "";
        note.base = r.data.base;
        note.again = false;
        note.info.textContent = "已重新載入";
      } else if (r.status === 413) {
        note.info.textContent = "隨手記太長，存不了";
      } else {
        note.info.textContent = "沒存成";
      }
    }).catch(function () {
      note.info.textContent = "連不上主機，沒存成";
    }).then(function () {
      note.saving = false;
      if (note.again) {
        note.again = false;
        return saveNote(note);
      }
      return null;
    });
  }

  /** 409 重新載入前，她剛打、沒存到的字放在下面一格，不讓它不見（要的話自己複製回去）。 */
  function keepUnsaved(note, text, server) {
    if (!text || text === server) return;
    if (!note.lost) {
      note.lost = h("textarea", { cls: "note", attrs: { readonly: true, "aria-label": "你剛打的（沒存到）" } });
      note.area.parentNode.appendChild(h("details", { attrs: { open: true } }, [h("summary", { text: "你剛打的（沒存到，要的話複製回去）" }), note.lost]));
    }
    note.lost.value = text;
  }

  /** 離開隨手記或游標離開時：還沒存的馬上存。 */
  function flushNote() {
    var note = state.note;
    if (note && note.timer) saveNote(note);
    if (note && !$("main").contains(note.area)) state.note = null;
  }

  // ---------- 開始 ----------

  /** chat.js 送話時問：主機沒開嗎、排一句。 */
  window.WillyOffline = {
    isOffline: function () { return state.offline; },
    queueChat: function (text, page) {
      var body = { text: text };
      if (page) body.page = page;
      return enqueue("chat", body, "跟 AI 說：" + text.slice(0, 30));
    },
    wentOffline: function () {
      state.offline = true;
      showConn(false);
      checkStatus();
    }
  };

  function start() {
    var menu = $("menu");
    showVault(lsGet(VAULT_KEY));
    menuItems("menu-item").forEach(function (a) { menu.appendChild(a); });
    Array.prototype.forEach.call(document.querySelectorAll(".tabs a"), function (a) {
      var ic = icon(a.getAttribute("data-icon"));
      if (ic) a.insertBefore(ic, a.firstChild);
    });
    mountChat();
    window.addEventListener("hashchange", route);
    window.addEventListener("beforeunload", flushNote);
    try {
      if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(function () {});
    } catch (e) {
      // 不給也沒關係（加到主畫面才是主要的保護）
    }
    reloadQueue();
    route();
    checkStatus();
    setInterval(checkStatus, STATUS_EVERY);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") checkStatus();
    });
    window.addEventListener("online", function () { checkStatus(); });
    // 對話沒有「AI 回完了」的事件可接：每 60 秒重抓首頁，AI 改了日程首頁跟著變
    setInterval(function () {
      if (state.view === "home" && document.visibilityState !== "hidden") refreshHome();
    }, HOME_EVERY);
  }

  start();
}());
