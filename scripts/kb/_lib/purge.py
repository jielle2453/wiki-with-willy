"""kb folders purge 的本體（manage 第二版第 5 點，D-174）：客戶說「不想讓 AI 看到」的夾，排除後把殘留清乾淨。

那夾的條目怎麼找：hidden 排除後清冊條目已整筆拿掉，所以從目前清冊與 versions 裡全部 inventory.json 舊版，找位置在那夾底下的條目編號，
扣掉目前還 active 的（疑義第 6 條）。還有綜合卡的「整理到」引用那些卡就不清（Blocked，先重寫）。
清的範圍（spec 七項加疑義第 5 條）：
1. versions/*/files/ 裡那些檔案卡的每個舊版刪掉；
2. 同一處的綜合卡舊版，「整理到」引用那些卡的刪掉（含這一回合重寫前剛備的那份）；
3. 同一處的 inventory.json、pending.json、agenda.json、rulings.json、findings.json 舊版拿掉那些條目與卡編號後寫回；
   待處理.md、日程.md、裁示.md 舊版從同一回合改過的 JSON 重渲染（沒有同一回合的 JSON、內容又提到那夾的就刪掉）；
   總目錄.md 舊版拿掉連到那些卡的行；moves.jsonl 搬進搬出那夾的那一行兩頭的 uri 換「（已排除）」（之後退回遇到就整個不退）；
   manifest 那幾行 action 改 purged、path 換「（已排除）」（Stop 補記的「改了」才不會帶出卡名，疑義第 4 條）；
4. .system/temp/ 裡那些條目的轉出檔（<編號>.md、<編號>_p<N>.png），office 工作區裡同檔名的副本；
5. 目前的 pending.json 那些條目刪掉、agenda.json 從那些卡抽的推測條目刪掉（確認過的留著、拿掉出處卡）、
   rulings.json 掛那些卡的拿掉卡編號（只掛那些卡的標已收回）、findings.json 依據含那些卡的推算整條刪掉；
6. 工作日誌：changed 與 note 裡指到那夾的路徑換「（已排除）」，有指到的回合 result 整欄換掉，prompt 不動（journal.scrub）；
7. ledger.tsv 與 hook-errors.log 裡指到那夾的路徑換「（已排除）」。
先改目前的狀態（備進這一回合），再清全部回合（含這一回合剛備的），最後是暫存、日誌與留痕。
"""
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from kb._lib import agenda, cards, entities, findings, inventory, journal, ledger, pending, rulings, turn
from kb._lib import sources as sources_lib
from kb._lib.io import FileLock, write_json_atomic, write_text_atomic
from kb._lib.paths import normalize

PURGED = journal.PURGED_TEXT
TEMP_POSIX = ".system/temp"
OFFICE = "office"
_STOP = "\t\n\"'「」『』（）()，、。；"
_PNG = re.compile(r"^(?P<id>.+)_p\d+\.png$")


class Blocked(Exception):
    """還有綜合卡寫著那夾的內容：args[0] 是那些綜合卡的名稱清單。"""


def folder_uri(alias, spec):
    return f"file://{alias}/{spec}"


def _in_folder(uri, prefix):
    return inventory.uri_key(uri) == inventory.uri_key(prefix) or inventory._under(uri, prefix) is not None


def _version_dirs(vault):
    root = Path(vault) / turn.VERSIONS_REL
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _loc_uris(entry):
    out = []
    for loc in entry.get("locations") or []:
        out.append(loc.get("uri", "") if isinstance(loc, dict) else str(loc))
    return out


def target(vault, alias, spec):
    """那夾的條目編號與原檔檔名：{"ids", "names"}。"""
    prefix = folder_uri(alias, spec)
    ids, names = set(), set()
    current = inventory.load(vault)["entries"]

    def take(entries):
        for e in entries if isinstance(entries, list) else []:
            if not isinstance(e, dict):
                continue
            for uri in _loc_uris(e):
                if _in_folder(uri, prefix):
                    ids.add(e.get("id"))
                    names.add(uri.rpartition("/")[2])
    take(current)
    for vdir in _version_dirs(vault):
        data = _read_json(vdir / "files" / inventory.INVENTORY_POSIX)
        if isinstance(data, dict):
            take(data.get("entries"))
    alive = {e.get("id") for e in current if e.get("status") == "active"}
    ids = {i for i in ids if isinstance(i, str) and i not in alive}
    return {"ids": ids, "names": {n for n in names if n}}


