"""路徑：plugin 自身的位置（從 __file__ 往上找，不寫死、不讀狀態檔，D-019）；
守門用的正規化、包含判斷、可攜 spec（D-021）展開。

normalize 把同一個檔的六種寫法（C:/、c:/、C:\\、/c/、相對 cwd、\\\\?\\ 前綴）壓成一個字串，
守門比對一律用它的結果（I-101：payload 原樣不正規化）。不做 Unicode NFC。
"""
import os
import re
from pathlib import Path

from kb._lib.io import read_json

MANIFEST_REL = Path(".claude-plugin") / "plugin.json"

_GIT_BASH = re.compile(r"^/([a-zA-Z])(?:/(.*))?$")
_DRIVE = re.compile(r"^[a-zA-Z]:")
_ENV_VAR = re.compile(r"\$\{([^}]+)\}")


def plugin_root():
    here = Path(__file__).resolve()
    for d in here.parents:
        if (d / MANIFEST_REL).is_file():
            return d
    raise FileNotFoundError(f"從 {here} 往上找不到 {MANIFEST_REL}")


def read_manifest():
    return read_json(plugin_root() / MANIFEST_REL)


def _is_abs(p):
    return bool(_DRIVE.match(p)) or p.startswith("/")


def normalize(p, cwd):
    """相對路徑接 cwd；/c/x 轉 C:/x；去 \\\\?\\ 前綴；統一正斜線；去尾斜線；Windows 上小寫。"""
    p = str(p).strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
        p = p[1:-1]
    if p.startswith("\\\\?\\UNC\\"):
        p = "\\\\" + p[8:]
    elif p.startswith("\\\\?\\"):
        p = p[4:]
    p = p.replace("\\", "/")
    if p == "~" or p.startswith("~/"):
        p = os.path.expanduser("~").replace("\\", "/") + p[1:]
    m = _GIT_BASH.match(p)
    if m:
        p = f"{m.group(1)}:/{m.group(2) or ''}"
    if not _is_abs(p):
        base = str(cwd).replace("\\", "/") if cwd else os.getcwd().replace("\\", "/")
        p = base.rstrip("/") + "/" + p
    p = os.path.normpath(p).replace("\\", "/")
    if len(p) > 1 and p.endswith("/") and not re.match(r"^[a-zA-Z]:/$", p):
        p = p.rstrip("/")
    if re.match(r"^[a-zA-Z]:$", p):
        p += "/"
    if os.name == "nt":
        p = p.lower()
    return p


def inside(path, dir_):
    """分段比對：C:/a/bc 不算在 C:/a/b 裡。兩邊都要先 normalize。"""
    dir_ = dir_.rstrip("/") or dir_
    return path == dir_ or path.startswith(dir_ + "/")


def resolve_spec(spec, base):
    """D-021 可攜 spec：~/x、${VAR}/x、相對 base 的 x 或 ../x、絕對 C:/x。展不開的環境變數回 None。"""
    spec = str(spec).strip()
    if "${" in spec:
        missing = [name for name in _ENV_VAR.findall(spec) if not os.environ.get(name)]
        if missing:
            return None
        spec = _ENV_VAR.sub(lambda m: os.environ[m.group(1)], spec)
    return normalize(spec, base)
