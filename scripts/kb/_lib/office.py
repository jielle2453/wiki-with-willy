"""做文件的工具與副本（produce 第 1 點，D-162、D-170）：kb office 的本體。不寫知識庫，只寫工作區 .system/temp/office/<回合>/。

工具夾在 %LOCALAPPDATA%\\wiki-with-willy\\tools\\（node\\、pylib\\），按機器、不進知識庫，路徑不寫進任何狀態檔（D-019 同理，每次從環境變數算）。
偵測：桌面版 Office 查登錄檔 App Paths（WINWORD、EXCEL、POWERPNT），LibreOffice 找 PATH 與常見位置，Node.js 與 npm 找 PATH，
套件看工具夾裡的套件夾。轉 PDF、重算：有 Office 用 Office（PowerShell COM），否則 LibreOffice（--headless --norestore、
固定 profile 在 .system/temp/office/_lo_profile、輸出一律用 UTF-8 解，B-081 spike 結論 4）；都沒有回 Unavailable 帶固定句。
呼叫外部程式一律經 _call（測試換掉它當替身，這台沒有 Office）；setup 裝完驗 import，失敗等 5 秒重試三次（spike 結論 2）。
核心只用標準函式庫；轉圖用的 pypdfium2 在子行程裡用（PYTHONPATH 帶工具夾），本行程不動模組搜尋路徑。
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from kb._lib import config
from kb._lib.paths import plugin_root

TEMP_POSIX = ".system/temp"
OFFICE_DIR = "office"
LO_PROFILE = "_lo_profile"
NODE_PACKAGES = ("docx", "pptxgenjs")
PY_PACKAGES = ("defusedxml", "lxml", "openpyxl", "pypdfium2")
PY_IMPORTS = "import defusedxml, lxml.etree, openpyxl, pypdfium2"
NODE_REQUIRE = "require('docx'); require('pptxgenjs')"
VERIFY_TRIES = 4  # 第一次加重試三次
VERIFY_WAIT = 5
RUN_TAIL = 40
OFFICE_APPS = {"word": "WINWORD.EXE", "excel": "EXCEL.EXE", "powerpoint": "POWERPNT.EXE"}
APP_NAMES = {"word": "Word", "excel": "Excel", "powerpoint": "PowerPoint"}
KIND_APP = {".docx": "word", ".doc": "word", ".docm": "word", ".rtf": "word", ".odt": "word",
            ".xlsx": "excel", ".xls": "excel", ".xlsm": "excel", ".ods": "excel", ".csv": "excel",
            ".pptx": "powerpoint", ".ppt": "powerpoint", ".pptm": "powerpoint", ".odp": "powerpoint"}
SOFFICE_PLACES = (r"C:\Program Files\LibreOffice\program\soffice.exe",
                  r"C:\Program Files (x86)\LibreOffice\program\soffice.exe")

# 固定句（spec 介面「降級固定句」）
NO_CONVERTER = "這台沒有能轉 PDF 或重算的程式；要的話可以裝 LibreOffice（免費），或用 Word／Excel 打開另存"
NO_RECALC_TAIL = "公式的結果要打開檔案才會更新"
NO_NODE = "新做 Word 或簡報要 Node.js；這台沒有，可以改做成線上文件，或請你裝 Node.js"
NO_PDFIUM = "看版面要 PDF 元件；跑 kb office setup 會裝"
NO_NODE_PACKAGES = "新做 Word 或簡報的工具還沒裝；跑 kb office setup 會裝"
NO_PY_PACKAGES = "這支腳本要的 python 套件還沒裝；跑 kb office setup 會裝"


class Unavailable(Exception):
    """工具不夠：args[0] 是給人看的句子（可能多行）。exit 3。"""


class Refused(Exception):
    """參數或位置不對：args[0] 是給人看的句子。exit 3。"""


# ---------- 呼叫層（測試換掉） ----------

def _call(argv, timeout, env=None, cwd=None):
    """跑外部程式，回 (結束碼, 輸出文字)；輸出合併 stdout 與 stderr，一律用 UTF-8 解（壞字元換掉，不靠系統編碼）。
    找不到程式丟 FileNotFoundError，逾時丟 subprocess.TimeoutExpired。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=cwd,
                         creationflags=flags, stdin=subprocess.DEVNULL)
    try:
        out, _err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)  # soffice.exe 會再開 soffice.bin：只殺外層會留一個佔著 profile 的
        p.communicate()
        raise
    return p.returncode, out.decode("utf-8", errors="replace")


