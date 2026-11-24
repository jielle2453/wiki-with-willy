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
  var CLAUDE_ASK = "有哪些要我跟你一起看的？";
  var COPY_TEXT = "這是副本，改了不會存回去";
  var UPLOAD_ACCEPT = "image/*,application/pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.md,.zip";
  var MENU = [
    { label: "首頁", hash: "#/", view: "home" },
    { label: "總目錄", page: INDEX_PAGE },
    { label: "日程", page: AGENDA_PAGE },
    { label: "待處理", page: "系統/待處理.md" },
    { label: "健檢報告", report: true },
    { label: "隨手記", hash: "#/note", view: "note" }
  ];
  var WEEKDAY = "日一二三四五六";
  var NEED_HOST = "主機開了才能用";
  var QUEUED_TEXT = "主機開了才會送出";
  var HINT_KEY = "willy.homeHint";
  var HINT_DAYS = 30;
  var QUEUE_ACTIONS = { done: "打勾", edit: "改日期", add: "新增" };

  var state = { home: null, homeSeq: 0, view: null, page: null, pageCache: null, chat: false, toastTimer: null, note: null, drawn: false,
    offline: false, login: false, homeSaved: null, homeAt: null, queue: [], flushing: false, swVersion: null, bar: null };
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

  function button(text, onClick, cls) {
    return h("button", { cls: cls || "btn", text: text, attrs: { type: "button" }, on: { click: onClick } });
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

  function showConn(ok) {
    var text = ok ? "主機連線中" : state.login ? "要重新登入" : "主機沒開";
    $("dot").className = ok ? "dot on" : "dot";
    $("state").textContent = text;
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
    el.textContent = n ? "還有 " + n + " 件沒送出" : "";
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
    var card = h("div", { cls: "hint" }, [h("p", { text: text })]);
    card.appendChild(button("知道了", function () {
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

  function toast(text, undo) {
    var el = $("toast");
    if (state.toastTimer) clearTimeout(state.toastTimer);
    el.textContent = "";
    el.appendChild(h("span", { text: text }));
    if (undo) {
      el.appendChild(button("復原", function () {
        el.hidden = true;
        undo();
      }, ""));
    }
    el.hidden = false;
    state.toastTimer = setTimeout(function () { el.hidden = true; }, TOAST_MS);
  }

  // ---------- 選單 ----------

  function menuItems(cls) {
    return MENU.map(function (m) {
      var a = h("a", { cls: cls, text: m.label, attrs: { href: m.page ? pageHash(m.page) : m.hash || "#" } });
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
    Array.prototype.forEach.call(document.querySelectorAll(".tabs a"), function (a) {
      var tab = state.view === "chat" ? "chat" : state.view === "home" ? "home" : "find";
      a.classList.toggle("on", a.dataset.tab === tab);
    });
  }

  function openReport() {
    api("/api/latest-report").then(function (r) {
      var p = r.data && r.data.path;
      if (p) location.hash = pageHash(p);
      else toast("還沒有健檢報告");
    }).catch(function () { toast("連不上主機"); });
  }

  // ---------- 路由 ----------

  function parseHash() {
    var s = location.hash || "#/";
    var m;
    if ((m = /^#\/find(?:\?q=(.*))?$/.exec(s))) return { view: "find", q: dec(m[1] || "") };
    if ((m = /^#\/page\/(.+)$/.exec(s))) return { view: "page", path: dec(m[1]) };
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
    $("main").scrollTop = 0;
    if (r.view === "page") return renderPage(r.path);
    setPage(null);
    if (r.view === "find") return renderFind(r.q);
    if (r.view === "note") return renderNote();
    return renderHome();
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
    else main([h("p", { cls: "muted", text: "讀取中…" })]);
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
      if (state.view === "home" && !state.home) main([h("p", { cls: "muted", text: "首頁讀不到：" + (e && e.message ? e.message : "連不上主機") })]);
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
      h("div", { cls: "hello" }, [h("p", { cls: "date", text: dayText(home.today) }), h("h1", { text: greeting() })]),
      search,
      todaySection(home),
      confirmSection(home),
      weekSection(home),
      recentSection(home)
    ]);
  }

  function todaySection(home) {
    var todo = home.todo || {};
    var items = arr(todo.overdue).concat(arr(todo.today));
    var queued = queuedActs();
    var adds = queued.add.map(function (b) {
      return h("div", { cls: "todo queued-row" }, [h("span", { cls: "todo-text", text: b.text || "" }),
        h("span", { cls: "queued", text: QUEUED_TEXT })]);
    });
    var list = h("div", { cls: "todo-list" }, items.length || adds.length ? items.map(function (it) { return todoRow(it, home, queued); }).concat(adds)
      : [h("p", { cls: "empty", text: "今天沒有要做的" })]);
    return h("section", { cls: "section" }, [
      h("div", { cls: "section-head" }, [h("h2", { text: "今天要做的" }), uploadButton()]),
      list,
      addRow(home)
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
    var p = it.card && it.card.path ? it.card.path : AGENDA_PAGE;
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
    when.addEventListener("click", function () {
      if (form) {
        form.remove();
        form = null;
        return;
      }
      form = editForm(it, function () {
        form.remove();
        form = null;
      });
      row.appendChild(form);
      form.querySelector("input").focus();
    });
    row.appendChild(h("label", { cls: "check" }, [box]));
    row.appendChild(h("button", { cls: "linkish todo-text", text: it.text || "", attrs: { type: "button" }, on: { click: function () { openItem(it); } } }));
    row.appendChild(when);
    if (waiting) {  // 離線排著的：先照做（打勾的劃掉、改的日期先顯示），標淡字
      if (queued.done[it.id]) {
        box.checked = true;
        box.disabled = true;
        row.classList.add("queued-done");
      } else {
        var e = queued.edit[it.id];
        when.textContent = shortDay(e.date || it.date) + (e.time || it.time ? " " + (e.time || it.time) : "");
      }
      row.appendChild(h("span", { cls: "queued", text: QUEUED_TEXT }));
    }
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
      button("取消", close)
    ]);
  }

  /** AI 推測的日程：對、改日期、不用了（照外掛）。 */
  function guessRow(it) {
    var row = h("div", { cls: "guess" }, [h("span", { text: "AI 推測的，對嗎？" })]);
    row.appendChild(button("對", function () { act({ action: "inferred_ok", id: it.id }); }, "linkish"));
    row.appendChild(button("改日期", function () {
      var date = h("input", { cls: "input", attrs: { type: "date", value: it.date || "", "aria-label": "新日期" } });
      var f = h("div", { cls: "form-row" }, [date, button("好", function () {
        if (date.value) act({ action: "inferred_move", id: it.id, date: date.value });
      }, "btn primary"), button("取消", function () { f.remove(); })]);
      row.appendChild(f);
      date.focus();
    }, "linkish"));
    row.appendChild(button("不用了", function () { act({ action: "inferred_cancel", id: it.id }); }, "linkish"));
    return row;
  }

  function addRow(home) {
    var wrap = h("div", { cls: "add" });
    var toggle = button("＋ 新增", function () {
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
      var form = h("div", { cls: "form-row" }, [date, time, text, button("送出", send, "btn primary"), button("取消", function () { form.remove(); })]);
      wrap.appendChild(form);
      text.focus();
    }, "linkish add-toggle");
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
      kids.push(button("還有 " + n + " 件要跟 AI 一起看 →", function () { askAi(CLAUDE_ASK); }, "linkish more-line"));
    }
    return h("section", { cls: "section" }, [h("div", { cls: "section-head" }, [h("h2", { text: "要你確認的" })])].concat(kids));
  }

  function askCard(c) {
    var card = h("div", { cls: "ask" + (c.urgent ? " urgent" : "") }, [h("p", { text: c.text || "" })]);
    var answers = h("div", { cls: "answers" });
    var slot = h("div");
    if (state.offline) card.appendChild(h("p", { cls: "muted small", text: NEED_HOST }));
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
    card.appendChild(answers);
    card.appendChild(slot);
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
    return h("div", { cls: "ask" }, [h("p", { text: o.text || "" }),
      h("div", { cls: "answers" }, [button("知道了", function () { act({ action: "dismiss", id: o.id }); })])]);
  }

  function weekSection(home) {
    var items = arr(home.todo && home.todo.week);
    if (!items.length) return null;
    var grid = h("div", { cls: "week" });
    items.forEach(function (it) {
      grid.appendChild(h("span", { cls: "day", text: shortDay(it.date) + (it.time ? " " + it.time : "") }));
      grid.appendChild(button(it.text || "", function () { openItem(it); }, "linkish"));
    });
    return h("section", { cls: "section" }, [h("div", { cls: "section-head" }, [h("h2", { text: "這週" })]), grid]);
  }

  function recentSection(home) {
    var items = arr(home.recent).slice(0, 5);
    if (!items.length) return null;
    return h("section", { cls: "section" }, [h("div", { cls: "section-head" }, [h("h2", { text: "最近" })])].concat(items.map(function (f) {
      return h("div", { cls: "list-row" }, [
        button(f.title || f.card_path || "", function () { if (f.card_path) location.hash = pageHash(f.card_path); }, "linkish"),
        h("span", { cls: "muted small", text: [f.type, f.date].filter(Boolean).join("・") })
      ]);
    })));
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
      return loadHome();
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
    return h("label", { cls: "btn upload" }, [h("span", { text: "傳照片或檔案" }), input]);
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

  function renderFind(q) {
    var input = h("input", { cls: "input search", attrs: { type: "search", placeholder: "找卡：打字按 Enter", value: q || "", "aria-label": "找卡" } });
    input.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter" || ev.isComposing || ev.keyCode === 229) return;
      var v = input.value.trim();
      location.hash = v ? "#/find?q=" + enc(v) : "#/find";
    });
    var out = h("div", { cls: "results" }, [h("p", { cls: "muted", text: "讀取中…" })]);
    main([quickRow(), input, out]);
    if (state.offline) {
      input.disabled = true;
      out.textContent = "";
      out.appendChild(h("p", { cls: "empty", text: "主機沒開：找卡" + NEED_HOST + "；最近開過的卡在首頁的「最近」點得開" }));
      return null;
    }
    if (!q) {
      return api("/api/page?path=" + enc(INDEX_PAGE)).then(function (r) {
        if (state.view !== "find") return;
        out.textContent = "";
        if (r.status !== 200 || !r.data) {
          out.appendChild(h("p", { cls: "empty", text: "還沒有總目錄" }));
          return;
        }
        out.appendChild(mdBlock(r.data, INDEX_PAGE));
      }).catch(function () { out.textContent = "連不上主機"; });
    }
    return api("/api/find?q=" + enc(q)).then(function (r) {
      if (state.view !== "find") return;
      out.textContent = "";
      var d = r.data || {};
      if (r.status !== 200) {
        out.appendChild(h("p", { cls: "empty", text: d.error || "沒找成" }));
        return;
      }
      var ents = arr(d.entities);
      var files = arr(d.files);
      if (ents.length) {
        out.appendChild(h("p", { cls: "result-head", text: "人、案子" }));
        ents.forEach(function (e) {
          out.appendChild(h("div", { cls: "list-row" }, [
            e.card_path ? button(e.name || "", function () { location.hash = pageHash(e.card_path); }, "linkish") : h("span", { cls: "linkish", text: e.name || "" }),
            h("span", { cls: "muted small", text: e.type || "" })
          ]));
        });
      }
      if (files.length) {
        out.appendChild(h("p", { cls: "result-head", text: "檔案" }));
        files.forEach(function (f) {
          out.appendChild(h("div", { cls: "list-row" }, [
            button(f.title || f.card_path || "", function () { if (f.card_path) location.hash = pageHash(f.card_path); }, "linkish"),
            h("span", { cls: "muted small", text: [f.type, f.date].filter(Boolean).join("・") })
          ]));
        });
      }
      if (!ents.length && !files.length) out.appendChild(h("p", { cls: "empty", text: "沒找到。換幾個字，或在對話問 AI" }));
      if (d.more) out.appendChild(h("p", { cls: "muted small", text: "還有 " + (d.total - files.length) + " 份沒列出來，加幾個字縮小範圍" }));
    }).catch(function () { out.textContent = "連不上主機"; });
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

  function renderPage(path) {
    setPage(path);
    var title = h("h1", { text: path.split("/").pop().replace(/\.md$/i, "") });
    var actions = h("div", { cls: "page-actions" });
    var back = button("← 返回", function () {
      if (history.length > 1) history.back();
      else location.hash = "#/";
    }, "linkish");
    var ask = button("問 AI", function () {
      if (wide()) {
        if (!chatCall("prefill", "")) toast("對話還沒裝好");
      } else {
        location.hash = "#/chat";
      }
    }, "btn");
    var body = h("div", { cls: "page-body" }, [h("p", { cls: "muted", text: "讀取中…" })]);
    main([h("div", { cls: "page-bar" }, [back, title, actions, ask]), body]);
    return getPage(path).then(function (r) {
      if (state.view !== "page" || state.page !== path) return;
      body.textContent = "";
      if (r.status !== 200 || !r.data) {
        var off = state.offline || (r.data && r.data.offline);
        body.appendChild(h("p", { cls: "empty", text: off ? "主機沒開，這頁沒存著（只看得到最近開過的卡）" : "找不到這頁，或這頁不給看" }));
        return;
      }
      title.textContent = r.data.title || title.textContent;
      if (r.data.path && r.data.path !== path) state.page = r.data.path;
      var panel = panelBlock(r.data.panel);
      if (panel) body.appendChild(panel);
      var block = mdBlock(r.data, r.data.path || path);
      var open = openButton(block.front);
      if (open) actions.appendChild(open);
      body.appendChild(block);
    }).catch(function () {
      body.textContent = "連不上主機";
    });
  }

  /** 綜合卡底下的「接下來」（D-340，kb entity panel）。 */
  function panelBlock(panel) {
    var items = panel && arr(panel.agenda);
    if (!items || !items.length) return null;
    return h("div", { cls: "panel" }, [h("h2", { text: "接下來" }), h("ul", {}, items.map(function (a) {
      return h("li", { cls: a.late ? "late" : "" }, [h("span", { cls: "day", text: shortDay(a.date) }), h("span", { text: a.text || a.line || "" })]);
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
  function openButton(front) {
    var link = String((front && front["開原檔"]) || "");
    var prefix = "obsidian://wiki-with-willy-open?id=";
    var id = "";
    if (link.toLowerCase().indexOf(prefix) === 0) id = dec(link.slice(prefix.length).split(/[&#]/)[0]);
    else if (/^file:/i.test(link)) id = front["編號"] || "";
    if (/^f-[0-9a-z]+$/i.test(id)) return button("開原檔", function () { openOriginal(id); }, "btn");
    if (/^https?:\/\//i.test(link)) {
      return h("a", { cls: "btn", text: "開原檔", attrs: { href: link, target: "_blank", rel: "noopener noreferrer" } });
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
        toast("找不到這頁，或這頁不給看");
        return null;
      }
      return api("/api/page?path=" + enc(alt)).then(function (r2) {
        if (r2.status === 200) {
          goPage(alt, r2);
        } else {
          toast("找不到這頁，或這頁不給看");
        }
      });
    }).catch(function () { toast("連不上主機"); });
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
    }).catch(function () { toast("連不上主機"); });
  }

  // ---------- 隨手記 ----------

  /** 主機沒開：隨手記讀不到，只能先記一句，主機開了接在最後（不整份覆蓋）。 */
  function renderOfflineNote() {
    var input = h("input", { cls: "input grow", attrs: { type: "text", placeholder: "離線時先記一句", "aria-label": "離線時先記一句" } });
    var list = h("div", { cls: "queued-notes" });
    function draw() {
      list.textContent = "";
      state.queue.filter(function (q) { return q.kind === "note"; }).forEach(function (q) {
        list.appendChild(h("p", { cls: "queued-row" }, [h("span", { text: q.body.append }), h("span", { cls: "queued", text: QUEUED_TEXT })]));
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
    main([h("div", { cls: "page-bar" }, [h("h1", { text: "隨手記" })]),
      h("p", { cls: "muted", text: "主機沒開，隨手記要主機開了才看得到。可以先記一句，主機開了會接在最後。" }),
      h("div", { cls: "form-row" }, [input, button("記下", save, "btn primary")]), list]);
    draw();
    return null;
  }

  function renderNote() {
    if (state.offline) return renderOfflineNote();
    var area = h("textarea", { cls: "note", attrs: { "aria-label": "隨手記", placeholder: "隨手記點什麼，停手一下就會存" } });
    var info = h("p", { cls: "muted small", text: "讀取中…" });
    var note = { area: area, info: info, base: null, timer: null, saving: false, again: false };
    state.note = note;
    area.disabled = true;
    main([h("div", { cls: "page-bar" }, [h("h1", { text: "隨手記" })]), area, info]);
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
    menuItems("menu-item").forEach(function (a) { menu.appendChild(a); });
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
