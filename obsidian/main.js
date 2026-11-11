'use strict';
/*
 * wiki-with-willy 的 Obsidian 外掛（D-252～D-267，裁定 37，issue #22）。
 *
 * 純 CommonJS、不打包、不加依賴。要算的都在 kb（kb home / kb find / kb renders apply …），這裡只負責：
 * 找 kb、叫 kb（一律帶 --via obsidian）、把 kb 給的 JSON 畫出來、把四個動作變成 kb 指令（和反悔的相反指令；D-266）。
 * 要測的純函式都從 module.exports 的 `lib` 匯出（tests/obsidian/*.test.js 用假的 obsidian 模組載入）。
 * 不把任何路徑寫進設定（D-019）：每次載入照 installed_plugins.json 找 kb.py。
 * 她打開綜合卡（檔頭編號是 e-）時叫 kb entity panel，把「日程」接在卡的最後（閱讀與即時預覽各一份），不寫進卡檔（D-340）。
 * 帳（帳頁、首頁的帳、庫存不足、公司還欠你）#65 起不畫；舊版 kb 給了 stock、owed、links、money 也忽略。
 */

const obsidian = require('obsidian');
const fs = require('fs');
const os = require('os');
const path = require('path');
const childProcess = require('child_process');

const { Plugin, ItemView, Modal, Notice, MarkdownView } = obsidian;

// ---------------------------------------------------------------------------------------------------------------
// 常數
// ---------------------------------------------------------------------------------------------------------------

const PLUGIN_ID = 'wiki-with-willy';
const VIEW_TYPE = 'wiki-with-willy-home';
const WATCH_FILES = ['agenda.json', 'pending.json', 'entities.json', 'inventory.json', 'profile.json'];
const DEBOUNCE_MS = 300;
// 多人共用 3（D-293）：git pull 會改 .git 底下這幾個檔；變了等 2 秒（去抖）叫 kb refresh。不靠 Obsidian 自己的通知。
const GIT_WATCH_FILES = ['HEAD', 'ORIG_HEAD', 'FETCH_HEAD'];
const GIT_DEBOUNCE_MS = 2000;
const GIT_NOTICE_TEXT = '同步有衝突，開 Claude 說一句話就會收拾';

/** kb refresh --json 的結果要不要在首頁頂端提示：有 conflicts 或 rebase 到一半。 */
function gitRefreshNotice(j) {
  if (!j || typeof j !== 'object') return null;
  const conflicts = Array.isArray(j.conflicts) ? j.conflicts.length : 0;
  return conflicts > 0 || j.rebase === true ? GIT_NOTICE_TEXT : null;
}

function isGitWatchFile(name) {
  return GIT_WATCH_FILES.includes(String(name || ''));
}
const OPENED_MAX = 30;
const KB_TIMEOUT_MS = 120000;
const LANE_HEIGHT = 26;
const KIND_LABEL = { deadline: '到期', meeting: '會議', promise: '承諾', reminder: '提醒', period: '期間' };
const KNOWN_KINDS = Object.keys(KIND_LABEL);
const KIND_BY_LABEL = { 到期: 'deadline', 會議: 'meeting', 承諾: 'promise', 提醒: 'reminder', 期間: 'period' };
const KNOWN_SHAPES = ['bar', 'point'];
const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六'];
const MANAGED_PAGES = ['系統/日程.md', '系統/待處理.md', '系統/裁示.md'];
const PANEL_CLS = 'wwh-panel';
const PANEL_TITLE = '日程';
const PANEL_CARD_ATTR = 'data-wwh-card'; // 那段是哪張卡的：分頁換了檔，不是它的就拿掉
const PANEL_REDRAW_MS = 50; // 切閱讀模式／即時預覽後，等 Obsidian 畫好那一邊再接
const GENERATED_BANNER = '這頁由程式產生；要改請在首頁改，或直接跟 Claude 說';
const NO_KB_TEXT = '找不到 wiki-with-willy：先在 Claude 裡打開這個知識庫一次';
const VERSION_MISMATCH_TEXT = '首頁跟 wiki-with-willy 版本不同，先在 Claude 裡打開這個知識庫一次';
const RELOAD_TEXT = '首頁更新了，按這裡重新載入';
const COPIED_TEXT = '已複製，貼到 Claude 的對話裡';
const NEED_CLAUDE_TEXT = '到對話跟 Claude 說';
const CARRIED_TEXT = '你剛在這頁改的我留在最下面『你加的』';
const UNRECOGNIZED_TEXT = '這頁你改的我沒認出來，記在待處理了';
const UPDATED_TEXT = '這頁剛被更新了';
const CARD_REF_RE = /〔(f-[0-9a-f]{4,})〕/g;

// ---------------------------------------------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------------------------------------------

/** setTimeout，但不拖住 node（測試用）；Obsidian 裡回的是數字，沒有 unref。 */
function softTimeout(fn, ms) {
  const t = setTimeout(fn, ms);
  if (t && typeof t.unref === 'function') t.unref();
  return t;
}

function firstLine(text) {
  for (const line of String(text == null ? '' : text).split(/\r?\n/)) {
    const t = line.trim();
    if (t) return t;
  }
  return '';
}

function asArray(v) {
  return Array.isArray(v) ? v : [];
}

function isObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function parseJson(text) {
  const t = String(text == null ? '' : text).trim();
  if (!t) return null;
  try {
    return JSON.parse(t);
  } catch (e) {
    // 前面若有別的輸出，取最後一行像 JSON 的
    const lines = t.split(/\r?\n/).reverse();
    for (const line of lines) {
      const s = line.trim();
      if (s.startsWith('{') || s.startsWith('[')) {
        try {
          return JSON.parse(s);
        } catch (e2) {
          // 繼續找
        }
      }
    }
  }
  return null;
}

function samePath(a, b, platform) {
  const plat = platform || process.platform;
  const norm = (p) => {
    let s = String(p == null ? '' : p).replace(/\\/g, '/').replace(/\/+$/, '');
    if (plat === 'win32' || plat === 'darwin') s = s.toLowerCase();
    return s;
  };
  return norm(a) !== '' && norm(a) === norm(b);
}

function isNetworkPath(p) {
  const s = String(p == null ? '' : p);
  if (s.startsWith('\\\\?\\')) return false; // 本機長路徑前綴
  return s.startsWith('\\\\') || /^\/\/[^/]/.test(s);
}

function nativePath(p, platform) {
  const plat = platform || process.platform;
  const s = String(p == null ? '' : p);
  if (plat === 'win32' && !isNetworkPath(s)) return s.replace(/\//g, '\\');
  return s;
}

// ---------------------------------------------------------------------------------------------------------------
// 日期與時間軸的幾何（kb 算好什麼時候、哪一列；這裡只換成畫面上的位置）
// ---------------------------------------------------------------------------------------------------------------

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})/;

function dayNumber(s) {
  const m = DATE_RE.exec(String(s == null ? '' : s));
  if (!m) return null;
  const n = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(n) ? null : Math.round(n / 86400000);
}

function dayToIso(n) {
  return new Date(n * 86400000).toISOString().slice(0, 10);
}

function weekdayOf(n) {
  return new Date(n * 86400000).getUTCDay();
}

function shortDate(iso) {
  const n = dayNumber(iso);
  if (n === null) return '';
  const d = new Date(n * 86400000);
  return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
}

function formatWhen(item) {
  const date = itemDate(item);
  const n = dayNumber(date);
  if (n === null) return '';
  let text = `${shortDate(date)}（週${WEEKDAY[weekdayOf(n)]}）`;
  if (item.time) text += ` ${item.time}`;
  return text;
}

function itemDate(m) {
  return (m && (m.end || m.date || m.ended || m.start)) || '';
}

/** 期間：時間軸的 mark 是英文 period，kb lookup 的日程是中文「期間」。 */
function isPeriodKind(kind) {
  return kind === 'period' || kind === '期間';
}

function kindLabel(kind) {
  return KIND_LABEL[kind] || KIND_LABEL[KIND_BY_LABEL[kind]] || String(kind || '');
}

/** 一筆 mark 在時間軸上的起訖（天數）；不認得的 shape／kind、日期壞掉回 null（不畫那筆、不報錯）。 */
function markSpan(m) {
  if (!isObject(m)) return null;
  if (m.shape !== undefined && m.shape !== null && !KNOWN_SHAPES.includes(m.shape)) return null;
  if (m.kind !== undefined && m.kind !== null && !KNOWN_KINDS.includes(m.kind)) return null;
  const s = dayNumber(m.start);
  const e = dayNumber(m.end || m.date || m.ended);
  const shape = m.shape || (s !== null && e !== null && s !== e ? 'bar' : 'point');
  if (s === null && e === null) return null;
  if (shape === 'point') {
    const at = e !== null ? e : s;
    return { shape, first: at, last: at };
  }
  const a = s !== null ? s : e;
  const b = e !== null ? e : s;
  return { shape, first: Math.min(a, b), last: Math.max(a, b) };
}

function baselineSpan(b) {
  if (!b) return null;
  if (typeof b === 'string') {
    const n = dayNumber(b);
    return n === null ? null : { first: n, last: n };
  }
  if (!isObject(b)) return null;
  const s = dayNumber(b.start);
  const e = dayNumber(b.end || b.date);
  if (s === null && e === null) return null;
  const a = s !== null ? s : e;
  const z = e !== null ? e : s;
  return { first: Math.min(a, z), last: Math.max(a, z) };
}

/**
 * 時間軸資料 → 每一列的 bar／點的位置（百分比）與 lane（同一列疊幾層）。
 * 回 {fromN, toN, spanDays, ticks, todayPct, rows:[{key,label,type,progress,lanes,marks,pauses,links}]}。
 * 不認得的 shape／kind、壞日期的那筆不畫；children 縮排畫在母項下面；links 回兩端的位置；baseline 回原定的位置。
 */
function layoutTimeline(timeline, opts) {
  const options = opts || {};
  const tl = isObject(timeline) ? timeline : {};
  const rowsIn = asArray(tl.rows).filter(isObject);
  let fromN = dayNumber(tl.from);
  let toN = dayNumber(tl.to);
  const todayN = dayNumber(options.today);
  if (fromN === null || toN === null || toN < fromN) {
    const all = [];
    for (const r of rowsIn) {
      for (const m of asArray(r.marks)) {
        const sp = markSpan(m);
        if (sp) all.push(sp.first, sp.last);
      }
    }
    const base = todayN !== null ? todayN : Math.floor(Date.now() / 86400000);
    all.push(base - 7, base + 60);
    fromN = Math.min(...all);
    toN = Math.max(...all);
  }
  const span = toN - fromN + 1;
  const visible = Number(options.visibleDays) > 0 ? Number(options.visibleDays) : span; // 畫面一次看幾天（裁定 34）；沒給就是整段
  const pct = (n) => ((n - fromN) / span) * 100;
  const minWidth = Math.max(0.5, 100 / span);

  function geom(sp) {
    const lo = Math.max(sp.first, fromN);
    const hi = Math.min(sp.last, toN);
    if (hi < lo) return null;
    const left = pct(lo);
    const width = Math.max(pct(hi + 1) - left, minWidth);
    return {
      leftPct: left,
      widthPct: Math.min(width, 100 - left),
      clippedLeft: sp.first < fromN,
      clippedRight: sp.last > toN,
    };
  }

  function drawMark(m, depth) {
    const sp = markSpan(m);
    if (!sp) return null;
    const g = geom(sp);
    if (!g) return null;
    const out = {
      id: m.id || null,
      shape: sp.shape,
      kind: m.kind || null,
      text: m.text || '',
      status: m.status || 'open',
      late: m.late === true,
      inferred: m.inferred === true,
      critical: m.critical === true,
      hard: m.hard === true,
      derived: m.derived === true,
      slack: m.slack === undefined ? null : m.slack,
      time: m.time || null,
      start: m.start || null,
      end: m.end || m.date || null,
      repeat: m.repeat || null,
      entities: asArray(m.entities),
      depth,
      lane: 0,
      leftPct: g.leftPct,
      widthPct: sp.shape === 'point' ? 0 : g.widthPct,
      clippedLeft: g.clippedLeft,
      clippedRight: g.clippedRight,
      baseline: null,
      raw: m,
    };
    if (sp.shape === 'point') {
      out.leftPct = pct(Math.max(sp.first, fromN)) + 0.5 * (100 / span);
    }
    const bl = baselineSpan(m.baseline);
    if (bl) {
      const bg = geom(bl);
      if (bg) out.baseline = { leftPct: bg.leftPct, widthPct: bg.widthPct };
    }
    return out;
  }

  const rows = [];
  for (const r of rowsIn) {
    const tops = [];
    for (const m of asArray(r.marks)) {
      const d = drawMark(m, 0);
      if (!d) continue;
      const kids = [];
      for (const c of asArray(m.children)) {
        const cd = drawMark(c, 1);
        if (cd) kids.push(cd);
      }
      tops.push({ mark: d, kids, sort: markSpan(m).first });
    }
    tops.sort((a, b) => a.sort - b.sort);
    const lanes = []; // 每層最後佔到的位置（百分比）；exclusive 的層不再放別人
    const placed = [];
    const labelReserve = 14 * Math.min(1, visible / span); // 點的字大約佔的寬度（百分比，整段的）
    for (const t of tops) {
      const m = t.mark;
      const need = m.shape === 'point' ? m.leftPct + labelReserve : m.leftPct + m.widthPct;
      if (t.kids.length === 0) {
        let lane = lanes.findIndex((l) => !l.exclusive && l.end <= m.leftPct - 0.2);
        if (lane < 0) {
          lanes.push({ end: 0, exclusive: false });
          lane = lanes.length - 1;
        }
        lanes[lane].end = need;
        m.lane = lane;
        placed.push(m);
      } else {
        lanes.push({ end: need, exclusive: true });
        m.lane = lanes.length - 1;
        placed.push(m);
        for (const k of t.kids) {
          lanes.push({ end: 100, exclusive: true });
          k.lane = lanes.length - 1;
          k.parent = m.id;
          placed.push(k);
        }
      }
    }
    const byId = {};
    for (const m of placed) if (m.id) byId[m.id] = m;
    const links = [];
    for (const l of asArray(r.links)) {
      if (!isObject(l)) continue;
      const a = byId[l.from];
      const b = byId[l.to];
      if (!a || !b) continue;
      links.push({
        from: l.from,
        to: l.to,
        gap: l.gap === undefined ? null : l.gap,
        critical: l.critical === true,
        x1: a.shape === 'point' ? a.leftPct : a.leftPct + a.widthPct,
        y1: a.lane,
        x2: b.leftPct,
        y2: b.lane,
      });
    }
    const pauses = [];
    for (const p of asArray(r.pauses)) {
      if (!isObject(p)) continue;
      const s = dayNumber(p.start);
      const e = dayNumber(p.end);
      if (s === null && e === null) continue;
      const g = geom({ first: s !== null ? s : e, last: e !== null ? e : s });
      if (g) pauses.push({ leftPct: g.leftPct, widthPct: g.widthPct });
    }
    rows.push({
      key: r.key || r.label || '',
      label: r.label || r.key || '',
      type: r.type || null,
      progress: r.progress || null,
      lanes: Math.max(1, lanes.length),
      marks: placed,
      pauses,
      links,
    });
  }

  // 軸上的刻度：每個月 1 號；畫面看兩週以內每天一格，其他每週一
  const ticks = [];
  const everyDay = visible <= 16;
  for (let n = fromN; n <= toN; n++) {
    const d = new Date(n * 86400000);
    if (d.getUTCDate() === 1) {
      const label = d.getUTCMonth() === 0 || n === fromN ? `${d.getUTCFullYear()}/${d.getUTCMonth() + 1}月` : `${d.getUTCMonth() + 1}月`;
      ticks.push({ date: dayToIso(n), pct: pct(n), label, kind: 'month' });
      if (everyDay) ticks.push({ date: dayToIso(n), pct: pct(n), label: '1', kind: 'day' });
    } else if (everyDay) {
      ticks.push({ date: dayToIso(n), pct: pct(n), label: String(d.getUTCDate()), kind: 'day' });
    } else if (d.getUTCDay() === 1 && span <= 800) {
      ticks.push({ date: dayToIso(n), pct: pct(n), label: String(d.getUTCDate()), kind: 'week' });
    }
  }
  const todayPct = todayN !== null && todayN >= fromN && todayN <= toN ? pct(todayN) + 0.5 * (100 / span) : null;
  return { fromN, toN, from: dayToIso(fromN), to: dayToIso(toN), spanDays: span, visibleDays: visible, slidePct: (span / visible) * 100, ticks, todayPct, rows };
}

// ---- 時間軸縮放與左右捲動（裁定 34、37）----
const MIN_ZOOM = 14; // Ctrl＋滾輪連續縮放，一次看 14～365 天（兩週～一年）
const MAX_ZOOM = 365;
const DEFAULT_ZOOM = 90;
const TIMELINE_LIMIT_DAYS = 730; // 往前往後最多載到今天的兩年

/** 畫面一次看幾天：14～365 之間（縮放途中可以是小數）；不是數字就用預設的三個月。 */
function zoomOf(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_ZOOM;
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, n));
}

/** data.json 裡存的天數：14～365 的整數才算，壞的回 null（照預設）。 */
function savedZoom(v) {
  return Number.isInteger(v) && v >= MIN_ZOOM && v <= MAX_ZOOM ? v : null;
}

/** Ctrl＋滾輪一下要乘多少：往上滾（deltaY 負）= 拉近 = 天數變少；觸控板拉近拉遠一下的量小，乘得也小。 */
function wheelZoomFactor(e) {
  if (!e) return 1;
  const unit = e.deltaMode === 1 ? 16 : 1;
  const dy = Math.max(-400, Math.min(400, (Number(e.deltaY) || 0) * unit));
  return Math.exp(dy * 0.002);
}

/** 以游標那天為中心縮放：frac 是游標在時間格裡的位置（0～1）；回新的 {start, visible}，游標底下還是同一天。 */
function zoomAround(start, visible, frac, factor) {
  const next = zoomOf(visible * factor);
  const f = Math.max(0, Math.min(1, Number(frac)));
  const day = start + f * visible;
  return { start: day - f * next, visible: next };
}

/** 沒捲過的時候，畫面最左邊是哪一天：今天前面留一點點（約可見天數的一成）。 */
function defaultTimelineStart(todayN, visible) {
  return todayN - Math.round(visible * 0.1);
}

/** 畫面最左邊那天不能捲出已載入的範圍；範圍比畫面窄就貼著起點。 */
function clampTimelineStart(start, visible, fromN, toN) {
  const max = Math.max(fromN, toN + 1 - visible);
  return Math.max(fromN, Math.min(start, max));
}

/** 捲到快出已載入的範圍（離邊不到半個畫面）時，要再載哪一段：回 {from, to}（YYYY-MM-DD）；不用載回 null。
 * 沒捲過（explicit 為 false）不看左邊；最多載到今天前後兩年，載不下去了也回 null。 */
function wantedTimelineRange(start, visible, fromN, toN, todayN, explicit) {
  const margin = visible * 0.5;
  const needLeft = explicit && start - fromN < margin;
  const needRight = toN + 1 - (start + visible) < margin;
  if (!needLeft && !needRight) return null;
  const lo = todayN - TIMELINE_LIMIT_DAYS;
  const hi = todayN + TIMELINE_LIMIT_DAYS;
  const nf = needLeft ? Math.max(lo, Math.min(fromN, Math.floor(start - visible))) : fromN;
  const nt = needRight ? Math.min(hi, Math.max(toN, Math.ceil(start + visible * 2))) : toN;
  if (nf === fromN && nt === toN) return null;
  return { from: dayToIso(nf), to: dayToIso(nt) };
}

/** 第一次叫 kb home 就帶的範圍：看一個月以內的用 kb 預設；看三個月、半年的先多載一些，免得一打開就要再叫一次。 */
function initialTimelineRange(zoom, todayIso) {
  const n = dayNumber(todayIso);
  if (zoomOf(zoom) <= 30 || n === null) return null;
  return { from: dayToIso(n - 7), to: dayToIso(n + Math.round(zoomOf(zoom) * 1.5)) };
}

/** 滾輪橫著捲的量（像素）：觸控板左右（deltaX 大過 deltaY），或按著 Shift 滾（deltaY）；直著滾回 0，交給頁面。 */
function horizontalWheel(e) {
  if (!e) return 0;
  const unit = e.deltaMode === 1 ? 16 : 1;
  const dx = (Number(e.deltaX) || 0) * unit;
  const dy = (Number(e.deltaY) || 0) * unit;
  if (Math.abs(dx) > Math.abs(dy)) return dx;
  if (e.shiftKey && dy) return dy;
  return 0;
}

/** 按住拖背景捲：在右邊的時間格上、不是記號才算；左欄、記號本身照舊是點。 */
function dragStartsHere(target) {
  let inTrack = false;
  for (let el = target; el; el = el.parentNode) {
    const c = el.classList;
    if (!c) continue;
    if (c.contains('wwh-gantt')) break;
    if (c.contains('wwh-mark') || c.contains('wwh-sum-dot') || c.contains('wwh-sum-bar') || c.contains('wwh-cell-left')) return false;
    if (c.contains('wwh-cell-track') || c.contains('wwh-axis-track') || c.contains('wwh-slide')) inTrack = true;
  }
  return inTrack;
}

/** 軸頭釘在左邊的月份：畫面最左邊那天所在的月（那天剛好是 1 號，或下個月 1 號就在左邊不遠處，就不用釘）。 */
function pinnedMonth(startN, visible) {
  const n = Math.floor(startN);
  const d = new Date(n * 86400000);
  const next = Math.round(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) / 86400000);
  const hide = d.getUTCDate() === 1 || next - startN < visible * 0.12;
  return { label: `${d.getUTCFullYear()}/${d.getUTCMonth() + 1}月`, hide };
}

/**
 * 月曆：回 {year, month, weeks:[{days:[{date, day, inMonth, isToday, marks}]×7, bars:[…], lanes}]}。
 * 期間（不只一天的）畫成橫跨那一週的一條 bar（col 0~6 起、span 天）：每一週那段都寫名字（寫在開頭、太長截斷，裁定 31），
 * 跨週的下一週接著畫；顏色照 timelineColors（同一列、同一個實體同色，單天的事也有 color，0~7 或 null，裁定 37）；單天的點進 days[].marks 佔格子。做完、取消的不畫（裁定 30）。
 */
function layoutMonth(timeline, opts) {
  const options = opts || {};
  const todayN = dayNumber(options.today);
  const base = todayN !== null ? new Date(todayN * 86400000) : new Date();
  const year = options.year !== undefined ? options.year : base.getUTCFullYear();
  const month = options.month !== undefined ? options.month : base.getUTCMonth(); // 0~11
  const first = Math.round(Date.UTC(year, month, 1) / 86400000);
  const lastDay = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
  const startN = first - weekdayOf(first); // 星期日起算
  const endN = first + lastDay - 1;
  const gridEnd = endN + (6 - weekdayOf(endN));
  const open = openMarksOnly(timeline);
  const colorOf = timelineColors(open).of;
  const flat = [];
  for (const r of asArray(open.rows)) {
    const stack = [];
    for (const m of asArray(r.marks)) {
      stack.push(m);
      for (const c of asArray(m.children)) stack.push(c);
    }
    for (const m of stack) {
      const sp = markSpan(m);
      if (sp) flat.push({ m, sp, row: r.label || r.key || '', rowKey: r.key || r.label || '' });
    }
  }
  const info = (f) => ({
    id: f.m.id || null,
    text: f.m.text || '',
    kind: f.m.kind || null,
    row: f.row,
    rowKey: f.rowKey,
    color: colorOf(f.rowKey, f.m), // 0～7 或 null（淡灰），時間軸同一列同色（裁定 37）
    time: f.m.time || null,
    label: f.m.time ? `${f.m.time} ${f.m.text || ''}` : f.m.text || '', // 有時間的寫「14:00 丈量」
    late: f.m.late === true,
    inferred: f.m.inferred === true,
    status: f.m.status || 'open',
    raw: f.m,
  });
  const weeks = [];
  for (let n = startN; n <= gridEnd; n += 7) {
    const days = [];
    for (let i = 0; i < 7; i++) {
      const day = n + i;
      days.push({
        date: dayToIso(day),
        day: new Date(day * 86400000).getUTCDate(),
        inMonth: day >= first && day <= endN,
        isToday: todayN === day,
        marks: flat.filter((f) => f.sp.first === day && f.sp.last === day).map(info),
        // 這天所有的事（含跨天的期間），有時間的照時間排在前；點日期跳出來的小框列這個
        all: flat
          .filter((f) => f.sp.first <= day && f.sp.last >= day)
          .map(info)
          .sort((a, b) => (a.time || '99:99') < (b.time || '99:99') ? -1 : (a.time || '99:99') > (b.time || '99:99') ? 1 : 0),
      });
    }
    const bars = flat
      .filter((f) => f.sp.last > f.sp.first && f.sp.first <= n + 6 && f.sp.last >= n)
      .map((f) => {
        const col = Math.max(f.sp.first, n) - n;
        const end = Math.min(f.sp.last, n + 6) - n;
        return Object.assign(info(f), {
          col,
          span: end - col + 1,
          continuesLeft: f.sp.first < n,
          continuesRight: f.sp.last > n + 6,
        });
      })
      .sort((a, b) => a.col - b.col || b.span - a.span || (a.text < b.text ? -1 : a.text > b.text ? 1 : 0));
    const laneEnds = [];
    for (const b of bars) {
      let lane = laneEnds.findIndex((e) => e < b.col);
      if (lane < 0) lane = laneEnds.length;
      laneEnds[lane] = b.col + b.span - 1;
      b.lane = lane;
    }
    weeks.push({ days, bars, lanes: laneEnds.length });
  }
  return { year, month, weeks };
}

