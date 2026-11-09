"""換了位置、換了電腦（備份還原與換電腦 要做的事 4，D-206、D-208）：整個知識庫客戶自己搬，程式在新位置補。

state（只讀，開場與 kb moved 共用）：env.json 這台電腦記的 vault_path 跟現在一樣是 same、不一樣是 moved；這台沒記、但別台有紀錄是 new_machine；
都沒有是 first（第一次，位置記下當沒換過）。
check：moved、new_machine 時算要補什麼（不寫）：(a) 相對位置的來源 spec 在新位置展開找不到、照舊位置展開找得到的，改寫成新位置的 spec（compress_spec）；
(b) 檔案卡的「開原檔」是外掛連結只帶編號（放寬丙 9，D-328），換位置不用重寫卡，cards_relinked 固定 0；(c) 這台找不到的來源；(d) 舊位置還有知識庫、vault.json 編號一樣的 old_copy；
(e) new_machine 時多回 env（kb build env 那支，不是 ok 的）與 unlock.json 用到、這台憑證管理員沒有的密碼名稱。
apply：做 (a)(b)，總目錄與受影響的綜合卡快取重寫，最後寫 vault_path；改檔前備進回合（退回救得回）。same、first 只記位置。
"""
import os
from pathlib import Path

from kb._lib import config, env, obsidian, paths, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib import vault as vault_lib

STATES = ("same", "moved", "new_machine", "first")


def here(vault):
    """知識庫現在的位置（realpath、正斜線）。"""
    return Path(os.path.realpath(str(vault))).as_posix()


def _same(a, b):
    a, b = str(a).rstrip("/"), str(b).rstrip("/")
    return a.casefold() == b.casefold() if os.name == "nt" else a == b


def state(vault):
    """只讀：{"state","from","to"}；from 是舊位置（moved 是這台記的、new_machine 是別台記的第一個、其他 None）。"""
    now = here(vault)
    recorded = env.vault_path(vault)
    if recorded:
        return {"state": "same" if _same(recorded, now) else "moved", "from": None if _same(recorded, now) else recorded,
                "to": now}
    if env.other_machines(vault):
        elsewhere = env.vault_paths_elsewhere(vault)
        return {"state": "new_machine", "from": elsewhere[0] if elsewhere else None, "to": now}
    return {"state": "first", "from": None, "to": now}


def note_place(vault):
    """first（這台與別台都沒有位置紀錄）時默默記位置、補 vault.json，回 state；其他狀態不動（換了位置、換了電腦要等 kb moved 補完才寫，
    不然會把舊位置蓋掉）。開場與這題的指令（backup、restore apply、versions clean、moved）第一次跑時都叫它（測試 T-2）；
    寫不了（唯讀、鎖）丟 OSError 由呼叫端決定要不要吞。不備進回合。"""
    st = state(vault)
    if st["state"] == "first":
        try:
            vault_lib.ensure_vault_id(vault)
        except ValueError:
            pass  # vault.json 壞了不覆寫（換編號密碼就對不上），位置照記
        env.set_vault_path(vault, st["to"])
    return st["state"]


def _old_bases(vault, st):
    """舊位置（找相對來源、舊的一份用）：moved 是這台記的，new_machine 是別台記的全部。"""
    if st["state"] == "moved":
        return [st["from"]]
    if st["state"] == "new_machine":
        return env.vault_paths_elsewhere(vault)
    return []


def _is_relative_spec(spec):
    s = str(spec or "").strip()
    return bool(s) and not paths.is_abs_spec(s) and not s.startswith("~") and "${" not in s


def _plan_sources(vault, raw, bases):
    """相對位置的來源 spec：新位置找不到、照舊位置找得到的換成新 spec。回 [(來源列, 新 spec)]。不改 raw。"""
    max_up = config.get(vault, "source_spec_max_up")
    vault_n = paths.normalize(str(vault), None)
    plan = []
    for s in raw["sources"]:
        spec = s.get("spec", "")
        if not _is_relative_spec(spec):
            continue
        new_root = paths.resolve_spec(spec, vault_n)
        if new_root and Path(new_root).exists():
            continue
        for base in bases:
            old_root = paths.resolve_spec(spec, paths.normalize(str(base), None))
            if old_root and Path(old_root).is_dir():
                plan.append((s, paths.compress_spec(old_root, vault, max_up)))
                break
    return plan


