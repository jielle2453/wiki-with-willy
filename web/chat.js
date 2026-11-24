// willy 網頁的對話區（#74 第 2 份，D-351～D-353）。接口固定（第 3 份照這個接）：
//   window.WillyChat.mount(el)     把整個對話區畫進 el、開始連線；只呼叫一次。
//   window.WillyChat.setPage(path) 她正在看的頁（知識庫根目錄起算的相對路徑或 null）；之後送的話帶 page。
//   window.WillyChat.prefill(text) 把字放進輸入框、游標放最後，不送出。
//   window.WillyChat.showPending(texts) 離線時排著還沒送的話（第 4 份），在最下面淡淡地列出來。
// 主機沒開（window.WillyOffline.isOffline()）或送的時候連不上：那句交給 WillyOffline.queueChat 排隊，主機開了照順序送。
// 不自己找容器、不改頁面標題；樣式全在 chat.css（wc- 開頭）。打開時 GET /api/chat 拿最近的，接 /api/events（SSE），
// 重連後用 /api/chat?after= 補；POST 一律帶 X-Willy: 1。
(function () {
  "use strict";
  var HOUR = 3600 * 1000;
  var root = null;
  var list = null;
  var busyLine = null;
  var input = null;
  var sendBtn = null;
  var page = null;
  var lastSeq = 0;
  var lastTime = null;
  var seen = {};
  var rows = [];
  var cardNodes = {};
  var cardRows = {};
  var sending = false;
  var waiting = {};
  var picks = {};
  var pendingBox = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) {
      n.className = cls;
    }
    if (text !== undefined && text !== null) {
      n.textContent = text;
    }
    return n;
  }

  function api(path, body) {
    var opt = { cache: "no-store", headers: { "X-Willy": "1" } };
    if (body !== undefined) {
      opt.method = "POST";
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    return fetch(path, opt);
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function stamp(t) {
    var d = new Date(t);
    if (isNaN(d.getTime())) {
      return "";
    }
    var now = new Date();
    var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
    if (d.toDateString() === now.toDateString()) {
      return "今天 " + hm;
    }
    return (d.getMonth() + 1) + "/" + d.getDate() + " " + hm;
  }

  function nearBottom() {
    return list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  }

  function toBottom() {
    list.scrollTop = list.scrollHeight;
  }

  // ---------- 卡片 ----------

  function answerCard(cardId, answer, node) {
    var buttons = node.querySelectorAll("button");
    for (var i = 0; i < buttons.length; i += 1) {
      buttons[i].disabled = true;
    }
    api("/api/chat/answer", { card_id: cardId, answer: answer }).then(function (r) {
      if (r.status === 409) {  // 別台回過了：那張收掉、重抓
        delete waiting[cardId];
        drawCard(cardId);
        catchUp();
        return;
      }
      if (!r.ok) {
        throw new Error("HTTP " + r.status);
      }
    }).catch(function () {
      for (var j = 0; j < buttons.length; j += 1) {
        buttons[j].disabled = false;
      }
      var err = node.querySelector(".wc-err") || node.appendChild(el("div", "wc-err"));
      err.textContent = "沒送出去，再按一次";
    });
  }

  function ownBox(holder, onDone) {
    var box = el("div", "wc-own");
    var area = el("textarea");
    area.rows = 1;
    area.setAttribute("aria-label", "我自己說");
    var ok = el("button", "wc-btn wc-main", "好");
    ok.type = "button";
    ok.addEventListener("click", function () {
      if (area.value.trim()) {
        onDone(area.value.trim());
      }
    });
    box.appendChild(area);
    box.appendChild(ok);
    holder.appendChild(box);
    area.focus();
  }

  function questionCard(card, node) {
    var qs = card.questions || [];
    var chosen = picks[card.card_id] || (picks[card.card_id] = {});
    var single = qs.length === 1 && !qs[0].multiSelect;
    var done = el("button", "wc-btn wc-main", "好了");
    done.type = "button";

    function ready() {
      for (var i = 0; i < qs.length; i += 1) {
        var a = chosen[qs[i].question];
        if (a === undefined || (a instanceof Array && !a.length)) {
          return false;
        }
      }
      return true;
    }

    function refresh() {
      done.disabled = !ready();
    }

    qs.forEach(function (q) {
      var box = el("div", "wc-q");
      box.appendChild(el("div", null, q.question));
      var opts = el("div", "wc-opts");
      var optButtons = [];
      (q.options || []).forEach(function (o) {
        var b = el("button", "wc-btn", o.label);
        b.type = "button";
        if (o.description) {
          b.appendChild(el("small", null, o.description));
        }
        b.addEventListener("click", function () {
          if (single) {
            var one = {};
            one[q.question] = o.label;
            answerCard(card.card_id, one, node);
            return;
          }
          if (q.multiSelect) {
            var cur = chosen[q.question] instanceof Array ? chosen[q.question] : [];
            var at = cur.indexOf(o.label);
            if (at >= 0) {
              cur.splice(at, 1);
            } else {
              cur.push(o.label);
            }
            chosen[q.question] = cur;
            b.classList.toggle("wc-on", at < 0);
          } else {
            chosen[q.question] = o.label;
            optButtons.forEach(function (x) {
              x.classList.toggle("wc-on", x === b);
            });
          }
          refresh();
        });
        optButtons.push(b);
        opts.appendChild(b);
      });
      var own = el("button", "wc-btn wc-plain", "我自己說");
      own.type = "button";
      own.addEventListener("click", function () {
        own.disabled = true;
        ownBox(box, function (text) {
          if (single) {
            var one = {};
            one[q.question] = { text: text };
            answerCard(card.card_id, one, node);
            return;
          }
          chosen[q.question] = { text: text };
          optButtons.forEach(function (x) {
            x.classList.remove("wc-on");
          });
          refresh();
        });
      });
      opts.appendChild(own);
      box.appendChild(opts);
      node.appendChild(box);
    });
    if (!single) {
      done.addEventListener("click", function () {
        if (ready()) {
          answerCard(card.card_id, chosen, node);
        }
      });
      refresh();
      node.appendChild(done);
    }
  }

  function permissionCard(card, node) {
    node.appendChild(el("div", null, card.reason || "AI 要做一件需要你同意的事"));
    var opts = el("div", "wc-opts");
    var yes = el("button", "wc-btn wc-main", "允許");
    var no = el("button", "wc-btn", "不允許");
    yes.type = "button";
    no.type = "button";
    yes.addEventListener("click", function () {
      answerCard(card.card_id, "allow", node);
    });
    no.addEventListener("click", function () {
      answerCard(card.card_id, "deny", node);
    });
    opts.appendChild(yes);
    opts.appendChild(no);
    node.appendChild(opts);
  }

  function drawCard(cardId) {
    var node = cardNodes[cardId];
    if (!node && waiting[cardId]) {
      // 卡片那行比畫面拿到的最近 100 則還舊：照樣在最下面畫出來，不然她看不到、AI 一直等
      if (!cardRows[cardId]) {
        cardRows[cardId] = { who: "card", text: "", card: { card_id: cardId, answer: "" } };
      }
      node = cardNodes[cardId] = list.appendChild(el("div", "wc-card"));
    }
    var row = cardRows[cardId];
    if (!node || !row) {
      return;
    }
    var info = row.card || {};
    var live = waiting[cardId];
    node.textContent = "";
    if (info.answer) {
      node.className = "wc-done";
      node.textContent = row.text;
    } else if (live) {
      node.className = "wc-card";
      if (live.kind === "question") {
        questionCard(live, node);
      } else {
        permissionCard(live, node);
      }
    } else {
      node.className = "wc-done";
      node.textContent = "（這張已經不能回了）";
    }
  }

  // ---------- 訊息 ----------

  function rowNode(row) {
    if (row.segment) {
      return el("div", "wc-time", stamp(row.t));
    }
    if (row.who === "her") {
      return el("div", "wc-her", row.text);
    }
    if (row.who === "ai") {
      // AI 常用 Markdown：有 md.js（window.WillyMd，先整份跳脫再轉）就畫成格式，沒有照舊純文字。她的與系統訊息一律純文字。
      if (window.WillyMd) {
        var n = el("div", "wc-ai wc-md");
        n.innerHTML = window.WillyMd.render(row.text || "").html;
        return n;
      }
      return el("div", "wc-ai", row.text);
    }
    return el("div", "wc-system", row.text);
  }

  function place(row) {
    if (row.who === "card" && row.card && cardNodes[row.card.card_id]) {
      cardRows[row.card.card_id] = row;  // 同一張只顯示最新那行，留在原位
      drawCard(row.card.card_id);
      return;
    }
    var t = new Date(row.t).getTime();
    if (!row.segment && lastTime !== null && !isNaN(t) && t - lastTime > HOUR) {
      list.appendChild(el("div", "wc-time", stamp(row.t)));
    }
    if (!isNaN(t)) {
      lastTime = t;
    }
    if (row.who === "card" && row.card && row.card.card_id) {
      cardRows[row.card.card_id] = row;
      cardNodes[row.card.card_id] = list.appendChild(el("div", "wc-card"));
      drawCard(row.card.card_id);
      return;
    }
    list.appendChild(rowNode(row));
  }

  function rebuild() {
    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
    cardNodes = {};
    lastTime = null;
    rows.forEach(place);
    Object.keys(waiting).forEach(drawCard);
  }

  function add(row) {
    if (!row || typeof row.seq !== "number" || seen[row.seq]) {
      return;
    }
    seen[row.seq] = true;
    var stick = nearBottom();
    if (row.seq > lastSeq) {
      rows.push(row);
      lastSeq = row.seq;
      place(row);
    } else {
      rows.push(row);
      rows.sort(function (a, b) { return a.seq - b.seq; });
      rebuild();
    }
    if (stick) {
      toBottom();
    }
  }

  function setBusy(on) {
    busyLine.textContent = on ? "AI 正在回…" : "";
  }

  function setCards(cards) {
    var fresh = {};
    (cards || []).forEach(function (c) {
      fresh[c.card_id] = c;
    });
    var ids = Object.keys(waiting).concat(Object.keys(fresh));
    var changed = ids.filter(function (id) { return !!waiting[id] !== !!fresh[id]; });
    waiting = fresh;
    changed.forEach(drawCard);
  }

  function load(data) {
    (data.messages || []).forEach(add);
    setCards(data.cards);
    setBusy(!!data.busy);
  }

  function catchUp() {
    var q = lastSeq ? "?after=" + lastSeq : "";
    return api("/api/chat" + q).then(function (r) {
      if (!r.ok) {
        throw new Error("HTTP " + r.status);
      }
      return r.json();
    }).then(load).catch(function () {});
  }

  function onEvent(e) {
    var data;
    try {
      data = JSON.parse(e.data);
    } catch (err) {
      return;
    }
    if (data.type === "message") {
      add(data.message);
    } else if (data.type === "card") {
      waiting[data.card.card_id] = data.card;
      if (data.message) {
        add(data.message);
      }
      drawCard(data.card.card_id);
    } else if (data.type === "card_done") {
      delete waiting[data.card_id];
      delete picks[data.card_id];
      if (data.message) {
        add(data.message);
      }
      drawCard(data.card_id);
    } else if (data.type === "busy") {
      setBusy(!!data.busy);
    }
  }

  function listen() {
    if (typeof EventSource !== "function") {
      setInterval(catchUp, 5000);
      return;
    }
    var src = new EventSource("/api/events");
    src.onmessage = onEvent;
    src.onopen = function () {
      catchUp();  // 剛連上或斷線重連：補中間漏掉的
    };
  }

  // ---------- 送話 ----------

  /** 主機沒開：交給 app.js 排隊（window.WillyOffline）；排好了清輸入框。沒有排隊的功能回 false。 */
  function queueIt(text) {
    var off = window.WillyOffline;
    if (!off || typeof off.queueChat !== "function") {
      return false;
    }
    sending = true;
    sendBtn.disabled = true;
    Promise.resolve(off.queueChat(text, page)).then(function (ok) {
      if (ok && input.value === text) {
        input.value = "";
        grow();
      }
    }).catch(function () {}).then(function () {
      sending = false;
      sendBtn.disabled = false;
    });
    return true;
  }

  function send() {
    var text = input.value;
    if (!text.trim() || sending) {
      return;  // 還在送上一句（連按 Enter）：不重送
    }
    var off = window.WillyOffline;
    if (off && typeof off.isOffline === "function" && off.isOffline() && queueIt(text)) {
      return;
    }
    sending = true;
    sendBtn.disabled = true;
    var body = { text: text };
    if (page) {
      body.page = page;
    }
    var lost = false;
    api("/api/chat/send", body).then(function (r) {
      if (!r.ok) {
        var wq = window.WillyQueue;
        if (wq && typeof wq.unreachable === "function" ? wq.unreachable(r.status) : r.status >= 502) {
          lost = true;  // 通道那頭沒人（Cloudflare 的錯誤頁）：當作連不上；500、524 可能已經收到，不排免得講兩次
        }
        throw new Error("HTTP " + r.status);
      }
      if (input.value === text) {
        input.value = "";
      }
      grow();
    }).catch(function (e) {
      if (lost || e instanceof TypeError) {
        lost = true;
        return;
      }
      busyLine.textContent = "沒送出去，再按一次送出";
    }).then(function () {
      sending = false;
      sendBtn.disabled = false;
      if (lost && off && typeof off.wentOffline === "function") {
        off.wentOffline();
        if (!queueIt(text)) {
          busyLine.textContent = "沒送出去，再按一次送出";
        }
      }
    });
  }

  function showPending(texts) {
    if (!pendingBox) {
      return;
    }
    var stick = nearBottom();
    pendingBox.textContent = "";
    (texts || []).forEach(function (t) {
      var row = el("div", "wc-her wc-pending", t);
      row.appendChild(el("small", null, "主機開了才會送出"));
      pendingBox.appendChild(row);
    });
    if (stick) {
      toBottom();
    }
  }

  function grow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight + 2, 160) + "px";
  }

  function mount(container) {
    if (root) {
      return;
    }
    root = el("section", "wc-chat");
    root.setAttribute("aria-label", "跟 AI 說");
    root.appendChild(el("div", "wc-head", "跟 AI 說"));
    list = el("div", "wc-list");
    list.setAttribute("aria-live", "polite");
    root.appendChild(list);
    pendingBox = el("div", "wc-pending-box");
    root.appendChild(pendingBox);
    busyLine = el("div", "wc-busy");
    root.appendChild(busyLine);
    var bar = el("div", "wc-bar");
    input = el("textarea", "wc-input");
    input.rows = 1;
    input.placeholder = "跟 AI 說";
    input.setAttribute("aria-label", "跟 AI 說");
    input.addEventListener("input", grow);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
        e.preventDefault();
        send();
      }
    });
    sendBtn = el("button", "wc-send", "送出");
    sendBtn.type = "button";
    sendBtn.addEventListener("click", send);
    bar.appendChild(input);
    bar.appendChild(sendBtn);
    root.appendChild(bar);
    container.appendChild(root);
    api("/api/chat").then(function (r) {
      if (!r.ok) {
        throw new Error("HTTP " + r.status);
      }
      return r.json();
    }).then(function (data) {
      load(data);
      toBottom();
    }).catch(function () {}).then(listen);
  }

  function setPage(path) {
    page = path || null;
  }

  function prefill(text) {
    if (!input) {
      return;
    }
    input.value = text || "";
    grow();
    input.focus();
    var end = input.value.length;
    input.setSelectionRange(end, end);
  }

  window.WillyChat = { mount: mount, setPage: setPage, prefill: prefill, showPending: showPending };
}());
