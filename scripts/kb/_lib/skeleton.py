"""骨架對帳（升級打包 要做的事 3，D-054 周邊、例句 9）：知識庫裡的 CLAUDE.md、.claude/settings.json 這些骨架檔，換版時哪些直接換新、哪些要問客戶。

`.system/skeleton.json`（schema 1）：{"schema_version": 1, "version": "0.21.0", "files": {"<相對路徑>": "<sha256>"}}，
記骨架每個檔「上一次寫進知識庫（或她說保留）」時，新版骨架的 sha256。kb init 建殼與 --finish 換新時更新。
換版時每個骨架檔：知識庫裡的跟記的一樣（她沒改過）→ 直接換新（寫前備進回合）；不一樣 → 列進 ask，AI 問她，
`kb upgrade skeleton <相對路徑> --keep|--replace`。新版這個檔沒改（記的指紋等於新版的）就不問、不動。
沒有 skeleton.json 的舊知識庫：跟 config/skeleton_history.json（發行程式每版追加：版本 → 每個骨架檔的 sha256）
裡任何一版對得上就當她沒改過；對不上就問，除非她那一版（from_version）的指紋跟新版一樣（新版沒改）。
--keep 把記的指紋換成新版的：她的版本留著，下一次新版這個檔又改了才會再問。
骨架檔指紋是檔案位元組的 sha256（init 複製時不改位元組）。只讀 skeleton/ 與 config/，只寫知識庫。
"""
import hashlib
import os
from pathlib import Path

from kb._lib import paths
from kb._lib.io import FileLock, read_json, read_text, write_json_atomic, write_text_atomic

SKELETON_JSON = ".system/skeleton.json"
SCHEMA_VERSION = 1
GITKEEP = ".gitkeep"
HIDDEN_ITEMS = ("CLAUDE.md", "AGENTS.md", ".claude", ".system")  # 同 init.HIDDEN_ITEMS：換掉檔之後要把隱藏屬性設回去
NOT_SKELETON = "這不是骨架檔：{}"


class Refused(Exception):
    """拒絕：訊息就是要給人看的那句（exit 3、一字不差）。"""


def skeleton_root():
    return paths.plugin_root() / "skeleton"


def history():
    """config/skeleton_history.json：{版本: {骨架檔: sha256}}；沒檔、壞檔回 {}。"""
    try:
        data = read_json(paths.plugin_root() / "config" / "skeleton_history.json", {})
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def plugin_version():
    return str((paths.read_manifest() or {}).get("version", "0"))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def files():
    """skeleton/ 底下 kb init 會複製的檔（相對路徑、正斜線、照字母；跳過 .gitkeep）。"""
    root = skeleton_root()
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != GITKEEP)


def hashes():
    """{相對路徑: 現在這版骨架檔的 sha256}。"""
    root = skeleton_root()
    return {rel: _sha(root / rel) for rel in files()}


def known(rel, sha):
    """這個內容是不是任何一版骨架的原樣（對 config/skeleton_history.json）。"""
    return any(isinstance(v, dict) and v.get(rel) == sha for v in history().values())


def recorded(vault):
    """.system/skeleton.json 的內容；沒檔、壞檔、形狀不對回 None（當沒有紀錄）。"""
    try:
        data = read_json(Path(vault) / SKELETON_JSON)
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return None
    return data


def record(vault, rels=None):
    """把 rels（沒給就全部）現在這版骨架檔的指紋記進 skeleton.json，其他的記錄保留；version 換成現在的。"""
    now = hashes()
    pick = list(now) if rels is None else [r for r in rels if r in now]
    p = Path(vault) / SKELETON_JSON
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = recorded(vault) or {"files": {}}
        files_ = dict(data["files"])
        for rel in pick:
            files_[rel] = now[rel]
        write_json_atomic(p, {"schema_version": SCHEMA_VERSION, "version": plugin_version(), "files": files_})


def _write_from_skeleton(vault, rel):
    target = Path(vault) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, read_text(skeleton_root() / rel))
    if rel in HIDDEN_ITEMS:  # 換檔之後隱藏屬性會掉（os.replace 換的是新檔）
        from kb import init
        init._hide(target)


def _backup(vault, rel, turn_id):
    from kb._lib import turn
    turn.backup(vault, rel, turn_id)


