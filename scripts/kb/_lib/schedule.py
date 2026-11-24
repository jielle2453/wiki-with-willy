"""背景比對的電腦排程（#60、#61，D-536）：Windows 工作排程器每 N 分鐘跑一次 kb changes --background。

工作名稱 wiki-with-willy\\變動比對-<知識庫編號 8 碼>（一台電腦好幾個知識庫各一個）；用 schtasks /create /xml 建（/tr 有 261 字上限）。
設定只用基本的：建好起與登入後每 N 分鐘、只在使用者登入時跑（InteractiveToken）、同一個工作還在跑不開新的；不補跑、不重試；
筆電用電池也照跑（排程器預設用電池不跑，背景比對就停了）。
ensure 回 created｜updated｜ok｜off｜unsupported｜failed，不丟例外；failed 記一件待處理（同一件不重開），之後設好了關掉。

網頁小伺服器（#74，D-356）：工作 wiki-with-willy\\網頁-<同一個 8 碼>，同一個樣板，執行上限 PT0S（不設上限）、每 1 分鐘重複；
指令 kb.py web serve --vault <庫> --task <名稱>；開關看 .system/local/web.json（web.read_switch）。ensure_web 回傳值同 ensure，
失敗記待處理（check web）。每天第一句不查網頁排程：換版由伺服器自己交棒，建置時 kb web on 建。
"""
import getpass
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, unescape

WINDOWS = os.name == "nt"
NO_SCHEDULE_ENV = "WIKI_WITH_WILLY_NO_SCHEDULE"  # 測試套件一律設（tests/__init__.py）：不碰這台真的排程（裁定 11）
FOLDER = "wiki-with-willy"
TASK_PREFIX = FOLDER + "\\變動比對-"
WEB_PREFIX = FOLDER + "\\網頁-"
PENDING_KEY = "schedule_failed"
PENDING_TEXT = "背景比對的排程設不起來（{reason}）；新放的檔要等 AI 當場比才看得到，請服務人員看一下"
WEB_PENDING_KEY = "web_schedule_failed"
WEB_PENDING_TEXT = "網頁的排程設不起來（{reason}）；網頁可能打不開，請服務人員看一下"
BACKGROUND_LIMIT = "PT12H"  # 背景比對：第一次冷讀（裁定 15）
WEB_LIMIT = "PT0S"  # 網頁：不設執行時間上限（D-356）
BACKGROUND_DESCRIPTION = "wiki-with-willy 背景比對：每 {minutes} 分鐘看一次資料夾有沒有新檔或改過的檔"
WEB_DESCRIPTION = "wiki-with-willy 網頁：這台電腦上的小伺服器，停了每 {minutes} 分鐘自己起來"
SCHTASKS_TIMEOUT = 2  # 秒，一次 schtasks；每天第一句的 turn.py 上限 5 秒（review 1）
_QUOT = {"&quot;": '"', "&apos;": "'"}
_QUOT_REV = {'"': "&quot;"}

XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{description}</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Repetition><Interval>{interval}</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger>
      <Repetition><Interval>{interval}</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{user}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>false</StartWhenAvailable>
    <ExecutionTimeLimit>{limit}</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{command}</Command><Arguments>{arguments}</Arguments></Exec>
  </Actions>