def _kill_tree(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        p.kill()


_sleep = time.sleep
_clock = time.monotonic


def _which(name):
    return shutil.which(name)


def _registry_app(exe):
    """App Paths 登錄檔有沒有這個 Office 程式（HKLM 與 HKCU）；不是 Windows 回 None。"""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                value, _t = winreg.QueryValueEx(k, "")
                if value and os.path.isfile(str(value).strip('"')):
                    return str(value).strip('"')
        except OSError:
            continue
    return None


# ---------- 偵測 ----------

def tools_dir():
    """%LOCALAPPDATA%\\wiki-with-willy\\tools；沒有這個環境變數（不是 Windows）用 ~/.local/share。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "wiki-with-willy" / "tools"


def node_modules():
    return tools_dir() / "node" / "node_modules"


def pylib():
    return tools_dir() / "pylib"


def find_office():
    return {app: _registry_app(exe) for app, exe in OFFICE_APPS.items()}


def find_soffice():
    found = _which("soffice") or _which("soffice.exe")
    if found:
        return found
    return next((p for p in SOFFICE_PLACES if os.path.isfile(p)), None)


def find_node():
    """回 (node 路徑, 版本) 或 (None, None)。"""
    node = _which("node")
    if not node:
        return None, None
    try:
        code, out = _call([node, "--version"], 10)
    except (OSError, subprocess.TimeoutExpired):
        return node, None
    return node, (out.strip().splitlines() or [None])[0] if code == 0 else None


def find_npm():
    return _which("npm") or _which("npm.cmd")


def node_packages_ok():
    return all((node_modules() / name).is_dir() for name in NODE_PACKAGES)


def py_packages_missing():
    return [name for name in PY_PACKAGES if not (pylib() / name).is_dir()]


def pdfium_ok():
    return (pylib() / "pypdfium2").is_dir() or importlib.util.find_spec("pypdfium2") is not None


def detect():
    """這台有什麼，回 dict（check 與其他子指令共用）。只讀。"""
    node, version = find_node()
    return {"office": find_office(), "soffice": find_soffice(), "node": node, "node_version": version,
            "npm": find_npm(), "node_packages": node_packages_ok(), "py_missing": py_packages_missing(),
            "pdfium": pdfium_ok(), "tools": str(tools_dir())}


def capabilities(env):
    """回 {"can", "after_setup", "cannot": [(事, 要裝什麼)], "setup"}。
    改 Word／Excel／簡報只要 python（office unpack／pack）；新做 Word、簡報要 Node.js 與 node 套件；新做 Excel 要 openpyxl；
    轉 PDF、看版面要 Office 或 LibreOffice（看版面另要 pypdfium2）；重算要 Excel 或 LibreOffice。"""
    can, later, cannot = ["改 Word", "改 Excel", "改簡報"], [], []
    py_ready = not env["py_missing"]
    if env["node"] and env["node_packages"]:
        can += ["新做 Word", "新做簡報"]
    elif env["node"] and env["npm"]:
        later += ["新做 Word", "新做簡報"]
    else:
        cannot.append(("新做 Word、新做簡報", "Node.js"))
    (can if py_ready else later).append("新做 Excel")
    office = env["office"]
    converter = bool(env["soffice"]) or any(office.values())
    if converter:
        can.append("轉 PDF")
        (can if env["pdfium"] else later).append("看版面")
    else:
        cannot.append(("轉 PDF、看版面", "LibreOffice"))
    if env["soffice"] or office.get("excel"):
        can.append("重算 Excel")
    else:
        cannot.append(("重算 Excel", "LibreOffice"))
    setup = bool(env["py_missing"]) or bool(env["node"] and env["npm"] and not env["node_packages"])
    return {"can": can, "after_setup": later, "cannot": cannot, "setup": setup}


def check_lines(env):
    office = env["office"]
    apps = [APP_NAMES[a] for a, p in office.items() if p]
    lines = [f"Office：{'有（' + '、'.join(apps) + '）' if apps else '沒有'}",
             f"LibreOffice：{'有（' + env['soffice'] + '）' if env['soffice'] else '沒有'}"]
    if env["node"]:
        npm = "" if env["npm"] else "；沒有 npm"
        version = f"（{env['node_version']}）" if env["node_version"] else ""
        lines.append(f"Node.js：有{version}{npm}；工具夾 node 套件：{'裝好了' if env['node_packages'] else '沒裝'}")
    else:
        lines.append("Node.js：沒有")
    missing = env["py_missing"]
    if not missing:
        lines.append("python 套件：裝好了")
    elif len(missing) == len(PY_PACKAGES):
        lines.append("python 套件：沒裝")
    else:
        lines.append(f"python 套件：缺 {'、'.join(missing)}")
    cap = capabilities(env)
    last = "可以：" + "、".join(cap["can"])
    if cap["after_setup"]:
        last += "；裝好工具後也可以" + "、".join(cap["after_setup"])
    if cap["cannot"]:
        last += "；不行：" + "、".join(f"{what}（要裝 {need}）" for what, need in cap["cannot"])
    lines.append(last)
    if cap["setup"]:
        lines.append("要先做：kb office setup（大約 1 分鐘）")
    return lines


# ---------- 安裝 ----------

def _py_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(pylib())
    return env


def _node_env():
    env = dict(os.environ)
    env["NODE_PATH"] = str(node_modules())
    return env


def _verify(argv, env):
    """裝完驗載得起來：剛寫進去的 .pyd 可能被系統掃描擋一下（spike 結論 2），失敗等 VERIFY_WAIT 秒再試，最多 VERIFY_TRIES 次。
    回 (成功嗎, 試了幾次, 最後的輸出)。"""
    out = ""
    for i in range(1, VERIFY_TRIES + 1):
        try:
            code, out = _call(argv, 60, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            code, out = 1, str(e)
        if code == 0:
            return True, i, out
        if i < VERIFY_TRIES:
            _sleep(VERIFY_WAIT)
    return False, VERIFY_TRIES, out


def setup(timeout=600):
    """裝到工具夾。回 {"already", "python", "node", "skipped_node", "seconds", "tries"}；裝不起來丟 Unavailable。"""
    env = detect()
    need_py = bool(env["py_missing"])
    need_node = bool(env["node"] and env["npm"] and not env["node_packages"])
    out = {"already": False, "python": [], "node": [], "skipped_node": not (env["node"] and env["npm"]),
           "seconds": 0, "tries": {}}
    if not need_py and not need_node:
        out["already"] = True
        return out
    started = _clock()
    if need_py:
        pylib().mkdir(parents=True, exist_ok=True)
        try:
            code, text = _call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                                "--target", str(pylib()), "--upgrade", *PY_PACKAGES], timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Unavailable(f"python 套件裝不起來：{_short(e)}") from None
        if code != 0:
            raise Unavailable(f"python 套件裝不起來：{_last_line(text)}")
        ok, tries, text = _verify([sys.executable, "-c", PY_IMPORTS], _py_env())
        out["tries"]["python"] = tries
        if not ok:
            raise Unavailable(f"python 套件裝好了但載不起來：{_last_line(text)}；過一兩分鐘再跑一次 kb office setup")
        out["python"] = list(PY_PACKAGES)
    if need_node:
        (tools_dir() / "node").mkdir(parents=True, exist_ok=True)
        try:
            code, text = _call([env["npm"], "install", "--prefix", str(tools_dir() / "node"), "--no-audit", "--no-fund",
                                *NODE_PACKAGES], timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Unavailable(f"node 套件裝不起來：{_short(e)}") from None
        if code != 0:
            raise Unavailable(f"node 套件裝不起來：{_last_line(text)}")
        ok, tries, text = _verify([env["node"], "-e", NODE_REQUIRE], _node_env())
        out["tries"]["node"] = tries
        if not ok:
            raise Unavailable(f"node 套件裝好了但載不起來：{_last_line(text)}")
        out["node"] = list(NODE_PACKAGES)
    out["seconds"] = round(_clock() - started, 1)
    return out


def _short(e):
    return type(e).__name__ if not str(e) else str(e).splitlines()[0][:200]


def _last_line(text):
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    return lines[-1][:200] if lines else "沒有訊息"


# ---------- 工作區 ----------

def _is_reparse(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def office_root(vault):
    return Path(vault) / TEMP_POSIX / OFFICE_DIR


def workspace(vault, turn_id, make=True):
    """這一回合的工作區 .system/temp/office/<回合代號>/。temp、office 或工作區是連結就拒絕（不寫到別處去）。"""
    if not re.fullmatch(r"[0-9A-Za-z_\-]+", str(turn_id)):
        raise Refused("回合代號不對，工作區建不起來")
    root = office_root(vault)
    ws = root / str(turn_id)
    for p in (Path(vault) / TEMP_POSIX, root, ws):
        if _is_reparse(p):
            raise Refused("暫存位置不對")
    if make:
        clean(vault, keep=str(turn_id))
        ws.mkdir(parents=True, exist_ok=True)
    return ws


def clean(vault, keep=None):
    """office/ 底下修改時間超過 temp_keep_hours 的夾整個刪掉（不跟連結、刪不掉留著）；keep 是這一回合的夾，不刪。"""
    root = office_root(vault)
    if not root.is_dir() or _is_reparse(root) or _is_reparse(root.parent):
        return
    limit = config.get(vault, "temp_keep_hours") * 3600
    now = time.time()
    for p in root.iterdir():
        try:
            if p.name == keep or _is_reparse(p) or not p.is_dir():
                continue
            if now - os.stat(p).st_mtime >= limit:
                shutil.rmtree(p)
        except OSError:
            pass


def _real(path):
    return os.path.normcase(os.path.realpath(str(path)))


def inside(path, root):
    """path（realpath）在 root（realpath）底下（不含 root 本身）。"""
    p, r = _real(path), _real(root)
    return p != r and p.startswith(r.rstrip("\\/") + os.sep)


def in_temp(vault, path):
    return inside(path, Path(vault) / TEMP_POSIX)


def resolve_arg(path, cwd=None):
    p = Path(path)
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    return Path(os.path.abspath(p))


def need_in_temp(vault, path, what="檔"):
    if not in_temp(vault, path):
        raise Refused(f"只收工作區裡的{what}：{path}")
    if not os.path.exists(path):
        raise Refused(f"找不到這個{what}：{path}")


# ---------- run ----------

def run_script(vault, script, args, turn_id):
    """跑一支做檔腳本：要在 .system/temp/ 底下；cwd 與 OUT_DIR 是這一回合的工作區，NODE_PATH、PYTHONPATH 指工具夾。
    回 (腳本結束碼, 末 RUN_TAIL 行, 工作區)。"""
    script = resolve_arg(script)
    if not in_temp(vault, script):
        raise Refused(f"腳本要放在工作區裡（{workspace(vault, turn_id, make=False)}），這支不在：{script}")
    if not script.is_file():
        raise Refused(f"找不到這支腳本：{script}")
    ext = script.suffix.lower()
    if ext in (".js", ".mjs", ".cjs"):
        node, _v = find_node()
        if not node:
            raise Unavailable(NO_NODE)
        argv = [node, str(script)]
    elif ext == ".py":
        argv = [sys.executable, str(script)]
    else:
        raise Refused("只跑 .js 或 .py 的腳本")
    ws = workspace(vault, turn_id)
    env = dict(os.environ)
    env.update(NODE_PATH=str(node_modules()), PYTHONPATH=str(pylib()), OUT_DIR=str(ws), PYTHONIOENCODING="utf-8")
    seconds = config.get(vault, "office_run_seconds")
    try:
        code, out = _call(argv + [str(a) for a in args], seconds, env=env, cwd=str(ws))
    except subprocess.TimeoutExpired:
        raise Unavailable(f"跑超過 {seconds} 秒，停掉了") from None
    return code, out.splitlines()[-RUN_TAIL:], ws


# ---------- 解開、打包 ----------

def unpack(vault, src, dest=None):
    """工作區的 Office 檔解開到工作區的夾（預設 <主檔名>_unpacked）；擋絕對路徑與 .. 的成員。回夾的路徑。"""
    src = resolve_arg(src)
    need_in_temp(vault, src)
    dest = resolve_arg(dest) if dest else src.with_name(src.stem + "_unpacked")
    if not in_temp(vault, dest):
        raise Refused(f"只能解開到工作區裡：{dest}")
    try:
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                name = info.filename
                if name.startswith(("/", "\\")) or ".." in re.split(r"[\\/]", name) or re.match(r"^[A-Za-z]:", name):
                    raise Refused(f"檔裡有不安全的路徑，不解開：{name}")
            dest.mkdir(parents=True, exist_ok=True)
            z.extractall(dest)
    except zipfile.BadZipFile:
        raise Refused("這不是 Office 檔（解不開）") from None
    return dest


def pack(vault, folder, to, like=None):
    """夾打包成 Office 檔（工作區裡）。like：照原檔的檔內順序與每個部件的壓縮方式（保住沒改的部件逐位元相同，spike），
    原檔沒有的新部件接在後面；沒給 like 時 [Content_Types].xml 放第一個。回輸出路徑。"""
    folder = resolve_arg(folder)
    need_in_temp(vault, folder, "夾")
    if not folder.is_dir():
        raise Refused(f"這不是資料夾：{folder}")
    out = resolve_arg(to) if os.path.isabs(str(to)) or os.sep in str(to) or "/" in str(to) else folder.parent / str(to)
    if not in_temp(vault, out):
        raise Refused(f"只能打包到工作區裡：{out}")
    names = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not _is_reparse(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if not _is_reparse(full):
                names.append(Path(full).relative_to(folder).as_posix())
    order, methods = [], {}
    if like:
        like = resolve_arg(like)
        need_in_temp(vault, like)
        try:
            with zipfile.ZipFile(like) as z:
                for info in z.infolist():
                    order.append(info.filename)
                    methods[info.filename] = info.compress_type
        except zipfile.BadZipFile:
            raise Refused("--like 的檔不是 Office 檔") from None
    have = set(names)
    first = [n for n in order if n in have] if like else [n for n in ("[Content_Types].xml",) if n in have]
    rest = sorted(n for n in names if n not in set(first))
    tmp = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        for name in first + rest:
            z.write(folder / name, name, compress_type=methods.get(name, zipfile.ZIP_DEFLATED))
    os.replace(tmp, out)
    return out


# ---------- 轉 PDF、重算、轉圖 ----------

_PS_PDF = {
    "word": "$a=New-Object -ComObject Word.Application;$a.Visible=$false;$d=$a.Documents.Open($env:KB_IN,$false,$true);"
            "$d.ExportAsFixedFormat($env:KB_OUT,17);$d.Close($false);$a.Quit()",
    "excel": "$a=New-Object -ComObject Excel.Application;$a.DisplayAlerts=$false;$b=$a.Workbooks.Open($env:KB_IN,0,$true);"
             "$b.ExportAsFixedFormat(0,$env:KB_OUT);$b.Close($false);$a.Quit()",
    "powerpoint": "$a=New-Object -ComObject PowerPoint.Application;$p=$a.Presentations.Open($env:KB_IN,$true,$false,$false);"
                  "$p.SaveAs($env:KB_OUT,32);$p.Close();$a.Quit()",
}
_PS_RECALC = ("$a=New-Object -ComObject Excel.Application;$a.DisplayAlerts=$false;$b=$a.Workbooks.Open($env:KB_IN);"
              "$a.CalculateFull();$b.Save();$b.Close($true);$a.Quit()")
RECALC_MACRO = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1" script:language="StarBasic">
    Sub RecalculateAndSave()
      ThisComponent.calculateAll()
      ThisComponent.store()
      ThisComponent.close(True)
    End Sub
</script:module>
"""


def _powershell(script, inp, out, seconds):
    env = dict(os.environ)
    env.update(KB_IN=str(inp), KB_OUT=str(out or ""))
    return _call(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], seconds, env=env)


