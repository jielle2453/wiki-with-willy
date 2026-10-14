"""來源登記表 .system/sources.json：讀、展開 spec、查一個路徑是登記的還是排除的。

schema 見 specs/2026-10-12-守門.md。排除的 spec 相對該來源根；兩種 kind（hidden、unused）守門一視同仁都不讀（D-024）。
壞 JSON 直接丟例外，讓 hook 走出錯路徑（deny，fail closed）。
骨架與來源登記：load_raw／save 給 kb folders 讀改寫原始 dict（Sources 類是展開後的，寫回要用原始）；new_id、RESERVED_ALIASES。
"""
import json
import secrets
from pathlib import Path

from kb._lib.io import FileLock, write_json_atomic
from kb._lib.paths import inside, normalize, resolve_spec

SOURCES_REL = ".system/sources.json"
# 檔案索引/<來源別名>/ 保留給知識庫自身、AI 產出、收件匣三個來源（D-042）
RESERVED_ALIASES = ("知識庫", "AI 產出", "收件匣")


class Sources:
    def __init__(self, vault, data):
        self.vault = normalize(vault, None)
        self.entries = []
        self.unresolved = []  # [(alias, spec)]
        if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
            raise ValueError(f"{SOURCES_REL} 格式不對：要有 sources 清單")
        for s in data["sources"]:
            alias, spec = s.get("alias", "?"), s.get("spec", "")
            root = resolve_spec(spec, self.vault) if spec else None
            if root is None:
                self.unresolved.append((alias, spec))
                continue
            excludes = []
            for ex in s.get("excludes", []) or []:
                ex_root = resolve_spec(ex.get("spec", ""), root) if ex.get("spec") else None
                if ex_root is None:
                    self.unresolved.append((alias, ex.get("spec", "")))
                    continue
                excludes.append({"root": ex_root, "kind": ex.get("kind", "hidden"), "reason": ex.get("reason", "")})
            self.entries.append({"id": s.get("id", ""), "alias": alias, "root": root, "excludes": excludes})

    def lookup(self, path):
        """path 要先 normalize。回 ("excluded"|"registered"|None, source)；排除贏。"""
        for src in self.entries:
            for ex in src["excludes"]:
                if inside(path, ex["root"]):
                    return "excluded", src
        best = None
        for src in self.entries:
            if inside(path, src["root"]) and (best is None or len(src["root"]) > len(best["root"])):
                best = src
        return ("registered", best) if best else (None, None)

    def excluded_under(self, path):
        """path 底下（含自己）有沒有排除夾：有就回那個來源的 alias（Z10b，S3）。"""
        for src in self.entries:
            if any(inside(ex["root"], path) for ex in src["excludes"]):
                return src["alias"]
        return None


def load(vault):
    return Sources(vault, load_raw(vault))


def load_raw(vault):
    """原始 dict。壞 JSON 丟 ValueError；格式不對（沒有 sources 清單）也丟 ValueError。"""
    with open(str(vault).rstrip("/\\") + "/" + SOURCES_REL, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError(f"{SOURCES_REL} 格式不對：要有 sources 清單")
    return data


def save(vault, data):
    p = Path(vault) / SOURCES_REL
    with FileLock(p):
        write_json_atomic(p, data)


def new_id(existing_ids):
    """src- 加 4 碼 hex，撞到重抽（跟 inventory 的 out- 同做法）。"""
    taken = set(existing_ids)
    while True:
        new = "src-" + secrets.token_hex(2)
        if new not in taken:
            return new
