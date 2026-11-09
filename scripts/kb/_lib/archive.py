"""封存（放寬丙 12，D-332）：舊的、過時的檔與實體收起來，還找得到，但不再整理、不再問、不拿來答「現在」的事。

清冊條目與實體清單的選填欄 archived（{"date","reason","turn"}）是正本，檔案卡檔頭「封存: YYYY-MM-DD」是它的渲染（cards.sync_fronts 照清冊補或拿掉）。
這裡只放「認出誰封存了」與「這件事跟封存的有關嗎」：整理（cards todo）、題目（plan.visible）、待處理（pending 渲染與列表）、健檢（lint runner）都從這裡問。
實體封存不改事類的結案狀態，兩者並存。讀不到清冊或實體清單當沒有封存的（不擋原本的功能）。
"""
import os
import re
from pathlib import Path

_ID = re.compile(r"^(f|out)-[0-9a-f]+$")


class ArchiveRefused(ValueError):
    """kb archive 的拒絕：訊息就是給人看的那句。"""


def sets(vault):
    """(封存的檔案編號集合, 封存的實體編號集合)；讀不到回兩個空的。"""
    from kb._lib import entities, inventory
    files, ents = set(), set()
    try:
        files = {e.get("id") for e in inventory.load(vault)["entries"] if e.get("archived")}
    except (ValueError, OSError):
        pass
    try:
        ents = {e.get("id") for e in entities.load(vault)["entities"] if e.get("archived")}
    except (ValueError, OSError):
        pass
    return files, ents


def hides(file_ids, entity_ids, archived_files, archived_entities):
    """這件事（題、待處理、健檢結果）跟封存的有關嗎：提到封存的檔，或對象的實體全是封存的。"""
    if any(f in archived_files for f in file_ids or ()):
        return True
    return bool(entity_ids) and all(e in archived_entities for e in entity_ids)


def hidden_item(item, archived_files, archived_entities):
    """待處理的一件（pending.json 的條目）是不是跟封存的有關。"""
    return hides(item.get("file_ids") or (), item.get("entity_ids") or (), archived_files, archived_entities)


def visible_items(vault, items):
    """待處理條目去掉跟封存有關的（只是不顯示，pending.json 不動）；沒有封存的原樣回。"""
    files, ents = sets(vault)
    if not files and not ents:
        return list(items)
    return [i for i in items if not hidden_item(i, files, ents)]


def resolve(vault, sources, target, cwd):
    """目標 → ("file", 清冊條目) 或 ("entity", 實體)。目標可以是清冊編號、file:// uri、實體名稱（別名也認）或磁碟路徑；認不出丟 ArchiveRefused。"""
    from kb._lib import entities, inventory
    target = str(target).strip()
    entries = inventory.load(vault)["entries"]
    if _ID.match(target):
        entry = next((e for e in entries if e.get("id") == target), None)
        if entry is None:
            raise ArchiveRefused(f"清冊沒有 {target}")
        return "file", entry
    if target.startswith("file://"):
        entry = inventory.entry_for(vault, target)
        if entry is None:
            raise ArchiveRefused(f"清冊沒有這個位置：{target}")
        return "file", entry
    found, _hit = entities.find(entities.load(vault), target)
    if found is not None and found.get("status") == "active":
        return "entity", found
    path = Path(target) if os.path.isabs(target) else Path(cwd) / target
    if path.exists():
        uri = inventory.uri_of(vault, sources, Path(os.path.abspath(path)))
        entry = inventory.entry_for(vault, uri) if uri else None
        if entry is not None:
            return "file", entry
    raise ArchiveRefused(f"找不到「{target}」：給清冊編號、實體名稱或檔案路徑")