def _lo_profile(vault):
    p = office_root(vault) / LO_PROFILE
    p.mkdir(parents=True, exist_ok=True)
    return p


def _soffice(vault, soffice, args, seconds):
    profile = "-env:UserInstallation=" + _lo_profile(vault).resolve().as_uri()
    return _call([soffice, "--headless", "--norestore", profile, *args], seconds)


def _app_for(path):
    return KIND_APP.get(Path(str(path)).suffix.lower())


def to_pdf(vault, src, ws, env=None):
    """轉 PDF 到工作區：Office 有那個程式用它，否則 LibreOffice；都沒有丟 Unavailable(NO_CONVERTER)。回 (PDF 路徑, 用了誰)。"""
    env = env or {"office": find_office(), "soffice": find_soffice()}
    src = Path(src)
    out = Path(ws) / (src.stem + ".pdf")
    seconds = config.get(vault, "office_convert_seconds")
    app = _app_for(src)
    if src.suffix.lower() == ".pdf":
        if _real(src) != _real(out):
            shutil.copyfile(src, out)
        return out, "pdf"
    try:
        if app and env["office"].get(app):
            code, text = _powershell(_PS_PDF[app], src, out, seconds)
            used = APP_NAMES[app]
        elif env["soffice"]:
            code, text = _soffice(vault, env["soffice"], ["--convert-to", "pdf", "--outdir", str(ws), str(src)], seconds)
            used = "LibreOffice"
        else:
            raise Unavailable(NO_CONVERTER)
    except subprocess.TimeoutExpired:
        raise Unavailable(f"轉 PDF 超過 {seconds} 秒，停掉了") from None
    except FileNotFoundError:
        raise Unavailable(NO_CONVERTER) from None
    if code != 0 or not out.is_file():
        raise Unavailable(f"轉不出 PDF（{used}）：{_last_line(text)}")
    return out, used


