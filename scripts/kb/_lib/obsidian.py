"""Obsidian 外掛放進知識庫、Obsidian 裝了沒、知識庫登記了沒（Obsidian 首頁，#22，D-258～D-260）。

外掛三個檔（manifest.json、main.js、styles.css）住在 plugin 的 obsidian/；kb init 每次都把它們換成目前版本放進
<知識庫>/.obsidian/plugins/wiki-with-willy/（內容相同不寫），community-plugins.json 沒有這個 id 就補（檔不在就建，壞檔不動並記 log）；
.obsidian/ 底下其他檔一律不碰。外部（Windows 登錄、%LOCALAPPDATA%、%APPDATA%、os.startfile、行程）都經 _ 開頭的函式，測試換替身。

上層 vault（#66 丙，D-542）：知識庫是她原本那個 Obsidian 資料夾裡的子夾時，上層的 .obsidian/ 也放一份同版的外掛（plan_outer／place_outer），
Obsidian 已登記上層就從上層打開。上層＝從知識庫根往上最多兩層、最近一個有 .obsidian/ 資料夾的夾；每次現找，不記在任何狀態檔（D-019）。
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
OUTER_DEPTH = 2  # 上層 vault 往上找幾層（配合外掛只往下找兩層，#66 乙）


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


def _plan(base):
    """base（知識庫或上層 vault）底下會寫的項目 [相對路徑]；plugin 沒帶外掛回 []。"""
    src = source_files()
    if src is None:
        return []
    todo = []
    for name, data in src.items():
        target = Path(base) / PLUGIN_REL / name
        if not target.is_file() or target.read_bytes() != data:
            todo.append(f"{PLUGIN_REL}/{name}")
    state, got = _community(base)
    if state == "missing" or (state == "ok" and PLUGIN_ID not in got):
        todo.append(COMMUNITY_REL)
    return todo


def _put(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def _place(base):
    """放進 base 的 .obsidian/：回 (寫了哪些相對路徑, community-plugins.json 是不是讀不懂)。讀不懂的不動。"""
    src = source_files()
    if src is None:
        return [], False
    wrote = []
    for name, data in src.items():
        target = Path(base) / PLUGIN_REL / name
        if target.is_file() and target.read_bytes() == data:
            continue
        _put(target, data)
        wrote.append(f"{PLUGIN_REL}/{name}")
    state, got = _community(base)
    if state == "bad":
        return wrote, True
    if state == "missing" or PLUGIN_ID not in got:
        path = Path(base) / COMMUNITY_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(path, json.dumps((got or []) + [PLUGIN_ID], ensure_ascii=False) + "\n")
        wrote.append(COMMUNITY_REL)
    return wrote, False


def plan(vault):
    """會寫的項目 [相對路徑]（外掛三個檔內容不同或不在、community-plugins.json 沒有這個 id）；plugin 沒帶外掛回 []。"""
    return _plan(vault)


def place(vault):
    """放外掛：回 (寫了哪些相對路徑, 要印的備註)。community-plugins.json 讀不懂就不動、記 log、備註一句。"""
    wrote, bad = _place(vault)
    notes = []
    if bad:
        ledger.log_error(vault, f"{COMMUNITY_REL} 讀不懂，Obsidian 外掛那一項沒補", source="obsidian")
        notes.append(f"{COMMUNITY_REL} 讀不懂，沒動；Obsidian 的外掛要自己在設定裡打開")
    return wrote, notes


# ---------- 上層 vault（#66 丙，D-542）----------

def outer_vault(vault):
    """知識庫在她原本的 Obsidian 資料夾裡：回 (上層夾的 Path, 前綴)，沒有回 None。只看往上一、二層，先近後遠，
    第一個 .obsidian 是資料夾的就是（知識庫自己不算）；路徑先 realpath。前綴＝知識庫根相對上層的路徑，/ 分隔。"""
    cur = Path(os.path.realpath(str(vault)))
    parts = []
    for _ in range(OUTER_DEPTH):
        if cur.parent == cur:
            return None
        parts.insert(0, cur.name)
        cur = cur.parent
        if (cur / ".obsidian").is_dir():
            return cur, "/".join(parts)
    return None


def _names(rels):
    return [rel.rsplit("/", 1)[-1] for rel in rels]


def plan_outer(vault):
    """上層 vault 那份會寫哪幾項 [檔名]（三個外掛檔、community-plugins.json）；沒有上層、都一樣回 []。"""
    outer = outer_vault(vault)
    return _names(_plan(outer[0])) if outer else []


def place_outer(vault):
    """上層 vault 也放一份：回 (寫了哪些檔名, 備註)。沒有上層回 ([], [])。上層寫不進去不丟出來，回一句備註；
    上層的 community-plugins.json 讀不懂就不動、記進知識庫的 log、備註一句。"""
    outer = outer_vault(vault)
    if outer is None:
        return [], []
    name = outer[0].name
    try:
        wrote, bad = _place(outer[0])
    except OSError as e:
        return [], [f"上層的 Obsidian 資料夾（{name}）外掛沒放成：{e}"]
    notes = []
    if bad:
        ledger.log_error(vault, f"上層（{name}）的 community-plugins.json 讀不懂，Obsidian 外掛那一項沒補", source="obsidian")
        notes.append(f"上層的 Obsidian 資料夾（{name}）的 community-plugins.json 讀不懂，沒動；Obsidian 的外掛要自己在設定裡打開")
    return _names(wrote), notes


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


def own_registered(vault):
    """obsidian.json 的 vaults 裡有沒有 path 等於這個夾（讀不懂當沒有）。"""
    try:
        vaults = read_config().get("vaults")
    except (ValueError, OSError):
        return False
    return isinstance(vaults, dict) and any(isinstance(v, dict) and v.get("path") and _norm(v["path"]) == _norm(vault)
                                            for v in vaults.values())


def outer_registered(vault):
    """要從上層 vault 打開：知識庫自己沒登記、有上層 vault 而且 Obsidian 已登記它 → (上層夾的 Path, 前綴)；其他回 None。"""
    if own_registered(vault):
        return None
    outer = outer_vault(vault)
    return outer if outer and own_registered(outer[0]) else None


def vault_registered(vault):
    """Obsidian 打得開這個知識庫：自己已登記，或它所在的上層 vault 已登記（#66 丙）。"""
    return own_registered(vault) or outer_registered(vault) is not None


def plugin_placed(vault):
    return source_files() is not None and not plan(vault)


def status(vault):
    outer = outer_registered(vault)
    return {"installed": installed(), "registered": own_registered(vault) or outer is not None, "plugin": plugin_placed(vault),
            "outer": None if outer is None else {"name": outer[0].name, "prefix": outer[1],
                                                 "plugin": source_files() is not None and not _plan(outer[0])}}


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
