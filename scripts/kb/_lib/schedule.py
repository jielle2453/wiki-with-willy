"""背景比對的電腦排程（#60、#61，D-536）：Windows 工作排程器每 N 分鐘跑一次 kb changes --background。

工作名稱 wiki-with-willy\\變動比對-<知識庫編號 8 碼>（一台電腦好幾個知識庫各一個）；用 schtasks /create /xml 建（/tr 有 261 字上限）。
設定只用基本的：建好起與登入後每 N 分鐘、只在使用者登入時跑（InteractiveToken）、同一個工作還在跑不開新的；不補跑、不重試；
筆電用電池也照跑（排程器預設用電池不跑，背景比對就停了）。
ensure 回 created｜updated｜ok｜off｜unsupported｜failed，不丟例外；failed 記一件待處理（同一件不重開），之後設好了關掉。
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
PENDING_KEY = "schedule_failed"
PENDING_TEXT = "背景比對的排程設不起來（{reason}）；新放的檔要等 AI 當場比才看得到，請服務人員看一下"
SCHTASKS_TIMEOUT = 2  # 秒，一次 schtasks；每天第一句的 turn.py 上限 5 秒（review 1）
_QUOT = {"&quot;": '"', "&apos;": "'"}
_QUOT_REV = {'"': "&quot;"}

XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>wiki-with-willy 背景比對：每 {minutes} 分鐘看一次資料夾有沒有新檔或改過的檔</Description></RegistrationInfo>
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
    <ExecutionTimeLimit>PT12H</ExecutionTimeLimit>
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


def expected(vault, minutes, name):
    """這台現在該有的指令與間隔。指令帶 --task 自己的名稱：庫刪了背景照它刪掉自己（裁定 12a）；不設起始夾，
    整條路照 --vault 找庫（裁定 33）。pythonw 找不到丟 Failed。"""
    from kb._lib import paths
    pyw = _pythonw()
    if not pyw:
        raise Failed(f"{Path(sys.executable).parent} 找不到 pythonw.exe")
    kb = paths.plugin_root() / "scripts" / "kb.py"
    vault = str(Path(vault))
    # list2cmdline 照 Windows 的引號、反斜線規則（裁定 14：手寫 "D:\" 會吞掉後面的參數）
    args = subprocess.list2cmdline([str(kb), "changes", "--background", "--vault", vault, "--task", name])
    return {"command": pyw, "arguments": args,
            "interval": f"PT{minutes}M", "minutes": minutes}


def _field(xml, tag):
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S)
    return unescape(m.group(1), _QUOT).strip() if m else None


def _same(xml, exp):
    """指令、參數、間隔一樣，而且沒有起始夾（裁定 33：舊版設成庫本身，庫刪了工作排程器啟動就失敗 0x8007010B，自刪走不到）。"""
    return _field(xml, "WorkingDirectory") is None and all(_field(xml, tag) == _codepage(exp[key]) for tag, key in
               (("Command", "command"), ("Arguments", "arguments"), ("Interval", "interval")))


def _xml(exp):
    user = (os.environ.get("USERDOMAIN", "") + "\\" if os.environ.get("USERDOMAIN") else "") + getpass.getuser()
    return XML.format(minutes=exp["minutes"], start=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), interval=exp["interval"],
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
    """背景比對找不到庫（裁定 12、12a）：照排程帶來的 --task 刪掉自己。不是本 plugin 的名稱不刪；回 True 是刪了。"""
    if not WINDOWS or not name or not name.startswith(TASK_PREFIX):
        return False
    try:
        _delete(name)
        return True
    except (Failed, Skipped, OSError, subprocess.SubprocessError):
        return False


def _turn_id(vault):
    from kb._lib import turn
    return turn.current(vault)["turn"]


def _record_failed(vault, reason):
    from kb._lib import ledger, pending
    try:
        pending.add_lint(vault, PENDING_KEY, PENDING_TEXT.format(reason=reason), {"reason": reason}, _turn_id(vault),
                         check="schedule")
    except Exception as e:  # noqa: BLE001  記不了待處理也不擋開場
        ledger.log_error(vault, f"排程失敗沒記成待處理：{type(e).__name__}: {e}", source="schedule")


def _resolve(vault):
    """設好了：之前記的「排程設不起來」關掉。"""
    from kb._lib import pending
    ids = [i["id"] for i in pending.load(vault)["items"] if i.get("key") == PENDING_KEY and i.get("status") == "open"]
    if ids:
        pending.close_ids(vault, ids, pending.LINT_GONE_HOW, _turn_id(vault))


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
