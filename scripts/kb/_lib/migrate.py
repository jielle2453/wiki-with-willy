"""狀態檔與卡檔頭的遷移表（升級打包 要做的事 2，D-054、D-215）：kb upgrade --finish 在新對話開場叫它轉格式。

STATE_FILES 是一張表：哪個檔、現在的 schema 版本、磁碟上存的版本、怎麼寫回。各檔用自己的 load（已有的遷移，在記憶體補欄位）讀、
照自己的寫法寫回（寫前備進回合，versions revert 退得回）。之後新增遷移的題，在這張表加一列（_lib/migrate.py 的表有變，
發行程式 tools/release.py 就不准標成小修，D-217）。
CARD_HEADER_MIGRATIONS 是卡的檔頭遷移：[{"from": 舊範本版本, "to": 新範本版本, "fn": 整張卡的文字 → 新文字}]，目前沒有要遷的（空表，D-054）；
只改檔頭機器欄位、正文不動，寫前備進回合；fn 改壞檔頭（解析不了、版本沒變成 to）就不寫、列進 skipped。
SYNTHESIS_MIGRATIONS 是綜合卡的同一套表（D-523：範本版本 1 → 2，synthesis.migrate_layout 把推算、紀事搬下去）：掃 active
實體的綜合卡，pending 一張一項、apply 遷的張數併進 "cards"；讀不懂、遷壞的列進 skipped，不擋其他張。
不需要遷移登記的：sources.json 排除列的選填欄（purge 等）、purge-pending.json、回合夾的 moves.jsonl、unlock.json、vault.json、
rulings.json（v0.19.0 沒升 schema）、lint.json、renders.json、set_aside.json、statements-archive.json、findings-archive.json（lint 題的新檔，schema 1）、batches.json、build.json（新檔 schema 1）、entities.json 的 ai_judged（schema 沒改）。
pending 只讀；apply 一個檔失敗不擋其他檔，失敗的列進 skipped（原因一句話）；rewrite 回清單的，那幾筆也併進 skipped。
entities.json schema 2 → 3（標籤記在實體上，D-505 七）：每個 active、有綜合卡的實體，檔頭的標籤行（第八節這個型別的欄、有值的）抄進 tags
（字典外的詞、單選寫兩個的那一欄不抄，列進 skipped）；tags_said 空、備份 by program（回答末尾不講）。沒有綜合卡、型別有標籤欄的
下次整理照 cards todo 的只補標籤項目補（裁定 4），不另外記。
型別夾補編號（型別夾編號，D-249）：另一列，rel 也是 profile.json（kb init 已經可能把 schema 升到 7，所以不看 schema）：
型別表有沒有 dir 的列算 6、都有算 7。rewrite 照 dir_plan 把夾名就是型別名的夾改成預設名字（01 案子）、寫 dir；沒有夾的 dir 寫預設名字
（之後照常補建）；她以前改過名、認得出來的（profile.renamed_dirs）dir 記那個夾、不動。設定檔與夾在同一套 _Undo、寫前備進回合，
做完照 profile_cli._follow 跟上清冊、檔案卡、總目錄與綜合卡快取；md 有手改未套用、背景整理在跑時 Skip。
暫存區（D-324，放寬甲第二輪）：再一列，rel 是 暫存區（不是狀態檔）：_lib/hold_migrate 的 needs 判斷第一層還有沒有能做的（收件匣改名、
AI 產出／收件匣的檔搬進暫存區、同名一樣的拿掉、空夾拿掉），有就算 0（要遷）、沒有算 1；升級前照 D-215 拍快照，不另備路徑。
彈性區撤回（#51，D-532）：彈性區那列（dir 是彈性區名改回預設名）與 _extras 改名那列拿掉；schema 6 → 7 照第一列寫回，dir 不動。
apply 按列判斷（同一個 rel 兩列各看各的），migrated 同一個 rel 只列一次；補編號那列多回 notes（「型別夾補上編號：…」，finish 印）。
"""
import json
from collections import namedtuple
from pathlib import Path

from kb._lib import cards, entities, hold_migrate, inventory, profile
from kb._lib import pending as pending_lib  # 這支自己有 pending()
from kb._lib.io import FileLock, read_json, write_text_atomic

Migration = namedtuple("Migration", "rel current stored rewrite")


