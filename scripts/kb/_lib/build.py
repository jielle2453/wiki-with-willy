"""建置（build 第 2 點，D-189～D-194、D-201、D-202）：.system/build.json schema 1 與 kb build 九個子子命令的本體。

建置在 cwd 建（桌面版開對話時選的那個資料夾，hook 才管得到）。步驟照 STEPS：env → setup → inventory → sample → cards → names
→ synthesis → adopt（記了舊 wiki 才有）→ finish → done；setup 做完時沒登記任何資料夾（no_sources）就跳過 inventory～synthesis（D-044）。
認知識庫只看 .system/（D-190 再補）：cwd 有 .system/ 但沒有 build.json、或建置做完了 → 擋（ALREADY）；cwd 在別的知識庫裡面也擋。
build.json 讀改寫都在它的鎖裡、寫前備進回合（by program）；壞檔丟 Unreadable 帶檔名。同時兩個建置（D-194）：記著的 session
不是這一個、lock_until 還沒到 → BUSY（說最後動作的時間；那個對話關掉了就說「接手建置」）；這個 session 每打一次 kb build
就把 last_action 記成現在、lock_until 往後推 LOCK_MINUTES。status --takeover：鎖換成這個 session、takeovers 記一行，其他不動。
session 取守門插的 --session（build 每個子子命令都插），沒有才讀 current.json（不開新回合）。
env（D-189）只讀、不碰 build.json、哪裡都能跑（建好的知識庫也行，換電腦 D-208 會叫它）；PDF 元件在子行程實跑一頁，
智慧型應用程式控制讀登錄檔，信任讀 ~/.claude.json，舊 llm-wiki 讀外掛清單與 enabledPlugins。外部的東西都經 _ 開頭的函式，測試換掉。
facts 只讀：每個來源的份數、格式、第一二層夾、讀不到的原因與估時（D-199 的算法，同 plan）。
adopt（D-193）：--check 只讀舊 vault；--extract 把要讀的文字（hot.md、CLAUDE.md、agenda.md、wiki 頁、log.md）遮值後切成段寫進
.system/temp/adopt/（每段不超過 adopt_chunk_chars），舊 vault 一個字都不改。
report（D-066、D-047）：--facts 給數字與清單；--from 收 AI 在工作區寫好的那份，驗六個標題、遮值、沒內容的段拿掉、數字段由程式接上，
寫 系統/建置報告.md（備進回合）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from kb import guard
from kb._lib import batches, config, credentials, entities, extract, inventory, office, pending, profile, turn
from kb._lib import sources as sources_lib
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic
from kb._lib.paths import read_manifest

BUILD_POSIX = ".system/build.json"
SCHEMA_VERSION = 1
STEPS = ("env", "setup", "inventory", "sample", "cards", "names", "synthesis", "adopt", "finish", "done")
STEP_TEXT = {"env": "環境", "setup": "訪談", "inventory": "盤點", "sample": "抽樣", "cards": "全量整理", "names": "確認名字",
             "synthesis": "綜合卡", "adopt": "收舊 wiki", "finish": "交付報告", "done": "完成"}
SOURCE_STEPS = ("inventory", "sample", "cards", "names", "synthesis")  # 沒登記資料夾就跳過（D-044）
LOCK_MINUTES = 30
REPORT_POSIX = "系統/建置報告.md"
REPORT_HEADS = ("你的資料去了哪", "怎麼開始問", "我猜的請改", "讀不到的", "要你決定的", "數字")  # D-066
NUMBERS = "數字"
OFFICE_POSIX = ".system/temp/office"
TIME = "%Y-%m-%dT%H:%M:%S"

ALREADY = "這裡已經是建好的知識庫，要加資料夾、整理檔案直接跟我說"
BUSY = "另一個對話正在建置（{step}，最後動作 {time}）；那個對話還開著就回那邊繼續，已經關掉了就說『接手建置』"
NOT_STARTED = "還沒開始建置，先跑 kb build start"
DONE_LINE = "建置：{step} 做完了，下一步 {next}"
FINISHED_LINE = "建置：{step} 做完了，建置完成"
NOT_NOW = "現在是「{now}」這一步，不是「{want}」"
REDO_LATER = "還沒做到「{step}」，不能重做"
REDO_SKIPPED = "這次建置沒有「{step}」這一步"
REDO_LINE = "建置：回到「{step}」"
SET_PEOPLE = "記下了：幾個人用：{value}"
SET_ADOPT = "記下了：要收的舊 wiki 在 {value}"
VALUE_EMPTY = "要記的值是空的"
VALUE_LONG = "太長了，一句話 200 字內"
NOT_OLD_VAULT = "這不像舊的 llm-wiki 資料夾"
PROFILE_ERROR = "客戶設定檔讀不懂：{error}，改好再往下"
PROFILE_PENDING = "客戶設定檔有改還沒套用，先跑 kb profile apply"
PROFILE_MISSING = "還沒有客戶設定檔，跑 kb init 補"
LEFT_CARDS = "還有 {n} 份沒整理完，整理完再往下"
RUNNING = "還有 {n} 批在背景整理，做完再往下"
LEFT_NAMES = "還有 {n} 個名字沒確認，確認完再往下"
LEFT_SYNTHESIS = "還有 {n} 張綜合卡沒寫好，寫完再往下"
RUNNING_ADOPT = "還在背景讀舊的 wiki，讀完再往下"
NO_REPORT = "建置報告還沒寫，先跑 kb build report --from <寫好的那份>"
REPORT_OK = "建置報告寫好了：" + REPORT_POSIX
REPORT_MISSING = "建置報告少了一段：{}"
REPORT_EXTRA = "建置報告多了一段：{}"
REPORT_PLACE = "建置報告要先寫在工作區（.system/temp/office/ 底下）再交給我"
REPORT_UNREADABLE = "讀不到寫好的建置報告：{}"
ADOPT_PLACE = "切段的暫存位置不對（.system/temp 是連結），沒有寫"
ENV_KEYS = ("python", "kb", "trust", "sac", "pdf", "office", "disk", "old_wiki")


class Refused(Exception):
    """一句給人看的拒絕（exit 3）。"""


class Unreadable(ValueError):
    pass


def _now():
    return datetime.now().replace(microsecond=0)


def _stamp(t=None):
    return (t or _now()).strftime(TIME)


def _parse(text):
    try:
        return datetime.strptime(str(text), TIME)
    except (TypeError, ValueError):
        return None


def _path(vault):
    return Path(vault) / BUILD_POSIX


def load(vault):
    """沒檔回 None；讀不懂或形狀不對丟 Unreadable（訊息帶檔名）。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise Unreadable(f"{BUILD_POSIX}：{e}") from None
    if data is None:
        return None
    if not (isinstance(data, dict) and data.get("step") in STEPS and isinstance(data.get("done"), dict)):
        raise Unreadable(f"{BUILD_POSIX}：形狀不對")
    return data