def _refs(text, ids):
    """綜合卡檔頭「整理到」有沒有引用 ids。"""
    try:
        lines = cards.parse_front(text).get("整理到")
    except ValueError:
        return False
    return isinstance(lines, list) and any(str(x).split(" ")[0] in ids for x in lines)


def blocking(vault, ids):
    """目前還引用那些卡的綜合卡（名稱）。"""
    if not ids:
        return []
    try:
        data = entities.load(vault)
    except ValueError:
        data = {"entities": []}
    by_id = {e.get("id"): e for e in data["entities"]}
    out = []
    for eid, rels in sorted(entities.synthesis_files(vault).items()):
        for rel in rels:
            try:
                text = (Path(vault) / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if _refs(text, ids):
                name = (by_id.get(eid) or {}).get("name") or rel.rsplit("/", 1)[-1][:-3]
                if name not in out:
                    out.append(name)
    return out


def _titles(vault, ids):
    """那些卡的標題：目前的卡或回合備份裡的舊版（卡已經拿掉了）。"""
    out = {}
    for cid, group in cards._scan_groups(vault)[0].items():
        if cid in ids:
            out[cid] = cards._title(group[0])
    for vdir in _version_dirs(vault):
        root = vdir / "files" / cards.CARDS_DIR
        if not root.is_dir():
            continue
        for f in root.rglob("*.md"):
            try:
                front = cards.parse_front(f.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            cid = front.get("編號")
            if cid in ids and cid not in out:
                title = front.get("標題")
                out[cid] = title if isinstance(title, str) and "（填" not in title else f.stem
    return out


def pending_detail(vault, alias, spec):
    """cards todo 的 purge_pending 一項：要重寫的綜合卡（名稱、路徑、引用了哪幾張那夾的卡與標題）。只讀。"""
    ids = target(vault, alias, spec)["ids"]
    titles = _titles(vault, ids) if ids else {}
    try:
        data = entities.load(vault)
    except ValueError:
        data = {"entities": []}
    by_id = {e.get("id"): e for e in data["entities"]}
    rows = []
    for eid, rels in sorted(entities.synthesis_files(vault).items()):
        for rel in rels:
            try:
                lines = cards.parse_front((Path(vault) / rel).read_text(encoding="utf-8")).get("整理到")
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            hit = [str(x).split(" ")[0] for x in lines or [] if str(x).split(" ")[0] in ids] if isinstance(lines, list) else []
            if hit:
                name = (by_id.get(eid) or {}).get("name") or rel.rsplit("/", 1)[-1][:-3]
                rows.append({"name": name, "path": rel, "cards": [{"id": c, "title": titles.get(c)} for c in hit]})
    return {"name": f"{alias}/{spec}", "alias": alias, "spec": spec, "synthesis": rows}


def masker(vault, alias, spec, root, names):
    """回 mask(text)：指到那夾的路徑（uri、別名/相對、磁碟上的絕對路徑，兩種斜線、大小寫不分）連同後面的檔名換「（已排除）」，
    那夾原檔的檔名單獨出現也換。"""
    forms = {folder_uri(alias, spec), f"{alias}/{spec}", str(root)}
    try:
        real = os.path.realpath(str(root))
        forms |= {real, real.replace("\\", "/"), real.replace("/", "\\")}
    except (OSError, ValueError):
        pass
    forms |= {f.replace("/", "\\") for f in list(forms) if not f.startswith("file://")}
    forms = sorted((f for f in forms if f), key=len, reverse=True)
    stop = re.escape(_STOP)
    rx = re.compile("(?:" + "|".join(re.escape(f) for f in forms) + rf")(?=$|[/\\{stop}\s])(?:[/\\][^{stop}]*)?",
                    re.IGNORECASE)
    words = sorted((n for n in names if len(n) >= 2), key=len, reverse=True)
    wx = re.compile("|".join(re.escape(n) for n in words), re.IGNORECASE) if words else None

    def mask(text):
        if not isinstance(text, str):
            return text
        out = rx.sub(PURGED, text)
        return wx.sub(PURGED, out) if wx else out
    return mask


# ---------- 拿掉 ids（目前的狀態檔與舊版共用） ----------

def drop_inventory(data, ids, prefix):
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return False
    keep, changed = [], False
    for e in data["entries"]:
        if isinstance(e, dict) and e.get("id") in ids:
            changed = True
            continue
        if isinstance(e, dict) and any(_in_folder(u, prefix) for u in _loc_uris(e)):
            e["locations"] = [loc for loc in e.get("locations") or []
                              if not _in_folder(loc.get("uri", "") if isinstance(loc, dict) else str(loc), prefix)]
            changed = True
            if not e["locations"]:
                continue
        keep.append(e)
    data["entries"] = keep
    return changed


def drop_pending(data, ids):
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return 0
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if not (isinstance(i, dict) and set(i.get("file_ids") or []) & ids)]
    return before - len(data["items"])


def drop_agenda(data, ids):
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return False
    keep, changed = [], False
    for it in data["items"]:
        origin = it.get("origin") if isinstance(it, dict) else None
        if isinstance(origin, dict) and origin.get("card") in ids:
            changed = True
            if it.get("source") == "inferred":
                continue
            it["origin"] = None
        prop = it.get("proposal") if isinstance(it, dict) else None
        if isinstance(prop, dict) and prop.get("card") in ids:
            it.pop("proposal", None)
            changed = True
        keep.append(it)
    data["items"] = keep
    return changed


def drop_rulings(data, ids):
    if not isinstance(data, dict) or not isinstance(data.get("rulings"), list):
        return False
    changed = False
    for r in data["rulings"]:
        hooked = r.get("cards") or [] if isinstance(r, dict) else []
        left = [c for c in hooked if not (isinstance(c, dict) and c.get("id") in ids)]
        if len(left) == len(hooked):
            continue
        r["cards"], changed = left, True
        if not left and not r.get("entities") and r.get("status") == "active":
            r["status"], r["revoked"] = "revoked", date.today().isoformat()
    return changed


def drop_findings(data, ids):
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return 0
    before = len(data["findings"])
    data["findings"] = [c for c in data["findings"]
                        if not (isinstance(c, dict) and {b.get("id") for b in c.get("basis") or [] if isinstance(b, dict)} & ids)]
    return before - len(data["findings"])


def _pending_md(data):
    with tempfile.TemporaryDirectory() as d:
        pending.render(d, data)
        return (Path(d) / pending.MD_POSIX).read_text(encoding="utf-8")


# ---------- 清 ----------

def _current_state(vault, ids, turn_id, counts):
    """第 5 點：目前的狀態檔（各自的鎖、先備進這一回合）；還在的檔案卡也拿掉。"""
    def p_change(items):
        data = {"items": items}
        n = drop_pending(data, ids)
        items[:] = data["items"]
        return n
    if (Path(vault) / pending.PENDING_POSIX).exists():
        pending._edit(vault, turn_id, p_change)
    if (Path(vault) / agenda.AGENDA_POSIX).exists():
        agenda._mutate(vault, turn_id, lambda data: (None, drop_agenda(data, ids)))
    if (Path(vault) / rulings.RULINGS_POSIX).exists():
        rulings._mutate(vault, turn_id, lambda data: (None, drop_rulings(data, ids)))
    if (Path(vault) / findings.FINDINGS_POSIX).exists():
        def f_change(data):
            n = drop_findings(data, ids)
            return n, bool(n)
        counts["findings"] += findings._mutate(vault, turn_id, f_change)
    for cid, group in cards._scan_groups(vault)[0].items():
        if cid in ids:
            for c in group:
                cards.remove_card(vault, c["path"], turn_id)
            counts["card_ids"].add(cid)


def _is_synthesis(rel):
    parts = rel.split("/")
    return (len(parts) == 3 and parts[2].casefold().endswith(".md") and not parts[0].startswith(".")
            and parts[0].casefold() not in inventory.VAULT_RESERVED_DIRS)


def _card_id(path):
    try:
        cid = cards.parse_front(path.read_text(encoding="utf-8")).get("編號")
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return cid if isinstance(cid, str) else None


JSON_DROPS = {
    inventory.INVENTORY_POSIX: lambda data, ids, prefix: drop_inventory(data, ids, prefix),
    pending.PENDING_POSIX: lambda data, ids, prefix: bool(drop_pending(data, ids)),
    agenda.AGENDA_POSIX: lambda data, ids, prefix: drop_agenda(data, ids),
    rulings.RULINGS_POSIX: lambda data, ids, prefix: drop_rulings(data, ids),
    findings.FINDINGS_POSIX: lambda data, ids, prefix: bool(drop_findings(data, ids)),
}


def _scrub_version(vault, vdir, ids, prefix, mask, counts):
    """一個回合夾：卡與綜合卡舊版刪、狀態檔舊版改寫、md 舊版重渲染、總目錄舊版拿掉那些行、manifest 標 purged、moves.jsonl 遮。"""
    files = vdir / "files"
    manifest = vdir / "manifest.jsonl"
    shorts = {cards.short_id(i) for i in ids}
    with FileLock(manifest):
        try:
            lines = manifest.read_text(encoding="utf-8").splitlines() if manifest.is_file() else []
        except (OSError, UnicodeDecodeError):
            lines = []
        rows = []
        for ln in lines:
            try:
                rows.append(json.loads(ln) if ln.strip() else None)
            except ValueError:
                rows.append(None)
        dirty, datas = False, {}
        for row in rows:
            if not isinstance(row, dict) or row.get("action") == "purged" or not isinstance(row.get("path"), str):
                continue
            rel = row["path"]
            f = files / rel
            if not f.is_file():
                continue
            gone = False
            if rel.startswith(cards.CARDS_DIR + "/") and rel.endswith(".md"):
                cid = _card_id(f)
                if cid in ids:
                    counts["card_ids"].add(cid)
                    gone = True
            elif _is_synthesis(rel):
                try:
                    gone = _refs(f.read_text(encoding="utf-8"), ids)
                except (OSError, UnicodeDecodeError):
                    gone = False
            elif rel in JSON_DROPS:
                data = _read_json(f)
                datas[rel] = data
                if data is not None and JSON_DROPS[rel](data, ids, prefix):
                    write_json_atomic(f, data)
                    counts["backups"] += 1
            elif rel == cards.INDEX_POSIX:
                text = f.read_text(encoding="utf-8")
                kept = [ln for ln in text.splitlines(keepends=True) if not any(f"-{s}.md)" in ln for s in shorts)]
                if len(kept) != len(text.splitlines(keepends=True)):
                    write_text_atomic(f, "".join(kept))
                    counts["backups"] += 1
            if gone:
                f.unlink()
                row["action"], row["path"] = "purged", PURGED
                counts["backups"] += 1
                dirty = True
        for json_rel, md_rel, render in ((pending.PENDING_POSIX, pending.MD_POSIX, _pending_md),
                                         (agenda.AGENDA_POSIX, agenda.MD_POSIX,
                                          lambda data: agenda.render_text(vault, data)),
                                         (rulings.RULINGS_POSIX, rulings.MD_POSIX,
                                          lambda data: rulings.render_text(data, agenda.card_titles(vault)))):
            md = files / md_rel
            if not md.is_file():
                continue
            text = md.read_text(encoding="utf-8")
            data = datas.get(json_rel)
            if isinstance(data, dict):
                try:
                    new = render(data)
                except Exception:  # noqa: BLE001  舊版的 JSON 形狀不對：照下一條（提到就刪）
                    new = None
                if new is not None:
                    if new != text:
                        write_text_atomic(md, new)
                        counts["backups"] += 1
                    continue
            if mask(text) != text:
                md.unlink()
                for row in rows:
                    if isinstance(row, dict) and row.get("path") == md_rel:
                        row["action"], row["path"] = "purged", PURGED
                counts["backups"] += 1
                dirty = True
        if dirty:
            write_text_atomic(manifest, "".join(
                (json.dumps(row, ensure_ascii=False) if row is not None else ln) + "\n"
                for row, ln in zip(rows, lines) if ln.strip()))
        moves = vdir / "moves.jsonl"
        if moves.is_file():
            text = moves.read_text(encoding="utf-8")
            out = []
            for ln in text.splitlines():
                try:
                    row = json.loads(ln)
                except ValueError:
                    out.append(ln)
                    continue
                if isinstance(row, dict) and any(isinstance(row.get(k), str) and _in_folder(row[k], prefix)
                                                 for k in ("from", "to")):
                    for k in ("from", "to"):  # 搬進搬出那夾的一行兩頭都遮：另一頭也帶著檔名
                        if row.get(k):
                            row[k] = PURGED
                out.append(json.dumps(row, ensure_ascii=False))
            new = "".join(x + "\n" for x in out)
            if new != text:
                write_text_atomic(moves, new)


def _temp(vault, ids, names):
    """.system/temp 的轉出檔與 office 工作區的同名副本。回刪了幾份。"""
    root = Path(vault) / TEMP_POSIX
    if not root.is_dir() or os.path.islink(root):
        return 0
    n = 0
    for p in root.iterdir():
        m = _PNG.match(p.name)
        stem = p.name[:-3] if p.name.endswith(".md") else (m.group("id") if m else None)
        if p.is_file() and stem in ids:
            p.unlink()
            n += 1
    office = root / OFFICE
    if office.is_dir() and not os.path.islink(office):
        low = {x.casefold() for x in names}
        for dirpath, dirnames, filenames in os.walk(office):
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
            for f in filenames:
                if f.casefold() in low:
                    os.remove(os.path.join(dirpath, f))
                    n += 1
    return n


def _scrub_text(vault, rel, mask):
    p = Path(vault) / rel
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    new = "\n".join(mask(ln) for ln in text.split("\n"))
    if new != text:
        write_text_atomic(p, new)


def purge(vault, alias, spec, root, turn_id):
    """清那夾的殘留（呼叫端持 sources.json 的鎖、已確認是 pending 的 hidden 排除）。還有綜合卡引用就丟 Blocked。
    回 {"cards","backups","temp","journal","findings"}（數字）。"""
    got = target(vault, alias, spec)
    ids, names = got["ids"], got["names"]
    names_blocking = blocking(vault, ids)
    if names_blocking:
        raise Blocked(names_blocking)
    counts = {"card_ids": set(), "backups": 0, "temp": 0, "journal": 0, "findings": 0}
    prefix = folder_uri(alias, spec)
    mask = masker(vault, alias, spec, root, names)
    _current_state(vault, ids, turn_id, counts)
    for vdir in _version_dirs(vault):
        _scrub_version(vault, vdir, ids, prefix, mask, counts)
    counts["temp"] = _temp(vault, ids, names)
    counts["journal"] = journal.scrub(vault, mask)
    _scrub_text(vault, ledger.LEDGER_REL, mask)
    _scrub_text(vault, ledger.ERROR_LOG_REL, mask)
    return {"cards": len(counts["card_ids"]), "backups": counts["backups"], "temp": counts["temp"],
            "journal": counts["journal"], "findings": counts["findings"], "ids": sorted(ids)}


def mark_done(vault, alias, spec, turn_id):
    """那條排除的 purge 改成今天（呼叫端持 sources.json 的鎖）；先備進回合。回有沒有寫。"""
    raw = sources_lib.load_raw(vault)
    for src in raw["sources"]:
        if str(src.get("alias", "")).casefold() != alias.casefold():
            continue
        for ex in src.get("excludes") or []:
            if ex.get("spec") == spec and ex.get("purge") == sources_lib.PURGE_PENDING:
                ex["purge"] = date.today().isoformat()
                turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
                write_json_atomic(Path(vault) / sources_lib.SOURCES_REL, raw)
                return True
    return False


def find(vault, sources, target_text, cwd):
    """purge 的對象：「別名/相對」的寫法或路徑，對到 sources 的一條排除。回 {"alias","spec","root","kind","purge"} 或 None。"""
    text = str(target_text).strip().replace("\\", "/").strip("/")
    for src in sources.entries:
        for ex in src["excludes"]:
            if f"{src['alias']}/{ex['spec']}".casefold() == text.casefold():
                return dict(ex, alias=src["alias"])
    path = normalize(str(target_text), str(cwd))
    for src in sources.entries:
        for ex in src["excludes"]:
            if ex["root"] == path:
                return dict(ex, alias=src["alias"])
    return None