def recalc(vault, src, env=None):
    """工作區的 xlsx 原地重算：Excel 有就用它，否則 LibreOffice（巨集 calculateAll 後存檔）；都沒有丟 Unavailable。
    回 {"used", "formulas", "errors": [(工作表內部檔名, 儲存格, 錯誤)]}。"""
    env = env or {"office": find_office(), "soffice": find_soffice()}
    seconds = config.get(vault, "office_convert_seconds")
    before = os.stat(src).st_mtime_ns, os.stat(src).st_size
    try:
        if env["office"].get("excel"):
            code, text = _powershell(_PS_RECALC, src, None, seconds)
            used = "Excel"
        elif env["soffice"]:
            profile = _lo_profile(vault)
            macro_dir = profile / "user" / "basic" / "Standard"
            if not macro_dir.is_dir():
                _soffice(vault, env["soffice"], ["--terminate_after_init"], seconds)
            if not macro_dir.is_dir():
                raise Unavailable("LibreOffice 的設定建不起來，沒有重算\n" + NO_RECALC_TAIL)
            (macro_dir / "Module1.xba").write_text(RECALC_MACRO, encoding="utf-8")
            code, text = _soffice(vault, env["soffice"], [
                "vnd.sun.star.script:Standard.Module1.RecalculateAndSave?language=Basic&location=application",
                str(Path(src).resolve())], seconds)
            used = "LibreOffice"
        else:
            raise Unavailable(NO_CONVERTER + "\n" + NO_RECALC_TAIL)
    except subprocess.TimeoutExpired:
        raise Unavailable(f"重算超過 {seconds} 秒，停掉了\n{NO_RECALC_TAIL}") from None
    except FileNotFoundError:
        raise Unavailable(NO_CONVERTER + "\n" + NO_RECALC_TAIL) from None
    if code != 0 or (os.stat(src).st_mtime_ns, os.stat(src).st_size) == before:
        raise Unavailable(f"沒有重算成（{used}）：{_last_line(text)}\n{NO_RECALC_TAIL}")
    formulas, errors = formula_report(src)
    return {"used": used, "formulas": formulas, "errors": errors}


