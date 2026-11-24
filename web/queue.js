// willy 網頁的離線排隊（#74 第 4 份，D-358）：主機沒開時，打勾、改日期、新增、隨手記、傳檔、跟 AI 講的話先存在這台的
// IndexedDB（資料庫 willy、表 queue；檔案本身另放表 files，列清單時不用整個讀出來），她打開網頁、主機連得到時照順序
// 一件一件送（iPhone 沒有背景同步，不靠它）。
// 只管存與送的順序；每件怎麼送（打哪個 API）由 app.js 給的 send(item) 決定。瀏覽器裡是 window.WillyQueue，node 測試 require 進來。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.WillyQueue = factory();
  }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DB = "willy";
  var TABLE = "queue";
  var FILES = "files";
  // 這幾個狀態碼是 Cloudflare 自己回的（通道那頭沒人、連不上這台）：請求沒到主機，留著再送。
  // 524（主機太久沒回）、500 不算：可能已經做了，再送會重複。
  var UNREACHABLE = [502, 503, 504, 521, 522, 523, 530];

  function unreachable(status) {
    return UNREACHABLE.indexOf(status) >= 0;
  }

  /** IndexedDB 的表：all()（照時間排，不含檔案本身）、add(item)（回新的 id；item.data 另存）、file(id)（那件的檔案）、
   *  remove(id)，都回 Promise。 */
  function idbStore(idb) {
    var opened = null;

    function db() {
      if (!opened) {
        opened = new Promise(function (resolve, reject) {
          var req = idb.open(DB, 2);
          req.onupgradeneeded = function () {
            var names = req.result.objectStoreNames;
            if (!names.contains(TABLE)) req.result.createObjectStore(TABLE, { keyPath: "id", autoIncrement: true });
            if (!names.contains(FILES)) req.result.createObjectStore(FILES, { keyPath: "id" });
          };
          req.onsuccess = function () { resolve(req.result); };
          req.onerror = function () { reject(req.error); };
        });
      }
      return opened;
    }

    /** 一個交易做完才 resolve：fn(t) 回一個 request（或 {result}），交易完成時給它的 result。 */
    function run(stores, mode, fn) {
      return db().then(function (d) {
        return new Promise(function (resolve, reject) {
          var t = d.transaction(stores, mode);
          var req = fn(t);
          t.oncomplete = function () { resolve(req ? req.result : undefined); };
          t.onerror = function () { reject(t.error); };
          t.onabort = function () { reject(t.error); };
        });
      });
    }

    return {
      all: function () {
        return run([TABLE], "readonly", function (t) { return t.objectStore(TABLE).getAll(); }).then(sorted);
      },
      add: function (item) {
        return run([TABLE, FILES], "readwrite", function (t) {
          var row = Object.assign({}, item);
          var data = row.data;
          delete row.data;
          var req = t.objectStore(TABLE).add(row);
          if (data !== undefined) {
            req.onsuccess = function () { t.objectStore(FILES).put({ id: req.result, data: data }); };
          }
          return req;
        });
      },
      file: function (id) {
        return run([FILES], "readonly", function (t) { return t.objectStore(FILES).get(id); }).then(function (r) {
          return r ? r.data : undefined;
        });
      },
      remove: function (id) {
        return run([TABLE, FILES], "readwrite", function (t) {
          t.objectStore(FILES).delete(id);
          return t.objectStore(TABLE).delete(id);
        });
      }
    };
  }

  /** 沒有 IndexedDB（或打不開）時的替身：存在記憶體，關掉網頁就沒了。測試也用它。 */
  function memoryStore() {
    var rows = [];
    var files = {};
    var next = 1;
    return {
      all: function () { return Promise.resolve(sorted(rows.slice())); },
      add: function (item) {
        var row = Object.assign({}, item, { id: next++ });
        if (row.data !== undefined) files[row.id] = row.data;
        delete row.data;
        rows.push(row);
        return Promise.resolve(row.id);
      },
      file: function (id) { return Promise.resolve(files[id]); },
      remove: function (id) {
        rows = rows.filter(function (r) { return r.id !== id; });
        delete files[id];
        return Promise.resolve();
      }
    };
  }

  function sorted(items) {
    return (items || []).slice().sort(function (a, b) {
      return (a.t || 0) - (b.t || 0) || (a.id || 0) - (b.id || 0);
    });
  }

  /** 打得開就用 IndexedDB，不然用記憶體。 */
  function open(idb) {
    var source = idb !== undefined ? idb : (typeof indexedDB !== "undefined" ? indexedDB : null);
    if (!source) return memoryStore();
    var store = idbStore(source);
    var fallback = null;
    function safe(name) {
      return function (arg) {
        if (fallback) return fallback[name](arg);
        return store[name](arg).catch(function () {
          fallback = fallback || memoryStore();
          return fallback[name](arg);
        });
      };
    }
    return { all: safe("all"), add: safe("add"), file: safe("file"), remove: safe("remove") };
  }

  /** 一件送出去的結果怎麼算：
   *  "stop"：網路錯誤、Cloudflare 回的連不上（UNREACHABLE）、403（要重新登入）、2xx 但不是 JSON（登入頁）
   *          → 停下，留著下次再送；
   *  "ok"：2xx 而且 ok 不是 false；
   *  "failed"：409（送到時那件已經變了）、其他 4xx、500、524、200 但 ok 是 false → 記下來、拿掉、繼續送下一件
   *          （到了主機、可能做了一半，再送會重複）。 */
  function judge(res) {
    if (!res || typeof res.status !== "number" || res.status === 0 || unreachable(res.status) || res.status === 403) {
      return "stop";
    }
    var json = res.data !== null && typeof res.data === "object";
    if (res.status >= 200 && res.status < 300) {
      if (!json) return "stop";
      return res.data.ok !== false ? "ok" : "failed";
    }
    return "failed";
  }

  /** 照順序一件一件送：send(item) 回 Promise<{status, data}>，網路錯誤就 reject。
   *  回 Promise<{sent, failed: [{item, message}], stopped, left}>。 */
  function flush(store, send) {
    var result = { sent: 0, failed: [], stopped: false, left: 0 };
    return store.all().then(function (items) {
      var chain = Promise.resolve();
      items.forEach(function (item) {
        chain = chain.then(function () {
          if (result.stopped) {
            result.left++;
            return null;
          }
          return Promise.resolve().then(function () { return send(item); }).then(function (res) {
            var how = judge(res);
            if (how === "stop") {
              result.stopped = true;
              result.left++;
              return null;
            }
            if (how === "ok") {
              result.sent++;
            } else {
              var d = (res.data && typeof res.data === "object") ? res.data : {};
              result.failed.push({ item: item, conflict: res.status === 409,
                                   message: String(d.message || d.error || ("沒做成（" + res.status + "）")) });
            }
            return store.remove(item.id);
          }, function () {
            result.stopped = true;
            result.left++;
          });
        });
      });
      return chain.then(function () { return result; });
    });
  }

  /** 送完有沒套上的：送到對話的那一句。全是 409（那時候已經被改過）照 spec 的說法，有別的原因就不說原因、逐件列。 */
  function failedText(failed) {
    var lines = failed.map(function (f) {
      return (f.item && f.item.label ? f.item.label + "：" : "") + f.message;
    });
    var why = failed.every(function (f) { return f.conflict; }) ? "沒套上，因為那時候已經被改過" : "沒送成";
    return "（手機離線時排的 " + failed.length + " 件" + why + "：\n" + lines.join("\n") + "）要怎麼處理？";
  }

  return { open: open, memoryStore: memoryStore, idbStore: idbStore, flush: flush, judge: judge, failedText: failedText,
           sorted: sorted, unreachable: unreachable, UNREACHABLE: UNREACHABLE };
}));
