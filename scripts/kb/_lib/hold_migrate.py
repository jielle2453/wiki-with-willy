"""收件匣與 AI 產出合成暫存區的升級遷移（放寬甲第二輪 13，D-324）：migrate.py 的遷移表有一列叫它，kb upgrade --finish 跑。

順序（升級前照 D-215 拍快照；升級前的回合備份不改寫路徑，升級前那些回合要退照快照）：
1. 第一層有 收件匣/、沒有 暫存區/ → 整個夾改名；兩個都有（使用者自己開過叫暫存區的夾）→ 收件匣的檔照下一點的同名規則併進去。
2. AI 產出/ 裡的檔與夾照相對路徑搬進 暫存區/：目的地沒有同名的就搬；同名而內容一樣，AI 產出那份先備進回合再拿掉、清冊那筆標
   duplicate_of 指到留下的那筆（status 改 deleted）；同名內容不同的不搬、留在原夾，結果列 left_in_output（收件匣併進去時同理，列 left_in_inbox）。
   原夾空了就拿掉；沒空的留著，之後就是一般使用者的夾（Z8）。
3. 清冊：file://收件匣/… 與 file://AI 產出/…（有搬的）改成 file://暫存區/…，編號不變；檔案卡從 檔案索引/收件匣、檔案索引/AI 產出
   搬到 檔案索引/暫存區 對應位置，卡的位置欄、綜合卡與總目錄的連結照 cards.sync 重寫（同 source_edit.do_move 那一段）。
再跑一次不做事：needs() 只在還有能做的動作（改名、搬、併、拿掉空夾）時才回 True；留下的同名不同內容的檔不算。
"""
import os
import shutil
from pathlib import Path

from kb._lib import cards, inventory, turn
from kb._lib.areas import HOLD_DIR, OLD_INBOX, OLD_OUTPUT

REL = HOLD_DIR  # migrate 表的 rel（給 migrated／skipped 列的名字）
OLD = (OLD_INBOX, OLD_OUTPUT)  # 照這個順序處理：收件匣先改名，AI 產出再併


def _is_dir(p):
    return p.is_dir() and not p.is_symlink()