def lock(vault):
    return FileLock(_path(vault))


def save(vault, data, turn_id):
    if turn_id:
        turn.backup(vault, BUILD_POSIX, turn_id, by="program")
    write_json_atomic(_path(vault), data)


def session_of(vault):
    """這一句的 session：守門插的 --session；沒有就讀 current.json（不開新回合）。"""
    if turn.SESSION:
        return turn.SESSION
    data = turn._read_turn(turn._current_file(vault))
    return data.get("session_id") if data else None


def _turn_id(vault):
    return turn.current(vault)["turn"]


# ---------- 在哪裡 ----------

def _above(p):
    """p 在別的知識庫裡面（上層有 .system/sources.json）就回那個知識庫。"""
    return guard.find_vault(str(p.parent))


IGNORED_NAMES = {"desktop.ini", "thumbs.db"}


def not_empty(p):
    """資料夾裡有沒有東西（. 開頭的與 desktop.ini、Thumbs.db 不算：桌面版、檔案總管自己會放）。"""
    try:
        return any(not x.name.startswith(".") and x.name.casefold() not in IGNORED_NAMES for x in Path(p).iterdir())
    except OSError:
        return False


def _vault_or_refuse(cwd):
    """建置中的知識庫：回 (vault, data)。不是知識庫丟 Refused(NOT_STARTED)，建好的或沒有建置紀錄的丟 Refused(ALREADY)。"""
    p = Path(cwd).resolve()
    if _above(p) is not None:
        raise Refused(ALREADY)
    if not (p / ".system").is_dir():
        raise Refused(NOT_STARTED)
    data = load(p)
    if data is None or data["step"] == "done":
        raise Refused(ALREADY)
    return p, data


def _last_action(data):
    t = _parse(data.get("last_action"))
    if t is None:
        until = _parse(data.get("lock_until"))
        t = until - timedelta(minutes=LOCK_MINUTES) if until is not None else None
    return t.strftime("%H:%M") if t is not None else "不明"


def _claim(data, session, takeover=False):
    """同時兩個建置（D-194）：記著的 session 不是這一個、lock_until 還沒到就擋（takeover 例外：換成這一個、記一行接手）；
    否則記成這一個、最後動作記現在、lock_until 往後推。"""
    now = _now()
    owner, until = data.get("session_id"), _parse(data.get("lock_until"))
    busy = owner and session and owner != session and until is not None and until > now
    if busy and not takeover:
        raise Refused(BUSY.format(step=STEP_TEXT[data["step"]], time=_last_action(data)))
    if takeover and owner and owner != session:
        data.setdefault("takeovers", []).append({"time": _stamp(now), "from": owner, "to": session})
    data["session_id"] = session or owner
    data["last_action"] = _stamp(now)
    data["lock_until"] = _stamp(now + timedelta(minutes=LOCK_MINUTES))


def _locked(cwd, change, takeover=False):
    """拿 build.json 的鎖、認領、做 change(vault, data, turn_id)、寫回。回 (vault, data, change 的回傳值)。"""
    vault, _data = _vault_or_refuse(cwd)
    with lock(vault):
        data = load(vault)
        if data is None or data["step"] == "done":
            raise Refused(ALREADY)
        _claim(data, session_of(vault), takeover)
        tid = _turn_id(vault)
        got = change(vault, data, tid)
        save(vault, data, tid)
    return vault, data, got


# ---------- 步驟 ----------

def sequence(data):
    """這次建置要走的步驟（沒登記資料夾跳過整理那幾步，沒記舊 wiki 沒有 adopt）。"""
    return [s for s in STEPS if not (data.get("no_sources") and s in SOURCE_STEPS)
            and not (s == "adopt" and not data.get("adopt"))]


def next_step(data, step):
    seq = sequence(data)
    return seq[seq.index(step) + 1]


def _arg(value):
    from kb._lib import plan
    got = plan.shell_arg(value)
    return got if got is not None else '"<路徑>"'


