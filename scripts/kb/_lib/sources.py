"""來源登記表 .system/sources.json：讀、展開 spec、查一個路徑是登記的還是排除的。

schema 見 specs/2026-10-12-守門.md。排除的 spec 相對該來源根；兩種 kind（hidden、unused）守門一視同仁都不讀（D-024）。
壞 JSON 直接丟例外，讓 hook 走出錯路徑（deny，fail closed）。
"""
import json

from kb._lib.paths import inside, normalize, resolve_spec

SOURCES_REL = ".system/sources.json"


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


def load(vault):
    with open(str(vault).rstrip("/\\") + "/" + SOURCES_REL, encoding="utf-8") as f:
        data = json.load(f)
    return Sources(vault, data)