def _files(root):
    """root 底下的檔，相對路徑（正斜線），照字母；不跟連結。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            out.append(Path(dirpath, name).relative_to(root).as_posix())
    return out


def _same(a, b):
    try:
        return os.path.getsize(a) == os.path.getsize(b) and inventory.sha256_of(a) == inventory.sha256_of(b)
    except OSError:
        return False


def run(vault, turn_id=None, dry=False):
    """做（或只看 dry）遷移。回 {"renamed": [舊名], "moved": [(舊名, 相對路徑)], "duplicates": [(舊名, 相對路徑)],
    "left": {舊名: [相對路徑]}, "removed": [舊名]}。dry 時不動任何東西。"""
    vault = Path(vault)
    hold = vault / HOLD_DIR
    res = {"renamed": [], "moved": [], "duplicates": [], "left": {OLD_INBOX: [], OLD_OUTPUT: []}, "removed": [],
           "_whole": set(), "_dup_to": {}}
    for old in OLD:
        root = vault / old
        if not _is_dir(root):
            continue
        if old == OLD_INBOX and not os.path.lexists(hold):
            res["renamed"].append(old)
            res["_whole"].add(old)
            if not dry:
                os.rename(root, hold)
            continue
        for rel in _files(root):
            src, dst = root / rel, hold / rel
            if not os.path.lexists(dst) or dry and not _is_dir(hold):
                res["moved"].append((old, rel))
                if not dry:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(src, dst)
            elif dst.is_file() and _same(src, dst):
                res["duplicates"].append((old, rel))
                res["_dup_to"][(old, rel)] = rel
                if not dry:
                    turn.backup(vault, f"{old}/{rel}", turn_id, action="delete")
                    os.remove(src)
            else:
                res["left"][old].append(rel)
        if dry:  # 沒有留下同名不同內容的檔，這個夾搬完就空了、可以拿掉
            if not res["left"][old]:
                res["removed"].append(old)
            continue
        if _prune(root, hold):
            res["removed"].append(old)
    return res


def _prune(root, hold):
    """搬完之後 root 底下的空夾由下往上拿掉（目的地沒有的夾先在暫存區建好，空夾也搬過去）；root 空了連它也拿掉。回 root 拿掉了嗎。"""
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        p = Path(dirpath)
        if os.listdir(p):
            continue
        rel = p.relative_to(root)
        (hold / rel).mkdir(parents=True, exist_ok=True)
        if p != root:
            os.rmdir(p)
    if _is_dir(root) and not os.listdir(root):
        os.rmdir(root)
        return True
    return False


def needs(vault):
    """還有動作可做嗎（給遷移表判斷要不要跑）：有 收件匣／AI 產出 的夾，而且做得了改名、搬、併或拿掉空夾其中一種。"""
    got = run(vault, dry=True)
    return bool(got["renamed"] or got["moved"] or got["duplicates"] or got["removed"])


def _rewrite_inventory(vault, res, turn_id):
    """清冊的位置換成暫存區（編號不變）；同名拿掉的那份標 duplicate_of、status 改 deleted。回改到幾筆。"""
    p = inventory._path(vault)
    if not p.exists():
        return 0
    moved = {(a.casefold(), rel.casefold()) for a, rel in res["moved"]}
    dups = {(a.casefold(), rel.casefold()): rel for (a, rel) in res["_dup_to"]}
    whole = {a.casefold() for a in res["_whole"]}
    count = 0
    with inventory.FileLock(p):
        data = inventory.load(vault)
        entries = data["entries"]
        changed = False
        olds = (OLD_INBOX.casefold(), OLD_OUTPUT.casefold())

        def old_locs(e):
            for loc in e.get("locations") or []:
                uri = loc.get("uri", "")
                if inventory.is_file_uri(uri):
                    alias, rel = inventory.split_uri(uri)
                    if alias.casefold() in olds:
                        yield loc, alias.casefold(), rel
        for e in entries:  # 先把搬了的位置換成暫存區，再處理同名拿掉的（找留下的那筆要看換好的位置）
            for loc, a, rel in old_locs(e):
                if a in whole or (a, rel.casefold()) in moved:
                    loc["uri"] = inventory.make_uri(HOLD_DIR, rel)
                    changed = True
                    count += 1
        for e in entries:
            for _loc, a, rel in list(old_locs(e)):
                if (a, rel.casefold()) in dups:
                    kept = inventory.uri_key(inventory.make_uri(HOLD_DIR, dups[(a, rel.casefold())]))
                    twin = next((x for x in entries if x is not e and x.get("status") == "active"
                                 and any(inventory.uri_key(l.get("uri", "")) == kept for l in x.get("locations") or [])), None)
                    if twin is not None:
                        e["duplicate_of"] = twin["id"]
                    e["status"] = "deleted"
                    changed = True
                    count += 1
        if changed:
            turn.backup(vault, inventory.INVENTORY_POSIX, turn_id)
            inventory._write(vault, entries)
    return count


def _follow_cards(vault, turn_id):
    """卡搬到新位置對應的夾、位置欄與綜合卡、總目錄的連結跟上（cards.sync 那套）。回 sync 的結果。"""
    from kb._lib import entities
    from kb._lib import sources as sources_lib
    sources = sources_lib.load(vault)
    undo = entities._Undo(vault)
    try:
        got = cards.sync(vault, sources, turn_id, undo)
        if got["moved"] or got["updated"] or got["dropped"]:
            undo.file(cards.INDEX_POSIX)
            cards.render_index(vault, turn_id)
        cards.refresh_synthesis(vault, got["touched"], turn_id)
        undo.flush()
    except Exception:
        undo.rollback()
        raise
    return got


def rewrite(vault, turn_id):
    """遷移表的 rewrite：做完回 {"notes": [...], "left_in_output": [...]}。"""
    res = run(vault, turn_id)
    n = _rewrite_inventory(vault, res, turn_id)
    got = _follow_cards(vault, turn_id) if (n or res["renamed"] or res["moved"]) else {"moved": []}
    notes = []
    if res["renamed"]:
        notes.append(f"「{OLD_INBOX}」改名成「{HOLD_DIR}」")
    if res["moved"] or res["duplicates"]:
        notes.append(f"「{OLD_OUTPUT}」與「{OLD_INBOX}」裡的 {len(res['moved'])} 份搬進「{HOLD_DIR}」"
                     + (f"，{len(res['duplicates'])} 份跟「{HOLD_DIR}」裡的一樣、拿掉了（回合備份留著）" if res["duplicates"] else ""))
    left = [{"name": rel, "existing": f"file://{HOLD_DIR}/{rel}"} for old in OLD for rel in res["left"][old]]
    return {"notes": notes, "left_in_output": left, "cards_moved": len(got["moved"])}


def left_count(vault):
    """AI 產出 裡還有幾份跟暫存區同名（開場那行用，只讀）：留著的就是內容不同的。"""
    root = Path(vault) / OLD_OUTPUT
    hold = Path(vault) / HOLD_DIR
    if not _is_dir(root) or not _is_dir(hold):
        return 0
    return sum(1 for rel in _files(root) if os.path.lexists(hold / rel))