def _workspace_report(vault):
    return (Path(vault) / OFFICE_POSIX / _turn_id(vault) / "建置報告.md").as_posix()


def view(vault, data):
    """build status 的 JSON：{"step","not_empty","do","read","after"} 加 done、people、adopt。"""
    step = data["step"]
    do = {
        "env": ["kb build env --json"],
        "setup": ['kb profile apply --from "<填好的設定檔>"', 'kb folders add "<資料夾>"',
                  'kb folders exclude "<別名/相對路徑>" --kind hidden|unused --reason "<一句>"',
                  "kb profile add <節> <欄>=<值>…", 'kb build set people "<她的話>"',
                  'kb build set adopt "<舊 wiki 的路徑>"'],
        "inventory": ["kb build facts --json"],
        "sample": ["kb build facts --json", "kb cards dispatch --ids <挑的編號> --json"],
        "cards": ["kb cards todo --json", "kb cards dispatch --json"],
        "names": ["kb cards todo --json"],
        "synthesis": ["kb cards dispatch --json"],
        "adopt": [f"kb build adopt {_arg(data.get('adopt') or '')} --check --json",
                  f"kb build adopt {_arg(data.get('adopt') or '')} --extract --json", "kb cards dispatch --adopt --json"],
        "finish": ["kb build report --facts --json", f"kb build report --from {_arg(_workspace_report(vault))}"],
    }[step]
    read = "references/adopt.md" if step == "adopt" else f"references/steps.md {STEP_TEXT[step]}"
    return {"step": step, "not_empty": False, "do": do, "read": read, "after": f"kb build done {step}",
            "done": data.get("done") or {}, "people": data.get("people"), "adopt": data.get("adopt"),
            "no_sources": bool(data.get("no_sources"))}


def status(cwd, takeover=False):
    """kb build status：還沒建（沒有 .system/）回 env 那步與 not_empty；建置中認領後回 view；別的擋。
    takeover（客戶或服務人員說「接手建置」）：別的對話拿著鎖也換成這一個。"""
    p = Path(cwd).resolve()
    if _above(p) is not None:
        raise Refused(ALREADY)
    if not (p / ".system").is_dir():
        return {"step": "env", "not_empty": not_empty(p), "do": ["kb build env --json"],
                "read": f"references/steps.md {STEP_TEXT['env']}", "after": "kb build start", "done": {},
                "people": None, "adopt": None, "no_sources": False}
    vault, data, _got = _locked(p, lambda v, d, t: None, takeover)
    return view(vault, data)


def start(cwd):
    """kb build start：在 cwd 跑 kb init、照環境檢查記下 env、寫 build.json（step 到 setup）。不開回合、不備份（init 之前沒有知識庫）。"""
    import argparse
    import contextlib
    import io as _io
    from kb import init
    p = Path(cwd).resolve()
    if _above(p) is not None or (p / ".system").is_dir():
        raise Refused(ALREADY)
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = init.run(argparse.Namespace(path=str(p), check=False, json=False))
    if code != 0:
        raise Refused(buf.getvalue().strip().splitlines()[-1] if buf.getvalue().strip() else "知識庫建不起來")
    now = _stamp()
    data = {"schema_version": SCHEMA_VERSION, "step": "setup", "started": now, "done": {"env": now}, "people": None,
            "adopt": None, "no_sources": False, "env": {"checked": now, "items": env_items(p)},
            "session_id": None, "lock_until": None}
    with lock(p):
        write_json_atomic(_path(p), data)
    return p, data


def done(cwd, step):
    """kb build done <步>：要是目前這一步、前提要過（_check），再記完成、往下一步。回 (vault, data, 說的那句)。"""
    def change(vault, data, tid):
        if step != data["step"]:
            raise Refused(NOT_NOW.format(now=STEP_TEXT[data["step"]], want=STEP_TEXT.get(step, step)))
        _check(vault, data, step)
        if step == "env":
            data["env"] = {"checked": _stamp(), "items": env_items(vault)}
        if step == "setup":
            data["no_sources"] = not sources_lib.load(vault).entries
        data["done"][step] = _stamp()
        data["step"] = next_step(data, step)
        if data["step"] == "done":
            return FINISHED_LINE.format(step=STEP_TEXT[step])
        return DONE_LINE.format(step=STEP_TEXT[step], next=STEP_TEXT[data["step"]])
    return _locked(cwd, change)


def redo(cwd, step):
    """kb build redo <步>：回到做過的那一步（例「重新抽樣」），那一步之後的完成紀錄拿掉。"""
    def change(vault, data, tid):
        seq = sequence(data)
        if step not in seq:
            raise Refused(REDO_SKIPPED.format(step=STEP_TEXT.get(step, step)))
        if seq.index(step) > seq.index(data["step"]):
            raise Refused(REDO_LATER.format(step=STEP_TEXT[step]))
        for s in seq[seq.index(step):]:
            data["done"].pop(s, None)
        data["step"] = step
        return REDO_LINE.format(step=STEP_TEXT[step])
    return _locked(cwd, change)


