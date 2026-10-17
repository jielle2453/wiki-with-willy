"""路徑：plugin 自身的位置（從 __file__ 往上找，不寫死、不讀狀態檔，D-019）；
守門用的正規化、包含判斷、可攜 spec（D-021）展開。

normalize 把同一個檔的各種寫法壓成一個字串，守門比對一律用它的結果（I-101：payload 原樣不正規化）：
C:/、c:/、C:\\、/c/（Git Bash）、相對 cwd、\\\\?\\ 與 \\\\.\\ 前綴、\\\\localhost\\c$ 本機管理共享、C:x 磁碟相對、
每段尾端的點與空白（Win32 會自己吃掉）、8.3 短名與 junction（存在的部分走 realpath）。不做 Unicode NFC。
"""
import os
import re
from pathlib import Path

from kb._lib.io import read_json

MANIFEST_REL = Path(".claude-plugin") / "plugin.json"

_GIT_BASH = re.compile(r"^/([a-zA-Z])(?:/(.*))?$")
_DRIVE_ABS = re.compile(r"^[a-zA-Z]:/")
_DRIVE_REL = re.compile(r"^[a-zA-Z]:(?!/)(.*)$")
_UNC_LOCAL = re.compile(r"^//(?:localhost|127\.0\.0\.1)/([a-zA-Z])\$(?:/(.*))?$", re.IGNORECASE)
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
    return bool(_DRIVE_ABS.match(p)) or p.startswith("/")


def _text_forms(p):
    """只做字串層的轉換（不接 cwd）：引號、前綴、本機 UNC、~、Git Bash、磁碟相對。回 (路徑, 是否磁碟相對)。"""
    p = str(p).strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
        p = p[1:-1]
    p = p.replace("\\", "/")
    if p.startswith("file://"):
        p = p[7:].lstrip("/")
        if not _DRIVE_ABS.match(p):
            p = "/" + p
    if p.startswith("//?/UNC/") or p.startswith("//./UNC/"):
        p = "//" + p[8:]
    elif p.startswith("//?/") or p.startswith("//./"):
        p = p[4:]
    m = _UNC_LOCAL.match(p)
    if m:
        p = f"{m.group(1)}:/{m.group(2) or ''}"
    if p == "~" or p.startswith("~/"):
        p = os.path.expanduser("~").replace("\\", "/") + p[1:]
    m = _GIT_BASH.match(p)
    if m:
        p = f"{m.group(1)}:/{m.group(2) or ''}"
    m = _DRIVE_REL.match(p)
    if m:
        return m.group(1), True
    return p, False


def _strip_segments(p):
    """Win32 會吃掉每段尾端的點與空白：薪資. 等於 薪資。保留 . 與 .. 本身。"""
    out = []
    for seg in p.split("/"):
        if seg in (".", "..") or seg.endswith(":"):
            out.append(seg)
            continue
        seg = seg.rstrip(" .")
        if seg:
            out.append(seg)
    if p.startswith("//"):
        return "//" + "/".join(s for s in out if s)
    if p.startswith("/"):
        return "/" + "/".join(out)
    return "/".join(out)


def _canonical(p):
    """存在的部分走 realpath（展開 8.3 短名、junction、符號連結、大小寫），不存在的尾段原樣接回。"""
    try:
        cur, tail = p, []
        while not os.path.exists(cur):
            head, _, name = cur.rpartition("/")
            if not head:
                return p
            if head.endswith(":"):
                head += "/"
            tail.insert(0, name)
            if head == cur:
                return p
            cur = head
        real = os.path.realpath(cur).replace("\\", "/")
        if real.startswith("//?/"):
            real = real[4:]
        parts = [real.rstrip("/") if len(real) > 3 else real.rstrip("/")] + tail
        return "/".join(parts) if tail else real
    except (OSError, ValueError):
        return p


def normalize(p, cwd):
    """相對路徑接 cwd；統一正斜線；去尾斜線；Windows 上小寫。回 str。"""
    p, drive_rel = _text_forms(p)
    if drive_rel or not _is_abs(p):
        if cwd:
            base, _ = _text_forms(cwd)
            if not _is_abs(base):
                base = os.getcwd().replace("\\", "/").rstrip("/") + "/" + base
        else:
            base = os.getcwd().replace("\\", "/")
        p = base.rstrip("/") + "/" + p
    p = _strip_segments(p)
    p = os.path.normpath(p).replace("\\", "/")
    if len(p) > 1 and p.endswith("/") and not re.match(r"^[a-zA-Z]:/$", p):
        p = p.rstrip("/")
    if re.match(r"^[a-zA-Z]:$", p):
        p += "/"
    if _DRIVE_ABS.match(p):
        p = _canonical(p)
    if os.name == "nt":
        p = p.lower()
    return p


def inside(path, dir_):
    """分段比對：C:/a/bc 不算在 C:/a/b 裡。兩邊都要先 normalize。"""
    dir_ = dir_.rstrip("/") or dir_
    return path == dir_ or path.startswith(dir_ + "/")


def compress_spec(target, vault, max_up):
    """把絕對路徑壓成可攜 spec（D-021 補；照舊 llm-wiki 的 portable_path）。target、vault 原始大小寫，存在的部分走 realpath。
    順序：同一顆磁碟且往上不超過 max_up 層 → 相對（../來源/x）；在家目錄底下 → ~/x；否則絕對（D:/x）。一律正斜線。
    """
    t = Path(target).resolve()
    v = Path(vault).resolve()
    try:
        rel = os.path.relpath(str(t), str(v)).replace("\\", "/")
        if sum(1 for seg in rel.split("/") if seg == "..") <= max_up:
            return rel
    except ValueError:
        pass  # 不同磁碟
    try:
        return "~/" + t.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        pass
    return t.as_posix()


def is_abs_spec(spec):
    """spec 是不是沒壓掉的絕對路徑（磁碟代號或 // 開頭）：換電腦會找不到。"""
    s = str(spec or "").replace("\\", "/")
    return bool(re.match(r"^[a-zA-Z]:/", s)) or s.startswith("/")


def long_path(p):
    r"""Windows 加 \\?\ 前綴（UNC 是 \\?\UNC\），避開 260 字元限制（I-017）；非 Windows 原樣。照搬舊 kbcommon.long_path。"""
    s = os.path.abspath(str(p))
    if os.name != "nt" or s.startswith("\\\\?\\"):
        return s
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def resolve_spec(spec, base):
    """D-021 可攜 spec：~/x、${VAR}/x、相對 base 的 x 或 ../x、絕對 C:/x。展不開的環境變數回 None。"""
    spec = str(spec).strip()
    if "${" in spec:
        missing = [name for name in _ENV_VAR.findall(spec) if not os.environ.get(name)]
        if missing:
            return None
        spec = _ENV_VAR.sub(lambda m: os.environ[m.group(1)], spec)
    return normalize(spec, base)
