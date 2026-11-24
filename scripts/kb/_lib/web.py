"""網頁小伺服器（#74，D-350、D-356、D-359）：客戶電腦上一直跑著的小伺服器，打開網址看得到一頁。只用標準函式庫。

開關與連接埠記在這台自己的 .system/local/web.json（不進 git）：{"on": true, "port": 47831}；手機從外面連（第 4 份，D-357）
多 public_host、access_team、access_aud 與加減名單用的 cf_account、cf_app、cf_policy（kb web public 寫，write_switch 不動它們）。
只綁 127.0.0.1；鎖＝佔住連接埠（allow_reuse_address = False，Windows 在 bind 前設 SO_EXCLUSIVEADDRUSE）。
綁不上：那個埠回的 /api/status 是這個知識庫 → 已有一個在跑，安靜退出；不是或連不上 → 記待處理（check web）再退出，都回 0。
每個請求先看 Host 標頭只收自己的網址（擋 DNS rebinding）；Host 是 public_host 的（經 Cloudflare 通道來的）一律驗 Access 的
簽章（web_access.verify），沒標頭、驗不過、公鑰拿不到 → 403 一頁「請重新登入」；127.0.0.1／localhost 不驗（她在主機前）。GET /api/status、GET / 與 web/ 夾的靜態檔，其他方法 405。
首頁與看卡的 API（GET、POST /api/…）交給 web_pages.route（#74 第 3 份）。
對話（第 2 份，D-351～D-353，本體在 web_chat）：起來時建一個 Chat；GET /api/chat?after=、GET /api/events（SSE，每 20 秒 ping）、
POST /api/chat/send、POST /api/chat/answer（409＝別台回過了）。POST 一律要 X-Willy: 1、Origin 有給就要是自己的網址
（http://127.0.0.1:<port>、http://localhost:<port>、有 public_host 時 https://<public_host>）、
body 最多 64 KB 的 JSON 物件，不符 403／413／400。
背景執行緒每 60 秒查一次：先讓對話檢查閒置（web_chat.Chat.tick），再看知識庫不見（照 --task 刪自己的排程）、開關 off 或檔不見、
連接埠改了、plugin 換了版而且空閒（沒 AI 在回、沒背景助手、沒卡片在等；跑新版的 kb.py web ensure 把排程指到新版，成功才退出），
任一條成立就關掉伺服器（先關對話的 claude）、回 0。
"""
import json
import os
import socket
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, unquote, urlsplit

from kb._lib import web_access, web_chat, web_pages
from kb._lib.io import read_json, write_json_atomic

APP = "wiki-with-willy"
DEFAULT_PORT = 47831
SWITCH_REL = ".system/local/web.json"
HOST = "127.0.0.1"
CHECK_SECONDS = 60
PROBE_SECONDS = 2
ENSURE_SECONDS = 30
PUBLIC_KEYS = ("public_host", "access_team", "access_aud", "cf_account", "cf_app", "cf_policy")
LOGIN_AGAIN = ("<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\">"
               "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>willy</title></head>"
               "<body style=\"font-family:system-ui,sans-serif;padding:24px;line-height:1.6\"><h1>請重新登入</h1>"
               "<p>登入過期了，或這台還沒登入。請關掉這頁再打開一次，輸入你的信箱、收驗證碼登入。</p></body></html>")
PORT_PENDING_KEY = "web_port_busy"
PORT_PENDING_TEXT = "網頁的連接埠 {port} 被別的程式佔了"
PING_SECONDS = 20
BODY_MAX = 64 * 1024
DRAIN_MAX = 1024 * 1024  # 超過 BODY_MAX 但在這以內的先讀掉再回 413
POST_ROUTES = ("/api/chat/send", "/api/chat/answer")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}


# ---------- 開關 ----------

def switch_path(vault):
    return Path(vault) / SWITCH_REL


def read_switch(vault):
    """回 {"on": bool, "port": int}；檔不在或讀不懂回 None。"""
    try:
        data = read_json(switch_path(vault))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    return {"on": data.get("on") is True, "port": port if valid_port(port) else DEFAULT_PORT}


def _raw_switch(vault):
    try:
        data = read_json(switch_path(vault))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_switch(vault, on, port):
    """寫 on、port；web.json 裡其他欄位（對外網址、Access）原樣留著。"""
    p = switch_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in _raw_switch(vault).items() if k in PUBLIC_KEYS}
    write_json_atomic(p, dict({"on": bool(on), "port": int(port)}, **data))