def _missing(vault, raw):
    """這台找不到的來源別名（算進換好 spec 之後的）。"""
    got = sources_lib.Sources(vault, raw)
    names = [u["alias"] for u in got.unresolved if u["kind"] == "source"]
    names += [e["alias"] for e in got.entries if not Path(e["root"]).exists()]
    return names


def _old_copy(vault, bases):
    mine = None
    try:
        mine = vault_lib.vault_id(vault)
    except (ValueError, OSError):
        return None
    if not mine:
        return None
    now = here(vault)
    for base in bases:
        if not base or _same(base, now) or not Path(base).is_dir():
            continue
        try:
            if vault_lib.vault_id(base) == mine:
                return Path(base).as_posix()
        except (ValueError, OSError):
            continue
    return None


def _passwords_missing(vault):
    """unlock.json 用到、這台憑證管理員沒有的密碼名稱。憑證管理員用不了就當看不出來。"""
    from kb._lib import unlock
    try:
        used = {str(k.get("credential", "")).partition("/")[2] for k in unlock.load(vault)["kinds"]}
        present = {name for _kind, name in unlock.entries(vault)}
    except (ValueError, OSError, unlock.Refused):
        return []
    return sorted(n for n in used if n and n not in present)


def _env_problems(vault):
    from kb._lib import build
    try:
        items = build.env_items(vault)
    except Exception:  # noqa: BLE001  環境檢查本身出錯不擋換電腦：env_items 自己每項都會 try，這裡只是保險
        return []
    return [i for i in items if i.get("level") != "ok"]


def check(vault):
    """要補什麼（只讀）：{"state","from","to","sources_rewritten","cards_relinked","missing_sources","old_copy","env","passwords_missing","obsidian_reopen"}。"""
    st = state(vault)
    out = dict(st, sources_rewritten=[], cards_relinked=0, missing_sources=[], old_copy=None, env=[], passwords_missing=[],
               obsidian_reopen=False)
    if st["state"] in ("same", "first"):
        return out
    bases = _old_bases(vault, st)
    raw = sources_lib.load_raw(vault)
    plan = _plan_sources(vault, raw, bases)
    for s, new_spec in plan:
        s["spec"] = new_spec  # 只改記憶體裡的 raw，下面算完就丟
    out["sources_rewritten"] = [s.get("alias", "") for s, _spec in plan]
    out["missing_sources"] = _missing(vault, raw)
    out["old_copy"] = _old_copy(vault, bases)
    # Obsidian（#22）：她用過 Obsidian（知識庫裡有 .obsidian/）而新位置 Obsidian 還沒登記：要重新打開（kb obsidian open）
    out["obsidian_reopen"] = (Path(vault) / ".obsidian").is_dir() and not obsidian.vault_registered(vault)
    if st["state"] == "new_machine":
        out["env"] = _env_problems(vault)
        out["passwords_missing"] = _passwords_missing(vault)
    return out


def apply(vault, turn_id):
    """補好：回 check 的結果，多 "changed"（給工作日誌的程式段）。same、first 只記位置。"""
    st = state(vault)
    try:
        vault_lib.ensure_vault_id(vault)  # 沒有編號的舊知識庫第一次跑時補（密碼與守門的公開函式）；壞檔不覆寫、不擋這次
    except (ValueError, OSError):
        pass
    out = check(vault)
    if st["state"] in ("same", "first"):
        env.set_vault_path(vault, st["to"])
        return dict(out, changed=[])
    changed = []
    sources_path = Path(vault) / sources_lib.SOURCES_REL
    with kio.FileLock(sources_path):
        raw = sources_lib.load_raw(vault)
        plan = _plan_sources(vault, raw, _old_bases(vault, st))
        if plan:
            for s, new_spec in plan:
                s["spec"] = new_spec
            turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
            kio.write_json_atomic(sources_path, raw)
            changed.append({"path": sources_lib.SOURCES_REL, "action": "modify"})
    env.set_vault_path(vault, st["to"])
    out["changed"] = changed
    return out
