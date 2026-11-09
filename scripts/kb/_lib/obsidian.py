"""Obsidian 外掛放進知識庫、Obsidian 裝了沒、知識庫登記了沒（Obsidian 首頁，#22，D-258～D-260）。

外掛三個檔（manifest.json、main.js、styles.css）住在 plugin 的 obsidian/；kb init 每次都把它們換成目前版本放進
<知識庫>/.obsidian/plugins/wiki-with-willy/（內容相同不寫），community-plugins.json 沒有這個 id 就補（檔不在就建，壞檔不動並記 log）；
.obsidian/ 底下其他檔一律不碰。外部（Windows 登錄、%LOCALAPPDATA%、%APPDATA%、os.startfile、行程）都經 _ 開頭的函式，測試換替身。
"""
import json
import os
import random
import time
import urllib.parse
from pathlib import Path

from kb._lib import ledger
from kb._lib.io import read_text, write_text_atomic
from kb._lib.paths import plugin_root

PLUGIN_ID = "wiki-with-willy"
SOURCE_REL = "obsidian"
FILES = ("manifest.json", "main.js", "styles.css")
PLUGIN_REL = f".obsidian/plugins/{PLUGIN_ID}"
COMMUNITY_REL = ".obsidian/community-plugins.json"
CONFIG_ENV = "OBSIDIAN_CONFIG"  # 測試與少見的裝法可以指到別的 obsidian.json


def source_dir():
    return plugin_root() / SOURCE_REL


def source_files():
    """plugin 的 obsidian/ 裡三個檔都在才回 {檔名: bytes}，缺任何一個回 None（舊版 plugin 沒有外掛，放的步驟略過）。"""
    out = {}
    for name in FILES:
        p = source_dir() / name
        if not p.is_file():
            return None
        out[name] = p.read_bytes()
    return out


def _community(vault):
    """(狀態, 內容)：missing 不在、ok 是清單、bad 讀不懂。"""
    p = Path(vault) / COMMUNITY_REL
    if not p.is_file():
        return "missing", None
    try:
        data = json.loads(read_text(p))
    except (ValueError, OSError):
        return "bad", None
    return ("ok", data) if isinstance(data, list) and all(isinstance(x, str) for x in data) else ("bad", None)


def plan(vault):
    """會寫的項目 [相對路徑]（外掛三個檔內容不同或不在、community-plugins.json 沒有這個 id）；plugin 沒帶外掛回 []。"""
    src = source_files()
    if src is None:
        return []
    todo = []
    for name, data in src.items():
        target = Path(vault) / PLUGIN_REL / name
        if not target.is_file() or target.read_bytes() != data:
            todo.append(f"{PLUGIN_REL}/{name}")
    state, got = _community(vault)
    if state == "missing" or (state == "ok" and PLUGIN_ID not in got):
        todo.append(COMMUNITY_REL)
    return todo


def place(vault):
    """放外掛：回 (寫了哪些相對路徑, 要印的備註)。community-plugins.json 讀不懂就不動、記 log、備註一句。"""
    src = source_files()
    if src is None:
        return [], []
    wrote, notes = [], []
    for name, data in src.items():
        target = Path(vault) / PLUGIN_REL / name
        if target.is_file() and target.read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        wrote.append(f"{PLUGIN_REL}/{name}")
    state, got = _community(vault)
    path = Path(vault) / COMMUNITY_REL
    if state == "bad":
        ledger.log_error(vault, f"{COMMUNITY_REL} 讀不懂，Obsidian 外掛那一項沒補", source="obsidian")
        notes.append(f"{COMMUNITY_REL} 讀不懂，沒動；Obsidian 的外掛要自己在設定裡打開")
    elif state == "missing" or PLUGIN_ID not in got:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps((got or []) + [PLUGIN_ID], ensure_ascii=False) + "\n")
        wrote.append(COMMUNITY_REL)
    return wrote, notes


# ---------- Obsidian 裝了沒、知識庫登記了沒 ----------

def _exe_paths():
    local = os.environ.get("LOCALAPPDATA")
    return [Path(local) / "Programs" / "Obsidian" / "Obsidian.exe"] if local else []


def _protocol_registered():
    """登錄裡有 obsidian:// 協定（Windows）。非 Windows 或讀不到回 False。"""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "obsidian"):
            return True
    except OSError:
        return False


def installed():
    return any(p.is_file() for p in _exe_paths()) or _protocol_registered()


def config_path():
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "obsidian" / "obsidian.json" if appdata else None


def _norm(path):
    return os.path.normcase(os.path.realpath(str(path))).rstrip("\\/")


def read_config():
    """obsidian.json 的內容；沒檔回 {}；讀不懂丟 ValueError。"""
    p = config_path()
    if p is None or not p.is_file():
        return {}
    data = json.loads(read_text(p))
    if not isinstance(data, dict):
        raise ValueError("obsidian.json 不是物件")
    return data


def vault_registered(vault):
    """obsidian.json 的 vaults 裡有沒有 path 等於這個知識庫（讀不懂當沒有）。"""
    try:
        vaults = read_config().get("vaults")
    except (ValueError, OSError):
        return False
    return isinstance(vaults, dict) and any(isinstance(v, dict) and v.get("path") and _norm(v["path"]) == _norm(vault)
                                            for v in vaults.values())


def plugin_placed(vault):
    return source_files() is not None and not plan(vault)


def status(vault):
    return {"installed": installed(), "registered": vault_registered(vault), "plugin": plugin_placed(vault)}


def _running():
    """Obsidian 有沒有在跑（Windows 用 tasklist；查不到當沒在跑）。"""
    if os.name != "nt":
        return False
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Obsidian.exe", "/NH"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "obsidian.exe" in out.lower()


def _startfile(url):
    os.startfile(url)  # noqa: S606  obsidian:// 由 Windows 交給 Obsidian


def _vault_id():
    return "".join(random.choice("0123456789abcdef") for _ in range(16))  # Obsidian 自己的編號是 16 碼 hex


def register(vault):
    """把知識庫寫進 obsidian.json 的 vaults（id 16 碼 hex、ts 現在）；已經在就不動。讀不懂丟 ValueError。回 id。"""
    p = config_path()
    if p is None:
        raise ValueError("找不到 Obsidian 的設定位置（%APPDATA%）")
    data = read_config()
    vaults = data.setdefault("vaults", {})
    if not isinstance(vaults, dict):
        raise ValueError("obsidian.json 的 vaults 不是物件")
    for key, v in vaults.items():
        if isinstance(v, dict) and v.get("path") and _norm(v["path"]) == _norm(vault):
            return key
    key = _vault_id()
    while key in vaults:
        key = _vault_id()
    vaults[key] = {"path": str(Path(vault)), "ts": int(time.time() * 1000)}
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p, json.dumps(data, ensure_ascii=False) + "\n")
    return key


def open_url(vault):
    """obsidian://open?path=<知識庫絕對路徑，整串百分比編碼>。"""
    return "obsidian://open?path=" + urllib.parse.quote(str(Path(vault)).replace("\\", "/"), safe="")