def read_public(vault):
    """對外網址與 Access 的設定：PUBLIC_KEYS 每個都有，沒填或不是字串是 None；public_host 一律小寫。"""
    data = _raw_switch(vault)
    out = {k: (data.get(k).strip() or None) if isinstance(data.get(k), str) else None for k in PUBLIC_KEYS}
    if out["public_host"]:
        out["public_host"] = out["public_host"].lower()
    return out


def write_public(vault, fields):
    """改 PUBLIC_KEYS 裡給的那幾個（值 None 或空字串＝拿掉）；on、port 原樣（沒有 web.json 時寫 on false、預設埠）。"""
    p = switch_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _raw_switch(vault)
    sw = read_switch(vault) or {"on": False, "port": DEFAULT_PORT}
    data.update(on=sw["on"], port=sw["port"])
    for k, v in fields.items():
        if k not in PUBLIC_KEYS:
            raise KeyError(k)
        if v:
            data[k] = v
        else:
            data.pop(k, None)
    write_json_atomic(p, data)


def valid_port(port):
    return isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535


def url(port):
    return f"http://{HOST}:{port}/"


# ---------- 伺服器 ----------

def web_root():
    from kb._lib import paths
    return paths.plugin_root() / "web"


def allowed_hosts(port):
    """在主機前用的 Host 標頭（小寫比）；經通道來的 public_host 另外驗 Access（_Handler._host_ok）。"""
    return {f"{HOST}:{port}", f"localhost:{port}"}


def own_origins(handler):
    """POST 的 Origin 收哪些：在主機前的 http://<Host>，有 public_host 時加 https://<public_host>。"""
    out = {f"http://{h}" for h in allowed_hosts(handler.server.server_address[1])}
    public = getattr(handler, "public_host", None)
    if public is None:
        public = read_public(handler.server.vault)["public_host"] or ""
    if public:
        out.add(f"https://{public}")
    return out


_chat = None  # serve() 跑著的那個伺服器的對話


def idle():
    """可以換版了嗎：沒有 AI 在回話、沒有背景助手在跑、沒有卡片在等她（chat.idle()）；沒有在跑的對話回 True。"""
    chat = _chat
    return True if chat is None else chat.idle()


def _log(vault, text):
    from kb._lib import ledger
    ledger.log_error(vault, text, source="web")


def _version():
    from kb._lib import paths
    try:
        return str((paths.read_manifest() or {}).get("version") or "")
    except (OSError, ValueError):
        return ""


def _status_info(vault):
    from kb._lib import vault as vault_lib
    return {"app": APP, "vault_id": vault_lib.ensure_vault_id(vault), "vault_name": Path(vault).name,
            "version": _version(), "pid": os.getpid(),
            "started": datetime.now().astimezone().isoformat(timespec="seconds")}


def _status_body(server):
    return json.dumps(server.status, ensure_ascii=False).encode("utf-8")


class PortBusy(OSError):
    """綁不上連接埠。"""


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False  # 鎖＝佔住連接埠：不准第二個綁上同一個埠
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows：沒設的話別的程式設 SO_REUSEADDR 還是綁得上
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        TCPServer.server_bind(self)  # 不走 HTTPServer.server_bind：它的 getfqdn 在 Windows 可能卡好幾秒
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        _log(self.vault, f"網頁請求出錯：{sys.exc_info()[0].__name__}: {str(sys.exc_info()[1])[:150]}")

    def server_close(self):
        chat = getattr(self, "chat", None)
        if chat is not None:  # 伺服器要退出：先關對話背後的 claude
            chat.close()
        super().server_close()