function shiftMonth(year, month, delta) {
  const d = new Date(Date.UTC(year, month + delta, 1));
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() };
}

function describeMark(m) {
  const kind = kindLabel(m.kind);
  let when = '';
  if (m.start && m.end && m.start !== m.end) when = `${m.start}～${m.end}`;
  else when = m.end || m.start || '';
  const bits = [`${kind ? kind + ' ' : ''}${m.text || ''}`.trim()];
  if (when) bits.push(`${when}${m.time ? ' ' + m.time : ''}`);
  if (m.late) bits.push('已經晚了');
  if (m.inferred) bits.push('推測的，還沒確認');
  if (m.status === 'done') bits.push('已完成');
  if (m.critical) bits.push('要徑');
  return bits.join('\n');
}

// ---------------------------------------------------------------------------------------------------------------
// kb home 的資料整理
// ---------------------------------------------------------------------------------------------------------------

function normalizeHome(raw) {
  const r = isObject(raw) ? raw : {};
  const todo = isObject(r.todo) ? r.todo : {};
  const counts = isObject(r.counts) ? r.counts : {};
  return {
    schema: r.schema || null,
    version: r.version || null,
    today: r.today || null,
    cheer: typeof r.cheer === 'string' ? r.cheer : '',
    counts: {
      overdue: Number(counts.overdue) || 0,
      today: Number(counts.today) || 0,
      week: Number(counts.week) || 0,
      confirm: Number(counts.confirm) || 0,
      other: Number(counts.other) || 0,
      claude: Number(counts.claude) || 0,
    },
    timeline: isObject(r.timeline) ? r.timeline : { rows: [] },
    todo: {
      overdue: asArray(todo.overdue),
      today: asArray(todo.today),
      week: asArray(todo.week),
    },
    confirm: asArray(r.confirm).filter(isObject),
    claude: asArray(r.claude).filter(isObject), // 要跟 Claude 一起看的題（裁定 39）：{key, kind, text}
    others: asArray(r.others).filter(isObject),
    recent: asArray(r.recent).filter(isObject),
  };
}

function todoGroups(home) {
  const t = home && home.todo ? home.todo : {};
  const out = [
    { key: 'overdue', label: '已經過了', items: asArray(t.overdue), defaultCollapsed: false },
    { key: 'today', label: '今天', items: asArray(t.today), defaultCollapsed: false },
    { key: 'week', label: '這週', items: asArray(t.week), defaultCollapsed: true },
  ];
  return out;
}

/** 頁上的那一行（kb 給的 match）：整行一樣的先、再找包含的、再找去掉「已過 N 天」的前段；找不到回 0（頁頂）。 */
function findPageLine(content, match) {
  const lines = String(content == null ? '' : content).split('\n').map((l) => l.replace(/\r$/, ''));
  const want = String(match || '').trim();
  if (!want) return 0;
  let at = lines.findIndex((l) => l.trim() === want);
  if (at < 0) at = lines.findIndex((l) => l.includes(want));
  if (at < 0) {
    const head = want.replace(/\s*已過 \d+ 天\s*$/, '');
    at = lines.findIndex((l) => l.includes(head));
  }
  return at < 0 ? 0 : at;
}

/** kb entity panel 的結果 → 要畫的（D-340）；沒有日程的回 null（不畫）。舊版 kb 給的 money、stock 不畫。 */
function panelModel(j) {
  if (!isObject(j)) return null;
  const agenda = asArray(j.agenda).filter(isObject);
  if (!agenda.length) return null;
  return { agenda };
}

function versionMismatch(homeVersion, pluginVersion) {
  return !!homeVersion && !!pluginVersion && String(homeVersion) !== String(pluginVersion);
}

function manifestChanged(loadedVersion, diskText) {
  const m = parseJson(diskText);
  return !!(m && m.version && loadedVersion && String(m.version) !== String(loadedVersion));
}

function sanitizeData(raw) {
  const opened = [];
  const seen = new Set();
  for (const o of asArray(isObject(raw) ? raw.opened : [])) {
    if (!isObject(o) || typeof o.card !== 'string' || !/^f-[0-9a-f]+$/.test(o.card)) continue;
    if (seen.has(o.card)) continue;
    seen.add(o.card);
    opened.push({ card: o.card, at: typeof o.at === 'string' ? o.at : '' });
  }
  const out = { opened: opened.slice(0, OPENED_MAX) };
  const zoom = isObject(raw) ? savedZoom(raw.timelineDays) : null; // 上次縮放到幾天（裁定 37）
  if (zoom !== null) out.timelineDays = zoom;
  if (isObject(raw) && raw.monthCollapsed === true) out.monthCollapsed = true; // 月曆收起來（裁定 39）
  return out;
}

/** 開過的卡編號放最前面、不重複、最多 30 筆；只存編號與時間（D-262）。 */
function recordOpened(data, card, nowIso) {
  const base = sanitizeData(data);
  if (typeof card !== 'string' || !/^f-[0-9a-f]+$/.test(card)) return base;
  const rest = base.opened.filter((o) => o.card !== card);
  return Object.assign({}, base, { opened: [{ card, at: nowIso || new Date().toISOString() }, ...rest].slice(0, OPENED_MAX) });
}

function openedIds(data) {
  return sanitizeData(data).opened.map((o) => o.card);
}

// ---------------------------------------------------------------------------------------------------------------
// 知識庫在 vault 的哪裡（#66 乙，D-540）：前綴＝知識庫根相對 vault 的路徑（'/' 分隔；vault 根就是知識庫時是 ''）
// ---------------------------------------------------------------------------------------------------------------

const NO_ROOTS_TEXT = '這個資料夾裡沒有找到 willy 的知識庫（找了這一層與下面兩層）';
const ROOT_DEPTH = 2; // vault 根沒有記號時往下找幾層
const ROOT_SKIP = ['node_modules'];
const SOURCES_FILE = ['.system', 'sources.json'];
const SOURCES_STREAM = ['.system', 'streams', 'sources']; // kb 的來源流水檔夾（scripts/kb/_lib/streams.py 的 stream_dir(vault, "sources")）

/** 這個夾是不是知識庫根：有 .system/sources.json（檔案），或來源流水檔夾裡有 *.jsonl（同 kb 的 guard.find_vault）。 */
function hasKbMark(dir, io) {
  if (io.exists(path.join(dir, ...SOURCES_FILE))) return true;
  try {
    return asArray(io.readdir(path.join(dir, ...SOURCES_STREAM))).some((n) => String(n).endsWith('.jsonl'));
  } catch (e) {
    return false;
  }
}

/** 回前綴的陣列：vault 根有記號就是 ['']（不往下找）；沒有就看第一、二層子夾，照前綴排序。
 *  名字以 . 開頭的夾、node_modules 不進去；讀不了的夾略過；有記號的夾不再往裡面找。 */
function findKbRoots(opts) {
  const o = opts || {};
  const isFile = (q) => {
    try {
      return fs.statSync(q).isFile();
    } catch (e) {
      return false;
    }
  };
  const isDir = (q) => {
    try {
      return fs.statSync(q).isDirectory();
    } catch (e) {
      return false;
    }
  };
  const io = { exists: o.exists || isFile, readdir: o.readdir || ((q) => fs.readdirSync(q)), isDir: o.isDir || isDir };
  const base = o.vaultPath;
  if (!base) return [];
  if (hasKbMark(base, io)) return [''];
  const out = [];
  const walk = (prefix, depth) => {
    let names;
    try {
      names = asArray(io.readdir(prefix ? path.join(base, ...prefix.split('/')) : base)).map(String);
    } catch (e) {
      return;
    }
    for (const name of names) {
      if (!name || name.startsWith('.') || ROOT_SKIP.includes(name)) continue;
      const next = prefix ? `${prefix}/${name}` : name;
      const dir = path.join(base, ...next.split('/'));
      if (!io.isDir(dir)) continue;
      if (hasKbMark(dir, io)) out.push(next);
      else if (depth < ROOT_DEPTH) walk(next, depth + 1);
    }
  };
  walk('', 1);
  return out.sort();
}

/** 庫內路徑 → vault 路徑。 */
function joinRoot(root, rel) {
  return root ? `${root}/${rel}` : String(rel);
}

/** vault 路徑 → 庫內路徑；不在這個庫裡回 null。 */
function stripRoot(root, vaultRel) {
  const s = String(vaultRel == null ? '' : vaultRel).replace(/\\/g, '/');
  if (!root) return s;
  return s.startsWith(root + '/') ? s.slice(root.length + 1) : null;
}

/** 包含這個檔（vault 路徑）的知識庫前綴；'' 包含全部；沒有回 null（D-543）。 */
function rootOfPath(roots, vaultRel) {
  const s = String(vaultRel == null ? '' : vaultRel).replace(/\\/g, '/');
  let best = null;
  for (const r of asArray(roots)) {
    if (r !== '' && !s.startsWith(r + '/')) continue;
    if (best === null || r.length > best.length) best = r;
  }
  return best;
}

/** 選單上的名字：前綴的最後一段；最後一段重複的顯示整個前綴。 */
function rootLabels(roots) {
  const last = asArray(roots).map((r) => String(r).split('/').pop());
  return asArray(roots).map((r, i) => (last.filter((x) => x === last[i]).length > 1 ? r : last[i]));
}

/** data.json 讀進來的樣子（#66 乙）：{current?, roots: {前綴: {opened}}, timelineDays?, monthCollapsed?}。
 *  current 要在找到的清單裡才算，不然用第一個（一個都沒有就不記）；舊格式最上層的 opened 搬進 roots[current]；
 *  roots 裡已經不在清單的鍵留著（她可能只是暫時搬走）。不存絕對路徑（D-019）。 */
function sanitizeStore(raw, roots) {
  const list = asArray(roots);
  const r = isObject(raw) ? raw : {};
  const out = { roots: {} };
  if (isObject(r.roots)) {
    for (const k of Object.keys(r.roots)) out.roots[k] = { opened: sanitizeData(r.roots[k]).opened };
  }
  const current = typeof r.current === 'string' && list.includes(r.current) ? r.current : list.length ? list[0] : null;
  if (current !== null) out.current = current;
  const legacy = sanitizeData(r).opened;
  if (legacy.length) {
    const k = current === null ? '' : current;
    if (!out.roots[k] || !out.roots[k].opened.length) out.roots[k] = { opened: legacy };
  }
  const zoom = savedZoom(r.timelineDays);
  if (zoom !== null) out.timelineDays = zoom;
  if (r.monthCollapsed === true) out.monthCollapsed = true;
  return out;
}

/** 這個庫最近開過的卡編號。data 還是舊樣子（沒有 roots）就照舊讀最上層。 */
function openedOf(data, root) {
  const d = isObject(data) ? data : {};
  if (!isObject(d.roots)) return openedIds(d);
  return openedIds(d.roots[root == null ? '' : root]);
}

/** 記進那個庫自己的「最近」（D-545）；其他庫、縮放、收合都不動。 */
function recordOpenedIn(data, root, card, nowIso) {
  const d = isObject(data) ? data : {};
  if (!isObject(d.roots)) return recordOpened(d, card, nowIso);
  const key = root == null ? '' : root;
  const one = recordOpened(d.roots[key], card, nowIso);
  return Object.assign({}, d, { roots: Object.assign({}, d.roots, { [key]: { opened: one.opened } }) });
}