_CELL = re.compile(r'<c\b([^>]*)>(.*?)</c>', re.S)
_REF = re.compile(r'\br="([A-Z]+[0-9]+)"')


def formula_report(path):
    """數 xlsx 的公式格與錯誤值格（t="e"）：回 (公式數, [(工作表檔名, 儲存格, 錯誤)])。只用標準函式庫讀 XML 字串。"""
    formulas, errors = 0, []
    with zipfile.ZipFile(path) as z:
        for name in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            text = z.read(name).decode("utf-8", errors="replace")
            for attrs, body in _CELL.findall(text):
                if "<f" in body:
                    formulas += 1
                if 't="e"' in attrs:
                    ref = _REF.search(attrs)
                    value = re.search(r"<v>(.*?)</v>", body, re.S)
                    errors.append((name.rsplit("/", 1)[-1][:-4], ref.group(1) if ref else "?",
                                   value.group(1) if value else "#?"))
    return formulas, errors


def parse_pages(spec, limit):
    """「1-5」「2」「1,3-4」→ 頁碼清單；沒給回 None（全部，最多 limit 頁）。寫錯丟 Refused。"""
    if not spec:
        return None
    pages = []
    for part in str(spec).split(","):
        m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", part)
        if not m:
            raise Refused(f"頁碼要寫成 1-5 或 2：{spec}")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b < a:
            raise Refused(f"頁碼要寫成 1-5 或 2：{spec}")
        pages.extend(range(a, b + 1))
    return sorted(set(pages))[:limit]