class Skip(Exception):
    """這個檔先不遷：args[0] 是原因（一句白話）。"""


def _raw_version(rel):
    """磁碟上那個檔記的 schema_version（沒檔、壞檔、不是整數回 None；沒寫版本當 1，清冊與實體清單最早的版本）。"""
    def read(vault):
        try:
            data = read_json(Path(vault) / rel)
        except (ValueError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        version = data.get("schema_version", 1)
        return version if isinstance(version, int) else None
    return read


def _rewrite_profile(vault, turn_id):
    with profile.lock(vault):
        data = profile.load(vault)
        if data is None:
            return
        if profile.md_sha256(vault) not in (None, data.get("md_sha256")):  # md 有沒套用的手改：重渲染會蓋掉，跟 kb init 一樣請先 apply
            raise Skip("設定檔有沒套用的改動，先 kb profile apply 再升級")
        profile.write_locked(vault, data, turn_id)


def _rewrite_inventory(vault, turn_id):
    from kb._lib import turn
    path = inventory._path(vault)
    with FileLock(path):
        turn.backup(vault, inventory.INVENTORY_POSIX, turn_id)
        inventory._write(vault, inventory.load(vault)["entries"])


def _rewrite_entities(vault, turn_id):
    from kb._lib import synthesis
    stored = _raw_version(entities.ENTITIES_POSIX)(vault)
    prof = entities._profile(vault)
    skipped, found = [], {}
    if isinstance(stored, int) and stored < 3:
        for e in entities.load(vault)["entities"]:
            if e.get("status") != "active" or entities.is_self(e) or not e.get("type") or "tags" in e:
                continue
            rel = entities.locate(vault, e) if e.get("card") else None
            if not rel:
                continue
            try:
                front = synthesis.read_front((Path(vault) / rel).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as err:
                skipped.append({"rel": rel, "why": f"綜合卡的標籤沒搬成（{_first_line(err)}）"})
                continue
            rows = {entities.key(t["field"]): t for t in profile.tags_for(prof, e["type"])}
            tags = {}
            for f, v in synthesis.card_tags(front, prof, e["type"]):
                if v == "":
                    continue
                row = rows[entities.key(f)]
                why = entities.tag_refusal(row, entities.tag_words(v))
                if why:
                    skipped.append({"rel": rel, "why": f"「{row['field']}」沒搬進實體清單：{why}"})
                    continue
                tags[row["field"]] = entities.tag_normal(row, v)
            if tags:
                found[e["id"]] = tags
    with entities.lock(vault):
        data = entities.load(vault)
        for e in data["entities"]:
            if e.get("id") in found and "tags" not in e:
                e.update(tags=found[e["id"]], tags_said=[], tags_at=entities._now_stamp())
        entities._save(vault, data, turn_id, by="program")
    return skipped


DIRS_PENDING = "設定檔有沒套用的改動，先 kb profile apply 再升級"
DIRS_BUSY = "背景整理還在跑，型別夾先不補編號；等它做完再跑一次 kb upgrade --finish"
DIRS_NOTE = "型別夾補上編號：{pairs}"


def _dirs_stored(vault):
    """補編號那列：型別表有沒有 dir 的列算 6、都有（或沒有型別）算 7；沒有設定檔、讀不了回 None。"""
    try:
        data = profile.load(vault)
    except (ValueError, OSError):
        return None
    if data is None:
        return None
    return 6 if any(not t.get("dir") for t in data["types"]) else profile.SCHEMA_VERSION


def _rewrite_dirs(vault, turn_id):
    """D-249：沒有 dir 的列補上編號（profile.dir_plan(number_legacy=True)）；她以前改過名的記她的夾名。回 notes 給 apply。"""
    from kb import profile_cli
    from kb._lib import backup
    if backup.batch_running(vault):  # 同升級那套判斷（D-215）：還有動靜的批次
        raise Skip(DIRS_BUSY)
    with profile.lock(vault):
        data = profile.load(vault)
        if data is None:
            return None
        if profile.md_sha256(vault) not in (None, data.get("md_sha256")):  # md 不見（不進 git，#42）不算手改
            raise Skip(DIRS_PENDING)
        effective = profile.with_renamed(data["types"], data["types"], [])
        found = profile.renamed_dirs(vault, effective, data["types"])
        plan = profile.dir_plan(vault, profile.with_renamed(data["types"], data["types"], found), data["types"],
                                number_legacy=True)
        if plan["refusal"]:
            raise Skip(plan["refusal"])
        renamed = [r for r in found if not r["default"]]
        pairs = [(r["from"], r["to"]) for r in renamed]
        from kb._lib import turn
        for d in plan["creates"]:  # 沒有夾的照預設名字先建（同 apply 先建夾再寫檔）：建不成這一列不算遷了，下次再試
            if not (Path(vault) / d).exists():
                (Path(vault) / d).mkdir()
                turn.record_folder(vault, turn_id, "make", d)

        def act(undo):
            changed = profile_cli._write_profile(vault, dict(data, types=plan["types"]), turn_id, undo)
            more, touched = profile_cli._dir_work(vault, undo, turn_id, plan["moves"], pairs)
            return changed + more, touched
        _changed, touched = entities._guarded(vault, act)
    if touched or plan["moves"] or pairs:
        profile_cli._refresh(vault, touched + profile_cli._moved_names(vault, data, list(plan["moves"]) + pairs), turn_id)
    notes = [DIRS_NOTE.format(pairs="、".join(f"{a} → {b}" for a, b in sorted(plan["moves"])))] if plan["moves"] else []
    if renamed:
        notes.append(profile_cli.RENAMED_DONE.format(pairs=profile.renamed_pairs(renamed)))
    return {"notes": notes}


def _rewrite_pending(vault, turn_id):
    """schema 1 → 2（lint 題，D-226）：load 補 set_aside、returned，照 pending 自己的寫法寫回（兩檔備進回合、重渲染）。"""
    pending_lib.refresh(vault, turn_id)


def _hold_stored(vault):
    """暫存區那列（D-324）：不看 schema，看夾——第一層還有能改名、搬、併、拿掉的 收件匣／AI 產出 算 0（要遷），沒有算 1。"""
    try:
        return 0 if hold_migrate.needs(vault) else 1
    except OSError:
        return None


STATE_FILES = (
    Migration(profile.PROFILE_POSIX, lambda: profile.SCHEMA_VERSION, profile.stored_schema, _rewrite_profile),
    Migration(inventory.INVENTORY_POSIX, lambda: inventory.SCHEMA_VERSION, _raw_version(inventory.INVENTORY_POSIX),
              _rewrite_inventory),
    Migration(entities.ENTITIES_POSIX, lambda: entities.SCHEMA_VERSION, _raw_version(entities.ENTITIES_POSIX),
              _rewrite_entities),
    Migration(pending_lib.PENDING_POSIX, lambda: pending_lib.SCHEMA_VERSION, _raw_version(pending_lib.PENDING_POSIX),
              _rewrite_pending),
    Migration(profile.PROFILE_POSIX, lambda: profile.SCHEMA_VERSION, _dirs_stored, _rewrite_dirs),
    Migration(hold_migrate.REL, lambda: 1, _hold_stored, hold_migrate.rewrite),  # 收件匣與 AI 產出合成暫存區（放寬甲第二輪 13）
)
CARD_HEADER_MIGRATIONS = []


def _synthesis_v2(text):
    from kb._lib import synthesis
    return synthesis.migrate_layout(text)


SYNTHESIS_MIGRATIONS = [{"from": "1", "to": "2", "fn": _synthesis_v2}]  # D-523：紀事放最後


def _synthesis_cards(vault):
    """[(rel, 範本版本)]：active、有綜合卡的實體（客戶自己不算）；讀不懂、檔頭沒有範本版本的卡範本版本是那個例外
    （讀不懂但那行已是現行版本的不算例外，不在遷移範圍）。實體清單讀不了回 None。"""
    from kb._lib import synthesis
    try:
        data = entities.load(vault)
    except (ValueError, OSError):
        return None
    out = []
    for e in data["entities"]:
        if e.get("status") != "active" or entities.is_self(e) or not e.get("card"):
            continue
        rel = entities.locate(vault, e)
        if not rel:
            continue
        p = Path(vault) / rel
        try:
            version = synthesis.read_front(p.read_text(encoding="utf-8")).get("範本版本")
            version = ValueError("檔頭沒有範本版本") if version is None else str(version)
        except (OSError, UnicodeDecodeError, ValueError) as err:
            version = synthesis.TEMPLATE_VERSION if _header_version(p) == synthesis.TEMPLATE_VERSION else err
        out.append((rel, version))
    return out


def _header_version(p):
    """讀不懂的卡：只在檔頭（開頭 --- 到下一個 ---）找「範本版本:」那行的值（去引號）；找不到、檔頭沒收尾回 None。"""
    try:
        lines = p.read_bytes().split(b"\n")
    except OSError:
        return None
    if not lines or lines[0].strip().lstrip(b"\xef\xbb\xbf") != b"---":
        return None
    end = next((n for n in range(1, len(lines)) if lines[n].strip() == b"---"), None)
    if end is None:
        return None
    key = "範本版本".encode("utf-8")
    for line in lines[1:end]:
        if line.startswith(key):
            k, sep, v = line.partition(b":")
            if sep and k.strip() == key:
                v = v.strip().strip(b"\"'")
                try:
                    return v.decode("utf-8")
                except UnicodeDecodeError:
                    return None
    return None


def _synthesis_due(version):
    return next((m for m in SYNTHESIS_MIGRATIONS if str(m["from"]) == version), None) if isinstance(version, str) else None


def _migrate_synthesis(vault, turn_id, skipped):
    """綜合卡的版面遷移（D-523）；回遷了幾張。寫前備進回合（by program）；基準照 card_base.record_write 換成搬完的、她沒處理的手改留成待處理（D-270），
    不算她手改。一張讀不懂、遷壞（版本沒變成 to）不寫、列進 skipped，不擋其他張。"""
    from kb._lib import card_base, synthesis, turn
    count = 0
    found = _synthesis_cards(vault)
    if found is None:  # 實體清單自己的遷移已經列了就不重複
        if not any(s.get("rel") == entities.ENTITIES_POSIX for s in skipped):
            skipped.append({"rel": entities.ENTITIES_POSIX, "why": "綜合卡版面沒遷成（實體清單讀不了）"})
        return 0
    for rel, version in found:
        if isinstance(version, Exception):
            skipped.append({"rel": rel, "why": f"綜合卡版面沒遷成（{_first_line(version)}）"})
            continue
        mig = _synthesis_due(version)
        if mig is None:
            continue
        target = Path(vault) / rel
        try:
            new = mig["fn"](target.read_bytes().decode("utf-8"))  # 不經 read_text 的換行轉換，CRLF、混用照原樣
            if str(synthesis.read_front(new).get("範本版本")) != str(mig["to"]):
                raise ValueError("遷移後範本版本不對")
            turn.backup(vault, rel, turn_id, by="program")
            write_text_atomic(target, new)
        except (ValueError, OSError, UnicodeDecodeError) as e:
            skipped.append({"rel": rel, "why": f"綜合卡版面沒遷成（{_first_line(e)}）"})
            continue
        # 同 add_events：她沒處理的手改留成待處理，基準換成搬完的（搬的段不算她改）
        card_base.record_write(vault, rel, turn_id, Path(vault) / turn.VERSIONS_REL / turn_id / "files" / rel)
        count += 1
    return count


def _due(vault, m):
    stored, current = m.stored(vault), m.current()
    return (stored, current) if isinstance(stored, int) and stored < current else None


def pending(vault):
    """要遷的狀態檔 [{"rel", "from", "to"}]：存的 schema 比現在舊的；同一個 rel 有兩列要遷時併成一項（from 取舊的）。
    再接要遷的綜合卡（一張一項，from／to 是範本版本，D-523）。只讀；沒檔、讀不了的不算。"""
    out = {}
    for m in STATE_FILES:
        due = _due(vault, m)
        if due:
            was = out.get(m.rel)
            out[m.rel] = {"rel": m.rel, "from": min(due[0], was["from"]) if was else due[0], "to": due[1]}
    for rel, version in _synthesis_cards(vault) or []:
        mig = _synthesis_due(version)
        if mig:
            out.setdefault(rel, {"rel": rel, "from": int(mig["from"]), "to": int(mig["to"])})
    return list(out.values())


def _first_line(e):
    text = str(e).strip().splitlines()
    return text[0] if text else type(e).__name__


def _migrate_cards(vault, turn_id, skipped):
    """卡的檔頭遷移；回遷了幾張。表是空的不掃卡。"""
    if not CARD_HEADER_MIGRATIONS:
        return 0
    from kb._lib import turn
    found, _broken = cards.scan_cards(vault)
    count = 0
    for card in found.values():
        rel = card["path"]
        version = str(card["front"].get("範本版本"))
        mig = next((m for m in CARD_HEADER_MIGRATIONS if str(m["from"]) == version), None)
        if mig is None:
            continue
        target = Path(vault) / rel
        try:
            text = target.read_text(encoding="utf-8")
            new = mig["fn"](text)
            if str(cards.parse_front(new).get("範本版本")) != str(mig["to"]):
                raise ValueError("遷移後範本版本不對")
            turn.backup(vault, rel, turn_id)
            write_text_atomic(target, new)
        except (ValueError, OSError) as e:
            skipped.append({"rel": rel, "why": f"卡的檔頭沒遷成（{_first_line(e)}）"})
            continue
        count += 1
    return count


def apply(vault, turn_id):
    """遷移全部要遷的。回 {"migrated": [rel…]（同一個 rel 只列一次）, "skipped": [{"rel","why"}], "cards": 遷了幾張卡的檔頭,
    "notes": [句子]（補編號那列講改了哪些夾）, "left_in_output": [{"name","existing"}]（暫存區那列：同名內容不同、留在 AI 產出的）}。按列判斷：每一列照開始時看到的版本決定要不要跑。
    一個檔失敗（Skip、讀不了、寫不進去）列進 skipped，不擋其他檔。rewrite 回清單的併進 skipped；回 {"notes"} 的併進 notes。"""
    migrated, skipped, notes, left = [], [], [], []
    todo = [m for m in STATE_FILES if _due(vault, m)]
    for m in todo:
        try:
            extra = m.rewrite(vault, turn_id)
        except Skip as e:
            if {"rel": m.rel, "why": str(e)} not in skipped:  # 同一個 rel 兩列同一句只列一次
                skipped.append({"rel": m.rel, "why": str(e)})
        except (ValueError, OSError) as e:
            skipped.append({"rel": m.rel, "why": f"沒遷成（{_first_line(e)}）"})
        else:
            if m.rel not in migrated:
                migrated.append(m.rel)
            if isinstance(extra, dict):
                notes += extra.get("notes") or []
                left += extra.get("left_in_output") or []
            else:
                skipped += extra or []
    count = _migrate_cards(vault, turn_id, skipped) + _migrate_synthesis(vault, turn_id, skipped)  # 綜合卡版面併進卡數
    return {"migrated": migrated, "skipped": skipped, "cards": count, "notes": notes, "left_in_output": left,
            "streams": migrate_streams(vault, turn_id)}


CONVERTED_REL = ".system/legacy/converted.jsonl"  # 哪一句做了格式轉換（這台的，不進同步）


def converted_turn(vault, token):
    """token（回合代號或「回合#序」）是做了格式轉換的那一句 → True。"""
    turn_code = str(token).split("#", 1)[0]
    try:
        lines = (Path(vault) / CONVERTED_REL).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("turn") == turn_code:
            return True
    return False


def migrate_streams(vault, turn_id=None):
    """舊的整份 JSON 轉成這台的流水檔（多人共用 1）：每種一個檔、每筆一行 create；舊檔搬到 .system/legacy/<版本>/（不進同步，留一個版本）。
    config.json 的 agenda_export_dir 搬到 env.json 這台。回轉了的種類。
    格式轉換不可退（jacky 2026-11-05 定，#45）：記下是哪一句，kb versions undo 碰到那一句講一句退不回。"""
    from kb._lib import config as config_lib, streams
    from kb._lib.paths import read_manifest
    try:
        version = read_manifest()["version"]
    except Exception:  # noqa: BLE001
        version = "current"
    done = []
    for kind in streams.KINDS:
        try:
            if streams.migrate_kind(vault, kind, streams.legacy_version_dir(version)):
                done.append(kind)
        except (OSError, ValueError):
            continue
    try:
        config_lib.move_export_dir_to_env(vault)
    except (OSError, ValueError, AttributeError):
        pass
    if done and turn_id:
        try:
            p = Path(vault) / CONVERTED_REL
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"turn": turn_id, "version": version, "kinds": done}, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return done