def set_value(cwd, key, value):
    """kb build set people|adopt "<值>"：people 記她的原話（一句、遮值）；adopt 要是舊 llm-wiki vault，記絕對路徑。"""
    def change(vault, data, tid):
        text = str(value or "").strip()
        if not text:
            raise Refused(VALUE_EMPTY)
        if key == "people":
            if len(text) > 200 or "\n" in text:
                raise Refused(VALUE_LONG)
            data["people"] = credentials.mask_secrets(text, credentials.known_names(vault))
            return SET_PEOPLE.format(value=data["people"])
        got = adopt_check(text)
        data["adopt"] = got["path"]
        return SET_ADOPT.format(value=got["path"])
    return _locked(cwd, change)


def _running(vault, kinds):
    """所有 session 認領中的批次（kinds 裡的種類）。"""
    try:
        data = batches.load(vault)
    except ValueError:
        return []
    now = _now()
    agents = batches._agents(data)
    minutes = config.get(vault, "card_batch_expire_minutes")
    return [b for b in data["batches"] if b.get("kind") in kinds and batches.live(b, now, minutes, agents)]


def _given_up(vault):
    """製卡助手回報沒做成過的項目（每個 job 的 failed）：這次建置不擋在它們身上，平常整理還會再試（D-196 的 S2 那套）。"""
    try:
        data = batches.load(vault)
    except ValueError:
        return set()
    return {k for j in data["jobs"] for k in (j.get("failed") or {})}


def _check(vault, data, step):
    """done 的前提（build 釐清）：不過丟 Refused 一句原因。"""
    if step == "setup":
        st = profile.status(vault)
        if st["state"] == "missing":
            raise Refused(PROFILE_MISSING)
        if st["state"] == "error":
            raise Refused(PROFILE_ERROR.format(error=st["error"]))
        if st["state"] == "changed" and profile.has_diff(st["diff"]):
            raise Refused(PROFILE_PENDING)
    elif step == "cards":
        run = _running(vault, ("cards", "agenda"))
        if run:
            raise Refused(RUNNING.format(n=len(run)))
        parts = batches.split(vault, batches._collect(vault))
        skip = _given_up(vault)
        n = (sum(1 for _b, k, _i in parts["cards"] + parts["agenda"] if k not in skip)
             + sum(1 for b, k, _i in parts["main"] if b in ("new", "stale", "unlinked") and k not in skip))
        if n:
            raise Refused(LEFT_CARDS.format(n=n))
    elif step == "names":
        from kb._lib import plan
        n = len(plan._names(batches._collect(vault), vault))
        if n:
            raise Refused(LEFT_NAMES.format(n=n))
    elif step == "synthesis":
        run = _running(vault, ("synthesis",))
        if run:
            raise Refused(RUNNING.format(n=len(run)))
        parts = batches.split(vault, batches._collect(vault))
        skip = _given_up(vault)
        n = sum(1 for _b, k, _i in parts["synthesis"] if k not in skip)
        if n:
            raise Refused(LEFT_SYNTHESIS.format(n=n))
    elif step == "adopt":
        if _running(vault, (batches.ADOPT,)):
            raise Refused(RUNNING_ADOPT)
    elif step == "finish":
        if not data.get("report"):
            raise Refused(NO_REPORT)


# ---------- 環境（D-189） ----------

def _item(key, level, text, fix=None):
    got = {"key": key, "level": level, "text": text}
    if fix:
        got["fix"] = fix
    return got


def _claude_home():
    return Path.home()


def _sac_state():
    """智慧型應用程式控制：登錄檔 VerifiedAndReputablePolicyState（0 關、1 開、2 評估中）；沒有這個值回 None，讀不了丟 OSError。"""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            value, _t = winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")
    except FileNotFoundError:
        return None
    return int(value)


def _probe_pdf_bytes():
    """一頁空白 PDF（實跑 pypdfium2 用，不寫檔）。"""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


_PROBE = ("import sys\nimport pypdfium2 as p\nd=p.PdfDocument(sys.stdin.buffer.read())\npg=d[0]\nb=pg.render(scale=0.5)\n"
          "b.close()\npg.close()\nd.close()\nprint('ok')\n")


def _pdf_probe():
    """在子行程實際 import pypdfium2、轉一頁（PYTHONPATH 帶工具夾）；成功回 True，被擋或起不來回 False。"""
    import tempfile
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP")}
    env["PYTHONPATH"] = str(office.pylib())
    try:
        r = subprocess.run([sys.executable, "-c", _PROBE], input=_probe_pdf_bytes(), capture_output=True, timeout=60,
                           env=env, cwd=tempfile.gettempdir(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == b"ok"


def _pdfium_installed():
    return office.pdfium_ok()


def _office_env():
    return office.detect()


def _disk_free(p):
    return shutil.disk_usage(str(p)).free


def _env_python():
    v = sys.version_info
    if v >= (3, 10):
        return _item("python", "ok", f"Python {v.major}.{v.minor}")
    return _item("python", "fail", f"Python {v.major}.{v.minor} 太舊，要 3.10 以上")


def _env_kb():
    m = read_manifest() or {}
    return _item("kb", "ok", f"kb 指令跑得起來（{m.get('name', 'wiki-with-willy')} {m.get('version', '')}）".replace(" ）", "）"))


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path))).rstrip("\\/")