_RENDER = ("import json,sys\nfrom kb._lib import extract\n"
           "a=json.loads(sys.argv[1])\n"
           "n=extract.pdf_pages_text(a['pdf'],0)[1]\n"
           "pages=[p for p in (a['pages'] or range(1,n+1)) if p<=n][:a['limit']]\n"
           "print(json.dumps(extract.render_pages(a['pdf'],pages,a['out'],a['stem'],a['scale'],a['limit'])))\n")


def render(vault, pdf, ws, pages=None):
    """PDF 每頁轉 PNG 到工作區（pypdfium2，在子行程裡用，PYTHONPATH 帶 scripts 與工具夾）。回 [(頁碼, 路徑)]。"""
    import json
    if not pdfium_ok():
        raise Unavailable(NO_PDFIUM)
    limit = config.get(vault, "pdf_read_pages_max")
    arg = json.dumps({"pdf": str(pdf), "pages": pages, "out": str(ws), "stem": Path(pdf).stem,
                      "scale": config.get(vault, "pdf_render_scale"), "limit": limit}, ensure_ascii=False)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(plugin_root() / "scripts"), str(pylib())])
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        code, text = _call([sys.executable, "-c", _RENDER, arg], config.get(vault, "office_convert_seconds"), env=env)
    except subprocess.TimeoutExpired:
        raise Unavailable("轉圖太久，停掉了") from None
    if code != 0:
        raise Unavailable(f"轉不出圖：{_last_line(text)}")
    try:
        return [tuple(x) for x in json.loads(text.strip().splitlines()[-1])]
    except (ValueError, IndexError):
        raise Unavailable(f"轉不出圖：{_last_line(text)}") from None