class _Handler(BaseHTTPRequestHandler):
    timeout = 30  # 一條連線讀寫卡住 30 秒就放掉（送一半的 body 不讓執行緒一直等）

    def version_string(self):  # Server 標頭只寫 willy，不帶 Python 版本
        return "willy"

    def log_message(self, format, *args):  # noqa: A002  pythonw 沒有 stderr；請求不記
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def __getattr__(self, name):
        if name.startswith("do_"):  # GET 以外的方法一律 405（不是 501）
            return self._not_allowed
        raise AttributeError(name)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host in allowed_hosts(self.server.server_address[1]):
            return True
        cfg = read_public(self.server.vault)
        self.public_host = cfg["public_host"] or ""
        if self.public_host and host == self.public_host:
            try:  # 經通道來的：驗 Access 的簽章（第 4 份第 1 點）
                claims = web_access.verify(self.headers.get(web_access.HEADER), cfg["access_team"], cfg["access_aud"])
            except web_access.Denied:
                if self.command == "POST":
                    web_pages._drain(self)
                self.close_connection = True
                self._send(403, "text/html; charset=utf-8", LOGIN_AGAIN.encode("utf-8"), "no-store")
                return False
            email = claims.get("email")
            self.access_email = email if isinstance(email, str) else None  # 之後多人用（B-548）；本份不拿來分人
            return True
        if self.command == "POST":
            web_pages._drain(self)  # 同 405：讀掉本文再回，用戶端才拿得到 403
        self.send_error(403)
        return False

    def _not_allowed(self):
        if self._host_ok():
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):  # noqa: N802
        try:
            if not self._host_ok():
                return
            url = urlsplit(self.path)
            if web_pages.route(self, "GET", url.path, url.query):
                return
            if url.path == "/api/status":
                self._send(200, "application/json; charset=utf-8", _status_body(self.server), "no-store")
            elif url.path == "/api/chat":
                self._chat(url.query)
            elif url.path == "/api/events":
                self._events()
            else:
                self._static(url.path)
        except Exception as e:  # noqa: BLE001  一個請求出錯不讓伺服器掛掉
            self._failed(e)

    def do_POST(self):  # noqa: N802
        try:
            if not self._host_ok():
                return
            url = urlsplit(self.path)
            path = url.path
            if path not in POST_ROUTES:
                if not web_pages.route(self, "POST", path, url.query):  # 首頁與看卡的 POST（第 3 份）
                    web_pages._drain(self)  # 先讀掉本文再 405：沒讀的本文留在連線上，Windows 關連線送 RST，用戶端讀不到 405
                    self._not_allowed()
                return
            body = self._body()
            if body is None:
                return
            chat = self.server.chat
            if path == "/api/chat/send":
                text, page = body.get("text"), body.get("page")
                if not isinstance(text, str) or not text.strip() or not (page is None or isinstance(page, str)):
                    self.send_error(400)
                    return
                try:
                    chat.send(text, page or None)
                except web_chat.Closed:  # 伺服器正在關（換版、關掉）：不再開 claude
                    self.send_error(503)
                    return
            else:
                if not isinstance(body.get("card_id"), str):
                    self.send_error(400)
                    return
                try:
                    chat.answer(body["card_id"], body.get("answer"))
                except web_chat.CardGone:
                    self.send_error(409)  # 別台回過了：網頁重抓
                    return
                except web_chat.Closed:
                    self.send_error(503)
                    return
                except ValueError:
                    self.send_error(400)
                    return
            self._json(200, {"ok": True})
        except Exception as e:  # noqa: BLE001
            self._failed(e)

    def _failed(self, e):
        _log(self.server.vault, f"網頁請求出錯（{self.path[:80]}）：{type(e).__name__}: {str(e)[:150]}")
        try:
            self.send_error(500)
        except OSError:
            pass

    def _body(self):
        """POST 的檢查（第 2 份第 2 點）：X-Willy: 1（外站送不出自訂標頭）、Origin 有給要是自己的網址、64 KB 內的 JSON 物件。
        不符就送 403／413／400、回 None。"""
        try:
            size = int(self.headers.get("Content-Length") or "")
        except ValueError:
            size = -1
        origin = self.headers.get("Origin")
        own = own_origins(self)
        if (self.headers.get("X-Willy") or "").strip() != "1" or (origin is not None and origin.strip().lower() not in own):
            return self._reject(403, size)
        if size > BODY_MAX:
            return self._reject(413, size)
        if size < 0:
            return self._reject(400, size)
        try:
            body = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            body = None
        if not isinstance(body, dict):
            self.send_error(400)
            return None
        return body

    def _reject(self, code, size):
        """不收這個 POST：body 在 DRAIN_MAX 以內先讀掉再回（沒讀完就關，Windows 會送 RST，對方收不到狀態碼），之後關連線。"""
        self.close_connection = True
        if 0 < size <= DRAIN_MAX:
            self.rfile.read(size)
        self.send_error(code)
        return None

    def _json(self, code, data):
        self._send(code, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False).encode("utf-8"), "no-store")

    def _chat(self, query):
        after = parse_qs(query).get("after")
        if after is not None:
            try:
                after = int(after[0])
            except ValueError:
                self.send_error(400)
                return
        self._json(200, self.server.chat.snapshot(after))

    def _events(self):
        """Server-Sent Events：有新訊息、卡片出現或消失、忙不忙變了就推一筆；沒事每 PING_SECONDS 秒一行 : ping。
        斷了瀏覽器自己重連（EventSource 內建），網頁再用 /api/chat?after= 補。"""
        chat = self.server.chat
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        n = chat.event_count()
        try:
            self.wfile.write(b": willy\n\n")
            self.wfile.flush()
            while not chat.closed:
                n, evs = chat.events_after(n, PING_SECONDS)
                if evs:
                    data = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in evs).encode("utf-8")
                elif chat.closed:
                    break
                else:
                    data = b": ping\n\n"
                self.wfile.write(data)
                self.wfile.flush()
        except OSError:
            pass  # 那台關了頁面

    def _send(self, code, ctype, body, cache):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        target = static_file(path)
        if target is None:
            self.send_error(404)
            return
        self._send(200, TYPES[target.suffix.lower()], target.read_bytes(), "no-cache")