def _env_trust(p):
    """~/.claude.json 的 projects.<路徑>.hasTrustDialogAccepted，上層資料夾信任過也算（舊 envcheck 的判法）。"""
    try:
        data = json.loads((_claude_home() / ".claude.json").read_text(encoding="utf-8"))
        projects = data.get("projects")
        if not isinstance(projects, dict):
            raise ValueError("projects")
    except (OSError, ValueError, AttributeError):
        return _item("trust", "warn", "看不出這個資料夾有沒有按過信任")
    target = _norm(p)
    for key, v in projects.items():
        k = _norm(key)
        if isinstance(v, dict) and v.get("hasTrustDialogAccepted") and (target == k or target.startswith(k + os.sep)):
            return _item("trust", "ok", "這個資料夾按過信任")
    return _item("trust", "fail", "這個資料夾還沒按過信任：在這個資料夾開對話、跳出「是否信任」時按信任，不然每一步都會被問")


def _env_pdf():
    if not _pdfium_installed():
        return _item("pdf", "fail", "還沒裝 PDF 元件，PDF 會讀不到", fix="kb office setup")
    if _pdf_probe():
        return _item("pdf", "ok", "PDF 元件可以用")
    return _item("pdf", "fail", "PDF 元件被擋，PDF 會讀不到")


def _env_sac(pdf_blocked):
    try:
        state = _sac_state()
    except (OSError, ValueError):
        return _item("sac", "warn", "看不出智慧型應用程式控制開著沒有")
    if state is None:
        return _item("sac", "ok", "這台沒有智慧型應用程式控制")
    if state == 0:
        return _item("sac", "ok", "智慧型應用程式控制關著")
    head = "智慧型應用程式控制開著" if state == 1 else "智慧型應用程式控制在評估中（之後可能自己打開）"
    tail = "它擋了 PDF 元件，PDF 會讀不到；不建議關，關了可能開不回來" if pdf_blocked else "目前沒擋到我們的程式"
    return _item("sac", "warn", f"{head}；{tail}")


def _env_office():
    env = _office_env()
    apps = [office.APP_NAMES[a] for a, path in (env.get("office") or {}).items() if path]
    setup = office.capabilities(env)["setup"]
    tail = "；做文件的工具還沒裝" if setup else ""
    fix = "kb office setup" if setup else None
    if apps:
        return _item("office", "ok", f"有 Office（{'、'.join(apps)}）{tail}", fix)
    if env.get("soffice"):
        return _item("office", "ok", f"有 LibreOffice{tail}", fix)
    return _item("office", "warn", f"沒有 Office 也沒有 LibreOffice：轉 PDF、重算 Excel 做不到，讀檔不受影響{tail}", fix)


def _size(n):
    return f"{n / 1024 ** 3:.0f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


def _disk_warn_mb(p):
    try:
        return config.get(p, "build_disk_warn_mb")
    except Exception:  # noqa: BLE001  不在知識庫（還沒建）：用預設
        return config.defaults()["build_disk_warn_mb"]


def _env_disk(p):
    free = _disk_free(p)
    limit = _disk_warn_mb(p)
    if free < limit * 1024 ** 2:
        return _item("disk", "warn", f"剩 {_size(free)}，少於 {_size(limit * 1024 ** 2)}，整理到一半可能寫不下")
    return _item("disk", "ok", f"剩 {_size(free)}")


def _enabled_plugins(p):
    """enabledPlugins：使用者的 settings.json，再被這個資料夾的 .claude/settings.json、settings.local.json 蓋過。"""
    got = {}
    for f in (_claude_home() / ".claude" / "settings.json", Path(p) / ".claude" / "settings.json",
              Path(p) / ".claude" / "settings.local.json"):
        try:
            value = json.loads(f.read_text(encoding="utf-8")).get("enabledPlugins")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(value, dict):
            got.update(value)
    return got


