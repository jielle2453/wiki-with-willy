// willy 網頁的 markdown 轉畫面（#74 第 3 份「網頁首頁與看卡」）：自己寫、不用外部套件。
// 先把整份字 HTML 跳脫，再轉；連結只做三種：站內的頁（#/page/…）、開原檔（交給 app.js 叫 /api/open）、http(s) 新分頁。
// 其他（javascript: 等）只顯示字、不能點。瀏覽器用 window.WillyMd，node 測試用 module.exports。
(function (root) {
  "use strict";

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  var UNESC = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" };
  var OPEN_PREFIX = "obsidian://wiki-with-willy-open?id=";
  var FILE_ID = /^f-[0-9a-z]+$/i;
  var ATTACH = /\.(png|jpe?g|gif|webp|bmp|svg|heic|pdf|docx?|xlsx?|pptx?|txt|csv|zip|mp4|mov|mp3|m4a|wav)$/i;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; });
  }

  function unesc(s) {
    return String(s).replace(/&(amp|lt|gt|quot|#39);/g, function (m, n) { return UNESC[n]; });
  }

  /** 開頭 --- 到 --- 的 key: value；回 {data, start}（內文從第幾行開始）。 */
  function frontMatter(text) {
    var lines = text.split("\n");
    if (!lines.length || lines[0].trim() !== "---") return { data: {}, start: 0 };
    for (var i = 1; i < lines.length; i++) {
      if (lines[i].trim() === "---") {
        var data = {};
        lines.slice(1, i).forEach(function (line) {
          var m = line.match(/^([^\s:#][^:]*):\s*(.*?)\s*$/);
          if (m) data[m[1].trim()] = m[2].replace(/^["']|["']$/g, "");
        });
        return { data: data, start: i + 1 };
      }
    }
    return { data: {}, start: 0 };
  }

  /** dir 與 target 接起來、去掉 . 與 ..；跳出知識庫根目錄回 null。 */
  function joinPath(dir, target) {
    var parts = (dir ? dir.split("/") : []).concat(target.split("/"));
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (p === "" || p === ".") continue;
      if (p === "..") {
        if (!out.length) return null;
        out.pop();
      } else {
        out.push(p);
      }
    }
    return out.length ? out.join("/") : null;
  }

  /** 連結的目標 → {primary, alt}：先照這頁所在的夾，alt 是照知識庫根目錄的寫法（D-541 之前的舊頁）；不是一頁回 null。 */
  function resolve(raw, dir, wiki) {
    var t = String(raw || "").trim();
    if (t.charAt(0) === "<" && t.charAt(t.length - 1) === ">") t = t.slice(1, -1);
    var hash = t.indexOf("#");
    if (hash >= 0) t = t.slice(0, hash);
    try {
      t = decodeURIComponent(t);
    } catch (e) {
      // 不是 URL 編碼的就照原字
    }
    t = t.replace(/\\/g, "/").trim();
    if (!t) return null;
    if (wiki && !/\.md$/i.test(t)) {
      if (ATTACH.test(t)) return null;
      t += ".md";
    }
    if (!/\.md$/i.test(t)) return null;
    var alt = joinPath("", t);
    var primary = t.charAt(0) === "/" ? alt : joinPath(dir || "", t);
    if (!primary) primary = alt;
    if (!primary) return null;
    return { primary: primary, alt: alt && alt !== primary ? alt : null };
  }

  function pageLink(target, text) {
    var html = '<a class="md-page" href="' + esc("#/page/" + encodeURIComponent(target.primary)) + '"';
    if (target.alt) html += ' data-alt="' + esc(target.alt) + '"';
    return html + ">" + text + "</a>";
  }

  function plain(text) {
    return '<span class="md-badlink">' + text + "</span>";
  }

  /** 有協定的網址：http(s) 新分頁；開原檔（obsidian://wiki-with-willy-open?id=f-…，file:/// 用這頁的編號）；其他只顯示字。 */
  function urlLink(raw, text, ctx) {
    var url = String(raw).trim();
    if (url.charAt(0) === "<" && url.charAt(url.length - 1) === ">") url = url.slice(1, -1).trim();
    var bare = url.replace(/[\s\u0000-\u001f\u007f]+/g, "");
    if (/^https?:\/\//i.test(bare) && bare === url) {
      return '<a class="md-ext" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + text + "</a>";
    }
    if (bare.toLowerCase().indexOf(OPEN_PREFIX) === 0) {
      var id = bare.slice(OPEN_PREFIX.length).split(/[&#]/)[0];
      try {
        id = decodeURIComponent(id);
      } catch (e) {
        id = "";
      }
      return FILE_ID.test(id) ? openLink(id, text) : plain(text);
    }
    if (/^file:/i.test(bare)) {
      return ctx.fileId && FILE_ID.test(ctx.fileId) ? openLink(ctx.fileId, text) : plain(text);
    }
    return null;
  }

  function openLink(id, text) {
    return '<a class="md-open" href="#" data-id="' + esc(id) + '">' + text + "</a>";
  }

  function hasScheme(s) {
    return /^[a-z][a-z0-9+.-]*:/i.test(String(s).replace(/[\s\u0000-\u001f\u007f]+/g, ""));
  }

  /** [[目標|字]]、[[目標]]（inner 是跳脫過的字）。 */
  function wikiLink(inner, ctx) {
    var raw = unesc(inner).replace(/\\\|/g, "|");
    var bar = raw.indexOf("|");
    var target = bar >= 0 ? raw.slice(0, bar) : raw;
    var shown = bar >= 0 ? raw.slice(bar + 1) : target.replace(/#.*$/, "").replace(/\.md$/i, "");
    var text = esc(shown.trim() || target.trim());
    if (hasScheme(target)) return urlLink(target, text, ctx) || plain(text);
    var t = resolve(target, ctx.dir, true);
    return t ? pageLink(t, text) : plain(text);
  }

  /** [字](目標)（兩個都是跳脫過的字）。 */
  function mdLink(textEsc, targetEsc, ctx) {
    var target = unesc(targetEsc).trim().replace(/\s+"[^"]*"$/, "");
    var text = textEsc.trim() || esc(target);
    if (hasScheme(target)) return urlLink(target, text, ctx) || plain(text);
    var t = resolve(target, ctx.dir, false);
    return t ? pageLink(t, text) : plain(text);
  }

  /** 一行裡的格式（s 是跳脫過的字）：碼、連結先收起來，再做粗體、斜體，最後放回去。 */
  function inline(s, ctx) {
    var slots = [];
    function keep(html) {
      slots.push(html);
      return "\u0000" + (slots.length - 1) + "\u0000";
    }
    s = s.replace(/`([^`\n]+)`/g, function (m, c) { return keep("<code>" + c + "</code>"); });
    s = s.replace(/!?\[\[([^\]\n]+?)\]\]/g, function (m, inner) { return keep(wikiLink(inner, ctx)); });
    s = s.replace(/!?\[([^\]\n]*)\]\(([^)\n]*)\)/g, function (m, text, target) { return keep(mdLink(text, target, ctx)); });
    s = s.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\s](?:[^*\n]*?[^*\s])?)\*(?![*\w])/g, "$1<em>$2</em>");
    return s.replace(/\u0000(\d+)\u0000/g, function (m, i) { return slots[+i]; });
  }

  function width(spaces) {
    return spaces.replace(/\t/g, "    ").length;
  }

  var LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

  function listItem(text, ctx) {
    var task = text.match(/^\[( |x|X)\]\s+([\s\S]*)$/);
    var body = task ? task[2] : text;
    var html = body.split("\n").map(function (l) { return inline(l, ctx); }).join("<br>");
    if (task) html = '<input type="checkbox" disabled' + (task[1] === " " ? "" : " checked") + "> " + html;
    return html;
  }

  function list(lines, i, ctx) {
    var items = [];
    while (i < lines.length) {
      var m = lines[i].match(LIST_ITEM);
      if (m) {
        items.push({ indent: width(m[1]), ordered: /\d/.test(m[2]), text: m[3] });
      } else if (items.length && lines[i].trim() && /^\s+/.test(lines[i])) {
        items[items.length - 1].text += "\n" + lines[i].trim();
      } else {
        break;
      }
      i++;
    }
    var html = "";
    var stack = [];
    items.forEach(function (it) {
      while (stack.length && it.indent < stack[stack.length - 1].indent) html += "</li></" + stack.pop().tag + ">";
      var top = stack[stack.length - 1];
      if (!top || it.indent > top.indent) {
        var tag = it.ordered ? "ol" : "ul";
        stack.push({ indent: it.indent, tag: tag });
        html += "<" + tag + ">";
      } else {
        html += "</li>";
      }
      html += "<li>" + listItem(it.text, ctx);
    });
    while (stack.length) html += "</li></" + stack.pop().tag + ">";
    return { html: html, next: i };
  }

  /** 表格的一列切成格：[[a|b]] 與 \| 裡的直線不切。 */
  function cells(line) {
    var s = line.trim();
    if (s.charAt(0) === "|") s = s.slice(1);
    if (s.charAt(s.length - 1) === "|" && s.charAt(s.length - 2) !== "\\") s = s.slice(0, -1);
    var out = [];
    var cur = "";
    var depth = 0;
    for (var i = 0; i < s.length; i++) {
      var c = s.charAt(i);
      if (c === "\\" && s.charAt(i + 1) === "|") {
        cur += "\\|";
        i++;
        continue;
      }
      if (c === "[" && s.charAt(i + 1) === "[") depth++;
      if (c === "]" && s.charAt(i + 1) === "]" && depth) depth--;
      if (c === "|" && !depth) {
        out.push(cur.trim());
        cur = "";
        continue;
      }
      cur += c;
    }
    out.push(cur.trim());
    return out;
  }

  function isSep(line) {
    return line.indexOf("|") >= 0 && line.indexOf("-") >= 0 && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line);
  }

  function cellHtml(c, ctx) {
    return inline(c.replace(/\\\|/g, "|"), ctx);
  }

  function blocks(lines, ctx) {
    var out = [];
    var para = [];
    function flush() {
      if (para.length) out.push("<p>" + para.map(function (l) { return inline(l.trim(), ctx); }).join("<br>") + "</p>");
      para = [];
    }
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      var fence = line.match(/^\s*(`{3,}|~{3,})/);
      if (fence) {
        flush();
        var buf = [];
        i++;
        while (i < lines.length && lines[i].trim().indexOf(fence[1]) !== 0) buf.push(lines[i++]);
        i++;
        out.push("<pre><code>" + buf.join("\n") + "</code></pre>");
        continue;
      }
      if (!line.trim()) {
        flush();
        i++;
        continue;
      }
      var h = line.match(/^(#{1,6})\s+(.*?)\s*$/);
      if (h) {
        flush();
        var n = Math.min(h[1].length, 4);
        out.push("<h" + n + ">" + inline(h[2], ctx) + "</h" + n + ">");
        i++;
        continue;
      }
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) {
        flush();
        out.push("<hr>");
        i++;
        continue;
      }
      if (/^\s*&gt;/.test(line)) {
        flush();
        var q = [];
        while (i < lines.length && /^\s*&gt;/.test(lines[i])) q.push(lines[i++].replace(/^\s*&gt;\s?/, ""));
        out.push("<blockquote>" + blocks(q, ctx) + "</blockquote>");
        continue;
      }
      if (line.indexOf("|") >= 0 && i + 1 < lines.length && isSep(lines[i + 1])) {
        flush();
        var head = cells(line);
        i += 2;
        var rows = [];
        while (i < lines.length && lines[i].trim() && lines[i].indexOf("|") >= 0) rows.push(cells(lines[i++]));
        var t = "<table><thead><tr>" + head.map(function (c) { return "<th>" + cellHtml(c, ctx) + "</th>"; }).join("") + "</tr></thead><tbody>";
        rows.forEach(function (r) {
          t += "<tr>" + head.map(function (_h, k) { return "<td>" + cellHtml(r[k] || "", ctx) + "</td>"; }).join("") + "</tr>";
        });
        out.push(t + "</tbody></table>");
        continue;
      }
      if (LIST_ITEM.test(line)) {
        flush();
        var got = list(lines, i, ctx);
        out.push(got.html);
        i = got.next;
        continue;
      }
      para.push(line);
      i++;
    }
    flush();
    return out.join("\n");
  }

  /**
   * markdown → {html, front}。opts.dir：這頁所在的夾（知識庫根目錄起算，用來解析相對連結）；
   * opts.fileId：這頁的檔案編號（f-…），file:/// 的開原檔連結用它。
   */
  function render(text, opts) {
    var o = opts || {};
    var src = String(text == null ? "" : text).replace(/\u0000/g, "").replace(/\r\n?/g, "\n").replace(/^﻿/, "");
    var fm = frontMatter(src);
    var body = src.split("\n").slice(fm.start).join("\n");
    var ctx = { dir: o.dir || "", fileId: o.fileId || "" };
    return { html: blocks(esc(body).split("\n"), ctx), front: fm.data };
  }

  var api = { render: render, resolve: resolve, frontMatter: frontMatter, escapeHtml: esc };
  root.WillyMd = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
}(typeof window !== "undefined" ? window : globalThis));