def static_file(path):
    """網址的路徑對到 web/ 底下的哪個檔；不在 web/ 底下、副檔名不在白名單、不是檔都回 None（送 404）。"""
    name = unquote(path)
    if name == "/":
        name = "/index.html"
    if not name.startswith("/") or any(c in name for c in "\\:\x00"):
        return None
    root = web_root().resolve()
    target = (root / name.lstrip("/")).resolve()
    if root not in target.parents or target.suffix.lower() not in TYPES or not target.is_file():
        return None
    return target


def start(vault, port):
    """綁好 127.0.0.1:<port>、還沒開始收請求的伺服器（port 0 讓系統給，實際的在 server_address[1]）；綁不上丟 PortBusy。"""
    info = _status_info(vault)
    try:
        srv = _Server((HOST, port), _Handler)
    except OSError as e:
        raise PortBusy(*e.args) from e
    srv.vault, srv.status = Path(vault), info
    try:
        srv.chat = web_chat.Chat(vault)
    except BaseException:
        srv.server_close()
        raise
    return srv


def probe(port, timeout=None):
    """GET 那個埠的 /api/status（不走 proxy）；回 dict，連不上或回的不是 JSON 回 None。"""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{HOST}:{port}/api/status", timeout=PROBE_SECONDS if timeout is None else timeout) as r:
            data = json.loads(r.read(65536).decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------- 每 60 秒的檢查 ----------

def _is_vault(vault):
    from kb import guard
    found = guard.find_vault(str(vault))
    return found is not None and os.path.normcase(os.path.realpath(found)) == os.path.normcase(os.path.realpath(vault))


def _identity(vault):
    """這個 plugin 在 installed_plugins.json 的那筆（市集、範圍）；讀不到或沒有（開發時 --plugin-dir）回 None。"""
    from kb._lib import upgrade
    try:
        return upgrade.plugin_identity(vault)
    except Exception:  # noqa: BLE001
        return None


def _installed(identity):
    """同 upgrade.installed_version 那筆（市集、範圍、projectPath 相同）的 version 與 installPath；沒有回 None。"""
    from kb._lib import upgrade
    plugins = upgrade._read_installed() or {}
    for rec in plugins.get(f"{upgrade.PLUGIN_NAME}@{identity['market']}") or []:
        if (isinstance(rec, dict) and rec.get("scope") == identity["scope"]
                and rec.get("projectPath") == identity.get("project")):
            return {"version": rec.get("version"), "path": rec.get("installPath")}
    return None


def _run(argv, timeout):
    """跑新版的 ensure，不開視窗；回結束碼。測試換掉它。"""
    r = subprocess.run(argv, capture_output=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode


def _python():
    from kb._lib import schedule
    return schedule._pythonw() or sys.executable


def _handoff(state):
    """plugin 換了版（升級或退回）而且空閒：跑新版的 kb.py web ensure；成功回 True（該退出），其他回 False 下一輪再試。"""
    vault = state["vault"]
    if state.get("identity") is None:
        state["identity"] = _identity(vault)
        if state["identity"] is None:
            return False
    rec = _installed(state["identity"])
    if not rec or not rec["version"] or not rec["path"] or rec["version"] == _version() or not idle():
        return False
    kb = Path(rec["path"]) / "scripts" / "kb.py"
    if not kb.is_file():
        _warn_once(state, (rec["version"], "missing"), f"plugin 換成 {rec['version']}，新版的 {kb} 不在，網頁還用舊版")
        return False
    try:
        code = _run([_python(), str(kb), "web", "ensure", "--vault", str(vault)], ENSURE_SECONDS)
    except (OSError, subprocess.SubprocessError) as e:
        code = f"{type(e).__name__}: {e}"
    if code != 0:
        _warn_once(state, (rec["version"], "ensure"),
                   f"plugin 換成 {rec['version']}，新版的 web ensure 沒成功（{code}），網頁還用舊版；每分鐘再試")
        return False
    return idle()  # ensure 跑的那幾秒她又說話了：先不退，下一輪（空閒時）再交棒


def _warn_once(state, key, text):
    """換版沒交棒成：同一版同一個原因只寫一行 hook-errors（每分鐘再試，不每分鐘寫一行）。"""
    if state.get("warned") != key:
        state["warned"] = key
        _log(state["vault"], text)


def _check(state):
    """回該退出的原因；不用退出回 None。"""
    vault = state["vault"]
    if not _is_vault(vault):
        from kb._lib import schedule
        gone = schedule.forget(state.get("task"))
        _log(None, f"網頁找不到知識庫 {vault}，{'刪了排程 ' + state['task'] if gone else '排程沒刪'}")
        return "gone"
    sw = read_switch(vault)
    if not sw or not sw["on"]:
        return "off"
    if state.get("port") is not None and sw["port"] != state["port"]:
        return "port"
    if _handoff(state):
        return "upgraded"
    return None


def _watch(srv, state, interval, stop):
    while not stop.wait(interval):
        try:
            srv.chat.tick()  # 閒置太久就關 claude、每天清一次舊的對話紀錄
        except Exception as e:  # noqa: BLE001
            _log(state["vault"], f"網頁對話的定時檢查出錯：{type(e).__name__}: {str(e)[:150]}")
        try:
            reason = _check(state)
        except Exception as e:  # noqa: BLE001  檢查出錯不讓伺服器掛掉，下一輪再查
            _log(state["vault"], f"網頁的定時檢查出錯：{type(e).__name__}: {str(e)[:150]}")
            continue
        if reason:
            srv.shutdown()
            return


def serve(vault, port=None, task=None, interval=CHECK_SECONDS):
    """在 127.0.0.1:<port> 一直跑到該退出；port 沒給用 web.json 的（之後 web.json 的埠改了也退出，讓排程用新的埠起來）。
    開關是 off 或沒有 web.json 就不起來。回結束碼：已有一個在跑、埠被佔、開關關著、該退出都是 0（不讓排程一直報錯）；
    伺服器本身起不來寫 hook-errors 回 1。"""
    vault = Path(vault)
    sw = read_switch(vault)
    if not sw or not sw["on"]:  # 關著（或排程沒刪成）：不起來，免得每分鐘開 60 秒又停
        return 0
    follow = port is None
    if follow:
        port = sw["port"]
    try:
        srv = start(vault, port)
    except PortBusy:
        from kb._lib import schedule
        from kb._lib import vault as vault_lib
        other = probe(port)
        if other and other.get("app") == APP and other.get("vault_id") == vault_lib.vault_id(vault):
            return 0
        schedule.record_pending(vault, PORT_PENDING_KEY, PORT_PENDING_TEXT.format(port=port), {"port": port}, "web")
        return 0
    except Exception as e:  # noqa: BLE001
        _log(vault, f"網頁小伺服器起不來：{type(e).__name__}: {str(e)[:150]}")
        return 1
    try:
        from kb._lib import schedule
        schedule.resolve_pending(vault, PORT_PENDING_KEY)
    except Exception as e:  # noqa: BLE001
        _log(vault, f"關「網頁的連接埠被佔」出錯：{type(e).__name__}: {e}")
    state = {"vault": vault, "port": port if follow else None, "task": task, "identity": _identity(vault)}
    stop = threading.Event()
    global _chat
    _chat = srv.chat
    threading.Thread(target=_watch, args=(srv, state, interval, stop), daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if _chat is srv.chat:
            _chat = None
        srv.server_close()  # 先關對話的 claude（_Server.server_close）
    return 0