def claim_rewrite(vault, rel):
    """共用檔改寫認領（多人共用 3 第 10 點，D-298 (3)）：多人而且是 git repo 時先推 willy/upgrade/<版本>/<路徑雜湊前 12 碼>，
    別台推了（taken）這台就不改——別台改了、pull 下來就有；自己推的標籤也算過。單人、不是 git、連不上、標籤出錯都照改。"""
    try:
        from kb._lib import git, upgrade
        if not git.sync_active(vault):
            return True
        name = f"willy/upgrade/{upgrade.plugin_version()}/{hashlib.sha256(rel.encode('utf-8')).hexdigest()[:12]}"
        me = git.me(vault)
        got = git.push_tag(vault, name, me)
        if got == "taken":
            return git.tag_message(vault, name) == me
        return True
    except Exception:  # noqa: BLE001  標籤機制出錯不擋升級
        return True


def reconcile(vault, turn_id, from_version=None):
    """每個骨架檔對一次帳。回 {"created": [...], "replaced": [...], "ask": [...]}（相對路徑）。
    created：知識庫裡沒有、補上；replaced：她沒改過、換成新版（舊的備進回合）；ask：她改過、新版也改了，不動、交給 AI 問。"""
    vault = Path(vault)
    now = hashes()
    rec = (recorded(vault) or {}).get("files") or {}
    have_record = recorded(vault) is not None
    prev = history().get(from_version) if from_version else None
    prev = prev if isinstance(prev, dict) else {}
    created, replaced, ask, settled = [], [], [], []
    for rel in files():
        new, target = now[rel], vault / rel
        if not target.is_file():
            if not claim_rewrite(vault, rel):
                continue
            _write_from_skeleton(vault, rel)
            created.append(rel)
            settled.append(rel)
            continue
        cur = _sha(target)
        if cur == new:
            settled.append(rel)
            continue
        if have_record and rel in rec:
            if rec[rel] == new:  # 新版沒改這個檔：留著她的，不問
                continue
            untouched = cur == rec[rel]
        else:
            if prev.get(rel) == new:  # 她那一版的骨架跟新版一樣：新版沒改，不問
                continue
            untouched = known(rel, cur)
        if untouched:
            if not claim_rewrite(vault, rel):
                continue
            _backup(vault, rel, turn_id)
            _write_from_skeleton(vault, rel)
            replaced.append(rel)
            settled.append(rel)
        else:
            ask.append(rel)
    _untrack_rendered(vault)
    if settled:
        record(vault, settled)
    return {"created": created, "replaced": replaced, "ask": ask}


UNTRACKED = ("系統/客戶設定檔.md",)  # 以前放行進 git、現在擋掉的渲染頁（jacky 2026-11-05 定，#42）


def _untrack_rendered(vault):
    """.gitignore 已經沒有放行的渲染頁（新版骨架，或她改過但也拿掉了），還在 git 裡的不再追蹤；她自己放行的不動。
    檔留在這台，別台 pull 後被刪的由 refresh 重畫。重跑無害（--ignore-unmatch）。不是 git、出錯都不擋升級，只記 log。"""
    try:
        from kb._lib import git
        if not git.is_repo(vault):
            return
        ignore = (Path(vault) / ".gitignore").read_text(encoding="utf-8") if (Path(vault) / ".gitignore").is_file() else ""
        allowed = {ln.strip().lstrip("!").lstrip("/") for ln in ignore.splitlines() if ln.strip().startswith("!")}
        rels = [r for r in UNTRACKED if r not in allowed]
        if rels and not git.untrack(vault, rels):
            raise OSError("git rm --cached 失敗")
    except Exception as e:  # noqa: BLE001
        try:
            from kb._lib import ledger
            ledger.log_error(vault, f"設定檔 md 沒能不再追蹤：{type(e).__name__}: {e}", source="skeleton")
        except Exception:  # noqa: BLE001
            pass


def _check_rel(rel):
    rel = str(rel).replace("\\", "/")
    if rel not in files():
        raise Refused(NOT_SKELETON.format(rel))
    return rel


def keep(vault, rel):
    """她說保留她那份：檔不動，記的指紋換成新版的（新版這個檔下次又改了才會再問）。"""
    rel = _check_rel(rel)
    record(vault, [rel])


def replace(vault, rel, turn_id):
    """她說換新：先把她那份備進回合、再寫新版、記指紋。"""
    rel = _check_rel(rel)
    target = Path(vault) / rel
    if target.is_file():
        _backup(vault, rel, turn_id)
    _write_from_skeleton(vault, rel)
    record(vault, [rel])
