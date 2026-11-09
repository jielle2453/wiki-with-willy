"""來源登記表 .system/sources.json：讀、展開 spec、查一個路徑是登記的還是排除的。

schema 見 specs/2026-10-12-守門.md。排除的 spec 相對該來源根；兩種 kind（hidden、unused）守門一視同仁都不讀（D-024）。
壞 JSON 直接丟例外，讓 hook 走出錯路徑（deny，fail closed）。
骨架與來源登記：load_raw 給 kb folders 讀原始 dict（Sources 類是展開後的，寫回要用原始；寫回由 folders 在自己的鎖裡做）；
new_id、RESERVED_ALIASES。條目不是物件、excludes 不是清單也丟 ValueError（N6）。
排除列選填 purge（manage 第二版，D-174）："pending" 是「不想讓 AI 看到」排除後殘留還沒清，清完改成清完的日期；舊列沒有就當沒有要清的。
PURGE_PENDING、pending_purges 給 cards todo 與 kb folders purge 找還沒清的那幾條。
"""
import json
import secrets

from kb._lib.areas import HOLD_DIR, OLD_INBOX, OLD_OUTPUT
from kb._lib.paths import inside, normalize, resolve_spec

SOURCES_REL = ".system/sources.json"
# 檔案索引/<來源別名>/ 保留給知識庫自身與暫存區（D-042、D-324）；
# 舊名（收件匣、AI 產出）也留著保留，免得被登記成來源別名、跟升級遷移前的舊資料撞名
VAULT_ALIAS = "知識庫"
RESERVED_ALIASES = ("知識庫", HOLD_DIR, OLD_OUTPUT, OLD_INBOX)


class Sources:
    def __init__(self, vault, data):
        self.vault = normalize(vault, None)
        self.entries = []
        self.unresolved = []  # [{"alias", "spec", "id", "kind"}]，kind 是 source｜exclude（清冊審閱裁定 2）
        self.vault_excludes = []  # 知識庫裡使用者的夾設成不讀（放寬甲一.6）：sources.json 頂層 vault_excludes，spec 相對知識庫根
        if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
            raise ValueError(f"{SOURCES_REL} 格式不對：要有 sources 清單")
        try:  # 這台電腦自己記的位置（D-292）：有就蓋過共用的 spec；env 讀不到當沒有
            from kb._lib import env
            local_paths = env.source_paths(vault)
        except Exception:  # noqa: BLE001
            local_paths = {}
        for s in data["sources"]:
            alias, spec, src_id = s.get("alias", "?"), s.get("spec", ""), s.get("id", "")
            where = local_paths.get(src_id) or spec
            root = resolve_spec(where, self.vault) if where else None
            if root is None:
                self.unresolved.append({"alias": alias, "spec": spec, "id": src_id, "kind": "source"})
                continue
            excludes = []
            for ex in s.get("excludes", []) or []:
                ex_root = resolve_spec(ex.get("spec", ""), root) if ex.get("spec") else None
                if ex_root is None:
                    self.unresolved.append({"alias": alias, "spec": ex.get("spec", ""), "id": src_id, "kind": "exclude"})
                    continue
                excludes.append({"root": ex_root, "kind": ex.get("kind", "hidden"), "reason": ex.get("reason", ""),
                                 "spec": ex.get("spec", ""), "purge": ex.get("purge")})
            self.entries.append({"id": src_id, "alias": alias, "root": root, "excludes": excludes,
                                 "shared": bool(s.get("shared"))})
        for ex in data.get("vault_excludes", []) or []:
            ex_root = resolve_spec(ex.get("spec", ""), self.vault) if ex.get("spec") else None
            if ex_root is None:
                self.unresolved.append({"alias": VAULT_ALIAS, "spec": ex.get("spec", ""), "id": "", "kind": "exclude"})
                continue
            self.vault_excludes.append({"root": ex_root, "kind": ex.get("kind", "hidden"), "reason": ex.get("reason", ""),
                                        "spec": ex.get("spec", ""), "purge": ex.get("purge")})

    def exclude_owners(self):
        """有排除可找的「來源」：登記的來源，加上知識庫（vault_excludes）當一個別名叫知識庫的假來源（purge 找排除用）。"""
        return list(self.entries) + [{"id": "", "alias": VAULT_ALIAS, "root": self.vault, "excludes": self.vault_excludes}]

    def vault_excluded(self, path):
        """path（已 normalize、在知識庫裡）在知識庫的不讀夾底下嗎。"""
        return any(inside(path, ex["root"]) for ex in self.vault_excludes)

    def vault_excluded_under(self, path):
        """path 底下（含自己）有知識庫的不讀夾嗎（Z10b）。"""
        return any(inside(ex["root"], path) for ex in self.vault_excludes)

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


def hold_root(vault):
    """多人共用 2（D-324、設計自定 1）：暫存區登記成保留別名「暫存區」的來源（團隊雲端的夾）時，這台那個夾的位置；沒登記回 None
    （照舊是知識庫裡的 暫存區/）。讀不到登記表回 None。"""
    try:
        for s in Sources(vault, load_raw(vault)).entries:
            if s["alias"] == HOLD_DIR:
                return s["root"]
    except (OSError, ValueError):
        pass
    return None


def raw_owners(raw):
    """sources.json 原始內容裡有排除的「來源」列：登記的來源加上知識庫的假列（excludes 就是頂層 vault_excludes 那個清單，
    改了會跟著寫回；沒有那個清單時是空的、不會寫出新鍵）。"""
    vex = raw.get("vault_excludes")
    return list(raw["sources"]) + [{"alias": VAULT_ALIAS, "excludes": vex if isinstance(vex, list) else []}]


def load_raw(vault):
    """原始 dict。壞 JSON 丟 ValueError；格式不對（沒有 sources 清單）也丟 ValueError。"""
    from kb._lib.io import read_json
    data = read_json(str(vault).rstrip("/\\") + "/" + SOURCES_REL)
    if data is None:
        raise FileNotFoundError(SOURCES_REL)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError(f"{SOURCES_REL} 格式不對：要有 sources 清單")
    for i, s in enumerate(data["sources"], 1):
        if not isinstance(s, dict):
            raise ValueError(f"{SOURCES_REL} 格式不對：第 {i} 筆來源不是物件")
        excludes = s.get("excludes")
        if excludes is not None and not isinstance(excludes, list):
            raise ValueError(f"{SOURCES_REL} 格式不對：第 {i} 筆來源的 excludes 不是清單")
        if any(not isinstance(ex, dict) for ex in excludes or []):
            raise ValueError(f"{SOURCES_REL} 格式不對：第 {i} 筆來源的排除有一條不是物件")
    vex = data.get("vault_excludes")
    if vex is not None and (not isinstance(vex, list) or any(not isinstance(ex, dict) for ex in vex)):
        raise ValueError(f"{SOURCES_REL} 格式不對：vault_excludes 要是物件的清單")
    return data


def new_id(existing_ids):
    """src- 加 4 碼 hex，撞到重抽（跟 inventory 的 out- 同做法）。"""
    taken = set(existing_ids)
    while True:
        new = "src-" + secrets.token_hex(2)
        if new not in taken:
            return new


PURGE_PENDING = "pending"


def pending_purges(sources):
    """還沒清的 hidden 排除：[{"alias", "spec", "root"}]（照登記順序）。"""
    return [{"alias": src["alias"], "spec": ex["spec"], "root": ex["root"]} for src in sources.entries
            for ex in src["excludes"] if ex.get("kind") == "hidden" and ex.get("purge") == PURGE_PENDING]
