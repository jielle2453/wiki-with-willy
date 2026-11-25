"""網頁首頁與看卡的 API（#74 第 3 份，D-355、D-266、D-268、D-252、D-340、D-328、D-324）：web.py 每個請求先問 route，
回 True 表示這裡處理了。只用標準函式庫。

叫 kb：[python, <plugin>/scripts/kb.py, (--via web), …]，工作目錄＝知識庫、PYTHONIOENCODING=utf-8、不開視窗、60 秒；
結束碼照外掛：0 成功（訊息用 stdout 第一行）、3 被拒（stdout 第一行原樣）、其他「沒做成：<stderr 第一行>」。
只讀的（home、find、lookup、entity panel，D-361 的 agenda list、pending list、cards list）不帶 --via web，跟外掛一樣不開回合；她按的動作帶 --via web 自己開回合（D-255）。
/api/cards 另外直接讀現成的 .system/inventory.json 補每張的原檔副檔名（ext，只讀）。
網頁不能送任意 argv：/api/act 只收幾種動作，參數驗過格式、值一律用 --旗標=值 傳（不會被當成別的旗標）；
「要你確認的」的答案由這裡重抓一次 kb home 找 argv，不收網頁送來的。
POST 一律要 X-Willy: 1、Origin（有的話）要是自己的網址、JSON 64 KB 上限（上傳另計 50 MB）；
GET /api/open 會在主機上開檔，也要 X-Willy: 1（別的網站的 <img>、連結帶不了自訂標頭）。
讀檔照守門的分區：.system、.claude、.obsidian、CLAUDE.md（Z7）與根目錄的 AGENTS.md、說不讀的夾（Z10、Z10b）、憑證樣式的檔名一律 404；
在主機開檔（/api/open）也過同一套，且副檔名要在白名單 OPENABLE。
手機離線排隊（第 4 份，D-358）：/api/act 的 done、edit 可帶 expect（排隊時在首頁看到的 date、start、time、status），
先用 kb agenda list --all --json 找那件，任一個不同或找不到 → 409「情況變了：…」不執行；
/api/note 收 {"append": "<字>", "at": "<時間>"}：只在 系統/隨手記.md 檔尾加一行「<字>（手機離線時記的，<時間>）」，不比 base。
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, quote

KB_TIMEOUT = 60
POST_MAX = 64 * 1024
UPLOAD_MAX = 50 * 1024 * 1024
PAGE_MAX = 2 * 1024 * 1024
FILE_MAX = 100 * 1024 * 1024
OPENED_REL = ".system/local/web/opened.json"
OPENED_MAX = 30
NOTE_REL = "系統/隨手記.md"
REPORT_DIR = "系統/健檢報告"
UPLOAD_ROOT = Path(tempfile.gettempdir()) / "willy-upload"
UPLOAD_TRIES = 9  # 同名不同內容：改用「<主檔名> 2」…「<主檔名> 10」再試
UPLOAD_NAME = "上傳的檔"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
KINDS = ("到期", "會議", "提醒", "期間")
DEFAULT_KIND = "到期"  # 外掛新增表單的預設
NO_PANEL_DIRS = ("系統/", "檔案索引/")
GONE = "這題已經不在了"
NOTHING_TO_EDIT = "沒有要改的"
OPENED_HERE = "已在電腦上打開"
NETWORK = "這個檔在網路磁碟上，請到檔案總管開：{path}"
NO_ORIGINAL = "這份沒有原檔可以開"
CANT_OPEN = "開不了這份：{why}"
NO_KIND = "這種檔請到檔案總管開：{path}"
NOT_FROM_WEB = "這個檔不能從網頁開"
# 從網頁在主機上開的檔只收這些（白名單）：她自己的文件、圖、影音；巨集檔（xlsm 等）交給 Office 自己的安全設定
OPENABLE = {".pdf", ".txt", ".md", ".csv", ".rtf", ".doc", ".docx", ".docm", ".dot", ".dotx", ".xls", ".xlsx", ".xlsm",
            ".xlsb", ".ppt", ".pptx", ".pptm", ".odt", ".ods", ".odp",
            ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".svg",
            ".mp3", ".m4a", ".wav", ".mp4", ".mov", ".avi",
            ".zip", ".dwg", ".dxf", ".skp"}
ROOT_HIDDEN = ("claude.md", "agents.md")  # 知識庫根目錄給 AI 的指示（Claude 版、Codex 版）：不給看
NOTE_CONFLICT = "別的地方剛改過，已重新載入"
NOTE_APPEND = "{text}（手機離線時記的，{at}）"
NOTE_APPEND_MAX = 2000
EXPECT_KEYS = ("date", "start", "time", "status")
EXPECT_ACTIONS = ("done", "edit")
CHANGED = "情況變了：{}"
STATUS_SAID = {"done": "已經做完了", "cancelled": "已經取消了", "open": "又打開了"}
UPLOAD_OK = "已放進暫存區，AI 會接著整理"
CHAT_UPLOADED = "我從網頁傳了 {n} 個檔到暫存區：{names}"
BAD_ZONES = ("Z7", "Z10", "Z10b", "Z11", "Z13")
INLINE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}  # SVG 不 inline（裡面可以有程式）
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,80}$")
_FID = re.compile(r"^f-[0-9A-Za-z]+$")
_EID = re.compile(r"^e-[0-9A-Za-z]+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_BAD_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')
_lock = threading.Lock()


class Bad(ValueError):
    """請求不對：回 400 與這句。"""


# ---------- 叫 kb ----------

def _python():
    """叫 kb 用的 python：小伺服器若是 pythonw 跑的，換成同一夾的 python.exe（不開視窗靠 CREATE_NO_WINDOW）。"""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        return str(exe.parent / "python.exe")
    return sys.executable


def _exec(argv, cwd, timeout):
    """跑一次，回 (結束碼, stdout, stderr)。測試換掉它。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return -2, "", f"超過 {timeout} 秒沒做完"
    except OSError as e:
        return -1, "", f"{type(e).__name__}: {e}"
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def run_kb(vault, args, via=False):
    from kb._lib import paths
    argv = [_python(), str(paths.plugin_root() / "scripts" / "kb.py")] + (["--via", "web"] if via else []) + list(args)
    return _exec(argv, str(vault), KB_TIMEOUT)