def _installed_plugins():
    try:
        data = json.loads((_claude_home() / ".claude" / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
        plugins = data.get("plugins")
    except (OSError, ValueError, AttributeError):
        return []
    return list(plugins) if isinstance(plugins, dict) else []


OLD_WIKI = "llm-wiki@"


def _env_old_wiki(p):
    enabled = _enabled_plugins(p)
    keys = [k for k in _installed_plugins() if k.startswith(OLD_WIKI)]
    keys += [k for k in enabled if k.startswith(OLD_WIKI) and k not in keys]
    if any(enabled.get(k) is True for k in keys):
        return _item("old_wiki", "fail", "舊的 llm-wiki 開著，要先在外掛頁停用")
    if any(enabled.get(k) is None for k in keys):
        return _item("old_wiki", "warn", "舊的 llm-wiki 裝著，看不出有沒有停用；請在外掛頁確認是停用的")
    if keys:
        return _item("old_wiki", "ok", "舊的 llm-wiki 停用了")
    return _item("old_wiki", "ok", "沒有舊的 llm-wiki")


def env_items(cwd):
    """八項（ENV_KEYS 的順序），每項 {"key","level","text"}（缺選配多 fix）。只讀；哪一項出錯就那項回 warn。"""
    p = Path(cwd).resolve()

    def safe(key, fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001  一項看不出來不擋其他項
            return _item(key, "warn", f"看不出來（{type(e).__name__}）")
    pdf = safe("pdf", _env_pdf)
    got = {"python": safe("python", _env_python), "kb": safe("kb", _env_kb), "trust": safe("trust", _env_trust, p),
           "sac": safe("sac", _env_sac, pdf["level"] == "fail" and pdf["text"].startswith("PDF 元件被擋")),
           "pdf": pdf, "office": safe("office", _env_office), "disk": safe("disk", _env_disk, p),
           "old_wiki": safe("old_wiki", _env_old_wiki, p)}
    return [got[k] for k in ENV_KEYS]


# ---------- 盤點（facts） ----------

PROBE_EXTS = (".pdf",) + extract.WORD_EXTS + extract.EXCEL_EXTS + extract.SLIDE_EXTS + extract.LEGACY_EXTS
READ_TEXT = {"placeholder": "只在雲端", "credential": "檔名像密碼", "too_large": "太大", "error": "讀不到"}


def _why_unreadable(vault, srcs, entry, open_reasons):
    """這份讀得到嗎：讀得到回 None，否則回一句原因（清冊的 read、待處理記過的、看檔頭認得出的加密與舊格式、不支援的格式）。"""
    if entry.get("read") in READ_TEXT:
        return READ_TEXT[entry["read"]]
    if entry.get("id") in open_reasons:
        return open_reasons[entry["id"]].split("：", 1)[0]
    uri = entry["locations"][0]["uri"]
    kind = extract.kind_by_name(uri)
    if kind == "unsupported":
        return "不支援的格式"
    if Path(uri).suffix.lower() in PROBE_EXTS:
        path = inventory.path_of(vault, srcs, uri)
        try:
            got = extract.probe(path) if path is not None else None
        except OSError:
            return "讀不到"
        if got == "encrypted":
            return "有密碼"
        if got == "legacy_office":
            return "舊格式"
        if got == "unsupported":
            return "不支援的格式"
    return None


def _count(d, key):
    d[key] = d.get(key, 0) + 1


def facts(vault):
    """kb build facts：每個來源 {"alias","files","formats","folders","subfolders","unreadable":{"count","reasons"}}；
    total、unreadable 合計、estimate（要整理的份數、大約幾分鐘、份數多時的用量那句）。只讀。"""
    from kb._lib import plan
    srcs = sources_lib.load(vault)
    aliases = {s["alias"].casefold(): s["alias"] for s in srcs.entries}
    try:
        items = pending.load(vault)["items"]
    except ValueError:
        items = []
    open_reasons = {(i.get("file_ids") or [None])[0]: str(i.get("reason") or "") for i in items
                    if i.get("kind") == "unreadable" and i.get("status") == "open"}
    per = {s["alias"]: {"alias": s["alias"], "files": 0, "formats": {}, "folders": {}, "subfolders": {},
                        "unreadable": {"count": 0, "reasons": {}}} for s in srcs.entries}
    for e in inventory.load(vault)["entries"]:
        if e.get("status") != "active" or inventory.is_link(e) or not e.get("locations"):
            continue
        alias, rel = inventory.split_uri(e["locations"][0]["uri"])
        alias = aliases.get(alias.casefold())
        if alias is None:
            continue
        row = per[alias]
        row["files"] += 1
        _count(row["formats"], Path(rel).suffix.lower() or "（沒有副檔名）")
        parts = rel.split("/")
        _count(row["folders"], parts[0] if len(parts) > 1 else "（直接放在這裡）")
        if len(parts) > 2:
            _count(row["subfolders"], "/".join(parts[:2]))
        why = _why_unreadable(vault, srcs, e, open_reasons)
        if why:
            row["unreadable"]["count"] += 1
            _count(row["unreadable"]["reasons"], why)
    rows = []
    for row in per.values():
        row["folders"] = [{"name": k, "files": n} for k, n in sorted(row["folders"].items(), key=lambda x: (-x[1], x[0]))]
        row["subfolders"] = [{"name": k, "files": n}
                             for k, n in sorted(row["subfolders"].items(), key=lambda x: (-x[1], x[0]))]
        rows.append(row)
    out = batches._collect(vault)
    waiting = batches.waiting(vault, out)
    how_long = plan._how_long(waiting, vault, config).strip() if waiting else "不用"
    many = waiting > config.get(vault, "ingest_auto_max")
    return {"sources": rows, "total": sum(r["files"] for r in rows),
            "unreadable": sum(r["unreadable"]["count"] for r in rows),
            "estimate": {"files": waiting, "text": f"大約 {how_long}" if waiting else "沒有要整理的",
                         "usage": "份數多，會用掉比平常多的額度" if many else None}}


# ---------- 收舊 vault（D-193） ----------

WIKI_SKIP = {"hot.md", "log.md", "agenda.md", "index.md"}
RULE_HEADS = ("鐵律", "裁示", "雷", "習慣", "規則")
_AGENDA_LINE = re.compile(r"^(✅\s*)?\d{4}-\d{2}-\d{2}\s")


def _old_root(path):
    p = Path(os.path.expandvars(str(path))).expanduser()
    try:
        p = p.resolve()
    except OSError:
        pass
    if not p.is_dir() or not ((p / "wiki").is_dir() or (p / "raw" / ".manifest.json").is_file()):
        raise Refused(NOT_OLD_VAULT)
    return p


def _read(p):
    """舊檔照 UTF-8 讀，不行再 cp950；都不行回 None（不改檔）。"""
    try:
        raw = Path(p).read_bytes()
    except OSError:
        return None
    for enc in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _wiki_pages(root):
    wiki = root / "wiki"
    if not wiki.is_dir():
        return []
    got = []
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(wiki).as_posix()
        if rel in WIKI_SKIP or rel.split("/")[0] == "meta" or p.is_symlink():
            continue
        got.append(p)
    return got


def _special(root, name):
    for p in (root / "wiki" / name, root / name):
        if p.is_file():
            return p
    return None


def _linked(root):
    """raw/.manifest.json 的 repos（_self 與 _ 開頭的備註不算）：[{"alias","path","exists"}]；spec 照舊系統的解析
    （環境變數、~、相對 vault 根、絕對）。"""
    try:
        data = json.loads((root / "raw" / ".manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    repos = data.get("repos") if isinstance(data, dict) else None
    got = []
    for alias, v in (repos or {}).items() if isinstance(repos, dict) else []:
        if alias == "_self" or alias.startswith("_") or not isinstance(v, dict) or not v.get("path"):
            continue
        s = os.path.expandvars(str(v["path"]))
        p = Path(s).expanduser() if s.startswith("~") else Path(s)
        if not p.is_absolute():
            p = root / p
        try:
            p = p.resolve()
        except OSError:
            pass
        got.append({"alias": alias, "path": p.as_posix(), "exists": p.is_dir()})
    return got


def _rules(root):
    """CLAUDE.md 裡規則那幾節（標題有 鐵律、裁示、雷、習慣、規則）的條目：- 開頭、有內容的行。"""
    text = _read(root / "CLAUDE.md") or ""
    n, inside = 0, False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = any(w in line for w in RULE_HEADS)
            continue
        if inside and re.match(r"^\s*[-*]\s+\S", line):
            n += 1
    return n


def _agenda(root):
    p = _special(root, "agenda.md")
    text = _read(p) if p else ""
    return sum(1 for line in (text or "").splitlines() if _AGENDA_LINE.match(line.strip()) and not line.strip().startswith("✅"))


def adopt_check(path):
    """kb build adopt <路徑> --check：只讀。不是舊 llm-wiki vault（沒有 wiki/ 也沒有 raw/.manifest.json）丟 Refused。"""
    root = _old_root(path)
    hot, log = _special(root, "hot.md"), _special(root, "log.md")
    return {"path": root.as_posix(), "raw": (root / "raw").as_posix() if (root / "raw").is_dir() else None,
            "linked": _linked(root), "wiki_pages": len(_wiki_pages(root)),
            "hot_chars": len(_read(hot) or "") if hot else 0, "log_chars": len(_read(log) or "") if log else 0,
            "rules": _rules(root), "agenda": _agenda(root)}


def _pieces(text, limit):
    """照行切成每段不超過 limit 字；一行比 limit 長就硬切。"""
    got, cur = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                got.append(cur)
                cur = ""
            got.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            got.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        got.append(cur)
    return [g for g in got if g.strip()]


HEAD_MAX = 200  # 段頭那行（來源路徑）最多佔的字數


def adopt_extract(vault, path):
    """kb build adopt <路徑> --extract：要讀的文字遮值後切段，寫進 .system/temp/adopt/001.txt…（先清掉上次的段）。
    回 {"chunks":[絕對路徑], "files":[{"path","chunks"}]}。舊 vault 只讀。"""
    root = _old_root(path)
    limit = config.get(vault, "adopt_chunk_chars")
    known = credentials.known_names(vault)
    order = [_special(root, "hot.md"), root / "CLAUDE.md", _special(root, "agenda.md")] + _wiki_pages(root) + \
        [_special(root, "log.md")]
    temp = Path(vault) / ".system" / "temp"
    d = temp / "adopt"
    for x in (temp, d):
        if x.is_symlink() or (x.exists() and os.path.realpath(x) != os.path.abspath(x)):
            raise Refused(ADOPT_PLACE)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.iterdir():
        if old.is_file() and old.suffix == ".txt":
            old.unlink()
    chunks, files, n = [], [], 0
    for p in order:
        if p is None or not p.is_file():
            continue
        text = _read(p)
        if not text or not text.strip():
            continue
        rel = p.relative_to(root).as_posix()
        parts = _pieces(credentials.mask_secrets(text, known), max(limit - HEAD_MAX, 500))
        for i, piece in enumerate(parts, 1):
            n += 1
            head = f"<!-- 來源：{rel[:HEAD_MAX - 40]}，第 {i}／{len(parts)} 段 -->\n"
            target = d / f"{n:03d}.txt"
            write_text_atomic(target, head + piece)
            chunks.append(target.resolve().as_posix())
        files.append({"path": rel, "chunks": len(parts)})
    return {"chunks": chunks, "files": files}


# ---------- 交付報告（D-066、D-047） ----------

def report_facts(vault):
    """kb build report --facts：數字與清單（份數、卡數、綜合卡數、讀不到的、AI 判斷登記的名字、合併了哪些、被擋掉的功能、
    幾個人用、花了多久）。只讀。"""
    from kb._lib import cards
    data = load(vault) or {}
    srcs = sources_lib.load(vault)
    aliases = {s["alias"].casefold() for s in srcs.entries}
    files = [e for e in inventory.load(vault)["entries"] if e.get("status") == "active" and not inventory.is_link(e)
             and e.get("locations") and inventory.split_uri(e["locations"][0]["uri"])[0].casefold() in aliases]
    try:
        items = pending.load(vault)["items"]
    except ValueError:
        items = []
    by_id = {e.get("id"): e for e in inventory.load(vault)["entries"]}
    unreadable = []
    for i in items:
        if i.get("kind") == "unreadable" and i.get("status") == "open":
            e = by_id.get((i.get("file_ids") or [None])[0])
            name = (e["locations"][0]["uri"].rsplit("/", 1)[-1] if e and e.get("locations") else i.get("title")) or "?"
            unreadable.append({"name": name, "reason": str(i.get("reason") or "")})
    counts = {}
    for u in unreadable:
        _count(counts, u["reason"].split("：", 1)[0])
    ents = entities.load(vault)["entities"]
    names = {e.get("id"): e.get("name") for e in ents}
    started = _parse(data.get("started"))
    now = _now()
    return {"sources": [s["alias"] for s in srcs.entries], "files": len(files),
            "cards": len(cards._scan_groups(vault)[0]),
            "synthesis": sum(len(v) for v in entities.synthesis_files(vault).values()),
            "unreadable": unreadable, "unreadable_counts": counts,
            "ai_judged": [e["name"] for e in ents if e.get("status") == "active" and e.get("source") == entities.AI_JUDGED],
            "merged": [{"from": e["name"], "into": names.get(e.get("merged_into"), e.get("merged_into"))}
                       for e in ents if e.get("status") == "merged"],
            "blocked": [i for i in (data.get("env") or {}).get("items") or [] if i.get("level") != "ok"],
            "people": data.get("people"), "no_sources": bool(data.get("no_sources")), "adopt": data.get("adopt"),
            "started": data.get("started"), "finished": _stamp(now),
            "minutes": int((now - started).total_seconds() // 60) if started else None}


def _took(minutes):
    if minutes is None:
        return "看不出來"
    if minutes < 60:
        return f"約 {max(minutes, 1)} 分鐘"
    days, rest = divmod(minutes, 24 * 60)
    return (f"{days} 天又 " if days else "") + f"約 {round(rest / 60, 1):g} 小時"


def numbers_lines(f):
    """數字段（程式接上，AI 不寫）。"""
    lines = [f"- 登記的資料夾 {len(f['sources'])} 個，共 {f['files']} 份檔" if not f["no_sources"] else "- 沒有登記資料夾",
             f"- 檔案卡 {f['cards']} 張、綜合卡 {f['synthesis']} 張"]
    if f["unreadable"]:
        why = "、".join(f"{k} {n}" for k, n in f["unreadable_counts"].items())
        lines.append(f"- 讀不到 {len(f['unreadable'])} 份（{why}）")
    if f["ai_judged"]:
        lines.append(f"- 名字由 AI 判斷登記的 {len(f['ai_judged'])} 個：{'、'.join(f['ai_judged'])}")
    if f["merged"]:
        lines.append("- 合併了：" + "、".join(f"{m['from']} 併進 {m['into']}" for m in f["merged"]))
    if f["blocked"]:
        lines.append("- 這台電腦要注意的：" + "；".join(i["text"] for i in f["blocked"]))
    if f["people"]:
        lines.append(f"- 幾個人用：{f['people']}")
    lines.append(f"- 建置花了：{_took(f['minutes'])}")
    return lines


_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _sections(text):
    """## 標題切段：回 ([(標題, 內文)], 第一個 ## 之前的文字)。"""
    got, head, cur, body = [], [], None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur is not None:
                got.append((cur, "\n".join(body)))
            cur, body = line[3:].strip(), []
        elif cur is None:
            head.append(line)
        else:
            body.append(line)
    if cur is not None:
        got.append((cur, "\n".join(body)))
    return got, "\n".join(head)


def report_from(cwd, src):
    """kb build report --from <路徑>：只收工作區（.system/temp/office/）底下的檔；六個標題要齊（缺或多 → Refused 說哪一段）；
    AI 的文字遮值、拿掉註解，沒內容的段不寫，數字段由程式接上；寫 系統/建置報告.md（備進回合），build.json 記 report。"""
    p = Path(cwd).resolve()
    vault = guard.find_vault(str(p))
    if vault is None:
        raise Refused(NOT_STARTED)
    vault = Path(os.path.realpath(str(vault)))
    target = Path(src)
    if not target.is_absolute():
        target = p / target
    ws = (vault / OFFICE_POSIX).resolve()
    try:
        real = target.resolve()
        real.relative_to(ws)
    except (OSError, ValueError):
        raise Refused(REPORT_PLACE) from None
    try:
        text = real.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(REPORT_UNREADABLE.format(getattr(e, "strerror", None) or type(e).__name__)) from None
    sections, _lead = _sections(_COMMENT.sub("", text))
    seen = [h for h, _b in sections]
    for h in seen:
        if h not in REPORT_HEADS or seen.count(h) > 1:
            raise Refused(REPORT_EXTRA.format(h))
    for h in REPORT_HEADS:
        if h not in seen:
            raise Refused(REPORT_MISSING.format(h))
    known = credentials.known_names(vault)
    bodies = dict(sections)
    out = [f"# 建置報告\n\n建好的日期：{_now().date().isoformat()}\n"]
    for h in REPORT_HEADS[:-1]:
        body = credentials.mask_secrets(bodies[h].strip(), known)
        if body:
            out.append(f"## {h}\n\n{body}\n")
    out.append(f"## {NUMBERS}\n\n" + "\n".join(numbers_lines(report_facts(vault))) + "\n")
    tid = _turn_id(vault)
    turn.backup(vault, REPORT_POSIX, tid)
    (vault / REPORT_POSIX).parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(vault / REPORT_POSIX, "\n".join(out))
    if _path(vault).is_file():
        with lock(vault):
            data = load(vault)
            if data is not None and data["step"] != "done":
                data["report"] = _stamp()
                save(vault, data, tid)
    return vault