function cssString(s) {
  if (typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function') return CSS.escape(s);
  return String(s).replace(/["\\]/g, '\\$&');
}

/** 前綴不空的每個知識庫：藏檔案樹裡它的「檔案索引」夾與 CLAUDE.md（同 styles.css 開頭那兩組，data-path 完全相符，D-544）。 */
function treeHideCss(roots) {
  return asArray(roots)
    .filter((r) => r)
    .map((r) => {
      const dir = cssString(`${r}/檔案索引`);
      const md = cssString(`${r}/CLAUDE.md`);
      return [
        `.nav-folder-title[data-path="${dir}"],`,
        `.nav-folder-title[data-path="${dir}"] + .nav-folder-children,`,
        `.nav-folder:has(> .nav-folder-title[data-path="${dir}"]),`,
        `.nav-file-title[data-path="${md}"],`,
        `.nav-file:has(> .nav-file-title[data-path="${md}"]) { display: none; }`,
      ].join('\n');
    })
    .join('\n');
}

/** 本機的今天（YYYY-MM-DD）；第一次叫 kb home 還不知道 kb 的今天時用。 */
function localIso(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function homeArgs(ids, range) {
  const args = ['home', '--json'];
  if (ids && ids.length) args.push('--opened', ids.join(','));
  if (range && range.from) args.push('--from', range.from);
  if (range && range.to) args.push('--to', range.to);
  return args;
}

// ---------------------------------------------------------------------------------------------------------------
// 找 kb、叫 kb
// ---------------------------------------------------------------------------------------------------------------

function claudeDir(env, home) {
  const c = env && env.CLAUDE_CONFIG_DIR;
  return c ? c : path.join(home, '.claude');
}

/**
 * 照 installed_plugins.json 找 wiki-with-willy 的 kb.py（D-258）。
 * 多筆時依序挑（#66 乙確認後裁定 6）：projectPath 是這個知識庫的；是同一個 vault 裡另一個知識庫的（opts.siblings）；
 * version 跟這個外掛一樣的（opts.version，user 範圍優先）；scope user 的；最後一筆。回 {ok, kbPath, …} 或 {ok:false, reason}。
 */
function findKb(opts) {
  const o = opts || {};
  const env = o.env || process.env;
  const home = o.home || os.homedir();
  const readFile = o.readFile || ((p) => fs.readFileSync(p, 'utf8'));
  const exists = o.exists || ((p) => fs.existsSync(p));
  const platform = o.platform || process.platform;
  const file = path.join(claudeDir(env, home), 'plugins', 'installed_plugins.json');
  let data;
  try {
    data = JSON.parse(readFile(file, 'utf8'));
  } catch (e) {
    return { ok: false, reason: 'no-installed-file' };
  }
  const plugins = isObject(data) && isObject(data.plugins) ? data.plugins : {};
  const records = [];
  for (const key of Object.keys(plugins)) {
    if (key !== PLUGIN_ID && !key.startsWith(PLUGIN_ID + '@')) continue;
    for (const rec of asArray(plugins[key])) {
      if (isObject(rec) && typeof rec.installPath === 'string' && rec.installPath) records.push(Object.assign({ key }, rec));
    }
  }
  if (!records.length) return { ok: false, reason: 'not-installed' };
  const withKb = records
    .map((r) => ({ rec: r, kbPath: path.join(r.installPath, 'scripts', 'kb.py') }))
    .filter((x) => exists(x.kbPath));
  if (!withKb.length) return { ok: false, reason: 'no-kb' };
  const isUser = (x) => (x.rec.scope || 'user') === 'user';
  const at = (dir) => withKb.find((x) => x.rec.projectPath && dir && samePath(x.rec.projectPath, dir, platform));
  const mine = at(o.vaultPath);
  let sibling;
  for (const dir of asArray(o.siblings)) sibling = sibling || at(dir);
  const same = o.version ? withKb.filter((x) => x.rec.version === o.version) : [];
  const user = withKb.find(isUser);
  const pick = mine || sibling || same.find(isUser) || same[same.length - 1] || user || withKb[withKb.length - 1];
  return {
    ok: true,
    kbPath: pick.kbPath,
    installPath: pick.rec.installPath,
    version: pick.rec.version || null,
    scope: pick.rec.scope || 'user',
    projectPath: pick.rec.projectPath || null,
  };
}

function pythonCandidates(platform) {
  const plat = platform || process.platform;
  if (plat === 'win32') return [{ cmd: 'python', pre: [] }, { cmd: 'py', pre: ['-3'] }];
  return [{ cmd: 'python3', pre: [] }, { cmd: 'python', pre: [] }];
}

/** 整個 argv：[…pre, kb.py, '--via', 'obsidian', …子命令與參數]（--via 一定緊接 kb.py）。 */
function buildKbArgs(kbPath, args, pre) {
  return [...asArray(pre), kbPath, '--via', 'obsidian', ...asArray(args).map(String)];
}

function createKbRunner(cfg) {
  const execFile = cfg.execFile || childProcess.execFile;
  const pythons = cfg.pythons || pythonCandidates(cfg.platform);
  let chosen = null;

  function once(py, args) {
    return new Promise((resolve) => {
      const argv = buildKbArgs(cfg.kbPath, args, py.pre);
      const options = {
        cwd: cfg.cwd,
        env: Object.assign({}, cfg.env || process.env, { PYTHONIOENCODING: 'utf-8' }),
        windowsHide: true,
        encoding: 'utf8',
        maxBuffer: 64 * 1024 * 1024,
        timeout: cfg.timeoutMs || KB_TIMEOUT_MS,
      };
      try {
        execFile(py.cmd, argv, options, (err, stdout, stderr) => {
          if (!err) return resolve({ code: 0, stdout: String(stdout || ''), stderr: String(stderr || '') });
          const missing = err.code === 'ENOENT' || err.code === 9009;
          const code = typeof err.code === 'number' ? err.code : err.killed ? -2 : -1;
          resolve({
            code,
            stdout: String(stdout || ''),
            stderr: String(stderr || '') || (missing ? '' : String(err.message || '')),
            missing,
          });
        });
      } catch (e) {
        resolve({ code: -1, stdout: '', stderr: String(e && e.message), missing: e && e.code === 'ENOENT' });
      }
    });
  }

  async function run(args) {
    const order = chosen ? [chosen] : pythons;
    let last = null;
    for (const py of order) {
      const res = await once(py, args);
      if (res.missing) {
        last = res;
        continue;
      }
      chosen = py;
      return res;
    }
    return { code: -1, stdout: '', stderr: '找不到 Python（試過 ' + pythons.map((p) => p.cmd).join('、') + '）', missing: true, last };
  }

  return { run, buildArgs: (args, py) => buildKbArgs(cfg.kbPath, args, (py || pythons[0]).pre) };
}

/** exit 0 = ok；exit 3 = kb 的拒絕句原樣；其他 = 「沒做成：<stderr 第一行>」。 */
function classify(res) {
  if (res.code === 0) return { kind: 'ok' };
  if (res.code === 3) {
    return { kind: 'refused', message: firstLine(res.stdout) || firstLine(res.stderr) || '沒做成' };
  }
  return { kind: 'failed', message: '沒做成：' + (firstLine(res.stderr) || firstLine(res.stdout) || `結束碼 ${res.code}`) };
}

// ---------------------------------------------------------------------------------------------------------------
// 動作（D-254、D-266）：首頁只留四個——打勾、改日期時間、新增日程、回答「要你確認的」；每個動作 = 指令 + 反悔的相反指令
// ---------------------------------------------------------------------------------------------------------------

const PROGRESS_WORD = { ongoing: '進行中', active: '進行中', closed: '結案', paused: '擱置', 進行中: '進行中', 結案: '結案', 擱置: '擱置' };

function progressWord(p) {
  return PROGRESS_WORD[p] || null;
}

/**
 * 「要你確認的」一張卡的答案怎麼分（裁定 39，D-268），照 argv 認、不照字：
 * main = 左邊的答案（點了就做；要填的 fill 也在裡面，點了在卡上展開）；aside = 右邊淡淡的「先放著」（asks／pending set-aside）。
 * argv 是 null 的（只能在對話裡做）不列；首頁不出現「不處理」，告知的題 kb 給的是「知道了」（pending dismiss），算一般答案。
 */
function splitAnswers(c) {
  const out = { main: [], aside: null };
  for (const r of asArray(c && c.results).filter(isObject)) {
    const a = r.argv;
    if (!Array.isArray(a)) continue;
    if ((a[0] === 'asks' || a[0] === 'pending') && a[1] === 'set-aside') {
      if (!out.aside) out.aside = r;
    } else {
      out.main.push(r);
    }
  }
  return out;
}

const ASIDE_TEXT = '先放著';
const CHAT_TIP = '跟 Claude 說';
const CHAT_COPIED_TEXT = '已複製，貼到對話跟 Claude 說';
const CONFIRM_SHOWN = 3; // 「要你確認的」先顯示幾題
const REVIEW_PHRASE = '幫我看一下待處理'; // 最下面那行的對話圖示複製這一句

/** 每張卡的對話圖示：複製這一句，她貼到對話再接著說。 */
function chatPhrase(c) {
  const q = String((c && c.text) || '').trim();
  return q ? `關於「${q}」：` : '關於這題：';
}

/** 把那句話複製起來並提示；複製不了就把話顯示出來讓她自己複製。 */
async function copyForChat(ctx, phrase) {
  try {
    await ctx.writeClipboard(phrase);
    ctx.toast(CHAT_COPIED_TEXT);
    return true;
  } catch (e) {
    ctx.toast(`沒辦法複製，請自己複製：${phrase}`);
    return false;
  }
}

/** 要填的答案怎麼填：date 日期選擇、select 下拉、text 文字框；kb 沒給 input 時看選項與規則。 */
function fillKind(fill) {
  if (!isObject(fill)) return null;
  if (fill.input === 'date' || fill.input === 'select' || fill.input === 'text') return fill.input;
  if (asArray(fill.options).length) return 'select';
  return fill.rule === 'YYYY-MM-DD' ? 'date' : 'text';
}

/** 下拉的選項：[{label, value, group}]，label 沒給用 value。 */
function fillOptions(fill) {
  return asArray(fill && fill.options)
    .filter((o) => isObject(o) && o.value !== undefined && o.value !== null)
    .map((o) => ({ label: String(o.label !== undefined ? o.label : o.value), value: String(o.value), group: o.group === undefined ? null : String(o.group) }));
}

/** 左邊答案上寫的字：要填的後面加「…」（kb 的字已經有「…」就不再加）。 */
function answerLabel(r) {
  const label = String((r && r.label) || '');
  return isObject(r && r.fill) && !label.includes('…') ? `${label}…` : label;
}

/** 填的值能不能送：不收空的；日期要是 YYYY-MM-DD。 */
function cleanFill(fill, raw) {
  const v = String(raw === undefined || raw === null ? '' : raw).trim();
  if (!v) return null;
  if (fillKind(fill) === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(v)) return null;
  return v;
}

// ---- 隨手記便條（D-267 修訂、裁定 37、39）：首頁右上角一張便條，內容就是「系統/隨手記.md」整頁，她打字直接改那一頁；Claude 不讀 ----
const OPEN_ACTION = 'wiki-with-willy-open'; // 卡的開原檔連結 obsidian://wiki-with-willy-open?id=…（kb 的 cards.OPEN_PROTOCOL 對齊）
const NOTE_DIR = '系統';
const NOTE_REL = `${NOTE_DIR}/隨手記.md`; // #51 起放 系統/，舊的由 kb upgrade 搬過來
const NOTE_DELAY_MS = 1500; // 停手這麼久就存回那一頁

function defaultNote() {
  return {
    text: '', // 便條現在的字
    saved: null, // 那一頁在磁碟上的字；還沒有那一頁是 null
    dirty: false, // 改了還沒存回去
    focused: false, // 她正在框裡打字
  };
}

/** 重讀到的那一頁要不要換進便條：回新的字，或 null = 不動。她正在打字（框有焦點、或還有沒存的字）就不蓋。 */
function noteIncoming(note, disk) {
  if (!note || note.dirty || note.focused) return null;
  const incoming = disk == null ? '' : String(disk);
  return incoming === note.text ? null : incoming;
}

function putFlag(args, flag, value) {
  if (value !== undefined && value !== null && value !== '') args.push(flag, String(value));
}

const actions = {
  /** 打勾：done；復原 reopen（重複的日程完成會跳到下一次，復原改回原日期）。 */
  done(item) {
    const id = item.id;
    return {
      id: 'done',
      args: ['agenda', 'done', id],
      undo: () => {
        if (item.repeat && itemDate(item)) return { args: ['agenda', 'edit', id, '--date', itemDate(item)] };
        return { args: ['agenda', 'reopen', id] };
      },
    };
  },

  /** 改日期、時間：只送有變的；復原改回原值（原本沒有時間的沒辦法清掉，那一項不復原）。 */
  edit(item, change) {
    const id = item.id;
    const args = ['agenda', 'edit', id];
    const back = ['agenda', 'edit', id];
    const orig = { date: itemDate(item), start: item.start || '', time: item.time || '' };
    let backable = false;
    let any = false;
    for (const [key, flag] of [['date', '--date'], ['start', '--start'], ['time', '--time']]) {
      const v = change[key];
      if (v === undefined || v === null || v === '' || v === orig[key]) continue;
      args.push(flag, v);
      any = true;
      if (orig[key]) {
        back.push(flag, orig[key]);
        backable = true;
      }
    }
    if (!any) return null;
    return { id: 'edit', args, undo: backable ? () => ({ args: back }) : null };
  },

  /** 新增日程；復原 = 取消新的那一條。 */
  add(form) {
    const args = ['agenda', 'add'];
    putFlag(args, '--kind', form.kind);
    putFlag(args, '--date', form.date);
    putFlag(args, '--text', form.text);
    putFlag(args, '--start', form.start);
    putFlag(args, '--time', form.time);
    for (const e of asArray(form.entities)) putFlag(args, '--entity', e);
    args.push('--json');
    return {
      id: 'add',
      args,
      json: true,
      toastText: (j) => (j && j.item ? `已加進日程：${j.item.text || form.text}` : '已加進日程'),
      undo: (j) => (j && j.created !== false && j.item && j.item.id ? { args: ['agenda', 'cancel', j.item.id] } : null),
    };
  },

  /** 「要你確認的」選一個結果：照 argv；`{名字}` 換成她填的（接著要跑的 after 也換）；不給復原。argv 是 null 的不是指令（回 null）。 */
  result(result, fillValue) {
    if (!isObject(result) || !Array.isArray(result.argv)) return null;
    const fill = result.fill;
    const tokens = ['{name}'];
    if (isObject(fill) && fill.name) tokens.push(`{${fill.name}}`);
    const put = (a) => {
      let s = String(a);
      if (fillValue !== undefined && fillValue !== null) for (const t of tokens) s = s.split(t).join(fillValue);
      return s;
    };
    const out = { id: 'result', args: result.argv.map(put), undo: null };
    if (Array.isArray(result.after)) out.then = result.after.map(put); // 這個結果做完要接著跑的指令（例：改名後確認名字）
    return out;
  },

  /** 推測的日程：對（不帶欄位 = 記成確認）、改到某天、不用了；後兩個可以復原。 */
  infer(item, how, date) {
    const id = item.id;
    if (how === 'confirm') return { id: 'infer', args: ['agenda', 'edit', id], undo: null };
    if (how === 'cancel') return { id: 'infer', args: ['agenda', 'cancel', id], undo: () => ({ args: ['agenda', 'reopen', id] }) };
    const back = itemDate(item);
    return { id: 'infer', args: ['agenda', 'edit', id, '--date', date], undo: back ? () => ({ args: ['agenda', 'edit', id, '--date', back] }) : null };
  },

};

async function runUndo(ctx, u) {
  const res = await ctx.kb(u.args);
  const c = classify(res);
  if (c.kind === 'ok') {
    ctx.toast(firstLine(res.stdout) || '已復原');
    if (ctx.refresh) ctx.refresh();
    return { ok: true };
  }
  ctx.toast(c.message);
  if (c.kind === 'failed' && ctx.log) ctx.log(res.stderr || res.stdout);
  return { ok: false };
}

/**
 * 執行一個動作：成功 toast kb 的那句（原樣）加「復原」；失敗照 exit 3／其他分。
 * ctx：{kb(args)→res, toast(text, {undo}), refresh?, log?}
 */
async function performAction(ctx, action) {
  if (!action) return { ok: false };
  const res = await ctx.kb(action.args);
  const c = classify(res);
  if (c.kind !== 'ok') {
    ctx.toast(c.message);
    if (c.kind === 'failed' && ctx.log) ctx.log(res.stderr || res.stdout);
    return { ok: false, refused: c.kind === 'refused' };
  }
  let tail = null;
  if (Array.isArray(action.then)) {
    tail = classify(await ctx.kb(action.then));
    if (tail.kind !== 'ok') {
      ctx.toast(tail.message);
      if (ctx.refresh) ctx.refresh();
      return { ok: false, refused: tail.kind === 'refused' };
    }
  }
  const json = action.json ? parseJson(res.stdout) : null;
  const text = (action.toastText ? action.toastText(json) : '') || firstLine(res.stdout) || '好了';
  const u = action.undo ? action.undo(json, res) : null;
  ctx.toast(text, { undo: u ? () => runUndo(ctx, u) : null });
  if (ctx.refresh) ctx.refresh();
  return { ok: true, json, undo: u };
}

// ---------------------------------------------------------------------------------------------------------------
// 搜尋
// ---------------------------------------------------------------------------------------------------------------

/** Enter 才搜；注音選字中（compositionstart～compositionend、isComposing、keyCode 229）不搜；空字串不搜。 */
function searchDecision(ev) {
  if (!ev || ev.key !== 'Enter') return { search: false };
  if (ev.composing || ev.isComposing || ev.keyCode === 229) return { search: false };
  const q = String(ev.value == null ? '' : ev.value).trim();
  if (!q) return { search: false, clear: true };
  return { search: true, query: q };
}

function findArgs(q) {
  return q.startsWith('-') ? ['find', '--json', '--', q] : ['find', q, '--json'];
}

/** 點掉「照這樣找」的一個：把那段字從原字串拿掉。 */
function removeChipText(q, text) {
  const s = String(q == null ? '' : q);
  const t = String(text == null ? '' : text);
  if (!t) return s.trim();
  const i = s.indexOf(t);
  const out = i < 0 ? s : s.slice(0, i) + ' ' + s.slice(i + t.length);
  return out.replace(/\s+/g, ' ').trim();
}

function askClaudeText(q) {
  return `幫我找：${q}`;
}

/** 「到對話問 Claude」：把「幫我找：<字>」複製到剪貼簿。 */
async function copyAskClaude(ctx, q) {
  const text = askClaudeText(q);
  try {
    await ctx.writeClipboard(text);
    ctx.toast(COPIED_TEXT);
    return true;
  } catch (e) {
    ctx.toast(`沒辦法複製，請自己複製：${text}`);
    return false;
  }
}

/** 開原檔：網路路徑不呼叫 openPath（B-050 第 7 列）；link 條目開網址。 */
async function openOriginal(ctx, open) {
  if (!isObject(open) || (!open.path && !open.url)) {
    ctx.toast('這份沒有原檔可以開');
    return false;
  }
  if (open.url) {
    ctx.openUrl(open.url);
    return true;
  }
  if (isNetworkPath(open.path)) {
    ctx.toast(`這份在網路磁碟上，從這裡開不了；到檔案總管開：${open.path}`);
    return false;
  }
  let err = '';
  try {
    err = await ctx.openPath(nativePath(open.path, ctx.platform));
  } catch (e) {
    err = String(e && e.message ? e.message : e);
  }
  if (err) {
    ctx.toast(`開不了這份：${err}`);
    return false;
  }
  return true;
}

// ---------------------------------------------------------------------------------------------------------------
// 程式產生的頁（D-263）
// ---------------------------------------------------------------------------------------------------------------

function isManagedPage(p) {
  const s = String(p || '').replace(/\\/g, '/');
  return MANAGED_PAGES.includes(s);
}

function isGeneratedPage(p) {
  const s = String(p || '').replace(/\\/g, '/');
  return s === '總目錄.md' || s.startsWith('系統/工作日誌/') || s.startsWith('系統/健檢報告/');
}

/** 卡底下那段接在哪（照 Obsidian 1.15 的 DOM，記帳 3 確認 #1、#2）：
 *  - 閱讀模式：renderer 的 footer（.mod-footer，內建「筆記底部顯示反向連結」也接這裡）。直接接在 .markdown-preview-sizer
 *    會被 renderer 重畫時拿掉；footer 的元素 renderer 一直留著。
 *  - 即時預覽：.cm-sizer 裡、內容（.cm-contentContainer）緊後面、反向連結前面。接在 sizer 最後會排在內容底部的半頁留白後面。
 *  回 [{parent, before, renderer}]；找不到的那邊不接。 */
function panelSpots(view) {
  const spots = [];
  const root = view && (view.containerEl || view.contentEl);
  const q = (el, sel) => (el && el.querySelector ? el.querySelector(sel) : null);
  let renderer = null;
  try {
    renderer = view && view.previewMode && view.previewMode.renderer;
  } catch (e) {
    renderer = null;
  }
  const footer = (renderer && renderer.footer && renderer.footer.el) || q(q(root, '.markdown-preview-sizer'), '.mod-footer');
  if (footer) spots.push({ parent: footer, before: null, renderer: renderer && renderer.footer && renderer.footer.el === footer ? renderer : null });
  const cm = q(root, '.cm-sizer');
  if (cm) {
    const kids = Array.from(cm.children || []);
    const i = kids.findIndex((k) => k.classList && k.classList.contains('cm-contentContainer'));
    spots.push({ parent: cm, before: i >= 0 ? kids[i + 1] || null : null, renderer: null });
  }
  return spots;
}

/** 拿掉這個分頁裡的那段；keep 是現在這個檔的路徑時，是這個檔的留著。 */
function removePanels(view, keep) {
  for (const { parent } of panelSpots(view)) {
    for (const old of Array.from(parent.children || [])) {
      if (!old.classList || !old.classList.contains(PANEL_CLS)) continue;
      if (keep !== undefined && old.getAttribute && old.getAttribute(PANEL_CARD_ATTR) === keep) continue;
      parent.removeChild(old);
    }
  }
}

/** 離開被改的那一行（游標換行）、切到別頁、或 Obsidian 視窗失去焦點（切到 Claude），才叫 kb renders apply。 */
function createApplyTracker() {
  const dirty = new Map();
  const lastEdit = new Map();
  const ignoreUntil = new Map();
  return {
    /** 她改了 page 的第 line 行；回 true = 她已經離開前一個被改的行。 */
    edited(page, line, now) {
      if (now < (ignoreUntil.get(page) || 0)) return false; // kb 剛改寫這頁，編輯器重載不算她打的
      lastEdit.set(page, now);
      const d = dirty.get(page);
      dirty.set(page, { line });
      return !!d && d.line !== line;
    },
    /** 游標移到第 line 行（沒打字）；回 true = 離開了被改的那一行。 */
    cursor(page, line) {
      const d = dirty.get(page);
      if (d && d.line !== line) {
        dirty.delete(page);
        return true;
      }
      return false;
    },
    /** 切到別頁、關掉；回 true = 這頁有沒套用的修改。 */
    leave(page) {
      return dirty.delete(page);
    },
    applied(page) {
      dirty.delete(page);
    },
    external(page, now) {
      dirty.delete(page);
      ignoreUntil.set(page, now + 800);
    },
    isDirty(page) {
      return dirty.has(page);
    },
    /** 有沒套用修改的頁（視窗失去焦點時全套用）。 */
    dirtyPages() {
      return [...dirty.keys()];
    },
    isTyping(page, now, windowMs) {
      const t = lastEdit.get(page);
      return t !== undefined && now - t <= (windowMs === undefined ? 5000 : windowMs);
    },
  };
}

/** 她開著的頁被外部改寫才 toast：不是她在打字、不是外掛自己叫 kb 剛改的。 */
function shouldToastModified(s) {
  if (!s.open) return false;
  if (s.applying) return false;
  if (s.typing) return false;
  if (s.lastKbAt && s.now - s.lastKbAt < 2500) return false;
  return true;
}

/** renders apply 的回傳（形狀是外掛這邊的假設）：{applied:[…]|數字, carried:[…]|數字}。 */
function summarizeApply(json, pageName) {
  const count = (v) => (Array.isArray(v) ? v.length : Number(v) || 0);
  const j = isObject(json) ? json : {};
  const applied = count(j.applied);
  const carried = count(j.carried);
  const parts = [];
  if (j.unrecognized) parts.push(UNRECOGNIZED_TEXT);
  if (applied) parts.push(`照你在${pageName || '這頁'}改的做了 ${applied} 件`);
  if (carried) parts.push(CARRIED_TEXT);
  return { applied, carried, text: parts.join('；') };
}

function pageName(p) {
  return String(p || '').split('/').pop().replace(/\.md$/, '');
}

/** 內文有「你加的（還沒處理）」那段（kb 剛把她的字搬進去）。 */
function hasCarriedSection(text) {
  return /^##\s*你加的（還沒處理）/m.test(String(text || ''));
}

// ---------------------------------------------------------------------------------------------------------------
// 出處〔f-…〕變連結（閱讀模式）
// ---------------------------------------------------------------------------------------------------------------

function splitCardRefs(text) {
  const out = [];
  const s = String(text == null ? '' : text);
  let last = 0;
  CARD_REF_RE.lastIndex = 0;
  let m;
  while ((m = CARD_REF_RE.exec(s)) !== null) {
    if (m.index > last) out.push({ text: s.slice(last, m.index) });
    out.push({ ref: m[1] });
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push({ text: s.slice(last) });
  return out;
}

/** 一段文字裡所有〔f-…〕的位置：[{from, to, ref}]（offset 是這段文字在整份文件裡的起點）。 */
function cardRefRanges(text, offset) {
  const out = [];
  const s = String(text == null ? '' : text);
  const base = offset || 0;
  const re = new RegExp(CARD_REF_RE.source, 'g');
  let m;
  while ((m = re.exec(s)) !== null) out.push({ from: base + m.index, to: base + m.index + m[0].length, ref: m[1] });
  return out;
}

// ---------------------------------------------------------------------------------------------------------------
// DOM 小幫手（只用 createElement／setAttribute／addEventListener／appendChild，方便測試用假 DOM）
// ---------------------------------------------------------------------------------------------------------------

const dom = {
  doc() {
    return globalThis.document;
  },
};

function kebab(s) {
  return s.startsWith('--') ? s : s.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
}

function applyKids(el, kids) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    if (Array.isArray(k)) applyKids(el, k);
    else if (typeof k === 'string' || typeof k === 'number') el.appendChild(dom.doc().createTextNode(String(k)));
    else el.appendChild(k);
  }
}

function applyProps(el, props) {
  if (!props) return;
  for (const k of Object.keys(props)) {
    const v = props[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === 'cls') el.className = Array.isArray(v) ? v.filter(Boolean).join(' ') : String(v);
    else if (k === 'text') el.textContent = String(v);
    else if (k === 'title') el.setAttribute('title', String(v));
    else if (k === 'style') for (const sk of Object.keys(v)) el.style.setProperty(kebab(sk), String(v[sk]));
    else if (k === 'on') for (const ev of Object.keys(v)) el.addEventListener(ev, v[ev]);
    else if (k === 'attrs') for (const ak of Object.keys(v)) el.setAttribute(ak, String(v[ak]));
    else el[k] = v; // value、checked、disabled、id …
  }
}

function h(tag, props, ...kids) {
  const el = dom.doc().createElement(tag);
  applyProps(el, props);
  applyKids(el, kids);
  return el;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
function hs(tag, attrs, ...kids) {
  const doc = dom.doc();
  const el = doc.createElementNS ? doc.createElementNS(SVG_NS, tag) : doc.createElement(tag);
  for (const k of Object.keys(attrs || {})) el.setAttribute(k, String(attrs[k]));
  applyKids(el, kids);
  return el;
}

function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

// ---------------------------------------------------------------------------------------------------------------
// 對話框
// ---------------------------------------------------------------------------------------------------------------

/**
 * 通用表單／確認框。opts：{title, lines:[], fields:[{key,label,type,value,options,placeholder,list}], okText, cancelText, hideCancel}
 * 回 Promise：按確定 = 欄位值的物件（沒有欄位就是 {}），其他 = null。
 */
class FormModal extends Modal {
  constructor(app, opts, resolve) {
    super(app);
    this.opts = opts || {};
    this.resolve = resolve;
    this.settled = false;
    this.inputs = {};
  }

  onOpen() {
    const o = this.opts;
    if (this.titleEl) this.titleEl.textContent = o.title || '';
    const root = this.contentEl;
    for (const line of asArray(o.lines)) root.appendChild(h('p', { cls: 'wwh-modal-line', text: line }));
    for (const f of asArray(o.fields)) {
      let input;
      if (f.type === 'select') {
        input = h('select', { cls: 'wwh-input' });
        for (const opt of asArray(f.options)) {
          const value = isObject(opt) ? opt.value : opt;
          const label = isObject(opt) ? opt.label : opt;
          input.appendChild(h('option', { value, text: label, selected: value === f.value }));
        }
        input.value = f.value === undefined ? '' : f.value;
      } else if (f.type === 'textarea') {
        input = h('textarea', { cls: 'wwh-input', value: f.value || '', attrs: { rows: 3, placeholder: f.placeholder || '' } });
      } else {
        const attrs = { type: f.type || 'text' };
        if (f.placeholder) attrs.placeholder = f.placeholder;
        if (f.list) attrs.list = `wwh-list-${f.key}`;
        input = h('input', {
          cls: 'wwh-input',
          value: f.value || '',
          attrs,
          on: {
            keydown: (e) => {
              if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229 && f.type !== 'textarea') {
                e.preventDefault();
                this.submit();
              }
            },
          },
        });
      }
      this.inputs[f.key] = input;
      const row = h('label', { cls: 'wwh-field' }, h('span', { cls: 'wwh-field-label', text: f.label || '' }), input);
      if (f.list) {
        const dl = h('datalist', { attrs: { id: `wwh-list-${f.key}` } });
        for (const v of f.list) dl.appendChild(h('option', { value: v }));
        row.appendChild(dl);
      }
      root.appendChild(row);
    }
    const buttons = h('div', { cls: 'wwh-modal-buttons' });
    if (!o.hideCancel) buttons.appendChild(h('button', { text: o.cancelText || '不要', on: { click: () => this.finish(null) } }));
    buttons.appendChild(h('button', { cls: 'mod-cta', text: o.okText || '好', on: { click: () => this.submit() } }));
    root.appendChild(buttons);
    const first = Object.values(this.inputs)[0];
    if (first && first.focus) first.focus();
  }

  values() {
    const out = {};
    for (const k of Object.keys(this.inputs)) out[k] = String(this.inputs[k].value == null ? '' : this.inputs[k].value).trim();
    return out;
  }

  submit() {
    for (const f of asArray(this.opts.fields)) {
      if (f.required && !this.values()[f.key]) {
        if (this.inputs[f.key] && this.inputs[f.key].focus) this.inputs[f.key].focus();
        return;
      }
    }
    this.finish(this.values());
  }

  finish(v) {
    if (this.settled) return;
    this.settled = true;
    this.resolve(v);
    this.close();
  }

  onClose() {
    if (!this.settled) {
      this.settled = true;
      this.resolve(null);
    }
    clear(this.contentEl);
  }
}

function openForm(app, opts) {
  return new Promise((resolve) => {
    new FormModal(app, opts, resolve).open();
  });
}

// ---------------------------------------------------------------------------------------------------------------
// 首頁分頁
// ---------------------------------------------------------------------------------------------------------------

function defaultUi() {
  return {
    timelineMode: 'timeline', // timeline | month
    timelineDays: DEFAULT_ZOOM, // 時間軸一次看幾天（Ctrl＋滾輪連續縮放，14～365），記在 data.json
    tlStart: null, // 畫面最左邊是哪一天（天數）；沒捲過是 null，照今天算
    tlRange: null, // 已經跟 kb 要過的範圍 {from, to}；捲到外面才會變大
    confirmCollapsed: false, // 「要你確認的」整區收起來
    confirmAll: false, // 點了「還有 N 題」，全部展開
    confirmFill: null, // 卡上展開要填的那一格 {key, n}（第 n 個答案）；一次只開一格
    monthCollapsed: false, // 月曆收起：期間變細色線、單天的變色點，記在 data.json
    note: defaultNote(), // 隨手記便條
    month: null, // {year, month}
    rowCollapsed: {},
    todoCollapsed: {},
    search: { q: '', active: false, focused: false, loading: false, result: null, error: null },
    drawer: { open: false, stack: [], data: null, loading: false, error: null },
  };
}

// ---------------------------------------------------------------------------------------------------------------
// 首頁的圖示、欄寬（設計稿 design/mockups/Obsidian首頁-設計稿.dc.html，裁定 28）
// ---------------------------------------------------------------------------------------------------------------

const HEAD_H = 48; // 時間軸每一塊（事類實體）標題列的高度
const ROW_H = 40; // 展開後每件一列的高度
const RECENT_MAX = 8;
const MONTH_COLORS = 8; // 淡色的個數，styles.css 的 --wwh-mc0～7（淡）與 --wwh-mt0～7（濃一點，給時間軸的條與點）
const OTHER_ROW_KEY = '_other'; // kb home 裡「其他」那一列的 key

/** 套用顏色的 class；沒有顏色（什麼都沒掛的）不加，畫淡灰。 */
function toneCls(n) {
  return n === null || n === undefined ? '' : `wwh-tone${n}`;
}

/**
 * 時間軸每一列（案子）一個淡色，照列的順序從 MONTH_COLORS 個顏色輪流給（裁定 32、37）；
 * 「其他」那列的事照它 entities 第一個（多半是客戶）給色，同一個實體同色，接在案子的顏色後面；什麼都沒掛的是 null（淡灰）。
 * timeline 要先過 openMarksOnly（時間軸與月曆都是這樣）：兩邊列的順序才一樣，同一件事才同色。
 * 回 {row(key), of(rowKey, mark)}。
 */
function timelineColors(timeline) {
  const rows = asArray(timeline && timeline.rows).filter(isObject);
  const byRow = new Map();
  let n = 0;
  for (const r of rows) {
    const key = r.key || r.label || '';
    if (key !== OTHER_ROW_KEY) byRow.set(key, n++ % MONTH_COLORS);
  }
  const firstEntity = (m) => asArray(m && m.entities).map((x) => String(x).trim()).find(Boolean) || null;
  const byEntity = new Map();
  for (const r of rows) {
    if ((r.key || r.label || '') !== OTHER_ROW_KEY) continue;
    for (const m of asArray(r.marks)) {
      for (const x of [m, ...asArray(m && m.children)]) {
        const name = firstEntity(x);
        if (name && !byEntity.has(name.toLowerCase())) byEntity.set(name.toLowerCase(), (n + byEntity.size) % MONTH_COLORS);
      }
    }
  }
  return {
    row: (key) => (byRow.has(key) ? byRow.get(key) : null),
    of: (rowKey, m) => {
      if (byRow.has(rowKey)) return byRow.get(rowKey);
      const name = firstEntity(m);
      return name && byEntity.has(name.toLowerCase()) ? byEntity.get(name.toLowerCase()) : null;
    },
  };
}
const ICON_SHAPES = {
  meeting: [
    ['circle', { cx: 6, cy: 5.5, r: 2.4 }],
    ['circle', { cx: 11.6, cy: 6.2, r: 1.9 }],
    ['path', { d: 'M1.6 13.5c.4-2.4 2.2-4 4.4-4s4 1.6 4.4 4M10.6 9.4c1.9-.3 3.5.9 3.9 3.4' }],
  ],
  promise: [
    ['circle', { cx: 8, cy: 8, r: 6 }],
    ['path', { d: 'm5.3 8.2 2 2 3.5-4' }],
  ],
  deadline: [['path', { d: 'M3.5 14.5V2M3.5 2.5h8.5l-2.2 3 2.2 3H3.5' }]],
  'message-circle': [['path', { d: 'M5.27 13.33A6 6 0 1 0 2.67 10.73L1.33 14.67Z' }]], // Obsidian（lucide）的 message-circle，縮到 16 格
  reminder: [
    ['path', { d: 'M4 11V7.5a4 4 0 0 1 8 0V11l1.2 1.5H2.8z' }],
    ['path', { d: 'M6.7 14.2a1.4 1.4 0 0 0 2.6 0' }],
  ],
  period: [['path', { d: 'M4.5 1.8h7M4.5 14.2h7M5 1.8c0 3.6 6 3.6 6 6.2s-6 2.6-6 6.2M11 1.8c0 3.6-6 3.6-6 6.2s6 2.6 6 6.2' }]],
  card: [
    ['rect', { x: 2.5, y: 2, width: 11, height: 12, rx: 1.5 }],
    ['path', { d: 'M5 5.5h6M5 8h6M5 10.5h3.5' }],
  ],
  info: [
    ['circle', { cx: 8, cy: 8, r: 6 }],
    ['path', { d: 'M8 7.2V11M8 5v.1' }],
  ],
  chevron: [['path', { d: 'M6 3.5 10.5 8 6 12.5' }]],
  // Obsidian（lucide）的 file-text，縮到 16 格：出處〔f-…〕的小檔案圖示（裁定 41）
  'file-text': [
    ['path', { d: 'M4 1.5h5.5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1Z' }],
    ['path', { d: 'M9.5 1.5v3h3M5.5 8h5M5.5 10.5h5' }],
  ],
  dots: [
    ['circle', { cx: 3.5, cy: 8, r: 1 }],
    ['circle', { cx: 8, cy: 8, r: 1 }],
    ['circle', { cx: 12.5, cy: 8, r: 1 }],
  ],
};

/** 小圖示（SVG，顏色跟著文字）；不認得的名字回 null。 */
function icon(name, size, width) {
  const shapes = ICON_SHAPES[name];
  if (!shapes) return null;
  const px = size || 14;
  return hs(
    'svg',
    {
      class: 'wwh-ico',
      width: px,
      height: px,
      viewBox: '0 0 16 16',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': width || 1.6,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
      'aria-hidden': 'true',
    },
    ...shapes.map(([tag, attrs]) => hs(tag, attrs)),
  );
}

/** 搜尋結果與「最近」那一列前面的小方塊：看原檔的副檔名（連結條目是 ↗）。 */
function fileExt(f) {
  const open = isObject(f) && isObject(f.open) ? f.open : {};
  if (open.url) return { label: '↗', cls: 'ext-link' };
  const m = /\.([A-Za-z0-9]{1,5})$/.exec(String(open.path || ''));
  const ext = m ? m[1].toLowerCase() : '';
  let cls = 'ext-other';
  if (['xls', 'xlsx', 'xlsm', 'csv'].includes(ext)) cls = 'ext-xls';
  else if (['doc', 'docx', 'txt', 'md'].includes(ext)) cls = 'ext-doc';
  else if (['ppt', 'pptx'].includes(ext)) cls = 'ext-ppt';
  else if (ext === 'pdf') cls = 'ext-pdf';
  else if (['png', 'jpg', 'jpeg', 'gif', 'heic', 'webp'].includes(ext)) cls = 'ext-img';
  return { label: (ext.length > 3 ? ext.slice(0, 3) : ext || '檔').toUpperCase(), cls }; // xlsx → XLS、docx → DOC：小方塊裡放得下
}

/** 「10 月 30 日 星期五」。 */
function longDate(iso) {
  const n = dayNumber(iso);
  if (n === null) return '';
  const d = new Date(n * 86400000);
  return `${d.getUTCMonth() + 1} 月 ${d.getUTCDate()} 日 星期${WEEKDAY[d.getUTCDay()]}`;
}

/** 這一塊預設收起；有逾期或今天那件還沒做的才預設展開（裁定 28）。 */
function rowDefaultCollapsed(row, todayN) {
  const marks = asArray(row && row.marks);
  return !marks.some((m) => m.status === 'open' && (m.late || isDueToday(m, todayN)));
}

function rowIsCollapsed(ui, row, todayN) {
  const set = ui.rowCollapsed[row.key];
  return set !== undefined ? !!set : rowDefaultCollapsed(row, todayN);
}

/** 展開後左欄第二行：點是「10/30（週五）· 過了 2 天」，期間是「9/1～11/30」。 */
function markWhen(m, todayN) {
  if (m.shape === 'bar') {
    const a = shortDate(m.start);
    const b = shortDate(m.end);
    return a && b ? `${a}～${b}` : a || b;
  }
  const n = dayNumber(m.end);
  if (n === null) return '';
  let text = `${shortDate(m.end)}（週${WEEKDAY[weekdayOf(n)]}）`;
  if (m.time) text += ` ${m.time}`;
  if (m.status === 'open' && todayN !== null && n < todayN) text += ` · 過了 ${todayN - n} 天`;
  return text;
}

/** 這一件是不是今天到期、還沒做的點。 */
function isDueToday(m, todayN) {
  return todayN !== null && m.status === 'open' && m.shape === 'point' && dayNumber(m.end) === todayN;
}

/** 首頁畫的時間軸與月曆：做完、取消的不畫（裁定 30）；整塊都沒了的那一列也不畫。 */
function openMarksOnly(timeline) {
  const keep = (m) => isObject(m) && m.status !== 'done' && m.status !== 'cancelled';
  const tl = isObject(timeline) ? timeline : {};
  const rows = asArray(tl.rows)
    .filter(isObject)
    .map((r) => Object.assign({}, r, { marks: asArray(r.marks).filter(keep).map((m) => Object.assign({}, m, { children: asArray(m.children).filter(keep) })) }))
    .filter((r) => r.marks.length);
  return Object.assign({}, tl, { rows });
}

/** 日程.md 裡這一件住在第幾行（0 起算）：先找「文字加日期」都有的，再找文字，找不到回 0（開頁頂）。 */
function findAgendaLine(content, item) {
  const lines = String(content == null ? '' : content).split('\n');
  const want = String((item && item.text) || '').trim();
  if (!want) return 0;
  const day = itemDate(item);
  const has = (l, t) => l.includes(t);
  let at = day ? lines.findIndex((l) => has(l, want) && has(l, day)) : -1;
  if (at < 0) at = lines.findIndex((l) => has(l, want));
  if (at < 0) at = lines.findIndex((l) => has(l, want.slice(0, 8)));
  return at < 0 ? 0 : at;
}

class HomeView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.composing = false;
  }

  getViewType() {
    return VIEW_TYPE;
  }

  getDisplayText() {
    return '首頁';
  }

  getIcon() {
    return 'home';
  }

  async onOpen() {
    this.contentEl.classList.add('wwh-view');
    this.build();
    this.render();
    this.plugin.refresh();
  }

  async onClose() {
    if (this.zoomSaveTimer) {
      clearTimeout(this.zoomSaveTimer);
      this.zoomSaveTimer = null;
      if (this.plugin.saveTimelineDays) this.plugin.saveTimelineDays(Math.round(this.plugin.ui.timelineDays));
    }
    if (this.plugin.flushNote) this.plugin.flushNote();
    clear(this.contentEl);
  }

  build() {
    this.root = h('div', { cls: 'wwh-root' });
    this.bannerEl = h('div', { cls: 'wwh-banners' });
    this.topEl = h('div', { cls: 'wwh-top' });
    this.searchEl = h('div', { cls: 'wwh-search' });
    this.dropdownEl = h('div', { cls: 'wwh-dropdown' });
    this.recentEl = h('div', { cls: 'wwh-dropdown wwh-recent' });
    this.timelineEl = h('section', { cls: 'wwh-section wwh-timeline-section' });
    this.leftEl = h('div', { cls: 'wwh-col wwh-col-left wwh-paper' });
    this.rightEl = h('div', { cls: 'wwh-col wwh-col-right' });
    this.colsEl = h('div', { cls: 'wwh-cols' }, this.leftEl, this.rightEl);
    this.drawerEl = h('aside', { cls: 'wwh-drawer' });
    this.buildNote();
    // 左欄＝日期、問候、搜尋框，右欄＝便條，兩欄上緣對齊（裁定 39、40）
    this.topLeftEl = h('div', { cls: 'wwh-topleft' }, this.topEl, this.searchEl);
    this.topRowEl = h('div', { cls: 'wwh-toprow' }, this.topLeftEl, this.noteEl);
    this.inputEl = h('input', {
      cls: 'wwh-search-input',
      attrs: { type: 'search', placeholder: '找檔案、人、案子，打完按 Enter（例：王先生 報價 上個月）' },
      on: {
        compositionstart: () => {
          this.composing = true;
        },
        compositionend: () => {
          this.composing = false;
        },
        keydown: (e) => {
          const d = searchDecision({
            key: e.key,
            isComposing: e.isComposing,
            keyCode: e.keyCode,
            composing: this.composing,
            value: this.inputEl.value,
          });
          if (d.search) {
            if (e.preventDefault) e.preventDefault();
            this.doSearch(d.query);
          } else if (d.clear) {
            this.clearSearch();
          }
        },
        input: () => {
          if (!String(this.inputEl.value || '').trim() && this.plugin.ui.search.active) this.clearSearch();
        },
        focus: () => {
          this.plugin.ui.search.focused = true;
          this.renderSearchPanels();
        },
        blur: () => {
          this.plugin.ui.search.focused = false;
          this.renderSearchPanels();
        },
      },
    });
    this.searchEl.appendChild(this.inputEl);
    this.searchEl.appendChild(this.dropdownEl);
    this.searchEl.appendChild(this.recentEl);
    for (const box of [this.dropdownEl, this.recentEl]) {
      box.addEventListener('mousedown', (e) => {
        if (e && e.preventDefault) e.preventDefault(); // 點下拉裡的列，不讓搜尋框先失去游標（失去了「最近」就收起來）
      });
    }
    this.root.appendChild(this.bannerEl);
    this.root.appendChild(this.topRowEl);
    this.root.appendChild(this.timelineEl);
    this.root.appendChild(this.colsEl);
    this.contentEl.addEventListener('mousedown', (e) => this.onPopOutside(e)); // 點框外關掉小框
    this.contentEl.addEventListener('keydown', (e) => {
      if (e && e.key === 'Escape') this.closePop();
    });
    this.contentEl.addEventListener('mousemove', (e) => this.onDragMove(e));
    this.contentEl.addEventListener('mouseup', () => this.onDragEnd());
    this.contentEl.addEventListener('mouseleave', () => this.onDragEnd());
    clear(this.contentEl);
    this.contentEl.appendChild(this.root);
    this.contentEl.appendChild(this.drawerEl); // 抽屜蓋在首頁右邊，不跟著首頁捲動
  }

  /** 首頁最底下的隨手記便條：文字框只建一次（重畫不洗掉她打到一半的字、也不掉游標）；改了就交給 plugin 排存檔。 */
  buildNote() {
    const p = this.plugin;
    this.noteTextEl = h('textarea', {
      cls: 'wwh-note-text',
      attrs: { placeholder: '隨手記一下…', rows: '5', spellcheck: 'false' },
      value: (p.ui.note && p.ui.note.text) || '',
      on: {
        input: () => {
          if (p.noteEdited) p.noteEdited(this.noteTextEl.value);
        },
        focus: () => {
          if (p.noteFocus) p.noteFocus(true);
        },
        blur: () => {
          if (p.noteFocus) p.noteFocus(false);
        },
      },
    });
    const title = h('h2', {
      cls: 'wwh-note-title',
      title: '開隨手記那一頁',
      attrs: { role: 'button', tabindex: '0' },
      text: '隨手記',
      on: {
        click: () => {
          if (p.flushNote) p.flushNote();
          p.openPathInVault(NOTE_REL);
        },
      },
    });
    this.noteEl = h('section', { cls: 'wwh-note' }, title, this.noteTextEl);
  }

  /** 重畫時把便條的字對齊 plugin 讀到的那一頁（她正在打字就不動）。不用 safe：safe 會把文字框清掉。 */
  syncNote() {
    const n = this.plugin.ui.note;
    if (!n || !this.noteTextEl || n.focused) return;
    if (this.noteTextEl.value !== n.text) this.noteTextEl.value = n.text;
  }

  /** 右欄「要你確認的」卡片正下方（在「還有 N 題」下面，收起時照樣顯示）一行淡字：還有 N 件要跟 Claude 一起看＋對話圖示（點了複製「幫我看一下待處理」）；N = 移到這邊的題數加原本的小事，0 不寫。 */
  renderClaude(el) {
    const home = this.plugin.home;
    const n = home ? asArray(home.claude).length + asArray(home.others).length : 0;
    this.claudeEl = null;
    if (!n) return;
    const copy = () => this.plugin.copyReview();
    this.claudeEl = h('div', { cls: 'wwh-others' });
    el.appendChild(this.claudeEl);
    this.claudeEl.appendChild(
      h(
        'div',
        { cls: 'wwh-others-line' },
        h('span', { cls: 'wwh-others-text', text: `還有 ${n} 件要跟 Claude 一起看` }),
        h('span', { cls: 'wwh-quiet wwh-chat-btn', title: CHAT_TIP, attrs: { role: 'button', tabindex: '0', 'aria-label': CHAT_TIP }, on: { click: copy } }, icon('message-circle', 15, 1.5)),
      ),
    );
  }

  safe(name, el, fn) {
    clear(el);
    try {
      fn(el);
    } catch (e) {
      console.error(`[wiki-with-willy] 畫「${name}」出錯`, e);
      el.appendChild(h('div', { cls: 'wwh-empty', text: `這一區畫不出來（${name}）` }));
    }
  }

  render() {
    if (!this.root) return;
    const p = this.plugin;
    this.closePop();
    const noRoots = Array.isArray(p.roots) && p.roots.length === 0; // 這個 vault 裡沒有知識庫（#66 乙）
    if (noRoots || !p.runner) {
      for (const el of [this.topRowEl, this.searchEl, this.recentEl, this.timelineEl, this.colsEl, this.drawerEl, this.bannerEl]) {
        el.classList.add('wwh-hidden');
      }
      const text = noRoots ? NO_ROOTS_TEXT : NO_KB_TEXT;
      if (!this.noKbEl) this.noKbEl = h('div', { cls: 'wwh-nokb', text });
      else if (this.noKbEl.textContent !== text) this.noKbEl.textContent = text;
      if (!this.noKbEl.parentNode) this.root.insertBefore(this.noKbEl, this.root.firstChild);
      return;
    }
    if (this.noKbEl && this.noKbEl.parentNode) this.root.removeChild(this.noKbEl);
    for (const el of [this.topRowEl, this.searchEl, this.recentEl, this.timelineEl, this.colsEl, this.drawerEl, this.bannerEl]) {
      el.classList.remove('wwh-hidden');
    }
    this.safe('提示', this.bannerEl, (el) => this.renderBanners(el));
    this.safe('今天', this.topEl, (el) => this.renderTop(el));
    this.renderSearchPanels();
    this.safe('時間軸', this.timelineEl, (el) => this.renderTimeline(el));
    this.safe('待辦', this.leftEl, (el) => this.renderTodo(el));
    this.safe('要你確認的', this.rightEl, (el) => this.renderRight(el));
    this.syncNote();
    this.safe('總覽', this.drawerEl, (el) => this.renderDrawer(el));
  }

  // ---- 提示條 ----
  renderBanners(el) {
    const p = this.plugin;
    if (p.needsReload) {
      el.appendChild(h('button', { cls: 'wwh-banner wwh-banner-reload', text: RELOAD_TEXT, on: { click: () => p.reloadSelf() } }));
    }
    if (p.home && versionMismatch(p.home.version, p.loadedVersion)) {
      el.appendChild(h('div', { cls: 'wwh-banner wwh-banner-warn', text: VERSION_MISMATCH_TEXT }));
    }
    if (p.homeError) {
      el.appendChild(h('div', { cls: 'wwh-banner wwh-banner-warn', text: `首頁的資料讀不到：${p.homeError}` }));
    }
    if (p.gitNotice) {
      el.appendChild(h('div', { cls: 'wwh-banner wwh-banner-warn', text: p.gitNotice }));
    }
  }

  // ---- 日期與問候（只寫問候，kb 挑的；不放數字，裁定 32）----
  renderTop(el) {
    const home = this.plugin.home;
    const picker = this.rootPicker();
    if (picker) el.appendChild(picker);
    if (!home) {
      el.appendChild(h('div', { cls: 'wwh-cheer', text: '載入中……' }));
      return;
    }
    el.appendChild(
      h('div', { cls: 'wwh-greet' }, h('p', { cls: 'wwh-date', text: longDate(home.today) }), h('h1', { cls: 'wwh-cheer', text: home.cheer || '' })),
    );
  }

  /** 同一個 vault 有好幾個知識庫時，最左邊的下拉選單（#66 乙）；只有一個不出現。 */
  rootPicker() {
    const p = this.plugin;
    const roots = typeof p.rootsList === 'function' ? p.rootsList() : [''];
    if (roots.length < 2) return null;
    const labels = rootLabels(roots);
    const cur = p.cur();
    const sel = h(
      'select',
      { cls: 'wwh-root-select', title: '換一個知識庫看', attrs: { 'aria-label': '知識庫' } },
      ...roots.map((r, i) => h('option', { value: r, text: labels[i], selected: r === cur })),
    );
    sel.value = cur;
    sel.addEventListener('change', () => p.switchRoot(sel.value));
    return sel;
  }

  // ---- 搜尋 ----
  async doSearch(q) {
    const ui = this.plugin.ui.search;
    ui.q = q;
    ui.active = true;
    ui.loading = true;
    ui.error = null;
    this.inputEl.value = q;
    ui.seq = (ui.seq || 0) + 1; // 請求編號跟搜尋狀態放同一處：重開分頁（新的 view）也只認最新那次
    const seq = ui.seq;
    this.renderSearchPanels();
    const res = await this.plugin.runner.run(findArgs(q));
    if (seq !== ui.seq) return; // 後面又搜了（或清掉了）：這個舊的結果丟掉
    const c = classify(res);
    ui.loading = false;
    if (c.kind === 'ok') {
      const j = parseJson(res.stdout);
      ui.result = isObject(j) ? j : { parsed: [], entities: [], files: [], total: 0, more: false };
    } else {
      ui.result = null;
      ui.error = c.message;
      if (c.kind === 'failed') console.error('[wiki-with-willy] kb find 失敗', res.stderr || res.stdout);
    }
    this.renderSearchPanels();
  }

  clearSearch() {
    const ui = this.plugin.ui.search;
    ui.seq = (ui.seq || 0) + 1; // 還在路上的查詢回來不算數
    ui.q = '';
    ui.active = false;
    ui.result = null;
    ui.error = null;
    ui.loading = false;
    this.inputEl.value = '';
    this.renderSearchPanels();
  }

  renderSearchPanels() {
    if (!this.dropdownEl) return;
    this.safe('搜尋結果', this.dropdownEl, (el) => this.renderDropdown(el));
    this.safe('最近', this.recentEl, (el) => this.renderRecent(el));
  }

  renderDropdown(el) {
    const ui = this.plugin.ui.search;
    el.classList.toggle('is-open', !!ui.active);
    if (!ui.active) return;
    if (ui.loading) {
      el.appendChild(h('div', { cls: 'wwh-dd-row wwh-muted', text: '找找看……' }));
      return;
    }
    if (ui.error) {
      el.appendChild(h('div', { cls: 'wwh-dd-row wwh-muted', text: ui.error }));
    }
    const r = ui.result;
    if (r) {
      const parsed = asArray(r.parsed).filter(isObject);
      if (parsed.length) {
        const chips = h('div', { cls: 'wwh-dd-row wwh-chips' }, h('span', { cls: 'wwh-muted', text: '照這樣找：' }));
        for (const p of parsed) {
          chips.appendChild(
            h(
              'button',
              {
                cls: ['wwh-chip', `wwh-chip-${p.kind || 'word'}`],
                title: '點一下就不用這一項，重新找',
                on: { click: () => this.doSearchAfterChip(ui.q, p) },
              },
              p.label || p.text || '',
              h('span', { cls: 'wwh-chip-x', text: ' ✕' }),
            ),
          );
        }
        el.appendChild(chips);
      }
      const ents = asArray(r.entities).filter(isObject);
      if (ents.length) {
        el.appendChild(h('div', { cls: 'wwh-dd-head', text: '案子、人、事' }));
        for (const e of ents) {
          el.appendChild(
            h(
              'div',
              { cls: 'wwh-dd-row wwh-dd-entity', on: { click: () => this.plugin.openEntity(e.name) } },
              h('span', { cls: 'wwh-dd-title', text: e.name || '' }),
              h('span', { cls: 'wwh-muted', text: e.type ? `　${e.type}` : '' }),
            ),
          );
        }
      }
      const files = asArray(r.files).filter(isObject);
      if (files.length) {
        el.appendChild(h('div', { cls: 'wwh-dd-head', text: r.more ? `檔案（${files.length} 份以上）` : `檔案（${files.length} 份）` }));
        for (const f of files) el.appendChild(this.fileRow(f));
      }
      if (!ents.length && !files.length) el.appendChild(h('div', { cls: 'wwh-dd-row wwh-muted', text: '沒有找到' }));
    }
    el.appendChild(
      h('div', { cls: 'wwh-dd-row wwh-dd-ask', on: { click: () => this.plugin.askClaude(ui.q) } }, '💬 到對話問 Claude'),
    );
  }

  doSearchAfterChip(q, chip) {
    const next = removeChipText(q, chip.text);
    if (!next) {
      this.clearSearch();
      return;
    }
    this.doSearch(next);
  }

  fileRow(f) {
    const meta = [f.type, f.date, asArray(f.entities).join('、')].filter(Boolean).join('　');
    const ext = fileExt(f);
    const main = h(
      'div',
      { cls: 'wwh-file-main', on: { click: () => this.plugin.openFile(f) } },
      h('span', { cls: ['wwh-ext', ext.cls], text: ext.label }),
      h('span', { cls: 'wwh-file-text' }, h('span', { cls: 'wwh-dd-title', text: f.title || f.card || '' }), h('span', { cls: 'wwh-muted wwh-file-meta', text: meta })),
    );
    return h('div', { cls: 'wwh-dd-row wwh-file' }, main, f.card_path ? this.cardButton(f) : null);
  }

  cardButton(f) {
    return h(
      'button',
      {
        cls: 'wwh-icon-btn wwh-card-btn',
        title: '開檔案卡',
        on: {
          click: (e) => {
            if (e && e.stopPropagation) e.stopPropagation();
            this.plugin.openCard(f);
          },
        },
      },
      icon('card', 15, 1.5),
    );
  }

  /** 「最近」：游標在搜尋框裡、還沒打字時從框下面掉下來，一列一份，最多 8 列（裁定 28）。 */
  renderRecent(el) {
    const ui = this.plugin.ui.search;
    const home = this.plugin.home;
    const show = !!(ui.focused && !ui.active && home && home.recent.length);
    el.classList.toggle('wwh-hidden', !show);
    el.classList.toggle('is-open', show);
    if (!show) return;
    el.appendChild(h('div', { cls: 'wwh-dd-head', text: '最近' }));
    for (const f of home.recent.slice(0, RECENT_MAX)) {
      const meta = [f.type, f.date, f.why === 'added' ? '剛放進來' : '你開過'].filter(Boolean).join('　');
      const ext = fileExt(f);
      el.appendChild(
        h(
          'div',
          { cls: ['wwh-dd-row', 'wwh-file', 'wwh-recent-item', f.why === 'added' ? 'is-added' : 'is-opened'] },
          h(
            'div',
            { cls: 'wwh-file-main', on: { click: () => this.plugin.openFile(f) } },
            h('span', { cls: ['wwh-ext', ext.cls], text: ext.label }),
            h('span', { cls: 'wwh-file-text' }, h('span', { cls: 'wwh-dd-title', text: f.title || f.card || '' }), h('span', { cls: 'wwh-muted wwh-file-meta', text: meta })),
          ),
          f.card_path ? this.cardButton(f) : null,
        ),
      );
    }
  }

  // ---- 時間軸 ----
  /** 時間軸的版面：做完、取消的不畫（裁定 30）。 */
  timelineLayout() {
    const home = this.plugin.home;
    return layoutTimeline(openMarksOnly(home.timeline), { today: home.today, visibleDays: zoomOf(this.plugin.ui.timelineDays) });
  }

  /** 畫面現在最左邊是哪一天、軸要平移多少（占整段的百分比）。 */
  tlGeom(layout) {
    const ui = this.plugin.ui;
    const lay = layout || this.tlLayout;
    const visible = lay.visibleDays;
    const explicit = typeof ui.tlStart === 'number';
    const todayN = dayNumber(this.plugin.home && this.plugin.home.today);
    const wanted = explicit ? ui.tlStart : defaultTimelineStart(todayN === null ? lay.fromN : todayN, visible);
    const start = clampTimelineStart(wanted, visible, lay.fromN, lay.toN);
    return { visible, start, explicit, tx: -((start - lay.fromN) / lay.spanDays) * 100 };
  }

  /** 把捲動位置寫到軸上（只改一個 CSS 變數，不重畫），釘著的月份跟著換，再看要不要多載一段。 */
  applyScroll() {
    if (!this.ganttEl || !this.tlLayout) return;
    const g = this.tlGeom();
    this.ganttEl.style.setProperty('--wwh-tx', `${g.tx}%`);
    if (this.pinnedEl) {
      const pm = pinnedMonth(g.start, g.visible);
      this.pinnedEl.textContent = pm.label;
      this.pinnedEl.classList.toggle('wwh-hidden', pm.hide);
    }
    this.queueRangeCheck();
  }

  queueRangeCheck() {
    if (this.rangeTimer) clearTimeout(this.rangeTimer);
    this.rangeTimer = softTimeout(() => {
      this.rangeTimer = null;
      this.checkRange();
    }, 250);
  }

  checkRange() {
    const p = this.plugin;
    if (!this.tlLayout || !p.ensureTimelineRange) return false;
    const g = this.tlGeom();
    return p.ensureTimelineRange(g.start, g.visible, g.explicit);
  }

  pxPerDay() {
    const el = this.axisTrackEl;
    const w = (el && (el.clientWidth || el.offsetWidth)) || 700;
    return w / this.tlLayout.visibleDays;
  }

  scrollTimelineTo(start) {
    if (!this.tlLayout) return;
    const lay = this.tlLayout;
    this.plugin.ui.tlStart = clampTimelineStart(start, lay.visibleDays, lay.fromN, lay.toN);
    this.applyScroll();
  }

  scrollTimelineByPx(px) {
    if (!this.tlLayout || !px) return;
    this.scrollTimelineTo(this.tlGeom().start + px / this.pxPerDay());
  }

  backToToday() {
    this.plugin.ui.tlStart = null;
    this.applyScroll();
  }

  /** Ctrl＋滾輪（觸控板兩指拉近拉遠在 Obsidian 也是帶 Ctrl 的 wheel）：以游標那天為中心連續縮放，14～365 天（裁定 37）。 */
  zoomByWheel(e) {
    if (!this.tlLayout) return;
    const g = this.tlGeom();
    const track = this.axisTrackEl;
    const rect = track && track.getBoundingClientRect ? track.getBoundingClientRect() : null;
    const frac = rect && rect.width > 0 ? (Number(e.clientX) - rect.left) / rect.width : 0.5;
    const z = zoomAround(g.start, g.visible, Number.isFinite(frac) ? frac : 0.5, wheelZoomFactor(e));
    if (z.visible === g.visible) return;
    const ui = this.plugin.ui;
    ui.timelineDays = z.visible;
    ui.tlStart = z.start;
    this.queueZoomSave();
    this.safe('時間軸', this.timelineEl, (el) => this.renderTimeline(el));
  }

  /** 縮放停手 0.4 秒才存 data.json（連續滾不要每一下都寫檔）。 */
  queueZoomSave() {
    if (!this.plugin.saveTimelineDays) return;
    if (this.zoomSaveTimer) clearTimeout(this.zoomSaveTimer);
    this.zoomSaveTimer = softTimeout(() => {
      this.zoomSaveTimer = null;
      this.plugin.saveTimelineDays(Math.round(this.plugin.ui.timelineDays));
    }, 400);
  }

  onGanttWheel(e) {
    if (e && e.ctrlKey) {
      if (e.preventDefault) e.preventDefault(); // 不讓 Obsidian 整頁跟著放大
      this.zoomByWheel(e);
      return;
    }
    const px = horizontalWheel(e);
    if (!px) return;
    if (e.preventDefault) e.preventDefault();
    this.scrollTimelineByPx(px);
  }

  onGanttDown(e) {
    if (!e || e.button || !this.tlLayout || !dragStartsHere(e.target)) return;
    this.drag = { x0: e.clientX, start0: this.tlGeom().start, ppd: this.pxPerDay(), moved: false };
  }

  onDragMove(e) {
    const d = this.drag;
    if (!d) return;
    const dx = d.x0 - e.clientX;
    if (!d.moved && Math.abs(dx) < 4) return;
    d.moved = true;
    if (this.ganttEl) this.ganttEl.classList.add('is-dragging');
    this.scrollTimelineTo(d.start0 + dx / d.ppd);
  }

  onDragEnd() {
    const d = this.drag;
    this.drag = null;
    if (this.ganttEl) this.ganttEl.classList.remove('is-dragging');
    if (d && d.moved) this.dragMoved = true; // 緊接著那下 click 不算
  }

  /** 拖完放開時瀏覽器會再補一個 click：吃掉，免得拖一下就把整塊收起來或開了一件。 */
  onGanttClickCapture(e) {
    if (!this.dragMoved) return;
    this.dragMoved = false;
    if (e && e.stopPropagation) e.stopPropagation();
    if (e && e.preventDefault) e.preventDefault();
  }

  /** 軸上、每塊上面的內容都放進這一層：寬度是整段除以畫面看的天數，平移由 --wwh-tx 決定。 */
  slide(...kids) {
    return h('div', { cls: 'wwh-slide', style: { width: `${this.tlLayout.slidePct}%` } }, ...kids);
  }

  renderTimeline(el) {
    const p = this.plugin;
    const home = p.home;
    const ui = p.ui;
    const head = h('div', { cls: 'wwh-section-head' }, h('h2', { text: '日程' }));
    const tools = h('div', { cls: 'wwh-tools' });
    if (home && ui.timelineMode === 'timeline') { // kb home 還沒回來時不畫，按了也沒東西可展開
      const layout = this.timelineLayout();
      const todayN = dayNumber(home.today);
      const allOpen = layout.rows.length > 0 && layout.rows.every((r) => !rowIsCollapsed(ui, r, todayN));
      tools.appendChild(
        h('span', { cls: 'wwh-text-btn', attrs: { role: 'button', tabindex: '0' }, text: allOpen ? '全部收起' : '全部展開', on: { click: () => this.setAllRows(allOpen) } }),
      );
    }
    if (home && ui.timelineMode === 'timeline') {
      tools.appendChild(h('span', { cls: 'wwh-text-btn wwh-today-btn', attrs: { role: 'button', tabindex: '0' }, text: '回到今天', on: { click: () => this.backToToday() } }));
    }
    if (home && ui.timelineMode === 'month') { // 月曆跟時間軸同一個位置、同樣的字（裁定 39）
      tools.appendChild(
        h('span', { cls: 'wwh-text-btn wwh-month-fold', attrs: { role: 'button', tabindex: '0' }, text: ui.monthCollapsed ? '全部展開' : '全部收起', on: { click: () => this.setMonthCollapsed(!ui.monthCollapsed) } }),
      );
      tools.appendChild(
        h('span', { cls: 'wwh-text-btn wwh-today-btn', attrs: { role: 'button', tabindex: '0' }, text: '回到今天', on: { click: () => this.monthToToday() } }),
      );
    }
    tools.appendChild(
      h(
        'span',
        { cls: 'wwh-toggle' },
        h('span', { cls: ['wwh-seg', ui.timelineMode === 'timeline' ? 'is-on' : ''], attrs: { role: 'button', tabindex: '0' }, text: '時間軸', on: { click: () => this.setMode('timeline') } }),
        h('span', { cls: ['wwh-seg', ui.timelineMode === 'month' ? 'is-on' : ''], attrs: { role: 'button', tabindex: '0' }, text: '月曆', on: { click: () => this.setMode('month') } }),
      ),
    );
    head.appendChild(tools);
    el.appendChild(head);
    if (!home) return;
    if (ui.timelineMode === 'month') {
      this.renderMonth(el, home);
      return;
    }
    const layout = this.timelineLayout();
    if (!layout.rows.length) {
      el.appendChild(h('div', { cls: 'wwh-empty', text: '手上沒有排日期的事' }));
      return;
    }
    const todayN = dayNumber(home.today);
    this.tlLayout = layout;
    this.tlColors = timelineColors(openMarksOnly(home.timeline));
    const table = h('div', { cls: 'wwh-gantt', on: { wheel: (e) => this.onGanttWheel(e), mousedown: (e) => this.onGanttDown(e) } }, this.axis(layout));
    table.addEventListener('click', (e) => this.onGanttClickCapture(e), true);
    this.ganttEl = table;
    layout.rows.forEach((row, i) => table.appendChild(this.timelineBlock(layout, row, i, todayN)));
    el.appendChild(table);
    el.appendChild(this.legend());
    this.applyScroll();
  }

  setMode(mode) {
    this.plugin.ui.timelineMode = mode;
    this.safe('時間軸', this.timelineEl, (el) => this.renderTimeline(el));
  }

  setMonthCollapsed(on) {
    this.plugin.ui.monthCollapsed = on;
    if (this.plugin.saveMonthCollapsed) this.plugin.saveMonthCollapsed(on);
    this.safe('時間軸', this.timelineEl, (el) => this.renderTimeline(el));
  }

  monthToToday() {
    this.plugin.ui.month = null;
    this.safe('時間軸', this.timelineEl, (el) => this.renderTimeline(el));
  }

  setAllRows(collapsed) {
    if (!this.plugin.home) return;
    const layout = this.timelineLayout();
    for (const r of layout.rows) this.plugin.ui.rowCollapsed[r.key] = collapsed;
    this.safe('時間軸', this.timelineEl, (el) => this.renderTimeline(el));
  }

  legend() {
    const kind = (name, text) => h('span', { cls: 'wwh-legend-item' }, icon(name, 13), text);
    const swatch = (cls, text) => h('span', { cls: 'wwh-legend-item' }, h('i', { cls: ['wwh-legend-swatch', cls] }), text);
    return h(
      'div',
      { cls: 'wwh-legend' },
      kind('meeting', '會議'),
      kind('promise', '承諾'),
      kind('deadline', '到期'),
      kind('reminder', '提醒'),
      swatch('is-bar', '期間'),
      swatch('is-inferred', '推測的'),
      swatch('is-late', '過了還沒收尾'),
      swatch('is-pause', '暫停'),
      swatch('is-today', '今天'),
    );
  }

  /** 軸頭（黏住）：上排月份、下排日期（兩週內每天，其他每週一）；今天的旗子放在日期那排，附近的日期讓位；捲走的月份釘在左邊。 */
  axis(layout) {
    const sl = this.slide();
    const near = Math.max(0.6, layout.visibleDays * 0.05) * (100 / layout.spanDays);
    for (const t of layout.ticks) {
      if (t.kind === 'month') sl.appendChild(h('span', { cls: 'wwh-tick is-month', style: { left: `${t.pct}%` }, text: t.label }));
      else {
        sl.appendChild(h('i', { cls: ['wwh-grid', `is-${t.kind}`], style: { left: `${t.pct}%` } }));
        if (layout.todayPct === null || Math.abs(t.pct - layout.todayPct) > near) {
          sl.appendChild(h('span', { cls: 'wwh-tick is-day', style: { left: `${t.pct}%` }, text: t.label }));
        }
      }
    }
    if (layout.todayPct !== null) sl.appendChild(h('span', { cls: 'wwh-today-flag', style: { left: `${layout.todayPct}%` }, text: '今天' }));
    this.pinnedEl = h('span', { cls: 'wwh-tick is-month is-pinned' });
    const track = h('div', { cls: 'wwh-axis-track' }, sl, this.pinnedEl);
    this.axisTrackEl = track;
    return h('div', { cls: 'wwh-axis' }, h('div', { cls: 'wwh-cell-left' }), track);
  }

  timelineBlock(layout, row, index, todayN) {
    const ui = this.plugin.ui;
    const collapsed = rowIsCollapsed(ui, row, todayN);
    const open = row.marks.filter((m) => m.status === 'open').length;
    const toggle = () => {
      ui.rowCollapsed[row.key] = !collapsed;
      this.safe('時間軸', this.timelineEl, (el) => this.renderTimeline(el));
    };
    // 收合只靠名字前的小箭頭（裁定 41）；名字本身開右邊的總覽，「其他」那列不開；單雙數交替淡底
    const openable = row.key !== OTHER_ROW_KEY && !!row.label;
    const name = h('span', {
      cls: ['wwh-row-name', openable ? 'is-link' : ''],
      title: openable ? `開「${row.label}」的總覽` : null,
      text: row.label,
      on: {
        click: (e) => {
          if (!openable) return;
          if (e && e.stopPropagation) e.stopPropagation();
          this.plugin.openEntity(row.label);
        },
      },
    });
    const chev = h(
      'span',
      {
        cls: 'wwh-fold-btn',
        title: collapsed ? '展開' : '收起',
        attrs: { role: 'button', tabindex: '0', 'aria-expanded': collapsed ? 'false' : 'true' },
        on: {
          click: (e) => {
            if (e && e.stopPropagation) e.stopPropagation();
            toggle();
          },
        },
      },
      h('span', { cls: ['wwh-chev', collapsed ? '' : 'is-open'] }, icon('chevron', 14, 2)),
    );
    const left = h(
      'div',
      { cls: 'wwh-cell-left wwh-row-head' },
      h(
        'div',
        { cls: 'wwh-fold' },
        chev,
        this.tlColors.row(row.key) === null ? null : h('i', { cls: ['wwh-row-dot', toneCls(this.tlColors.row(row.key))] }),
        name,
        h('span', { cls: 'wwh-muted wwh-row-count', text: open ? `${open} 件` : '' }),
        row.progress && progressWord(row.progress) !== '進行中'
          ? h('span', { cls: ['wwh-badge', `is-${row.progress}`], text: progressWord(row.progress) || row.progress })
          : null,
      ),
    );
    const headLine = h('div', { cls: 'wwh-line wwh-head-line' }, left, h('div', { cls: 'wwh-cell-track' }, collapsed ? this.slide(this.summary(row, todayN)) : null));
    const block = h('div', { cls: ['wwh-row', collapsed ? 'is-collapsed' : 'is-open', index % 2 ? 'is-alt' : ''] }, headLine);
    if (!collapsed) {
      const rows = h('div', { cls: 'wwh-rows' });
      row.marks.forEach((m) => rows.appendChild(this.markLine(m, todayN, row)));
      block.appendChild(rows);
    }
    block.appendChild(this.overlay(layout, row, collapsed));
    return block;
  }

  /** 收起來的摘要：期間（實心）的色條加每件一個點。 */
  summary(row, todayN) {
    const box = h('div', { cls: 'wwh-summary' });
    for (const m of row.marks) {
      if (m.depth) continue;
      if (m.shape === 'bar' && !m.inferred) {
        box.appendChild(h('span', { cls: ['wwh-sum-bar', m.late ? 'is-late' : '', toneCls(this.tlColors.of(row.key, m))], style: { left: `${m.leftPct}%`, width: `${m.widthPct}%` }, title: describeMark(m) }));
        continue;
      }
      const at = m.shape === 'bar' ? Math.min(100, m.leftPct + m.widthPct) : m.leftPct;
      box.appendChild(
        h('span', {
          cls: ['wwh-sum-dot', m.late ? 'is-late' : '', isDueToday(m, todayN) ? 'is-today' : '', m.inferred ? 'is-inferred' : '', toneCls(this.tlColors.of(row.key, m))],
          style: { left: `${at}%` },
          title: describeMark(m),
        }),
      );
    }
    return box;
  }

  /** 每塊的背景層：週線、暫停、連線、今天線；蓋在右邊那欄上、不吃點擊。 */
  overlay(layout, row, collapsed) {
    const outer = h('div', { cls: 'wwh-overlay' });
    const lay = this.slide();
    outer.appendChild(lay);
    const rows = collapsed ? 0 : row.marks.length;
    const height = HEAD_H + rows * ROW_H;
    for (const t of layout.ticks) lay.appendChild(h('i', { cls: ['wwh-grid', `is-${t.kind}`], style: { left: `${t.pct}%` } }));
    for (const p of row.pauses) lay.appendChild(h('i', { cls: 'wwh-pause', style: { left: `${p.leftPct}%`, width: `${p.widthPct}%` } }));
    if (!collapsed && row.links.length) lay.appendChild(this.linksSvg(row, height));
    if (layout.todayPct !== null) lay.appendChild(h('i', { cls: 'wwh-today-line', style: { left: `${layout.todayPct}%` } }));
    return outer;
  }

  linksSvg(row, height) {
    const index = {};
    row.marks.forEach((m, i) => {
      if (m.id) index[m.id] = i;
    });
    const svg = hs('svg', { class: 'wwh-links', viewBox: `0 0 100 ${height}`, preserveAspectRatio: 'none' });
    for (const l of row.links) {
      if (index[l.from] === undefined || index[l.to] === undefined) continue;
      const y1 = HEAD_H + index[l.from] * ROW_H + ROW_H / 2;
      const y2 = HEAD_H + index[l.to] * ROW_H + ROW_H / 2;
      const mid = Math.min(Math.max(l.x1 + 0.6, (l.x1 + l.x2) / 2), Math.max(l.x2 - 0.4, l.x1 + 0.6));
      const d = `M ${l.x1} ${y1} H ${mid} V ${y2} H ${l.x2}`;
      svg.appendChild(hs('path', { d, class: l.critical ? 'wwh-link-path is-critical' : 'wwh-link-path', fill: 'none' }));
    }
    return svg;
  }

  /** 展開後的一件一列：左欄圖示、文字、日期；右邊同一列只放記號。只有過期、今天的記號上色，其他淺灰；點下去開日程.md 那一行。 */
  markLine(m, todayN, row) {
    const state = [m.late ? 'is-late' : '', isDueToday(m, todayN) ? 'is-today' : '', m.inferred ? 'is-inferred' : '', m.critical ? 'is-critical' : ''];
    const onClick = (anchor) => this.markClick(m.raw, anchor);
    const kind = m.kind && ICON_SHAPES[m.kind] ? m.kind : m.shape === 'bar' ? 'period' : null;
    const leftCls = ['wwh-cell-left', 'wwh-mark-label', m.depth ? 'is-child' : '', ...state];
    const left = h(
      'div',
      { cls: leftCls, title: describeMark(m), on: {} },
      h('span', { cls: ['wwh-kind-ico', m.kind ? `kind-${m.kind}` : ''] }, kind ? icon(kind, 15, 1.5) : null),
      h('span', { cls: 'wwh-mark-lines' }, h('span', { cls: 'wwh-mark-text', text: m.text }), h('span', { cls: 'wwh-mark-when', text: markWhen(m, todayN) })),
    );
    left.addEventListener('click', () => onClick(left));
    const sl = this.slide();
    const track = h('div', { cls: 'wwh-cell-track' }, sl);
    if (m.baseline) {
      sl.appendChild(h('i', { cls: 'wwh-baseline', style: { left: `${m.baseline.leftPct}%`, width: `${m.baseline.widthPct}%` }, title: '原定' }));
    }
    const tone = row ? toneCls(this.tlColors.of(row.key, m)) : '';
    sl.appendChild(this.markEl(m, tone ? state.concat(tone) : state, kind, onClick));
    return h('div', { cls: ['wwh-line', 'wwh-mark-line', ...state] }, left, track);
  }

  markEl(m, state, kind, onClick) {
    const cls = ['wwh-mark', `is-${m.shape}`, m.kind ? `kind-${m.kind}` : '', ...state, m.depth ? 'is-child' : '', m.clippedLeft ? 'clip-left' : '', m.clippedRight ? 'clip-right' : ''];
    const el =
      m.shape === 'point'
        ? h('span', { cls, style: { left: `${m.leftPct}%` }, title: describeMark(m) }, kind ? icon(kind, 13, 1.7) : null)
        : h('span', { cls, style: { left: `${m.leftPct}%`, width: `${m.widthPct}%` }, title: describeMark(m) });
    el.addEventListener('click', () => onClick(el));
    return el;
  }

  /** 點日程那件：推測的先跳小框問她對不對（裁定 39）；其他照舊開相關的卡。 */
  markClick(raw, anchor) {
    if (raw && raw.inferred && raw.id) this.openInferPop(anchor, raw);
    else this.plugin.openItem(raw);
  }

  renderMonth(el, home) {
    const ui = this.plugin.ui;
    const today = dayNumber(home.today);
    const base = today !== null ? new Date(today * 86400000) : new Date();
    const cur = ui.month || { year: base.getUTCFullYear(), month: base.getUTCMonth() };
    const m = layoutMonth(home.timeline, { today: home.today, year: cur.year, month: cur.month });
    const collapsed = ui.monthCollapsed === true;
    const rerender = () => this.safe('時間軸', this.timelineEl, (e2) => this.renderTimeline(e2));
    const go = (delta) => {
      ui.month = shiftMonth(cur.year, cur.month, delta);
      rerender();
    };
    const textBtn = (text, fn) => h('span', { cls: 'wwh-text-btn', attrs: { role: 'button', tabindex: '0' }, text, on: { click: fn } });
    el.appendChild(h('div', { cls: 'wwh-month-head' }, textBtn('‹', () => go(-1)), h('span', { cls: 'wwh-month-title', text: `${m.year} 年 ${m.month + 1} 月` }), textBtn('›', () => go(1))));
    const grid = h('div', { cls: ['wwh-month', collapsed ? 'is-collapsed' : ''] });
    const dows = h('div', { cls: 'wwh-month-dows' });
    for (const w of WEEKDAY) dows.appendChild(h('div', { cls: 'wwh-month-dow', text: w }));
    grid.appendChild(dows);
    const onClick = (raw, anchor) => () => this.markClick(raw, anchor());
    for (const week of m.weeks) {
      const wk = h('div', { cls: 'wwh-week' });
      const marksRow = String(week.lanes + 2);
      const allRows = `1 / ${week.lanes + 3}`;
      week.days.forEach((d, i) => {
        const col = String(i + 1);
        // 整個日期欄的底：每天之間一條淡直線；點日期數字或空白處跳出那天的所有事
        const bg = h('div', { cls: ['wwh-day-bg', i === 0 ? 'is-first' : '', d.isToday ? 'is-today' : ''], style: { gridColumn: col, gridRow: allRows } });
        bg.addEventListener('click', () => this.openDayPop(bg, d));
        wk.appendChild(bg);
        const dayEl = h('div', { cls: ['wwh-day', d.inMonth ? '' : 'is-out', d.isToday ? 'is-today' : ''], style: { gridColumn: col, gridRow: '1' } }, h('span', { cls: 'wwh-day-num', text: String(d.day) }));
        dayEl.addEventListener('click', () => this.openDayPop(dayEl, d));
        wk.appendChild(dayEl);
        if (!d.marks.length) return;
        const cell = h('div', { cls: ['wwh-day-marks', collapsed ? 'is-dots' : ''], style: { gridColumn: col, gridRow: marksRow } });
        cell.addEventListener('click', (e) => {
          if (!e || !e.target || e.target === cell) this.openDayPop(cell, d); // 點到格子的空白處
        });
        for (const mk of collapsed ? d.marks : d.marks.slice(0, 3)) {
          const item = h('div', {
            cls: [collapsed ? 'wwh-day-dot' : 'wwh-day-mark', toneCls(mk.color), mk.kind ? `kind-${mk.kind}` : '', mk.late ? 'is-late' : '', d.isToday ? 'is-today' : '', mk.inferred ? 'is-inferred' : ''],
            text: collapsed ? '' : mk.label,
            title: `${mk.row ? mk.row + '：' : ''}${mk.text}`,
          });
          item.addEventListener('click', onClick(mk.raw, () => item));
          cell.appendChild(item);
        }
        if (!collapsed && d.marks.length > 3) {
          const more = h('div', { cls: 'wwh-muted wwh-day-more', text: `還有 ${d.marks.length - 3} 件` });
          more.addEventListener('click', () => this.openDayPop(more, d));
          cell.appendChild(more);
        }
        wk.appendChild(cell);
      });
      for (const b of week.bars) {
        const bar = h('div', {
          cls: ['wwh-mbar', collapsed ? 'is-thin' : '', toneCls(b.color), b.late ? 'is-late' : '', b.inferred ? 'is-inferred' : '', b.continuesLeft ? 'clip-left' : '', b.continuesRight ? 'clip-right' : ''],
          style: { gridColumn: `${b.col + 1} / span ${b.span}`, gridRow: String(b.lane + 2) },
          title: `${b.row ? b.row + '：' : ''}${b.text}`,
          text: collapsed ? '' : b.label,
        });
        bar.addEventListener('click', onClick(b.raw, () => bar));
        wk.appendChild(bar);
      }
      grid.appendChild(wk);
    }
    el.appendChild(grid);
  }

  // ---- 小框（點推測的日程、點月曆的日期；點框外或 Esc 關，裁定 39）----
  /** 在 anchor 旁邊放一個小框（一次只有一個）。 */
  showPop(anchor, cls, ...kids) {
    this.closePop();
    const pop = h('div', { cls: ['wwh-pop', cls], attrs: { role: 'dialog' } }, ...kids);
    this.root.appendChild(pop);
    const rect = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
    const base = this.root.getBoundingClientRect ? this.root.getBoundingClientRect() : null;
    if (rect && base) {
      const maxLeft = Math.max(8, (base.width || 0) - 308);
      pop.style.setProperty('left', `${Math.max(8, Math.min((rect.left || 0) - (base.left || 0), maxLeft))}px`);
      pop.style.setProperty('top', `${(rect.bottom || 0) - (base.top || 0) + 6}px`);
    }
    this.pop = pop;
    this.popAnchor = anchor;
    return pop;
  }

  closePop() {
    if (this.pop && this.pop.parentNode) this.pop.parentNode.removeChild(this.pop);
    this.pop = null;
    this.popAnchor = null;
  }

  onPopOutside(e) {
    if (!this.pop) return;
    const inside = (root, node) => {
      for (let n = node; n; n = n.parentNode) if (n === root) return true;
      return false;
    };
    const target = e && e.target;
    if (target && (inside(this.pop, target) || inside(this.popAnchor, target))) return;
    this.closePop();
  }

  /** 推測的日程：問她對不對。對（記成確認）、改到某天…（日期）、不用了；答完重畫，虛線變實線、「推測」小字消失。 */
  openInferPop(anchor, item) {
    if (!item || !item.id) return null;
    const p = this.plugin;
    const run = (how, date) => {
      this.closePop();
      return p.runAction(actions.infer(item, how, date));
    };
    const date = h('input', {
      cls: 'wwh-fill-input is-date wwh-hidden',
      attrs: { type: 'date' },
      on: {
        change: () => {
          const v = cleanFill({ input: 'date' }, date.value);
          if (v !== null) run('date', v);
        },
        keydown: (e) => {
          const v = cleanFill({ input: 'date' }, date.value);
          if (e.key === 'Enter' && v !== null) run('date', v);
        },
      },
    });
    const btn = (text, fn, cls) => h('span', { cls: ['wwh-answer', cls || ''], attrs: { role: 'button', tabindex: '0' }, text, on: { click: fn } });
    const toDate = btn('改到某天…', () => {
      date.classList.toggle('wwh-hidden');
      if (date.focus) date.focus();
    }, 'has-fill');
    const when = formatWhen(item);
    return this.showPop(
      anchor,
      'wwh-infer-pop',
      h('div', { cls: 'wwh-pop-title', text: item.text || '' }),
      when ? h('div', { cls: 'wwh-pop-meta', text: when }) : null,
      h('div', { cls: 'wwh-pop-ask', text: '這條是我從檔案推測的，對嗎？' }),
      h('div', { cls: 'wwh-confirm-buttons' }, btn('對', () => run('confirm')), toDate, btn('不用了', () => run('cancel'))),
      date,
      h('span', {
        cls: 'wwh-text-btn wwh-pop-open',
        attrs: { role: 'button', tabindex: '0' },
        text: '開相關的卡',
        on: {
          click: () => {
            this.closePop();
            p.openItem(item);
          },
        },
      }),
    );
  }

  /** 小框裡的案子名：點了開右邊總覽（「其他」那列不開）。 */
  entityName(mk, cls) {
    const openable = mk.rowKey !== OTHER_ROW_KEY && !!mk.row;
    return h('span', {
      cls: [cls, openable ? 'is-link' : ''],
      text: mk.row,
      title: openable ? `開「${mk.row}」的總覽` : null,
      on: {
        click: (e) => {
          if (!openable) return;
          if (e && e.stopPropagation) e.stopPropagation();
          this.closePop();
          this.plugin.openEntity(mk.row);
        },
      },
    });
  }

  /** 點月曆某天的日期或空白處：旁邊跳一個小框列那天所有的事（時間、事情、案子名，點一件開相關的卡），最下面「＋這天加一件」。 */
  openDayPop(anchor, day) {
    const p = this.plugin;
    const list = h('div', { cls: 'wwh-pop-list' });
    const all = asArray(day && day.all);
    if (!all.length) list.appendChild(h('div', { cls: 'wwh-empty', text: '這天沒有事' }));
    for (const mk of all) {
      const row = h(
        'div',
        { cls: ['wwh-pop-row', mk.late ? 'is-late' : '', mk.inferred ? 'is-inferred' : ''], attrs: { role: 'button', tabindex: '0' } },
        h('span', { cls: 'wwh-pop-time', text: mk.time || '' }),
        h('span', { cls: ['wwh-pop-dot', toneCls(mk.color)] }),
        h('span', { cls: 'wwh-pop-text', text: mk.text || '' }),
        mk.row ? this.entityName(mk, 'wwh-muted wwh-pop-row-name') : null,
      );
      row.addEventListener('click', () => {
        this.closePop();
        p.openItem(mk.raw);
      });
      list.appendChild(row);
    }
    return this.showPop(
      anchor,
      'wwh-day-pop',
      h('div', { cls: 'wwh-pop-title', text: formatWhen({ date: day.date }) || shortDate(day.date) }),
      list,
      h('span', {
        cls: 'wwh-text-btn wwh-pop-add',
        attrs: { role: 'button', tabindex: '0' },
        text: '＋這天加一件',
        on: {
          click: () => {
            this.closePop();
            p.addAgendaForm({ date: day.date });
          },
        },
      }),
    );
  }

  // ---- 待辦 ----
  renderTodo(el) {
    const p = this.plugin;
    el.appendChild(
      h(
        'div',
        { cls: 'wwh-section-head' },
        h('h2', { text: '待辦' }),
        h('span', { cls: 'wwh-text-btn wwh-add-btn', attrs: { role: 'button', tabindex: '0' }, text: '＋新增日程', on: { click: () => p.addAgendaForm() } }),
      ),
    );
    if (!p.home) return;
    const ui = p.ui;
    for (const g of todoGroups(p.home)) {
      const collapsed = ui.todoCollapsed[g.key] !== undefined ? ui.todoCollapsed[g.key] : g.defaultCollapsed;
      // 標題前一個小箭頭，標題本身可點；不畫成按鈕
      const head = h(
        'div',
        {
          cls: ['wwh-group-head', `is-${g.key}`],
          attrs: { role: 'button', tabindex: '0', 'aria-expanded': collapsed ? 'false' : 'true' },
          on: {
            click: () => {
              ui.todoCollapsed[g.key] = !collapsed;
              this.safe('待辦', this.leftEl, (e2) => this.renderTodo(e2));
            },
          },
        },
        h('span', { cls: ['wwh-chev', collapsed ? '' : 'is-open'] }, icon('chevron', 14, 2)),
        h('span', { text: g.label }),
        h('span', { cls: 'wwh-group-count', text: String(g.items.length) }),
      );
      const box = h('div', { cls: 'wwh-group' }, head);
      el.appendChild(box);
      if (collapsed) continue;
      if (!g.items.length) {
        box.appendChild(h('div', { cls: 'wwh-empty', text: '沒有' }));
        continue;
      }
      for (const it of g.items) box.appendChild(this.todoRow(it, g.key));
    }
  }

  /** 一件待辦：勾選框、標題（點下去開日程.md 那一行）、案子·種類·推測合成一行淡小字、日期（點了改日期時間）。 */
  todoRow(it, groupKey) {
    const row = h('div', { cls: ['wwh-todo', groupKey === 'overdue' || it.late ? 'is-late' : '', it.inferred ? 'is-inferred' : ''] });
    const box = h('input', {
      cls: 'wwh-check',
      attrs: { type: 'checkbox' },
      title: '打勾 = 完成',
      on: {
        click: () => {
          row.classList.add('is-done');
          this.plugin.runAction(actions.done(it));
        },
      },
    });
    const names = asArray(it.entities);
    const bits = [];
    if (names.length) {
      bits.push(
        h('span', {
          cls: 'wwh-todo-entity wwh-link',
          title: `開「${names[0]}」的總覽`,
          text: names[0],
          on: {
            click: (e) => {
              if (e && e.stopPropagation) e.stopPropagation();
              this.plugin.openEntity(names[0]);
            },
          },
        }),
      );
    }
    if (kindLabel(it.kind)) bits.push(h('span', { text: kindLabel(it.kind) }));
    if (it.inferred) {
      const tag = h('span', { cls: 'wwh-tag-inferred is-link', title: '這條是我推測的，對嗎？', attrs: { role: 'button', tabindex: '0' }, text: '推測' });
      tag.addEventListener('click', () => this.openInferPop(tag, it));
      bits.push(tag);
    }
    const meta = h('div', { cls: 'wwh-todo-meta' });
    bits.forEach((b, i) => {
      if (i) meta.appendChild(h('span', { cls: 'wwh-dot-sep', text: '·' }));
      meta.appendChild(b);
    });
    row.appendChild(box);
    row.appendChild(
      h('div', { cls: 'wwh-todo-body' }, h('span', { cls: 'wwh-todo-text', title: '開相關的卡', text: it.text || '', on: { click: () => this.plugin.openItem(it) } }), meta),
    );
    row.appendChild(
      h('span', { cls: 'wwh-todo-when', attrs: { role: 'button', tabindex: '0' }, title: '改日期、時間', text: formatWhen(it) || '（沒日期）', on: { click: () => this.plugin.editDateForm(it) } }),
    );
    return row;
  }

  // ---- 要你確認的（裁定 37）----
  /** 整區可收合（標題前小箭頭，收起時標題旁淡淡寫題數）；先顯示 3 題，其餘收在「還有 N 題」。 */
  renderRight(el) {
    const p = this.plugin;
    const ui = p.ui;
    const list = p.home ? p.home.confirm : [];
    const collapsed = !!ui.confirmCollapsed;
    const rerender = () => this.safe('要你確認的', this.rightEl, (e2) => this.renderRight(e2));
    const sure = h('section', { cls: 'wwh-block wwh-block-confirm' });
    el.appendChild(sure);
    sure.appendChild(
      h(
        'div',
        {
          cls: 'wwh-group-head is-confirm',
          attrs: { role: 'button', tabindex: '0', 'aria-expanded': collapsed ? 'false' : 'true' },
          on: {
            click: () => {
              ui.confirmCollapsed = !collapsed;
              rerender();
            },
          },
        },
        h('span', { cls: ['wwh-chev', collapsed ? '' : 'is-open'] }, icon('chevron', 14, 2)),
        h('h2', { text: '要你確認的' }),
        collapsed && list.length ? h('span', { cls: 'wwh-group-count', text: String(list.length) }) : null,
      ),
    );
    if (!p.home) return;
    if (collapsed) {
      this.renderClaude(el);
      return;
    }
    if (!list.length) sure.appendChild(h('div', { cls: 'wwh-empty', text: '沒有' }));
    const shown = ui.confirmAll ? list : list.slice(0, CONFIRM_SHOWN);
    for (const c of shown) sure.appendChild(this.confirmCard(c));
    if (shown.length < list.length) {
      sure.appendChild(
        h('span', {
          cls: 'wwh-text-btn wwh-confirm-more',
          attrs: { role: 'button', tabindex: '0' },
          text: `還有 ${list.length - shown.length} 題`,
          on: {
            click: () => {
              ui.confirmAll = true;
              rerender();
            },
          },
        }),
      );
    }
    this.renderClaude(el);
  }

  /** 一題一張卡：題目在上；左邊是答案（要填的字後面有「…」，點了在卡上展開），右邊淡淡的「先放著」和一個對話圖示（點了複製「關於「題目」：」）。 */
  confirmCard(c) {
    const p = this.plugin;
    const ui = p.ui;
    const parts = splitAnswers(c);
    const openN = ui.confirmFill && ui.confirmFill.key === c.key ? ui.confirmFill.n : -1;
    const answers = h('div', { cls: 'wwh-confirm-buttons' });
    parts.main.forEach((r, n) => {
      const fills = isObject(r.fill);
      answers.appendChild(
        h('span', {
          cls: ['wwh-answer', fills ? 'has-fill' : '', openN === n ? 'is-on' : ''],
          attrs: { role: 'button', tabindex: '0' },
          text: answerLabel(r),
          on: {
            click: () => {
              if (!fills) {
                p.chooseResult(c, r);
                return;
              }
              ui.confirmFill = openN === n ? null : { key: c.key, n };
              this.safe('要你確認的', this.rightEl, (e2) => this.renderRight(e2));
            },
          },
        }),
      );
    });
    const quiet = h('div', { cls: 'wwh-confirm-quiet' });
    if (parts.aside) {
      quiet.appendChild(h('span', { cls: 'wwh-quiet is-aside', attrs: { role: 'button', tabindex: '0' }, text: ASIDE_TEXT, on: { click: () => p.chooseResult(c, parts.aside) } }));
    }
    quiet.appendChild(
      h('span', { cls: 'wwh-quiet wwh-chat-btn', title: CHAT_TIP, attrs: { role: 'button', tabindex: '0', 'aria-label': CHAT_TIP }, on: { click: () => p.copyAsk(c) } }, icon('message-circle', 15, 1.5)),
    );
    const card = h('div', { cls: 'wwh-confirm wwh-paper' }, h('div', { cls: 'wwh-confirm-text', text: c.text || '' }), h('div', { cls: 'wwh-confirm-foot' }, answers, quiet));
    if (openN >= 0 && parts.main[openN]) card.appendChild(this.fillRow(c, parts.main[openN]));
    return card;
  }

  /** 卡上展開的那一格：日期用日期選擇、名字用文字框、選單用下拉；Enter 或選好就跑，Esc 收起。 */
  fillRow(c, r) {
    const p = this.plugin;
    const ui = p.ui;
    const fill = r.fill;
    const kind = fillKind(fill);
    let sent = false;
    const close = () => {
      ui.confirmFill = null;
      this.safe('要你確認的', this.rightEl, (e2) => this.renderRight(e2));
    };
    const submit = (raw) => {
      const v = cleanFill(fill, raw);
      if (v === null || sent) return;
      sent = true;
      ui.confirmFill = null;
      p.chooseResult(c, r, v);
      this.safe('要你確認的', this.rightEl, (e2) => this.renderRight(e2));
    };
    let control;
    if (kind === 'select') {
      control = h('select', { cls: 'dropdown wwh-fill-select', on: { change: () => submit(control.value) } });
      control.appendChild(h('option', { attrs: { value: '' }, text: '選一個…' }));
      let prev;
      for (const o of fillOptions(fill)) {
        if (prev !== undefined && o.group !== prev) control.appendChild(h('option', { cls: 'wwh-fill-sep', attrs: { disabled: 'disabled' }, text: '──────' }));
        prev = o.group;
        control.appendChild(h('option', { attrs: { value: o.value }, text: o.label }));
      }
    } else {
      let composing = false;
      control = h('input', {
        cls: ['wwh-fill-input', kind === 'date' ? 'is-date' : 'is-text'],
        attrs: { type: kind === 'date' ? 'date' : 'text', placeholder: kind === 'date' ? '' : (fill && fill.name) || '' },
        on: {
          compositionstart: () => {
            composing = true;
          },
          compositionend: () => {
            composing = false;
          },
          keydown: (e) => {
            if (e.key === 'Escape') {
              close();
              return;
            }
            const d = searchDecision({ key: e.key, isComposing: e.isComposing, keyCode: e.keyCode, composing, value: control.value });
            if (d.search) {
              if (e.preventDefault) e.preventDefault();
              submit(d.query);
            }
          },
          change: () => {
            if (kind === 'date') submit(control.value);
          },
        },
      });
    }
    this.fillControl = control;
    softTimeout(() => {
      if (control.focus) control.focus();
    }, 0);
    return h('div', { cls: 'wwh-fill' }, control);
  }

  // ---- 實體總覽（右側抽屜）----
  renderDrawer(el) {
    const d = this.plugin.ui.drawer;
    el.classList.toggle('is-open', !!d.open);
    if (!d.open) return;
    const head = h(
      'div',
      { cls: 'wwh-drawer-head' },
      h('button', { cls: 'wwh-small-btn', text: '← 回上一頁', on: { click: () => this.plugin.drawerBack() } }),
      h('button', { cls: 'wwh-icon-btn', text: '✕', title: '關掉', on: { click: () => this.plugin.drawerClose() } }),
    );
    el.appendChild(head);
    if (d.loading && !d.data) {
      el.appendChild(h('div', { cls: 'wwh-empty', text: '讀取中……' }));
      return;
    }
    if (d.error) {
      el.appendChild(h('div', { cls: 'wwh-empty', text: d.error }));
      return;
    }
    if (d.data) this.drawerBody(el, d.data);
  }

  drawerBody(el, got) {
    const p = this.plugin;
    const e = got.entity;
    if (!e) {
      el.appendChild(h('h3', { text: got.query || '' }));
      el.appendChild(h('div', { cls: 'wwh-empty', text: asArray(got.mentions).length ? '沒有登記這個名稱；有這些卡提到它：' : '沒有登記這個名稱，也沒有卡提到它' }));
      for (const m of asArray(got.mentions)) el.appendChild(h('div', { cls: 'wwh-drawer-line', text: m.title || '' }));
      return;
    }
    const prog = e.progress ? progressWord(e.progress) : null;
    el.appendChild(
      h(
        'div',
        { cls: 'wwh-drawer-title' },
        h('h3', { text: e.name }),
        h('span', { cls: 'wwh-muted', text: [e.type, prog && prog !== '進行中' ? prog : ''].filter(Boolean).join('，') }),
      ),
    );
    const aliases = asArray(e.aliases);
    if (aliases.length) el.appendChild(h('div', { cls: 'wwh-drawer-aliases' }, h('span', { cls: 'wwh-muted', text: '也叫：' }), aliases.join('、')));
    // 標籤
    const tags = asArray(got.tags).filter(isObject);
    if (tags.length) {
      const box = h('div', { cls: 'wwh-drawer-tags' });
      for (const t of tags) {
        box.appendChild(h('div', { cls: 'wwh-drawer-line' }, h('span', { cls: 'wwh-muted', text: `${t.field}：` }), asArray(t.words).join('、') || '（沒有）'));
      }
      el.appendChild(box);
    }
    if (got.synthesis) {
      el.appendChild(h('div', { cls: 'wwh-drawer-line' }, h('a', { cls: 'wwh-link', text: '開綜合卡', on: { click: () => p.openPathInVault(got.synthesis) } })));
    }
    const section = (title, items, render) => {
      if (!items.length) return;
      el.appendChild(h('h4', { text: `${title}（${items.length}）` }));
      for (const it of items) el.appendChild(render(it));
    };
    section('日程', asArray(got.agenda), (a) =>
      h(
        'div',
        { cls: 'wwh-drawer-line' },
        h('input', { attrs: { type: 'checkbox' }, title: '打勾 = 完成', on: { click: () => p.runAction(actions.done(a)) } }),
        h('a', { cls: 'wwh-link', text: `${formatWhen(a) || ''} `, on: { click: () => p.editDateForm(a) } }),
        `${a.kind || ''} ${a.text || ''}`,
      ),
    );
    section('檔案卡', asArray(got.cards), (c) =>
      h(
        'div',
        { cls: ['wwh-drawer-line', c.ruled_out ? 'is-cancelled' : ''] },
        h('a', { cls: 'wwh-link', text: c.title || c.id, on: { click: () => p.openLookupCard(c) } }),
        h('span', { cls: 'wwh-muted', text: `　${[c.card_type, c.date].filter(Boolean).join('　')}${c.stale ? '　原檔改過' : ''}` }),
      ),
    );
    section('你說過的', asArray(got.statements), (s) => h('div', { cls: 'wwh-drawer-line', text: `${s.date ? s.date + ' ' : ''}${s.text || ''}` }));
    section('裁示', asArray(got.rulings), (r) => h('div', { cls: 'wwh-drawer-line', text: r.text || '' }));
    section('推算', asArray(got.findings), (f) => h('div', { cls: 'wwh-drawer-line', text: f.text ? `${f.text}` : f.redo || '' }));
  }
}

// ---------------------------------------------------------------------------------------------------------------
// 外掛本體
// ---------------------------------------------------------------------------------------------------------------

class WikiWithWillyPlugin extends Plugin {
  async onload() {
    this.ui = defaultUi();
    this.home = null;
    this.homeError = null;
    this.needsReload = false;
    this.refreshing = false;
    this.refreshAgain = false;
    this.lastKbAt = 0;
    this.applying = new Set();
    this.tracker = createApplyTracker();
    this.cardIndexes = null;
    this.activePath = null;
    this.loadedVersion = this.manifest && this.manifest.version;
    this.vaultPath = this.basePath();
    this.roots = findKbRoots({ vaultPath: this.vaultPath }); // 這個 vault 裡的知識庫（#66 乙，D-540）
    this.data = sanitizeStore(await this.loadData(), this.roots);
    this.ui.timelineDays = zoomOf(this.data.timelineDays);
    this.ui.monthCollapsed = this.data.monthCollapsed === true;
    this.runners = new Map();
    this.infos = new Map();
    this.runner = null;
    this.useRoot(this.data.current === undefined ? null : this.data.current);
    this.syncTreeStyle();
    if (this.atVaultRoot()) { // 開在上層時不動她的設定（D-544）
      try {
        this.app.vault.setConfig('showUnsupportedFiles', true);
      } catch (e) {
        console.error('[wiki-with-willy] 沒辦法開「偵測所有副檔名」', e);
      }
    }

    this.registerView(VIEW_TYPE, (leaf) => new HomeView(leaf, this));
    this.addRibbonIcon('home', '開啟首頁', () => this.activateHome());
    this.addCommand({ id: 'open-home', name: '開啟首頁', callback: () => this.activateHome() });
    // 卡上的「開原檔」是 obsidian://wiki-with-willy-open?id=<清冊編號>（D-328）：點了叫 kb 查這份在這台的位置再開
    if (this.registerObsidianProtocolHandler) {
      this.registerObsidianProtocolHandler(OPEN_ACTION, (params) => this.openById(params && params.id, this.protocolRoot()));
    }
    this.registerMarkdownPostProcessor((el, ctx) => this.linkifyCardRefs(el, ctx));
    if (this.registerEditorExtension) this.registerEditorExtension(this.cardRefExtension());

    const ws = this.app.workspace;
    this.registerEvent(ws.on('file-open', (file) => this.onFileOpen(file)));
    this.registerEvent(ws.on('active-leaf-change', () => this.onActiveChange()));
    this.registerEvent(ws.on('layout-change', () => this.onLayoutChange())); // 同一分頁切閱讀模式／即時預覽：卡底下那段重接
    this.registerEvent(ws.on('editor-change', (editor, info) => this.onEditorChange(editor, info)));
    this.registerEvent(this.app.vault.on('modify', (file) => this.onVaultModify(file)));
    this.registerDomEvent(document, 'selectionchange', () => this.onSelection());
    // 改了一行沒換行就切到 Claude 對話：Obsidian 視窗失去焦點也套用（記帳 3 確認 #5）
    if (typeof window !== 'undefined') this.registerDomEvent(window, 'blur', () => this.onWindowBlur());
    this.registerInterval(setInterval(() => this.checkSelfUpdate(), 60000));

    ws.onLayoutReady(() => {
      this.startWatch();
      this.startGitWatch();
      this.refresh();
      if (this.atVaultRoot()) this.ensureHomeOpen(); // 開在上層時不自動跳出首頁（D-544）
    });
  }

  onunload() {
    this.stopWatch();
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    if (this.panelTimer) clearTimeout(this.panelTimer);
    if (this.treeStyleEl && this.treeStyleEl.parentNode) this.treeStyleEl.parentNode.removeChild(this.treeStyleEl);
    this.treeStyleEl = null;
    this.flushNote();
  }

  // ---- 知識庫在 vault 的哪裡（#66 乙）：沒跑過 onload（roots 沒設）當成 vault 根就是知識庫 ----
  rootsList() {
    return Array.isArray(this.roots) ? this.roots : [''];
  }

  /** 首頁目前看的知識庫前綴；一個都沒有是 null。 */
  cur() {
    return this.current === undefined ? '' : this.current;
  }

  atVaultRoot() {
    const r = this.rootsList();
    return r.length === 1 && r[0] === '';
  }

  rootOf(vaultRel) {
    return rootOfPath(this.rootsList(), vaultRel);
  }

  /** vault 路徑 → {root, rel（庫內路徑）}；不在任何知識庫裡回 null（D-543：什麼都不做）。 */
  loc(vaultRel) {
    const s = String(vaultRel || '').replace(/\\/g, '/');
    if (!s) return null;
    const root = this.rootOf(s);
    return root === null ? null : { root, rel: stripRoot(root, s) };
  }

  /** 庫內路徑 → vault 路徑；root 沒給就是首頁目前看的那個。 */
  vp(rel, root) {
    return joinRoot(root === undefined ? this.cur() : root, rel);
  }

  rootDir(root) {
    return root ? path.join(this.vaultPath, ...String(root).split('/')) : this.vaultPath;
  }

  /** 那個知識庫的 kb runner（cwd 是知識庫根，findKb 比對的 projectPath 也是它）；用到才建，找不到 kb 是 null。 */
  ensureRunner(root) {
    if (!this.runners) this.runners = new Map();
    if (!this.infos) this.infos = new Map();
    if (this.runners.has(root)) return this.runners.get(root);
    const dir = this.rootDir(root);
    const siblings = this.rootsList().filter((r) => r !== root).map((r) => this.rootDir(r));
    const info = findKb({ vaultPath: dir, siblings, version: this.loadedVersion });
    let runner = null;
    if (info.ok) {
      runner = createKbRunner({ kbPath: info.kbPath, cwd: dir, execFile: this.execFileOverride });
      const inner = runner.run;
      runner.run = async (args) => {
        try {
          return await inner(args);
        } finally {
          this.lastKbAt = Date.now();
        }
      };
    }
    this.infos.set(root, info);
    this.runners.set(root, runner);
    return runner;
  }

  runnerFor(root) {
    if (root === null || root === undefined) return null;
    if (root === this.cur()) return this.runner || null;
    return this.ensureRunner(root);
  }

  /** 換首頁看的知識庫：this.runner、this.kbInfo 跟著換成那個庫的。 */
  useRoot(root) {
    if (!this.runners) this.runners = new Map();
    if (typeof this.current === 'string' && this.runner) this.runners.set(this.current, this.runner);
    this.current = root;
    if (root === null) {
      this.runner = null;
      this.kbInfo = { ok: false, reason: 'no-root' };
      return;
    }
    this.runner = this.ensureRunner(root);
    this.kbInfo = this.infos.get(root);
  }

  /** 重新找一次知識庫；跟上次不一樣才回 true（目前看的那個不見了就換成第一個）。 */
  rescanRoots() {
    const found = findKbRoots({ vaultPath: this.vaultPath });
    if (JSON.stringify(found) === JSON.stringify(this.rootsList())) return false;
    this.roots = found;
    this.cardIndexes = null;
    this.syncTreeStyle();
    if (!found.includes(this.current)) this.switchRoot(found.length ? found[0] : null, false, true);
    return true;
  }

  /** 首頁上方選了別的知識庫（#66 乙第 10 條）：便條還沒存的字先寫回原來那個庫，再換、重讀。 */
  async switchRoot(root, remember, gone) {
    if (root === this.current || (root !== null && !this.rootsList().includes(root))) return;
    if (gone) {
      // 原來那個庫已經不在了：不寫便條（寫了會把她搬走的夾建回來）
      if (this.noteTimer) clearTimeout(this.noteTimer);
      this.noteTimer = null;
    } else if (!(await this.flushNote())) {
      this.renderAll(); // 便條沒存成就留在原來那個庫（選單選回去），字還在
      return;
    }
    this.stopWatch();
    this.useRoot(root);
    if (remember !== false && root !== null) {
      this.data = Object.assign({}, this.data, { current: root });
      try {
        await this.saveData(this.data);
      } catch (e) {
        console.error('[wiki-with-willy] 存不了選的知識庫', e);
      }
    }
    const ui = this.ui;
    ui.drawer = { open: false, stack: [], data: null, loading: false, error: null };
    Object.assign(ui.search, { q: '', active: false, loading: false, result: null, error: null, seq: (ui.search.seq || 0) + 1 });
    ui.note = defaultNote();
    this.home = null;
    this.homeError = null;
    this.gitNotice = null;
    for (const v of this.views()) if (v.inputEl) v.inputEl.value = '';
    this.startWatch();
    this.startGitWatch();
    this.renderAll();
    await this.refresh();
  }

  /** 前綴不空的知識庫：在 document.head 放一個 <style> 藏它的「檔案索引」夾與 CLAUDE.md（卸載時拿掉）。 */
  syncTreeStyle() {
    const doc = typeof document !== 'undefined' ? document : null;
    const css = treeHideCss(this.rootsList());
    if (!css) {
      if (this.treeStyleEl && this.treeStyleEl.parentNode) this.treeStyleEl.parentNode.removeChild(this.treeStyleEl);
      this.treeStyleEl = null;
      return;
    }
    if (!doc || !doc.head) return;
    if (!this.treeStyleEl) {
      this.treeStyleEl = doc.createElement('style');
      this.treeStyleEl.setAttribute('data-wwh', 'tree');
      doc.head.appendChild(this.treeStyleEl);
    }
    this.treeStyleEl.textContent = css;
  }

  /** 從 obsidian:// 協定進來的開原檔：跟作用中那個檔所在的庫走；沒有作用中的檔、不在任何庫裡，用首頁目前看的。 */
  protocolRoot() {
    let file = null;
    try {
      const ws = this.app.workspace;
      file = ws.getActiveFile ? ws.getActiveFile() : null;
      if (!file && ws.getActiveViewOfType) {
        const view = ws.getActiveViewOfType(MarkdownView);
        file = view && view.file;
      }
    } catch (e) {
      file = null;
    }
    const l = file ? this.loc(this.filePathOf(file)) : null;
    return l ? l.root : this.cur();
  }

  basePath() {
    const a = this.app.vault.adapter;
    if (a && typeof a.getBasePath === 'function') return a.getBasePath();
    return (a && a.basePath) || '';
  }

  // ---- 分頁 ----
  views() {
    return this.app.workspace
      .getLeavesOfType(VIEW_TYPE)
      .map((l) => l.view)
      .filter((v) => v && typeof v.render === 'function');
  }

  renderAll() {
    for (const v of this.views()) v.render();
  }

  ensureHomeOpen() {
    if (this.app.workspace.getLeavesOfType(VIEW_TYPE).length === 0) this.activateHome();
  }

  async activateHome() {
    if (this.vaultPath && Array.isArray(this.roots) && this.rescanRoots()) this.refresh(); // 每次開首頁再找一次，不一樣才重畫
    const ws = this.app.workspace;
    let leaf = ws.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = ws.getLeaf('tab');
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    ws.revealLeaf(leaf);
  }

  // ---- 叫 kb ----
  /** root 沒給就是首頁目前看的那個知識庫。 */
  async kb(args, root) {
    const runner = this.runnerFor(root === undefined ? this.cur() : root);
    if (!runner) return { code: -1, stdout: '', stderr: NO_KB_TEXT };
    return runner.run(args);
  }

  toast(text, opts) {
    const o = opts || {};
    if (!o.undo) return new Notice(text, o.timeout || 6000);
    const frag = document.createDocumentFragment();
    frag.appendChild(h('span', { text: `${text}　` }));
    let notice = null;
    frag.appendChild(
      h('button', {
        cls: 'wwh-undo',
        text: '復原',
        on: {
          click: () => {
            if (notice && notice.hide) notice.hide();
            o.undo();
          },
        },
      }),
    );
    notice = new Notice(frag, o.timeout || 10000);
    return notice;
  }

  actionCtx() {
    return {
      kb: (args) => this.kb(args),
      toast: (t, o) => this.toast(t, o),
      refresh: () => this.scheduleRefresh(),
      log: (m) => console.error('[wiki-with-willy]', m),
    };
  }

  runAction(action) {
    return performAction(this.actionCtx(), action);
  }

  // ---- 首頁資料 ----
  scheduleRefresh() {
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = softTimeout(() => {
      this.debounceTimer = null;
      this.refresh();
    }, DEBOUNCE_MS);
  }

  async refresh() {
    if (!this.runner) {
      this.renderAll();
      return;
    }
    if (this.refreshing) {
      this.refreshAgain = true;
      return;
    }
    this.refreshing = true;
    try {
      do {
        this.refreshAgain = false;
        const root = this.cur();
        const runner = this.runner;
        if (!runner) { // 切到的那個庫叫不到 kb
          this.renderAll();
          break;
        }
        const range = this.ui.tlRange || initialTimelineRange(this.ui.timelineDays, localIso(new Date()));
        const res = await runner.run(homeArgs(openedOf(this.data, root), range));
        if (root !== this.cur()) { // 等的時候她切到別的知識庫：這份不畫，照新的再讀一次
          this.refreshAgain = true;
          continue;
        }
        const c = classify(res);
        if (c.kind === 'ok') {
          const j = parseJson(res.stdout);
          if (isObject(j)) {
            this.home = normalizeHome(j);
            this.homeError = null;
          } else {
            this.homeError = 'kb 回的不是首頁資料';
          }
        } else {
          this.homeError = c.message;
          if (c.kind === 'failed') console.error('[wiki-with-willy] kb home 失敗', res.stderr || res.stdout);
        }
        this.checkSelfUpdate();
        await this.readNote();
        this.renderAll();
        this.refreshPanels();
        if (this.ui.drawer.open && this.ui.drawer.stack.length) this.loadDrawer(false);
      } while (this.refreshAgain);
    } finally {
      this.refreshing = false;
    }
  }

  /** 關掉 .system 與 .git 的監看（切換知識庫、卸載）。 */
  stopWatch() {
    for (const key of ['watcher', 'gitWatcher']) {
      if (!this[key]) continue;
      try {
        this[key].close();
      } catch (e) {
        // 關不掉也沒關係
      }
      this[key] = null;
    }
    if (this.gitTimer) {
      clearTimeout(this.gitTimer);
      this.gitTimer = null;
    }
  }

  startWatch() {
    if (this.cur() === null) return;
    const dir = path.join(this.rootDir(this.cur()), '.system');
    try {
      this.watcher = fs.watch(dir, { persistent: false }, (evt, name) => {
        if (name && !WATCH_FILES.includes(String(name))) return;
        this.scheduleRefresh();
      });
      this.watcher.on('error', (e) => console.error('[wiki-with-willy] 看 .system 出錯', e));
    } catch (e) {
      console.error('[wiki-with-willy] 沒辦法看 .system 的變動', e);
    }
  }

  // 看 .git 的 HEAD／ORIG_HEAD／FETCH_HEAD（多人共用 3，D-293）：變了等 2 秒叫 kb refresh，再刷新首頁。沒有 .git 就不看。
  // 只看知識庫夾自己的 .git，上層 vault 的不看（D-546）。
  startGitWatch() {
    if (this.cur() === null) return;
    const dir = path.join(this.rootDir(this.cur()), '.git');
    try {
      if (!fs.existsSync(dir)) return;
      this.gitWatcher = fs.watch(dir, { persistent: false }, (evt, name) => {
        if (!isGitWatchFile(name)) return;
        if (this.gitTimer) clearTimeout(this.gitTimer);
        this.gitTimer = softTimeout(() => {
          this.gitTimer = null;
          this.gitRefresh();
        }, GIT_DEBOUNCE_MS);
      });
      this.gitWatcher.on('error', (e) => console.error('[wiki-with-willy] 看 .git 出錯', e));
    } catch (e) {
      console.error('[wiki-with-willy] 沒辦法看 .git 的變動', e);
    }
  }

  async gitRefresh() {
    const root = this.cur();
    const res = await this.kb(['refresh', '--json'], root);
    if (root !== this.cur()) return; // 她已經切到別的知識庫
    const c = classify(res);
    this.gitNotice = c.kind === 'ok' ? gitRefreshNotice(parseJson(res.stdout)) : null;
    this.scheduleRefresh();
  }

  checkSelfUpdate() {
    if (this.needsReload || !this.manifest || !this.manifest.dir) return;
    try {
      const text = fs.readFileSync(path.join(this.vaultPath, this.manifest.dir, 'manifest.json'), 'utf8');
      if (manifestChanged(this.loadedVersion, text)) {
        this.needsReload = true;
        this.renderAll();
      }
    } catch (e) {
      // 讀不到就不提示
    }
  }

  async reloadSelf() {
    const plugins = this.app.plugins;
    try {
      await plugins.disablePlugin(PLUGIN_ID);
      await plugins.enablePlugin(PLUGIN_ID);
    } catch (e) {
      console.error('[wiki-with-willy] 重新載入失敗', e);
      new Notice('重新載入沒成功，請到設定裡把這個外掛關掉再打開');
    }
  }

  // ---- 開檔 ----
  openCtx() {
    let shell = null;
    try {
      shell = require('electron').shell;
    } catch (e) {
      shell = null;
    }
    return {
      platform: process.platform,
      toast: (t) => this.toast(t),
      openPath: (p) => (shell ? shell.openPath(p) : Promise.resolve('這台電腦沒辦法從 Obsidian 開檔')),
      openUrl: (u) => {
        if (shell && shell.openExternal) shell.openExternal(u);
        else if (typeof window !== 'undefined') window.open(u);
      },
    };
  }

  /** 隨手記那一頁：打開首頁、每次重新整理都讀一次（沒有就是空白）；她正在打字就不蓋掉。 */
  async readNote() {
    const note = this.ui.note;
    const adapter = this.app && this.app.vault && this.app.vault.adapter;
    if (!note || !adapter || !adapter.exists || this.cur() === null) return;
    const rel = this.vp(NOTE_REL); // 目前看的那個知識庫的 系統/隨手記.md
    try {
      const disk = (await adapter.exists(rel)) ? await adapter.read(rel) : null;
      if (note !== this.ui.note) return; // 讀的時候她切到別的知識庫
      note.saved = disk;
      const next = noteIncoming(note, disk);
      if (next !== null) note.text = next;
    } catch (e) {
      console.error('[wiki-with-willy] 讀不到隨手記那一頁', e);
    }
  }

  /** 便條的字改了：記下來，停手約 1.5 秒就存回那一頁。 */
  noteEdited(text) {
    const note = this.ui.note;
    note.text = String(text == null ? '' : text);
    note.dirty = note.text !== (note.saved == null ? '' : note.saved);
    if (this.noteTimer) clearTimeout(this.noteTimer);
    this.noteTimer = note.dirty ? softTimeout(() => this.flushNote(), NOTE_DELAY_MS) : null;
  }

  /** 游標進、出便條：進去時重畫不蓋她的字；離開框就立刻存。 */
  noteFocus(on) {
    this.ui.note.focused = !!on;
    if (!on) this.flushNote();
  }

  /** 把便條存回那一頁（用 Obsidian 的 vault；沒有就建，空的也照存、不刪檔）。回有沒有存成。 */
  async flushNote() {
    if (this.noteTimer) {
      clearTimeout(this.noteTimer);
      this.noteTimer = null;
    }
    const note = this.ui.note;
    if (!note.dirty || this.cur() === null) return true;
    const text = note.text;
    const adapter = this.app && this.app.vault && this.app.vault.adapter;
    const dir = this.vp(NOTE_DIR); // 先算好：存的途中切換知識庫也寫回原來那個
    const rel = this.vp(NOTE_REL);
    try {
      if (!(await adapter.exists(dir))) await adapter.mkdir(dir);
      await adapter.write(rel, text);
      note.saved = text;
      note.dirty = note.text !== text; // 存的時候她又打了字就還沒完
      return true;
    } catch (e) {
      console.error('[wiki-with-willy] 隨手記沒存成', e);
      this.toast('隨手記沒存成');
      return false;
    }
  }

  /** 記住時間軸上次選的縮放（data.json）。 */
  async saveTimelineDays(days) {
    this.data = Object.assign({}, this.data, { timelineDays: Math.round(zoomOf(days)) });
    try {
      await this.saveData(this.data);
    } catch (e) {
      console.error('[wiki-with-willy] 存不了時間軸的縮放', e);
    }
  }

  /** 記住月曆收起或展開（data.json）。 */
  async saveMonthCollapsed(on) {
    this.data = Object.assign({}, this.data, { monthCollapsed: on === true });
    try {
      await this.saveData(this.data);
    } catch (e) {
      console.error('[wiki-with-willy] 存不了月曆的收起', e);
    }
  }

  /** 時間軸捲到快出已載入的範圍：多跟 kb 要一段（記下範圍，之後每次重新整理都帶著）。回有沒有去要。 */
  ensureTimelineRange(start, visible, explicit) {
    const tl = this.home && this.home.timeline;
    if (!tl || !this.runner) return false;
    const from = dayNumber(tl.from);
    const to = dayNumber(tl.to);
    const today = dayNumber(this.home.today);
    if (from === null || to === null || today === null) return false;
    const want = wantedTimelineRange(start, visible, from, to, today, explicit);
    if (!want) return false;
    const have = this.ui.tlRange;
    if (have && have.from === want.from && have.to === want.to) return false; // 要過了還沒回來（或回不來），不重複要
    this.ui.tlRange = want;
    this.refresh();
    return true;
  }

  async recordOpened(card, root) {
    if (!card) return;
    this.data = recordOpenedIn(this.data, root === undefined ? this.cur() : root, card, new Date().toISOString());
    try {
      await this.saveData(this.data);
    } catch (e) {
      console.error('[wiki-with-willy] 存不了最近開過的', e);
    }
  }

  async openFile(f) {
    if (f.card) this.recordOpened(f.card);
    const hasOpen = isObject(f.open) && (f.open.path || f.open.url);
    if (!hasOpen && f.card_path) {
      this.openPathInVault(f.card_path); // 沒有原檔可開，退一步開卡
      return;
    }
    await openOriginal(this.openCtx(), f.open);
  }

  /** 開原檔連結（D-328）：kb lookup --id 查位置；查不到就說一句、退一步開卡。root 沒給就是首頁目前看的知識庫。 */
  async openById(id, root) {
    const fid = String(id || '').trim();
    if (!fid) {
      this.toast('這個連結少了檔案編號');
      return false;
    }
    const at = root === undefined ? this.cur() : root;
    const res = await this.kb(['lookup', '--id', fid, '--json'], at);
    const c = classify(res);
    if (c.kind !== 'ok') {
      this.toast(c.message);
      return false;
    }
    const j = parseJson(res.stdout);
    const f = isObject(j) ? j : {};
    if (f.card_path) this.recordOpened(fid, at);
    if (isObject(f.open) && (f.open.path || f.open.url)) return openOriginal(this.openCtx(), f.open);
    this.toast('這份沒有原檔可以開');
    if (f.card_path) this.openPathInVault(f.card_path, at);
    return false;
  }

  openCard(f) {
    if (f.card) this.recordOpened(f.card);
    if (f.card_path) this.openPathInVault(f.card_path);
  }

  openLookupCard(c) {
    if (c.id) this.recordOpened(c.id);
    if (c.path) this.openPathInVault(c.path);
  }

  /** kb 給的庫內路徑 → 交給 Obsidian 的 vault 路徑。root 沒給是首頁目前看的知識庫；已經是 vault 路徑的帶 ''。 */
  openPathInVault(rel, root) {
    this.app.workspace.openLinkText(this.vp(String(rel), root).replace(/\.md$/, ''), '', false);
  }

  clipCtx() {
    return {
      toast: (t) => this.toast(t),
      writeClipboard: async (t) => {
        try {
          await navigator.clipboard.writeText(t);
        } catch (e) {
          require('electron').clipboard.writeText(t);
        }
      },
    };
  }

  async askClaude(q) {
    return copyAskClaude(this.clipCtx(), q);
  }

  // ---- 實體抽屜 ----
  openEntity(name) {
    const d = this.ui.drawer;
    d.open = true;
    if (d.stack[d.stack.length - 1] !== name) d.stack.push(name);
    d.data = null;
    d.error = null;
    this.loadDrawer(true);
  }

  drawerBack() {
    const d = this.ui.drawer;
    d.stack.pop();
    if (!d.stack.length) {
      this.drawerClose();
      return;
    }
    d.data = null;
    this.loadDrawer(true);
  }

  drawerClose() {
    const d = this.ui.drawer;
    d.open = false;
    d.stack = [];
    d.data = null;
    d.error = null;
    this.renderAll();
  }

  async loadDrawer(showLoading) {
    const d = this.ui.drawer;
    const name = d.stack[d.stack.length - 1];
    if (!name) return;
    d.loading = true;
    if (showLoading) this.renderAll();
    const res = await this.kb(['lookup', name, '--json']);
    if (d !== this.ui.drawer || d.stack[d.stack.length - 1] !== name) return; // 她已經點去別的、或切到別的知識庫
    const c = classify(res);
    d.loading = false;
    if (c.kind === 'ok') {
      d.data = parseJson(res.stdout);
      d.error = d.data ? null : 'kb 回的不是總覽資料';
      if (d.data && d.data.entity && d.data.entity.name) d.stack[d.stack.length - 1] = d.data.entity.name; // 改名、別名查到現在的名字
    } else {
      d.error = c.message;
    }
    this.renderAll();
  }

  // ---- 點下去：日程開相關的卡、案子開綜合卡（D-266、裁定 32）----
  /** 點日程那件：kb 給的 card（origin 的檔案卡 → 掛的第一個實體的綜合卡）；都沒有才開日程.md 那一行。 */
  async openItem(it) {
    const card = it && isObject(it.card) ? it.card : null;
    if (card && card.path) {
      this.openPathInVault(card.path);
      return;
    }
    await this.openAgendaLine(it);
  }

  async openAgendaLine(it, root) {
    const rel = this.vp(MANAGED_PAGES[0], root);
    let line = 0;
    try {
      line = findAgendaLine(await this.app.vault.adapter.read(rel), it);
    } catch (e) {
      console.error('[wiki-with-willy] 讀不到日程.md', e);
    }
    await this.app.workspace.openLinkText(rel.replace(/\.md$/, ''), '', false);
    const view = this.app.workspace.getActiveViewOfType ? this.app.workspace.getActiveViewOfType(MarkdownView) : null;
    if (view && view.editor) {
      const pos = { line, ch: 0 };
      view.editor.setCursor(pos);
      if (view.editor.scrollIntoView) view.editor.scrollIntoView({ from: pos, to: pos }, true);
    }
  }

  /** 開一頁、游標跳到 kb 給的那一行（日程頁；卡底下那段用）。 */
  async openPageLine(page, match, root) {
    const rel = this.vp(String(page), root);
    let line = 0;
    try {
      line = findPageLine(await this.app.vault.adapter.read(rel), match);
    } catch (e) {
      console.error('[wiki-with-willy] 讀不到', rel, e);
    }
    await this.app.workspace.openLinkText(String(rel).replace(/\.md$/, ''), '', false);
    const view = this.app.workspace.getActiveViewOfType ? this.app.workspace.getActiveViewOfType(MarkdownView) : null;
    if (view && view.editor) {
      const pos = { line, ch: 0 };
      view.editor.setCursor(pos);
      if (view.editor.scrollIntoView) view.editor.scrollIntoView({ from: pos, to: pos }, true);
    }
  }

  /** 點案子名稱開它的綜合卡；還沒有綜合卡就開旁邊的小預覽。 */
  async openSynthesis(name) {
    const root = this.cur();
    const res = await this.kb(['lookup', name, '--json'], root);
    if (root !== this.cur()) return; // 等的時候她切到別的知識庫了
    const got = classify(res).kind === 'ok' ? parseJson(res.stdout) : null;
    if (got && got.synthesis) {
      this.openPathInVault(got.synthesis, root);
      return;
    }
    this.openEntity(name);
  }

  entityNames() {
    const rows = this.home && this.home.timeline ? asArray(this.home.timeline.rows) : [];
    return rows.filter((r) => r && r.key !== '_other' && r.label).map((r) => r.label);
  }

  async addAgendaForm(prefill) {
    const p = prefill || {};
    const today = (this.home && this.home.today) || '';
    const v = await openForm(this.app, {
      title: '新增日程',
      okText: '加進日程',
      fields: [
        { key: 'kind', label: '種類', type: 'select', options: ['到期', '會議', '承諾', '提醒', '期間'], value: p.kind || '到期' },
        { key: 'date', label: '日期（期間是結束日）', type: 'date', value: p.date || today, required: true },
        { key: 'start', label: '開始日（期間才填）', type: 'date', value: p.start || '' },
        { key: 'time', label: '時間（可不填）', type: 'time', value: p.time || '' },
        { key: 'text', label: '要做什麼', type: 'text', value: p.text || '', required: true },
        { key: 'entity', label: '哪件事（可不填）', type: 'text', value: p.entity || '', list: this.entityNames() },
      ],
    });
    if (!v) return null;
    const r = await this.runAction(
      actions.add({ kind: v.kind, date: v.date, start: v.start, time: v.time, text: v.text, entities: v.entity ? [v.entity] : [] }),
    );
    return r;
  }

  async editDateForm(it) {
    const isPeriod = isPeriodKind(it.kind);
    const fields = [{ key: 'date', label: isPeriod ? '結束日' : '日期', type: 'date', value: itemDate(it), required: true }];
    if (isPeriod) fields.unshift({ key: 'start', label: '開始日', type: 'date', value: it.start || '' });
    fields.push({ key: 'time', label: '時間（可不填）', type: 'time', value: it.time || '' });
    const v = await openForm(this.app, { title: `改「${it.text || ''}」的日期`, okText: '改', fields });
    if (!v) return null;
    const a = actions.edit(it, v);
    if (!a) {
      this.toast('沒有要改的');
      return null;
    }
    return this.runAction(a);
  }

  /** 最下面那行的對話圖示：複製「幫我看一下待處理」。 */
  copyReview() {
    return copyForChat(this.clipCtx(), REVIEW_PHRASE);
  }

  /** 每張確認卡的對話圖示：複製「關於「題目」：」。 */
  copyAsk(item) {
    return copyForChat(this.clipCtx(), chatPhrase(item));
  }

  async chooseResult(item, result, fillValue) {
    const argv = result.argv;
    if (!Array.isArray(argv)) return null; // 只能在對話裡做的，首頁不列（裁定 37）
    // 「加成日程」這類只給了原文的：開新增表單，原文填進「要做什麼」，存了再接著做 result.after
    if (argv[0] === 'agenda' && argv[1] === 'add' && !argv.includes('--date')) {
      const at = argv.indexOf('--text');
      const text = (isObject(result.fill) && result.fill.value) || (at >= 0 ? argv[at + 1] : '') || '';
      const r = await this.addAgendaForm({ text });
      if (r && r.ok && Array.isArray(result.after)) await this.kb(result.after);
      return r;
    }
    if (result.fill && fillValue === undefined) {
      const f = isObject(result.fill) ? result.fill : { label: String(result.fill) };
      const v = await openForm(this.app, {
        title: result.label || '請填',
        okText: '好',
        fields: [{ key: 'value', label: f.label || f.rule || f.name || '請填', type: 'text', value: f.value || '', required: true }],
      });
      if (!v) return null;
      fillValue = v.value;
    }
    return this.runAction(actions.result(result, fillValue));
  }

  // ---- 程式產生的頁 ----
  filePathOf(file) {
    return file && file.path ? String(file.path).replace(/\\/g, '/') : '';
  }

  onFileOpen(file) {
    const p = this.filePathOf(file);
    this.sweepPanels();
    this.switchedTo(p);
    const l = this.loc(p);
    if (l && isGeneratedPage(l.rel)) this.makeReadOnly(p);
    if (l && file) this.showPanel(file);
  }

  /** 這個 vault 路徑是不是某個知識庫的受管頁（用庫內路徑判斷；不在任何庫裡的不算）。 */
  managed(p) {
    const l = this.loc(p);
    return !!l && isManagedPage(l.rel);
  }

  // ---- 綜合卡底下的「日程」（D-340）：畫面，不寫進卡檔 ----
  /** 是綜合卡（檔頭的編號是 e-）才叫 kb entity panel；kb 叫不到時那段不畫、不報錯（記 console）。 */
  async showPanel(file) {
    const p = this.filePathOf(file);
    const l = this.loc(p);
    if (!l || !l.rel.endsWith('.md') || l.rel.startsWith('系統/') || l.rel.startsWith('檔案索引/')) return;
    let id = null;
    try {
      const cache = this.app.metadataCache.getFileCache(file);
      const fm = cache && cache.frontmatter;
      id = fm && typeof fm['編號'] === 'string' && /^e-[0-9a-z]+$/i.test(fm['編號']) ? fm['編號'] : null;
    } catch (e) {
      id = null;
    }
    if (!id || !this.runnerFor(l.root)) return;
    if (!this.panels) this.panels = new Map();
    let model = null;
    try {
      const res = await this.kb(['entity', 'panel', id, '--json'], l.root);
      const c = classify(res);
      if (c.kind === 'ok') model = panelModel(parseJson(res.stdout));
      else console.error('[wiki-with-willy] kb entity panel 失敗', res.stderr || res.stdout || c.message);
    } catch (e) {
      console.error('[wiki-with-willy] kb entity panel 失敗', e);
    }
    if (model) this.panels.set(p, Object.assign(model, { root: l.root })); // 點那段開的頁在同一個庫
    else this.panels.delete(p);
    this.drawPanels(p);
  }

  /** 開著這張卡的每個分頁：閱讀模式與即時預覽各接一份（先拿掉舊的；接在哪看 panelSpots）。 */
  drawPanels(p) {
    const model = this.panels ? this.panels.get(p) : null;
    for (const leaf of this.app.workspace.getLeavesOfType('markdown')) {
      const view = leaf.view;
      if (!view || !view.file || this.filePathOf(view.file) !== p) continue;
      removePanels(view);
      if (!model) continue;
      for (const spot of panelSpots(view)) {
        const box = this.buildPanel(model);
        box.setAttribute(PANEL_CARD_ATTR, p);
        spot.parent.insertBefore(box, spot.before);
        if (spot.renderer && typeof spot.renderer.updateFooter === 'function') {
          try {
            spot.renderer.updateFooter(); // footer 高度變了，讓閱讀模式重算捲動
          } catch (e) {
            console.error('[wiki-with-willy] 閱讀模式重算 footer 失敗', e);
          }
        }
      }
    }
  }

  /** 分頁換了檔（點那段的一行跳到日程頁）：不是它現在這個檔的那段拿掉（記帳 3 確認 #3）。 */
  sweepPanels() {
    for (const leaf of this.app.workspace.getLeavesOfType('markdown')) {
      const view = leaf.view;
      if (view) removePanels(view, view.file ? this.filePathOf(view.file) : '');
    }
  }

  /** 版面變了（同一分頁切閱讀模式與即時預覽、分頁搬動、換檔）：換了檔的拿掉；已經算好的那段照接回去，不再叫 kb；drawPanels 先拿掉舊的，不會重複。 */
  onLayoutChange() {
    this.sweepPanels();
    if (!this.panels || !this.panels.size) return;
    if (this.panelTimer) clearTimeout(this.panelTimer);
    this.panelTimer = softTimeout(() => {
      this.panelTimer = null;
      for (const p of this.panels.keys()) this.drawPanels(p);
    }, PANEL_REDRAW_MS);
  }

  /** 首頁資料重讀時（日程變了），開著的綜合卡底下那段跟著重算。 */
  refreshPanels() {
    if (!this.panels || !this.panels.size) return;
    const open = new Map();
    for (const leaf of this.app.workspace.getLeavesOfType('markdown')) {
      const f = leaf.view && leaf.view.file;
      if (f && this.panels.has(this.filePathOf(f))) open.set(this.filePathOf(f), f);
    }
    for (const p of [...this.panels.keys()]) if (!open.has(p)) this.panels.delete(p);
    for (const f of open.values()) this.showPanel(f);
  }

  buildPanel(model) {
    const line = (text, page, match) =>
      h('li', { cls: 'wwh-panel-line', text, title: '開那一頁的這一行', on: { click: () => this.openPageLine(page, match, model.root) } });
    const box = h('div', { cls: [PANEL_CLS, 'markdown-rendered'], attrs: { contenteditable: 'false' } }, h('div', { cls: 'wwh-panel-title', text: PANEL_TITLE }));
    box.appendChild(h('ul', {}, ...model.agenda.map((a) => line(a.line || a.text || '', a.page || MANAGED_PAGES[0], a.match || a.text))));
    return box;
  }

  onActiveChange() {
    this.sweepPanels();
    const view = this.app.workspace.getActiveViewOfType ? this.app.workspace.getActiveViewOfType(MarkdownView) : null;
    this.switchedTo(view && view.file ? this.filePathOf(view.file) : null);
  }

  switchedTo(now) {
    const prev = this.activePath;
    this.activePath = now || null;
    if (prev && prev !== now && this.managed(prev) && this.tracker.leave(prev)) this.applyPage(prev);
  }

  makeReadOnly(p) {
    const leaf = this.app.workspace.getLeavesOfType('markdown').find((l) => l.view && l.view.file && this.filePathOf(l.view.file) === p);
    if (!leaf) return;
    const view = leaf.view;
    const finish = () => this.addGeneratedBanner(view);
    try {
      const state = leaf.getViewState();
      if (state && state.state && state.state.mode !== 'preview') {
        const next = Object.assign({}, state, { state: Object.assign({}, state.state, { mode: 'preview' }) });
        Promise.resolve(leaf.setViewState(next)).then(() => setTimeout(finish, 50), finish);
        return;
      }
    } catch (e) {
      console.error('[wiki-with-willy] 切不了閱讀模式', e);
    }
    finish();
  }

  addGeneratedBanner(view) {
    try {
      const root = view && view.contentEl;
      if (!root || (root.querySelector && root.querySelector('.wwh-generated'))) return;
      const bar = h('div', { cls: 'wwh-generated', text: GENERATED_BANNER });
      root.insertBefore(bar, root.firstChild);
    } catch (e) {
      console.error('[wiki-with-willy] 加不了提示條', e);
    }
  }

  onEditorChange(editor, info) {
    const p = this.filePathOf(info && info.file);
    if (!p || !this.managed(p)) return;
    const line = editor.getCursor().line;
    if (this.tracker.edited(p, line, Date.now())) this.applyPage(p);
  }

  /** Obsidian 視窗失去焦點：還沒套用的受管頁都套用（她改了一行沒換行就切到 Claude 對話，記帳 3 確認 #5）。 */
  onWindowBlur() {
    for (const p of this.tracker.dirtyPages()) if (this.tracker.leave(p)) this.applyPage(p);
  }

  onSelection() {
    const view = this.app.workspace.getActiveViewOfType ? this.app.workspace.getActiveViewOfType(MarkdownView) : null;
    if (!view || !view.file || !view.editor) return;
    const p = this.filePathOf(view.file);
    if (!this.managed(p)) return;
    if (this.tracker.cursor(p, view.editor.getCursor().line)) this.applyPage(p);
  }

  async saveOpen(p) {
    for (const leaf of this.app.workspace.getLeavesOfType('markdown')) {
      const v = leaf.view;
      if (v && v.file && this.filePathOf(v.file) === p && typeof v.save === 'function') {
        try {
          await v.save();
        } catch (e) {
          console.error('[wiki-with-willy] 存不了開著的頁', e);
        }
      }
    }
  }

  async applyPage(p) {
    const l = this.loc(p);
    const runner = l ? this.runnerFor(l.root) : null; // 那一頁所在的知識庫（D-543），傳給 kb 的是庫內路徑
    if (!runner || this.applying.has(p)) return;
    this.applying.add(p);
    try {
      await this.saveOpen(p);
      const res = await runner.run(['renders', 'apply', l.rel, '--json']);
      this.tracker.applied(p);
      const c = classify(res);
      if (c.kind !== 'ok') {
        this.toast(c.message);
        if (c.kind === 'failed') console.error('[wiki-with-willy] kb renders apply 失敗', res.stderr || res.stdout);
        return;
      }
      const sum = summarizeApply(parseJson(res.stdout), pageName(p));
      if (sum.text) this.toast(sum.text);
      this.scheduleRefresh();
    } finally {
      // kb 改寫檔案的 modify 事件會晚一點才到，多擋一下
      softTimeout(() => this.applying.delete(p), 1500);
    }
  }

  onVaultModify(file) {
    const p = this.filePathOf(file);
    const l = this.loc(p);
    if (!l || !(isManagedPage(l.rel) || isGeneratedPage(l.rel))) return;
    const open = this.app.workspace.getLeavesOfType('markdown').some((l) => l.view && l.view.file && this.filePathOf(l.view.file) === p);
    const now = Date.now();
    const typing = this.tracker.isTyping(p, now);
    if (!shouldToastModified({ open, applying: this.applying.has(p), typing, lastKbAt: this.lastKbAt, now })) return;
    this.tracker.external(p, now);
    const reading = this.app.vault.cachedRead ? this.app.vault.cachedRead(file) : Promise.resolve('');
    Promise.resolve(reading).then(
      (text) => this.toast(hasCarriedSection(text) && this.tracker.isTyping(p, now, 60000) ? `${UPDATED_TEXT}；${CARRIED_TEXT}` : UPDATED_TEXT),
      () => this.toast(UPDATED_TEXT),
    );
  }

  // ---- 〔f-…〕變連結 ----
  /** 〔f-…〕那張卡的 vault 路徑：只在同一個知識庫的 檔案索引/ 裡找，索引每個庫各一份（D-543）。root 沒給是首頁目前看的。 */
  cardPathOf(id, root) {
    const at = root === undefined ? this.cur() : root;
    if (at === null) return null;
    if (!this.cardIndexes) this.cardIndexes = new Map();
    const now = Date.now();
    let ix = this.cardIndexes.get(at);
    if (!ix || (!ix.map.has(id) && now - ix.at > 10000)) {
      const map = new Map();
      const titles = new Map();
      const dir = joinRoot(at, '檔案索引/');
      for (const f of this.app.vault.getMarkdownFiles()) {
        if (!String(f.path).startsWith(dir)) continue;
        const cache = this.app.metadataCache.getFileCache(f);
        const fm = cache && cache.frontmatter;
        const cid = fm && (fm['編號'] || fm.id);
        if (typeof cid === 'string') {
          map.set(cid, f.path);
          const t = fm['標題'] || fm.title;
          if (typeof t === 'string' && t.trim()) titles.set(cid, t.trim());
        }
      }
      ix = { map, titles, at: now };
      this.cardIndexes.set(at, ix);
    }
    return ix.map.get(id) || null;
  }

  cardTitleOf(id, root) {
    const at = root === undefined ? this.cur() : root;
    this.cardPathOf(id, at);
    const ix = this.cardIndexes && this.cardIndexes.get(at);
    return (ix && ix.titles.get(id)) || null;
  }

  /** 出處的小檔案圖示（裁定 41）：跟字同高、淡色，滑過去提示那張卡的標題，點了開那張卡；檔案裡存的字不變。
   *  editor 是編輯模式（裁定 43）：CodeMirror 的 mousedown 會先把游標放進那段、圖示換回原字，click 到不了，所以在 mousedown 就開卡、不讓游標動。 */
  cardRefIcon(ref, target, editor, root) {
    const title = this.cardTitleOf(ref, root) || String(target).replace(/^.*\//, '').replace(/\.md$/, '');
    const a = dom.doc().createElement('a');
    a.className = 'wwh-cardref';
    a.setAttribute('href', target);
    a.setAttribute('data-href', target);
    a.setAttribute('title', title);
    a.setAttribute('aria-label', `出處：${title}`);
    a.appendChild(icon('file-text', 14, 1.4));
    const stop = (e) => {
      if (e && e.preventDefault) e.preventDefault();
      if (e && e.stopPropagation) e.stopPropagation();
    };
    if (editor) {
      a.addEventListener('mousedown', (e) => {
        stop(e);
        if (e && e.button) return; // 右鍵、中鍵不開
        this.openPathInVault(target, ''); // target 已經是 vault 路徑
      });
      a.addEventListener('click', stop);
      return a;
    }
    a.addEventListener('click', (e) => {
      stop(e);
      this.openPathInVault(target, '');
    });
    return a;
  }

  /** 編輯模式（Live Preview）的裝飾：把〔f-…〕換成同一個小圖示；游標進到那段、找不到卡、原始碼模式都顯示原字。回 CodeMirror 的 extension（讀不到 CM 回 []）。 */
  cardRefExtension() {
    let view;
    let state;
    try {
      view = require('@codemirror/view');
      state = require('@codemirror/state');
    } catch (e) {
      return [];
    }
    if (!view || !state || !view.ViewPlugin || !view.Decoration || !view.WidgetType) return [];
    const plugin = this;
    class RefWidget extends view.WidgetType {
      constructor(ref, target, root) {
        super();
        this.ref = ref;
        this.target = target;
        this.root = root;
      }

      eq(other) {
        return other.ref === this.ref && other.target === this.target;
      }

      toDOM() {
        return plugin.cardRefIcon(this.ref, this.target, true, this.root);
      }

      // 圖示上的滑鼠事件編輯器不管（不放游標），由圖示自己處理
      ignoreEvent() {
        return true;
      }
    }
    const build = (v) => {
      const lp = obsidian.editorLivePreviewField;
      if (lp && v.state.field && v.state.field(lp, false) === false) return view.Decoration.none; // 原始碼模式不改
      // 這個編輯器開的檔在哪個知識庫（讀不到就用首頁目前看的）；不在任何庫裡的不換
      let root = plugin.cur();
      try {
        const info = obsidian.editorInfoField && v.state.field ? v.state.field(obsidian.editorInfoField, false) : null;
        if (info && info.file && info.file.path) root = plugin.rootOf(String(info.file.path));
      } catch (e) {
        root = plugin.cur();
      }
      if (root === null) return view.Decoration.none;
      const sel = v.state.selection.ranges;
      const marks = [];
      for (const r of v.visibleRanges) {
        const text = v.state.doc.sliceString(r.from, r.to);
        for (const x of cardRefRanges(text, r.from)) {
          if (sel.some((q) => q.from <= x.to && q.to >= x.from)) continue; // 游標進到那段：顯示原字照常改
          const target = plugin.cardPathOf(x.ref, root);
          if (!target) continue;
          marks.push(view.Decoration.replace({ widget: new RefWidget(x.ref, target, root) }).range(x.from, x.to));
        }
      }
      return view.Decoration.set(marks, true);
    };
    return view.ViewPlugin.fromClass(
      class {
        constructor(v) {
          this.decorations = build(v);
        }

        update(u) {
          if (u.docChanged || u.selectionSet || u.viewportChanged) this.decorations = build(u.view);
        }
      },
      { decorations: (v) => v.decorations },
    );
  }

  linkifyCardRefs(el, ctx) {
    // 這一頁在哪個知識庫（後處理的 ctx.sourcePath）；沒給就用首頁目前看的；不在任何庫裡的頁不動
    const root = ctx && ctx.sourcePath ? this.rootOf(String(ctx.sourcePath)) : this.cur();
    if (root === null) return;
    const doc = el.ownerDocument || document;
    const walker = doc.createTreeWalker(el, 4 /* NodeFilter.SHOW_TEXT */);
    const texts = [];
    let n = walker.nextNode();
    while (n) {
      if (n.nodeValue && n.nodeValue.indexOf('〔f-') >= 0) texts.push(n);
      n = walker.nextNode();
    }
    for (const node of texts) {
      const parent = node.parentNode;
      if (!parent || (parent.closest && parent.closest('a, code, pre'))) continue;
      const parts = splitCardRefs(node.nodeValue);
      if (!parts.some((x) => x.ref)) continue;
      const frag = doc.createDocumentFragment();
      for (const part of parts) {
        if (part.text !== undefined) {
          frag.appendChild(doc.createTextNode(part.text));
          continue;
        }
        const target = this.cardPathOf(part.ref, root);
        if (!target) {
          frag.appendChild(doc.createTextNode(`〔${part.ref}〕`)); // 找不到那張卡的照舊顯示原字
          continue;
        }
        frag.appendChild(this.cardRefIcon(part.ref, target, false, root));
      }
      parent.replaceChild(frag, node);
    }
  }
}

module.exports = WikiWithWillyPlugin;
module.exports.default = WikiWithWillyPlugin;
module.exports.lib = {
  PLUGIN_ID,
  VIEW_TYPE,
  WATCH_FILES,
  OPENED_MAX,
  GENERATED_BANNER,
  NO_KB_TEXT,
  VERSION_MISMATCH_TEXT,
  RELOAD_TEXT,
  COPIED_TEXT,
  CARRIED_TEXT,
  UPDATED_TEXT,
  NEED_CLAUDE_TEXT,
  firstLine,
  parseJson,
  samePath,
  isNetworkPath,
  nativePath,
  dayNumber,
  dayToIso,
  formatWhen,
  markSpan,
  layoutTimeline,
  layoutMonth,
  shiftMonth,
  describeMark,
  normalizeHome,
  todoGroups,
  versionMismatch,
  manifestChanged,
  sanitizeData,
  recordOpened,
  openedIds,
  NO_ROOTS_TEXT,
  findKbRoots,
  joinRoot,
  stripRoot,
  rootOfPath,
  rootLabels,
  sanitizeStore,
  openedOf,
  recordOpenedIn,
  treeHideCss,
  homeArgs,
  findKb,
  pythonCandidates,
  buildKbArgs,
  createKbRunner,
  classify,
  progressWord,
  actions,
  performAction,
  runUndo,
  searchDecision,
  findArgs,
  removeChipText,
  askClaudeText,
  copyAskClaude,
  openOriginal,
  isManagedPage,
  isGeneratedPage,
  findPageLine,
  panelModel,
  createApplyTracker,
  shouldToastModified,
  gitRefreshNotice,
  isGitWatchFile,
  GIT_DEBOUNCE_MS,
  summarizeApply,
  hasCarriedSection,
  splitCardRefs,
  cardRefRanges,
  rowDefaultCollapsed,
  isDueToday,
  openMarksOnly,
  findAgendaLine,
  rowIsCollapsed,
  markWhen,
  fileExt,
  longDate,
  icon,
  RECENT_MAX,
  MONTH_COLORS,
  OPEN_ACTION,
  NOTE_REL,
  NOTE_DELAY_MS,
  REVIEW_PHRASE,
  fillKind,
  fillOptions,
  answerLabel,
  cleanFill,
  defaultNote,
  noteIncoming,
  DEFAULT_ZOOM,
  MIN_ZOOM,
  MAX_ZOOM,
  zoomOf,
  savedZoom,
  wheelZoomFactor,
  zoomAround,
  timelineColors,
  toneCls,
  defaultTimelineStart,
  clampTimelineStart,
  wantedTimelineRange,
  initialTimelineRange,
  horizontalWheel,
  dragStartsHere,
  pinnedMonth,
  splitAnswers,
  chatPhrase,
  copyForChat,
  localIso,
  HomeView,
  FormModal,
  defaultUi,
  h,
};