</Task>
"""


class Failed(Exception):
    """schtasks 失敗；args[0] 是寫進待處理的原因。"""


class Skipped(Exception):
    """設了 NO_SCHEDULE_ENV：不叫 schtasks。"""


def _schtasks(argv):
    """跑 schtasks，回 (exit code, 輸出文字)。輸出是系統碼頁（實測 /query /xml 也是）。測試換掉它。
    設了 NO_SCHEDULE_ENV 丟 Skipped、不叫（裁定 11）。"""
    if os.environ.get(NO_SCHEDULE_ENV):
        raise Skipped()
    r = subprocess.run(argv, capture_output=True, timeout=SCHTASKS_TIMEOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, (r.stdout or b"").decode("mbcs", "replace") + (r.stderr or b"").decode("mbcs", "replace")


def _codepage(text):
    """照系統碼頁來回一次：/query 印出來時碼頁沒有的字會變 ?，比對前兩邊一樣處理。"""
    return text.encode("mbcs", "replace").decode("mbcs", "replace") if WINDOWS else text


def _pythonw():
    """跟這支 python 同一夾的 pythonw.exe（不開黑色視窗）；沒有回 None。"""
    p = Path(sys.executable).with_name("pythonw.exe")
    return str(p) if p.is_file() else None


def task_name(vault):
    from kb._lib import vault as vault_lib
    vid = vault_lib.ensure_vault_id(vault)
    return TASK_PREFIX + vid.removeprefix("kb-")[:8]


def web_task_name(vault):
    return WEB_PREFIX + task_name(vault).removeprefix(TASK_PREFIX)


def _exp(vault, minutes, name, argv, limit, description):
    from kb._lib import paths
    pyw = _pythonw()
    if not pyw:
        raise Failed(f"{Path(sys.executable).parent} 找不到 pythonw.exe")
    kb = paths.plugin_root() / "scripts" / "kb.py"
    vault = str(Path(vault))
    # list2cmdline 照 Windows 的引號、反斜線規則（裁定 14：手寫 "D:\" 會吞掉後面的參數）
    args = subprocess.list2cmdline([str(kb)] + argv + ["--vault", vault, "--task", name])
    return {"command": pyw, "arguments": args, "interval": f"PT{minutes}M", "minutes": minutes, "limit": limit,
            "description": description.format(minutes=minutes)}


def expected(vault, minutes, name):
    """這台現在該有的指令與間隔。指令帶 --task 自己的名稱：庫刪了背景照它刪掉自己（裁定 12a）；不設起始夾，
    整條路照 --vault 找庫（裁定 33）。pythonw 找不到丟 Failed。"""
    return _exp(vault, minutes, name, ["changes", "--background"], BACKGROUND_LIMIT, BACKGROUND_DESCRIPTION)


def web_expected(vault, name):
    """網頁小伺服器的排程：每 1 分鐘、不設執行上限（D-356）；其他同 expected。"""
    return _exp(vault, 1, name, ["web", "serve"], WEB_LIMIT, WEB_DESCRIPTION)


def _field(xml, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
    return unescape(m.group(1), _QUOT).strip() if m else None


def _same(xml, exp):
    """指令、參數、間隔、執行上限一樣，而且沒有起始夾（裁定 33：舊版設成庫本身，庫刪了工作排程器啟動就失敗 0x8007010B，自刪走不到）。"""
    return _field(xml, "WorkingDirectory") is None and all(_field(xml, tag) == _codepage(exp[key]) for tag, key in
               (("Command", "command"), ("Arguments", "arguments"), ("Interval", "interval"),
                ("ExecutionTimeLimit", "limit")))


def _xml(exp):
    user = (os.environ.get("USERDOMAIN", "") + "\\" if os.environ.get("USERDOMAIN") else "") + getpass.getuser()
    return XML.format(description=escape(exp["description"]), limit=exp["limit"], start=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), interval=exp["interval"],
                      user=escape(user), command=escape(exp["command"]), arguments=escape(exp["arguments"], _QUOT_REV))


def _query(name):
    code, out = _schtasks(["schtasks", "/query", "/tn", name, "/xml"])
    return out if code == 0 else None


def _create(name, exp):
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(tmp).write_text(_xml(exp), encoding="utf-16")
        code, out = _schtasks(["schtasks", "/create", "/xml", tmp, "/tn", name, "/f"])
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if code != 0:
        raise Failed(" ".join(out.split())[:120] or f"schtasks 回 {code}")


def _delete(name):
    code, out = _schtasks(["schtasks", "/delete", "/tn", name, "/f"])
    if code != 0:
        raise Failed(" ".join(out.split())[:120] or f"schtasks 回 {code}")


def forget(name):
    """背景比對或網頁找不到庫（裁定 12、12a）：照排程帶來的 --task 刪掉自己。不是本 plugin 的名稱不刪；回 True 是刪了。"""
    if not WINDOWS or not name or not name.startswith((TASK_PREFIX, WEB_PREFIX)):
        return False
    try:
        _delete(name)
        return True
    except (Failed, Skipped, OSError, subprocess.SubprocessError):
        return False


def _turn_id(vault):
    from kb._lib import turn
    return turn.current(vault)["turn"]


def record_pending(vault, key, text, data, check):
    """記一件待處理（同一件不重開）；記不了只寫 hook-errors。網頁的連接埠被佔也用這個（web.py）。"""
    from kb._lib import ledger, pending
    try:
        pending.add_lint(vault, key, text, data, _turn_id(vault), check=check)
    except Exception as e:  # noqa: BLE001  記不了待處理也不擋開場
        what = "排程失敗" if check == "schedule" else key
        ledger.log_error(vault, f"{what}沒記成待處理：{type(e).__name__}: {e}", source=check)


def resolve_pending(vault, key):
    """好了：之前記的那件（key）關掉。"""
    from kb._lib import pending
    ids = [i["id"] for i in pending.load(vault)["items"] if i.get("key") == key and i.get("status") == "open"]
    if ids:
        pending.close_ids(vault, ids, pending.LINT_GONE_HOW, _turn_id(vault))


def _record_failed(vault, reason):
    record_pending(vault, PENDING_KEY, PENDING_TEXT.format(reason=reason), {"reason": reason}, "schedule")


def _resolve(vault):
    """設好了：之前記的「排程設不起來」關掉。"""
    resolve_pending(vault, PENDING_KEY)


def ensure(vault):
    """查這台的排程在不在、對不對，不在或不對就重設。background_scan_minutes 是 0 → 有就刪。"""
    if not WINDOWS:
        return "unsupported"
    try:
        from kb._lib import config
        minutes = config.get(vault, "background_scan_minutes")
        name = task_name(vault)
        current = _query(name)
        if minutes <= 0:
            if current is not None:
                _delete(name)
            got = "off"
        else:
            exp = expected(vault, minutes, name)
            if current is not None and _same(current, exp):
                got = "ok"
            else:
                _create(name, exp)
                got = "updated" if current is not None else "created"
    except Skipped:
        return "unsupported"
    except Exception as e:  # noqa: BLE001
        reason = e.args[0] if isinstance(e, Failed) and e.args else f"{type(e).__name__}: {e}"
        _record_failed(vault, str(reason)[:120])
        return "failed"
    try:
        _resolve(vault)
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"關「排程設不起來」出錯：{type(e).__name__}: {e}", source="schedule")
    return got


def _web_on(vault):
    from kb._lib import web
    return bool((web.read_switch(vault) or {}).get("on"))


def ensure_web(vault):
    """網頁的排程：開關 on 而排程不在或不對就重設；off（或沒有 web.json）有就刪。回傳值與失敗處理照 ensure，待處理 check web。"""
    if not WINDOWS:
        return "unsupported"
    try:
        name = web_task_name(vault)
        current = _query(name)
        if not _web_on(vault):
            if current is not None:
                _delete(name)
            got = "off"
        else:
            exp = web_expected(vault, name)
            if current is not None and _same(current, exp):
                got = "ok"
            else:
                _create(name, exp)
                got = "updated" if current is not None else "created"
    except Skipped:
        return "unsupported"
    except Exception as e:  # noqa: BLE001
        reason = str(e.args[0] if isinstance(e, Failed) and e.args else f"{type(e).__name__}: {e}")[:120]
        record_pending(vault, WEB_PENDING_KEY, WEB_PENDING_TEXT.format(reason=reason), {"reason": reason}, "web")
        return "failed"
    try:
        resolve_pending(vault, WEB_PENDING_KEY)
    except Exception as e:  # noqa: BLE001
        from kb._lib import ledger
        ledger.log_error(vault, f"關「網頁的排程設不起來」出錯：{type(e).__name__}: {e}", source="web")
    return got


def web_task_state(vault):
    """kb web status 用，只查不改：ok 有且指令對；missing 沒有；stale 有但指的不是這一版或設定不同；
    unsupported 不是 Windows 或測試環境（設了 NO_SCHEDULE_ENV）。"""
    if not WINDOWS:
        return "unsupported"
    try:
        name = web_task_name(vault)
        current = _query(name)
    except Skipped:
        return "unsupported"
    except (OSError, subprocess.SubprocessError):
        return "missing"
    if current is None:
        return "missing"
    try:
        return "ok" if _same(current, web_expected(vault, name)) else "stale"
    except Failed:
        return "stale"