def _first(text):
    for line in str(text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def outcome(code, out, err):
    """(成功嗎, 給她看的一句)。"""
    if code == 0:
        return True, _first(out)
    if code == 3:
        return False, _first(out) or _first(err) or "沒做成"
    return False, "沒做成：" + (_first(err) or _first(out) or f"結束碼 {code}")


def _parse(out):
    try:
        got = json.loads(out)
    except ValueError:
        return None
    return got


# ---------- 回應 ----------

def _send(handler, code, body, ctype="application/json; charset=utf-8", extra=None):
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for k, v in (extra or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler, code, obj):
    _send(handler, code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _error(handler, code, text=""):
    handler.close_connection = True
    _json(handler, code, {"error": text or {400: "請求不對", 403: "不准", 404: "找不到", 405: "不收這個方法",
                                            409: "已經變了", 413: "太大了"}.get(code, "出錯了")})


# ---------- 路由 ----------

def route(handler, method, path, query):
    """web.py 每個請求先叫這支（Host 已經檢查過）；處理了回 True。"""
    gets = {"/api/home": _home, "/api/find": _find, "/api/page": _page, "/api/latest-report": _latest,
            "/api/open": _open, "/api/file": _file, "/api/note": _note_get,
            "/api/agenda": _agenda, "/api/pending": _pending, "/api/cards": _cards}
    posts = {"/api/act": _act, "/api/upload": _upload, "/api/note": _note_post}
    table = gets if method == "GET" else posts if method == "POST" else {}
    vault = Path(handler.server.vault)
    params = {k: v[0] for k, v in parse_qs(query or "", keep_blank_values=True).items()}
    if path in table:
        table[path](handler, vault, params)
        return True
    if path in gets or path in posts:
        allow = ", ".join(m for m, t in (("GET", gets), ("POST", posts)) if path in t)
        handler.close_connection = True
        _send(handler, 405, b"", "text/plain; charset=utf-8", {"Allow": allow})
        return True
    return False


def _host(handler):
    return (handler.headers.get("Host") or "").strip().lower()


def _is_local(handler):
    """她就在主機前（網址是 127.0.0.1 或 localhost）；第 4 份的手機走別的網域。"""
    return _host(handler).rsplit(":", 1)[0] in LOCAL_HOSTS


DRAIN_MAX = 1024 * 1024


def _drain(handler):
    """擋掉的請求先把 body 讀掉（1 MB 以內）：沒讀的 body 留在連線上，關連線時 Windows 送 RST，用戶端讀不到錯誤碼。"""
    try:
        n = int(handler.headers.get("Content-Length", ""))
    except ValueError:
        return
    if 0 < n <= DRAIN_MAX:
        handler.rfile.read(n)


def _willy_ok(handler):
    """X-Willy: 1，有 Origin 時要是自己的網址；不對送 403 回 False。"""
    from kb._lib import web
    origin = handler.headers.get("Origin")
    if (handler.headers.get("X-Willy") or "").strip() != "1" or (
            origin is not None and origin.strip().lower() not in web.own_origins(handler)):
        if handler.command == "POST":
            _drain(handler)
        _error(handler, 403)
        return False
    return True


def _length(handler, limit):
    """Content-Length；沒有或不對送 400、超過 limit 送 413，回 None。"""
    try:
        n = int(handler.headers.get("Content-Length", ""))
    except ValueError:
        n = -1
    if n < 0:
        _error(handler, 400, "少了 Content-Length")
        return None
    if n > limit:
        _drain(handler)
        _error(handler, 413)
        return None
    return n


def _body(handler):
    """POST 的 JSON（照網頁對話的規矩）；不對送錯誤回 None。"""
    if not _willy_ok(handler):
        return None
    n = _length(handler, POST_MAX)
    if n is None:
        return None
    raw = handler.rfile.read(n)
    try:
        return {"data": json.loads(raw.decode("utf-8"))}
    except (ValueError, UnicodeDecodeError):
        _error(handler, 400, "不是 JSON")
        return None


# ---------- 首頁、找卡 ----------

def _opened_path(vault):
    return Path(vault) / OPENED_REL


def _read_opened(vault):
    from kb._lib.io import read_json
    try:
        data = read_json(_opened_path(vault))
    except (OSError, ValueError):
        return []
    ids = data.get("ids") if isinstance(data, dict) else None
    return [i for i in ids if isinstance(i, str) and _FID.match(i)][:OPENED_MAX] if isinstance(ids, list) else []


def _remember(vault, fid):
    """看卡時記「最近開過」：新的在前、最多 30 個。"""
    from kb._lib.io import write_json_atomic
    with _lock:
        ids = [fid] + [i for i in _read_opened(vault) if i != fid]
        p = _opened_path(vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, {"ids": ids[:OPENED_MAX]})


def _home(handler, vault, params):
    opened = _read_opened(vault)
    code, out, err = run_kb(vault, ["home", "--json"] + (["--opened", ",".join(opened)] if opened else []))
    if code != 0:
        _json(handler, 502, {"error": outcome(code, out, err)[1]})
        return
    _send(handler, 200, out.encode("utf-8"))


def _read_only(handler, vault, args, shape, wrap=None):
    """只讀的 kb 指令（不帶 --via web）的 --json 原樣轉給網頁（D-361）；回的不是 shape（dict／list）就 502。
    wrap：list 包成 {wrap: list}。"""
    code, out, err = run_kb(vault, args)
    got = _parse(out) if code == 0 else None
    if not isinstance(got, shape):
        _json(handler, 502, {"error": outcome(code, out, err)[1] if code else "沒做成：回的看不懂"})
        return
    _json(handler, 200, {wrap: got} if wrap else got)


def _agenda(handler, vault, params):
    """日程頁（D-361）：kb agenda list --all --json（含過去、做完、取消的）。"""
    _read_only(handler, vault, ["agenda", "list", "--all", "--json"], dict)


def _pending(handler, vault, params):
    """待處理頁（D-361）：kb pending list --json（還沒處理的，含先放著的），包成 {"items": […]}。"""
    _read_only(handler, vault, ["pending", "list", "--json"], list, "items")


def _cards(handler, vault, params):
    """總目錄頁（D-361）：kb cards list --json（全部的檔案卡：標題、卡型、日期、路徑…），每張補 ext（原檔副檔名，圖示上色用）。"""
    code, out, err = run_kb(vault, ["cards", "list", "--json"])
    got = _parse(out) if code == 0 else None
    if not isinstance(got, dict):
        _json(handler, 502, {"error": outcome(code, out, err)[1] if code else "沒做成：回的看不懂"})
        return
    exts = _exts(vault)
    for c in got.get("cards") or []:
        if isinstance(c, dict):
            c["ext"] = exts.get(c.get("id"), "")
    _json(handler, 200, got)


def _exts(vault):
    """原檔副檔名 {f-編號: "pdf"}：直接讀 .system/inventory.json 現成的（不經 inventory.load，那會重寫衍生副本），
    取每份第一個位置的副檔名；網址條目、讀不到、形狀不對都當沒有（圖示退回照卡型）。"""
    from kb._lib.io import read_json
    try:
        data = read_json(Path(vault) / ".system" / "inventory.json")
    except (OSError, ValueError):
        return {}
    out = {}
    for e in (data.get("entries") if isinstance(data, dict) else None) or []:
        if not isinstance(e, dict) or e.get("kind") == "link" or not isinstance(e.get("id"), str):
            continue
        locs = e.get("locations") if isinstance(e.get("locations"), list) else []
        uri = locs[0].get("uri") if locs and isinstance(locs[0], dict) else ""
        m = re.search(r"\.([0-9A-Za-z]{1,8})$", str(uri or ""))
        if m:
            out[e["id"]] = m.group(1).lower()
    return out


def _find(handler, vault, params):
    q = str(params.get("q") or "").strip()
    if not q:
        _json(handler, 200, {"q": "", "parsed": [], "entities": [], "files": [], "total": 0, "more": False})
        return
    code, out, err = run_kb(vault, ["find", "--json", "--", q] if q.startswith("-") else ["find", "--json", q])
    got = _parse(out) if code == 0 else None
    if not isinstance(got, dict):
        _json(handler, 502, {"error": outcome(code, out, err)[1] if code else "沒做成：找卡回的看不懂"})
        return
    _entity_paths(vault, got.get("entities"))
    _json(handler, 200, got)


def _entity_paths(vault, rows):
    """找卡的實體列補 card_path（綜合卡現在在哪），點了開那張；找不到是 None。"""
    from kb._lib import entities
    try:
        data = entities.load(vault)
    except (OSError, ValueError):
        data = None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        row["card_path"] = None
        try:
            e = entities.find(data, row.get("name"))[0] if data else None
            row["card_path"] = entities.locate(vault, e, data) if e else None
        except (OSError, ValueError, KeyError, TypeError):
            pass


# ---------- 讀知識庫裡的頁 ----------

def _guard_ctx(vault):
    from kb._lib import paths
    from kb._lib import sources as sources_lib
    real = os.path.realpath(str(vault))
    return real, paths.normalize(real, None), sources_lib.load(vault)


def _refused(path_n, vault_n, sources, rel_segments=()):
    """守門的分區：Z7（.system、.claude、.obsidian、CLAUDE.md）、Z13、說不讀的夾（Z10、Z10b）、知識庫外沒登記的（Z11）、
    憑證樣式的檔名（來源裡用守門的第一道；知識庫裡守門不比，這裡另外比每一段）都不給。"""
    from kb import guard
    from kb._lib import credentials
    zone, _alias = guard.classify(path_n, vault_n, sources)
    if zone in BAD_ZONES:
        return True
    if guard.credential_hit(path_n, zone, vault_n, sources) is not None:
        return True
    return credentials.match_segment(list(rel_segments)) is not None


def resolve_page(vault, rel):
    """網頁給的相對路徑（知識庫根目錄起算）→ (真正的路徑, 正規的相對路徑)；不准讀的回 None。"""
    from kb._lib import paths
    text = str(rel or "").replace("\\", "/")
    if not text.strip() or "\x00" in text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    real_v, vault_n, sources = _guard_ctx(vault)
    target = os.path.realpath(os.path.join(real_v, text))
    target_n = paths.normalize(target, None)
    if not paths.inside(target_n, vault_n) or target_n == vault_n:
        return None
    if not target.lower().endswith(".md") or not os.path.isfile(target):
        return None
    rel_out = os.path.relpath(target, real_v).replace("\\", "/")
    if rel_out.lower() in ROOT_HIDDEN:
        return None
    if _refused(target_n, vault_n, sources, rel_out.split("/")):
        return None
    return target, rel_out


def front_matter(text):
    """開頭 --- 到 --- 的 key: value（只認一行一個的簡單寫法）；回 (dict, 內文從第幾行開始)。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            out = {}
            for line in lines[1:i]:
                m = re.match(r"^([^\s:#][^:]*):\s*(.*?)\s*$", line)
                if m:
                    out[m.group(1).strip()] = m.group(2).strip("\"'")
            return out, i + 1
    return {}, 0


def _title(text, start, rel):
    for line in text.split("\n")[start:]:
        if line.startswith("# "):
            return line[2:].strip() or Path(rel).stem
    return Path(rel).stem


def _page(handler, vault, params):
    got = resolve_page(vault, params.get("path"))
    if got is None:
        _error(handler, 404)
        return
    target, rel = got
    if os.path.getsize(target) > PAGE_MAX:
        _error(handler, 404)
        return
    with open(target, "rb") as f:
        text = f.read().decode("utf-8-sig", "replace").replace("\r\n", "\n")
    front, start = front_matter(text)
    fid = str(front.get("編號") or "")
    panel = None
    if _EID.match(fid) and not rel.startswith(NO_PANEL_DIRS):
        code, out, _err = run_kb(vault, ["entity", "panel", fid, "--json"])
        panel = _parse(out) if code == 0 else None
        if not isinstance(panel, dict):
            panel = None
    elif _FID.match(fid):
        _remember(vault, fid)
    _json(handler, 200, {"path": rel, "title": _title(text, start, rel), "markdown": text, "panel": panel})


def _latest(handler, vault, params):
    """健檢報告最新的一份（檔名排最後的 .md）；沒有回 null。"""
    folder = Path(vault) / REPORT_DIR
    names = sorted(p.name for p in folder.glob("*.md") if p.is_file()) if folder.is_dir() else []
    rel = f"{REPORT_DIR}/{names[-1]}" if names else None
    _json(handler, 200, {"path": rel if rel and resolve_page(vault, rel) else None})


# ---------- 開原檔、下載 ----------

def open_file(target):
    """在主機上用預設程式開（網址用預設瀏覽器）。測試換掉它，不真的開。"""
    if hasattr(os, "startfile"):
        os.startfile(target)  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])  # noqa: S603


def _lookup(handler, vault, params):
    """id → kb lookup --id 的結果；id 不對送 400 回 None；kb 不成回 {"error": 句子}。"""
    fid = str(params.get("id") or "")
    if not _FID.match(fid):
        _error(handler, 400, "檔案編號不對")
        return None
    code, out, err = run_kb(vault, ["lookup", "--id", fid, "--json"])
    got = _parse(out) if code == 0 else None
    if not isinstance(got, dict):
        return {"id": fid, "error": outcome(code, out, err)[1] if code else "沒做成：查原檔回的看不懂"}
    got["id"] = fid
    return got


def _open(handler, vault, params):
    if not _willy_ok(handler):
        return
    got = _lookup(handler, vault, params)
    if got is None:
        return
    if got.get("error"):
        _json(handler, 200, {"ok": False, "message": got["error"]})
        return
    target = got.get("open") if isinstance(got.get("open"), dict) else {}
    url, path = str(target.get("url") or ""), str(target.get("path") or "")
    web_url = url if re.match(r"^https?://", url, re.I) else ""
    if not _is_local(handler):
        if path:
            _json(handler, 200, {"download": f"/api/file?id={quote(got['id'])}"})
        elif web_url:
            _json(handler, 200, {"url": web_url})
        else:
            _json(handler, 200, {"ok": False, "message": NO_ORIGINAL})
        return
    if not web_url and not path:
        _json(handler, 200, {"ok": False, "message": NO_ORIGINAL})
        return
    native = web_url or os.path.normpath(path)
    if not web_url and (native.startswith("\\\\") or path.startswith("//")):
        _json(handler, 200, {"ok": False, "message": NETWORK.format(path=native)})
        return
    if not web_url:
        if os.path.splitext(native)[1].lower() not in OPENABLE:
            _json(handler, 200, {"ok": False, "message": NO_KIND.format(path=native)})
            return
        real = resolve_file(vault, native)  # 跟 /api/file 同一套：說不讀的夾、憑證名、知識庫外沒登記的都不開
        if real is None:
            _json(handler, 200, {"ok": False, "message": NOT_FROM_WEB})
            return
        if os.path.splitext(real)[1].lower() not in OPENABLE:  # 連結解出來是別種檔
            _json(handler, 200, {"ok": False, "message": NO_KIND.format(path=real)})
            return
        native = real
    try:
        open_file(native)
    except OSError as e:
        _json(handler, 200, {"ok": False, "message": CANT_OPEN.format(why=e.strerror or type(e).__name__)})
        return
    _json(handler, 200, {"ok": True, "message": OPENED_HERE})


def resolve_file(vault, path):
    """原檔的絕對路徑 → 真正的路徑；說不讀的、憑證名、知識庫外沒登記的、.system 等回 None。"""
    from kb._lib import paths
    if not path or "\x00" in str(path):
        return None
    real_v, vault_n, sources = _guard_ctx(vault)
    target = os.path.realpath(os.path.normpath(str(path)))
    if not os.path.isfile(target):
        return None
    target_n = paths.normalize(target, None)
    segs = os.path.relpath(target, real_v).replace("\\", "/").split("/") if paths.inside(target_n, vault_n) else ()
    if _refused(target_n, vault_n, sources, segs):
        return None
    return target


def _file(handler, vault, params):
    got = _lookup(handler, vault, params)
    if got is None:
        return
    target = got.get("open") if isinstance(got.get("open"), dict) else {}
    path = resolve_file(vault, target.get("path")) if not got.get("error") else None
    if path is None:
        _error(handler, 404)
        return
    size = os.path.getsize(path)
    if size > FILE_MAX:
        _error(handler, 413)
        return
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    ctype = INLINE_TYPES.get(ext, "application/octet-stream")
    how = "inline" if ext in INLINE_TYPES else "attachment"
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(size))
    handler.send_header("Content-Disposition", f"{how}; filename*=UTF-8''{quote(name, safe='')}")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    with open(path, "rb") as f:
        shutil.copyfileobj(f, handler.wfile, 1024 * 1024)


# ---------- 按的動作 ----------

def _id(body, key="id"):
    v = body.get(key)
    if not isinstance(v, str) or not _ID.match(v):
        raise Bad(f"{key} 不對")
    return v


def _opt(body, key, rx):
    v = body.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not rx.match(v):
        raise Bad(f"{key} 格式不對")
    return v


def _item(vault, aid):
    from kb._lib import agenda
    try:
        return next((i for i in agenda.load(vault)["items"] if i.get("id") == aid), None)
    except (OSError, ValueError, KeyError):
        return None


def _orig(it):
    it = it or {}
    return {"date": it.get("date") or "", "start": it.get("start") or "", "time": it.get("time") or ""}


def _run_act(vault, args):
    code, out, err = run_kb(vault, args, via=True)
    ok, msg = outcome(code, out, err)
    return ok, msg, out


def _act(handler, vault, params):
    got = _body(handler)
    if got is None:
        return
    body = got["data"]
    try:
        if not isinstance(body, dict) or not isinstance(body.get("action"), str) or body["action"] not in ACTIONS:
            raise Bad("不認得的動作")
        changed = _expect(vault, body) if body["action"] in EXPECT_ACTIONS and "expect" in body else None
        if changed is not None:
            status, res = changed
        else:
            status, res = ACTIONS[body["action"]](vault, body)
    except Bad as e:
        _error(handler, 400, str(e))
        return
    _json(handler, status, res)


def _expect(vault, body):
    """離線排隊送來的（第 4 份）：那件現在的 date、start、time、status 跟 expect 一樣才做；回 None（照做）或 (狀態碼, 回應)。
    日程讀不到回 503（排隊留著再送），那件不在或欄位不同回 409。"""
    exp = body.get("expect")
    if (not isinstance(exp, dict) or not exp or any(k not in EXPECT_KEYS for k in exp)
            or any(v is not None and not isinstance(v, str) for v in exp.values())):
        raise Bad("expect 不對")
    aid = _id(body)
    code, out, err = run_kb(vault, ["agenda", "list", "--all", "--json"])
    data = _parse(out) if code == 0 else None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        # 503：排隊那邊當主機暫時不行、留著下次再送（回 200 ok:false 會被當成沒套上而丟掉）
        return 503, {"ok": False, "message": outcome(code, out, err)[1] if code else "沒做成：日程讀不到", "undo": None}
    it = next((i for i in data["items"] if isinstance(i, dict) and i.get("id") == aid), None)
    if it is None:
        return 409, {"ok": False, "message": CHANGED.format("這件已經不在日程上了"), "undo": None}
    name = f"「{it.get('text') or aid}」"
    for k in EXPECT_KEYS:
        if k not in exp or (exp[k] or "") == (it.get(k) or ""):
            continue
        now = it.get(k) or ""
        if k == "status":
            said = name + STATUS_SAID.get(now, f"狀態變成 {now}")
        else:
            label = {"date": "日期", "start": "開始日", "time": "時間"}[k]
            said = f"{name}的{label}已經{'改成 ' + now if now else '拿掉了'}"
        return 409, {"ok": False, "message": CHANGED.format(said), "undo": None}
    return None


def _simple(args, undo=None):
    def go(vault, body):
        aid = _id(body)
        ok, msg, _out = _run_act(vault, [*args, aid])
        return 200, {"ok": ok, "message": msg, "undo": (undo(aid, body) if undo else None) if ok else None}
    return go


def _done(vault, body):
    """打勾：done；復原 reopen（重複的日程完成會跳到下一次，復原改回原日期，照外掛）。"""
    aid = _id(body)
    it = _item(vault, aid)
    ok, msg, _out = _run_act(vault, ["agenda", "done", aid])
    undo = ({"action": "edit", "id": aid, "date": it["date"]} if it and it.get("repeat") and it.get("date")
            else {"action": "reopen", "id": aid})
    return 200, {"ok": ok, "message": msg, "undo": undo if ok else None}


def _edit(vault, body):
    """改日期、時間：只送有給、跟原本不同的；復原改回原值（原本空的沒辦法清掉，那一項不復原）。"""
    aid = _id(body)
    change = {"date": _opt(body, "date", _DATE), "start": _opt(body, "start", _DATE), "time": _opt(body, "time", _TIME)}
    orig = _orig(_item(vault, aid))
    keys = [k for k in ("date", "start", "time") if change[k] and change[k] != orig[k]]
    if not keys:
        return 200, {"ok": False, "message": NOTHING_TO_EDIT, "undo": None}
    ok, msg, _out = _run_act(vault, ["agenda", "edit", aid] + [f"--{k}={change[k]}" for k in keys])
    back = {k: orig[k] for k in keys if orig[k]}
    return 200, {"ok": ok, "message": msg, "undo": dict({"action": "edit", "id": aid}, **back) if ok and back else None}


def _add(vault, body):
    """新增日程；復原 = 取消新的那一條。日期沒給用 kb 的今天（網頁不自己算日期，D-252）。"""
    from kb._lib import agenda
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise Bad("要寫做什麼")
    kind = body.get("kind") or DEFAULT_KIND
    if kind not in KINDS:
        raise Bad("種類不對")
    date = _opt(body, "date", _DATE) or agenda._today()
    start, time = _opt(body, "start", _DATE), _opt(body, "time", _TIME)
    entity = body.get("entity")
    if entity is not None and not isinstance(entity, str):
        raise Bad("entity 不對")
    args = ["agenda", "add", f"--kind={kind}", f"--date={date}", f"--text={text.strip()}"]
    args += [f"--start={start}"] if start else []
    args += [f"--time={time}"] if time else []
    args += [f"--entity={entity.strip()}"] if entity and entity.strip() else []
    ok, msg, out = _run_act(vault, args + ["--json"])
    if not ok:
        return 200, {"ok": False, "message": msg, "undo": None}
    got = _parse(out) or {}
    it = got.get("item") if isinstance(got.get("item"), dict) else {}
    said = it.get("text") or text.strip()
    if got.get("created") is False:
        return 200, {"ok": True, "message": f"日程裡已經有這條：{said}", "undo": None}
    return 200, {"ok": True, "message": f"已加進日程：{said}",
                 "undo": {"action": "cancel", "id": it["id"]} if it.get("id") else None}


def _answer(vault, body):
    """「要你確認的」選一個答案：重抓 kb home 找這題的第 result 個答案，照它的 argv 跑（{name}／{<fill.name>} 換成她填的），
    有 after 接著跑；找不到這題 409。沒有復原。"""
    key, n = body.get("key"), body.get("result")
    if not isinstance(key, str) or not key or not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise Bad("key 或 result 不對")
    code, out, err = run_kb(vault, ["home", "--json"])
    home = _parse(out) if code == 0 else None
    if not isinstance(home, dict):
        return 200, {"ok": False, "message": outcome(code, out, err)[1] if code else "沒做成：首頁回的看不懂", "undo": None}
    ask = next((c for c in home.get("confirm") or [] if isinstance(c, dict) and c.get("key") == key), None)
    if ask is None:
        return 409, {"ok": False, "message": GONE, "undo": None}
    results = ask.get("results") or []
    r = results[n] if n < len(results) and isinstance(results[n], dict) else None
    label = body.get("label")
    if not isinstance(label, str):
        raise Bad("要帶 label")
    if r is None or r.get("label") != label:  # 她按的那個答案已經換了位置或不見了：不跑旁邊那個
        return 409, {"ok": False, "message": GONE, "undo": None}
    if not isinstance(r.get("argv"), list):
        raise Bad("這個答案不能在網頁做")
    fill = r.get("fill") if isinstance(r.get("fill"), dict) else None
    tokens = ["{name}"]
    value = body.get("value")
    if fill:
        if not isinstance(value, str) or not value.strip() or value.strip().startswith("-") or len(value) > 200:
            raise Bad("要填的沒填")
        value = value.strip()
        if fill.get("input") == "date" and not _DATE.match(value):
            raise Bad("日期格式不對")
        options = fill.get("options")
        if isinstance(options, list) and options and value not in [o.get("value") for o in options if isinstance(o, dict)]:
            raise Bad("要從選單選")
        if fill.get("name"):
            tokens.append("{" + str(fill["name"]) + "}")
    else:
        value = None

    def put(a):
        s = str(a)
        if value is not None:
            for t in tokens:
                s = s.replace(t, value)
        return s
    ok, msg, _out = _run_act(vault, [put(a) for a in r["argv"]])
    if ok and isinstance(r.get("after"), list):
        ok2, msg2, _out2 = _run_act(vault, [put(a) for a in r["after"]])
        if not ok2:
            ok, msg = False, msg2
    return 200, {"ok": ok, "message": msg, "undo": None}


def _inferred_move(vault, body):
    aid = _id(body)
    date = _opt(body, "date", _DATE)
    if not date:
        raise Bad("要給日期")
    back = _orig(_item(vault, aid))["date"]
    ok, msg, _out = _run_act(vault, ["agenda", "edit", aid, f"--date={date}"])
    return 200, {"ok": ok, "message": msg, "undo": {"action": "edit", "id": aid, "date": back} if ok and back else None}


ACTIONS = {
    "done": _done,
    "reopen": _simple(["agenda", "reopen"]),
    "edit": _edit,
    "add": _add,
    "cancel": _simple(["agenda", "cancel"]),
    "answer": _answer,
    "dismiss": _simple(["pending", "dismiss"]),
    "inferred_ok": _simple(["agenda", "edit"]),
    "inferred_move": _inferred_move,
    "inferred_cancel": _simple(["agenda", "cancel"], lambda aid, body: {"action": "reopen", "id": aid}),
}


# ---------- 傳檔進暫存區 ----------

def safe_name(name):
    """網頁給的檔名去掉路徑與 Windows 不收的字元；空的用「上傳的檔」。"""
    text = str(name or "").replace("\\", "/").split("/")[-1]
    text = _BAD_NAME.sub("", text).strip().strip(".").strip()
    if not text:
        return UPLOAD_NAME
    stem, ext = os.path.splitext(text)
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        stem = f"{UPLOAD_NAME} {stem}"
    return (stem[:120] or UPLOAD_NAME) + ext[:20]


def _conflict(payload):
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("conflict")) or any(isinstance(i, dict) and i.get("result") == "conflict"
                                                for i in payload.get("items") or [])


def _upload(handler, vault, params):
    if not _willy_ok(handler):
        return
    n = _length(handler, UPLOAD_MAX)
    if n is None:
        return
    name = safe_name(params.get("name"))
    folder = Path(UPLOAD_ROOT) / secrets.token_hex(8)
    try:
        folder.mkdir(parents=True)
        tmp = folder / name
        left = n
        with open(tmp, "wb") as f:
            while left > 0:
                chunk = handler.rfile.read(min(left, 1024 * 1024))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            _error(handler, 400, "檔沒傳完")
            return
        stem = os.path.splitext(name)[0]
        args = ["intake", str(tmp), "--json"]
        for i in range(UPLOAD_TRIES + 1):
            code, out, err = run_kb(vault, args, via=True)
            payload = _parse(out)
            if code == 3 and _conflict(payload) and i < UPLOAD_TRIES:
                args = ["intake", str(tmp), f"--title={stem} {i + 2}", "--json"]
                continue
            break
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    ok, msg = outcome(code, out, err)
    res = dict(payload) if isinstance(payload, dict) else {}
    items = [i for i in res.get("items") or [] if isinstance(i, dict)]
    res.update(ok=ok, message=UPLOAD_OK if ok else (next((i.get("said") for i in items if i.get("said")), None) or msg))
    if ok:
        _tell_chat(handler, CHAT_UPLOADED.format(n=1, names=name))
    _json(handler, 200, res)


def _tell_chat(handler, text):
    """上傳後在對話送一句（網頁對話的 Chat）；小伺服器有 chat 物件才送（測試用的假伺服器可能沒有）。"""
    chat = getattr(handler.server, "chat", None)
    send = getattr(chat, "send", None)
    if send is None:
        return
    try:
        send(text)
    except Exception as e:  # noqa: BLE001  送不出去不影響上傳的結果
        from kb._lib import ledger
        ledger.log_error(handler.server.vault, f"上傳後送對話沒成：{type(e).__name__}: {str(e)[:150]}", source="web")


# ---------- 隨手記 ----------

def _note_bytes(vault):
    p = Path(vault) / NOTE_REL
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return b""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _note_get(handler, vault, params):
    raw = _note_bytes(vault)
    _json(handler, 200, {"text": raw.decode("utf-8", "replace"), "base": _sha(raw)})


def _note_post(handler, vault, params):
    """整頁寫回 系統/隨手記.md（照外掛）；base 是她打開時讀到的 sha256，跟現在的不一樣 → 409，不蓋掉 AI 或別台剛改的。
    帶 append 的（手機離線時記的，第 4 份）只在檔尾加一行。"""
    from kb._lib.io import write_text_atomic
    got = _body(handler)
    if got is None:
        return
    body = got["data"]
    if isinstance(body, dict) and "append" in body:
        _note_append(handler, vault, body)
        return
    if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not isinstance(body.get("base"), str):
        _error(handler, 400, "要有 text 與 base")
        return
    with _lock:
        raw = _note_bytes(vault)
        if _sha(raw) != body["base"]:
            _json(handler, 409, {"ok": False, "message": NOTE_CONFLICT, "text": raw.decode("utf-8", "replace"), "base": _sha(raw)})
            return
        p = Path(vault) / NOTE_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(p, body["text"])
        new = _note_bytes(vault)
    _json(handler, 200, {"ok": True, "base": _sha(new)})


def _note_append(handler, vault, body):
    text, at = body.get("append"), body.get("at")
    if not isinstance(text, str) or not text.strip() or len(text) > NOTE_APPEND_MAX:
        _error(handler, 400, "append 要是一句話")
        return
    if not isinstance(at, str) or not at.strip() or len(at) > 40:
        _error(handler, 400, "要有 at（記的時間）")
        return
    line = NOTE_APPEND.format(text=" ".join(text.split()), at=" ".join(at.split()))
    p = Path(vault) / NOTE_REL
    with _lock:
        raw = _note_bytes(vault)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "ab") as f:  # 只接在最後，不整份覆蓋
            f.write((b"\n" if raw and not raw.endswith(b"\n") else b"") + line.encode("utf-8") + b"\n")
        new = _note_bytes(vault)
    _json(handler, 200, {"ok": True, "base": _sha(new)})
