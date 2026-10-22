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
第一輪修（測試報告）：範圍（卡編號、office 副本檔名的雜湊）第一次就記進那條排除（purge_ids、purge_names），
重跑照記下的清（舊版清過之後就算不出來了）；哪一項沒清掉（檔開著、鎖等不到）就留著 pending、回還剩什麼，全部清完才標完成、
拿掉記的範圍。遮罩只換完整路徑（uri、別名/相對、檔案索引/別名/相對、磁碟上的絕對路徑），前後都要是路徑的邊界，不做子字串。
"""
import hashlib
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


def _is_reparse(path):
    """存在而且是連結或 junction；不存在回 False（只有 moves.jsonl、沒有 files/ 的回合夾照掃，第三輪修）。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & inventory.REPARSE_POINT)


def _version_dirs(vault):
    """versions 底下的回合夾；連結與 junction 跳過、不跟（第二輪修：跟進去會刪到庫外的檔）。"""
    root = Path(vault) / turn.VERSIONS_REL
    if not root.is_dir() or _is_reparse(root):
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not _is_reparse(p) and not _is_reparse(p / "files"))


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


def name_hash(name):
    """office 副本檔名記雜湊，不把那夾的檔名寫進 sources.json。"""
    return hashlib.sha256(str(name).casefold().encode("utf-8")).hexdigest()[:16]


def _row(vault, alias, spec):
    """sources.json 裡那條排除（原始 dict）；沒有回 {}。"""
    for src in sources_lib.load_raw(vault)["sources"]:
        if str(src.get("alias", "")).casefold() == alias.casefold():
            for ex in src.get("excludes") or []:
                if ex.get("spec") == spec:
                    return ex
    return {}


def scope(vault, alias, spec):
    """這次要清的範圍：算得出來的加上記在那條排除裡的。回 {"ids", "hashes"}。"""
    got = target(vault, alias, spec)
    row = _row(vault, alias, spec)
    return {"ids": got["ids"] | {i for i in row.get("purge_ids") or [] if isinstance(i, str)},
            "hashes": {name_hash(n) for n in got["names"]} | {h for h in row.get("purge_names") or [] if isinstance(h, str)}}


def _write_row(vault, alias, spec, turn_id, change):
    """改那條排除（呼叫端持 sources.json 的鎖）：change(row) 回真才備份、寫。回有沒有寫。"""
    raw = sources_lib.load_raw(vault)
    for src in raw["sources"]:
        if str(src.get("alias", "")).casefold() != alias.casefold():
            continue
        for ex in src.get("excludes") or []:
            if ex.get("spec") == spec and change(ex):
                turn.backup(vault, sources_lib.SOURCES_REL, turn_id)
                write_json_atomic(Path(vault) / sources_lib.SOURCES_REL, raw)
                return True
    return False


def record_scope(vault, alias, spec, sc, turn_id):
    def change(ex):
        ids, names = sorted(sc["ids"]), sorted(sc["hashes"])
        if ex.get("purge_ids") == ids and ex.get("purge_names") == names:
            return False
        ex["purge_ids"], ex["purge_names"] = ids, names
        return True
    return _write_row(vault, alias, spec, turn_id, change)


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
    ids = scope(vault, alias, spec)["ids"]
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


def masker(vault, alias, spec, root):
    """回 mask(text)：指到那夾的完整路徑（uri、別名/相對、檔案索引/別名/相對、磁碟上的絕對路徑，兩種斜線、大小寫不分）
    連同後面的檔名換「（已排除）」。前面要是開頭或分隔的字、後面要是結尾或路徑分隔，不換子字串（第一輪修）。"""
    forms = {folder_uri(alias, spec), f"{alias}/{spec}", f"{cards.CARDS_DIR}/{alias}/{spec}", str(root)}
    try:
        real = os.path.realpath(str(root))
        forms |= {real, real.replace("\\", "/"), real.replace("/", "\\")}
    except (OSError, ValueError):
        pass
    forms |= {f.replace("/", "\\") for f in list(forms) if not f.startswith("file://")}
    forms = sorted((f for f in forms if f), key=len, reverse=True)
    stop = re.escape(_STOP)
    # 都是含分隔符的完整路徑：左邊不看邊界（「已搬好：客戶案件/私人/…」也要遮，第二輪修），右邊要是結尾或路徑分隔
    rx = re.compile("(?:" + "|".join(re.escape(f) for f in forms)
                    + rf")(?=$|[/\\{stop}\s])(?:[/\\][^{stop}]*)?", re.IGNORECASE)

    def mask(text):
        if not isinstance(text, str):
            return text
        return rx.sub(PURGED, text)
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
    def f_change(data):
        n = drop_findings(data, ids)
        return n, bool(n)
    parts = ((pending.PENDING_POSIX, lambda: pending._edit(vault, turn_id, p_change)),
             (agenda.AGENDA_POSIX, lambda: agenda._mutate(vault, turn_id, lambda data: (None, drop_agenda(data, ids)))),
             (rulings.RULINGS_POSIX, lambda: rulings._mutate(vault, turn_id, lambda data: (None, drop_rulings(data, ids)))),
             (findings.FINDINGS_POSIX, lambda: counts.__setitem__(
                 "findings", counts["findings"] + findings._mutate(vault, turn_id, f_change))))
    for rel, act in parts:
        if (Path(vault) / rel).exists():
            try:
                act()
            except (OSError, ValueError):
                counts["left"]["state"] += 1
    for cid, group in cards._scan_groups(vault)[0].items():
        if cid in ids:
            for c in group:
                try:
                    cards.remove_card(vault, c["path"], turn_id)
                except OSError:
                    counts["left"]["state"] += 1
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


def _under_files(files, f):
    try:
        real, base = os.path.realpath(str(f)), os.path.realpath(str(files))
    except (OSError, ValueError):
        return False
    return os.path.normcase(real).startswith(os.path.normcase(base).rstrip("\\/") + os.sep)


def _is_link(path):
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & inventory.REPARSE_POINT)


def _scrub_version(vault, vdir, ids, prefix, mask, counts):
    """一個回合夾；鎖等不到、寫不了的那幾份算進還剩的。"""
    try:
        _scrub_version_locked(vault, vdir, ids, prefix, mask, counts)
    except OSError:
        counts["left"]["backups"] += 1


def _scrub_version_locked(vault, vdir, ids, prefix, mask, counts):
    """一個回合夾：卡與綜合卡舊版刪、狀態檔舊版改寫、md 舊版重渲染、總目錄舊版拿掉那些行、manifest 標 purged、moves.jsonl 遮。
    每一份各自 try（寫不了的算進還剩的），manifest 不管中途怎樣都在最後寫（第二輪修：刪了檔、帳卻沒改，重跑就認不出來）。"""
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
        state = {"dirty": False}
        datas = {}
        try:
            _scrub_rows(vault, files, rows, ids, prefix, mask, counts, shorts, datas, state)
            _scrub_md(vault, files, rows, mask, counts, datas, state)
        finally:
            if state["dirty"]:
                try:
                    write_text_atomic(manifest, "".join(
                        (json.dumps(row, ensure_ascii=False) if row is not None else ln) + "\n"
                        for row, ln in zip(rows, lines) if ln.strip()))
                except OSError:
                    counts["left"]["backups"] += 1
        _scrub_moves(vdir, prefix, counts)


_NODE = re.compile(r"^( *)- (.*?)\r?\n?$")


def _drop_index_node(lines, path):
    """總目錄（巢狀清單，兩個空白一層）裡那夾的樹節點連同底下全部拿掉（第三輪修：「 - 私人」那行也帶夾名）。"""
    want = [p.casefold() for p in path]
    out, stack, skip_at = [], [], None
    for ln in lines:
        m = _NODE.match(ln)
        if not m:
            skip_at = None
            out.append(ln)
            continue
        depth = len(m.group(1)) // 2
        if skip_at is not None:
            if depth > skip_at:
                continue
            skip_at = None
        del stack[depth:]
        label = m.group(2)
        stack.append(label.casefold())
        if not label.startswith("[") and stack == want:
            skip_at = depth
            continue
        out.append(ln)
    return out


def _drop_empty_nodes(lines):
    """拿掉卡之後空掉的夾節點（不是連結、底下沒有更深一層的行）。"""
    while True:
        out = []
        for i, ln in enumerate(lines):
            m = _NODE.match(ln)
            if m and not m.group(2).startswith("["):
                nxt = next((x for x in lines[i + 1:] if x.strip()), "")
                n = _NODE.match(nxt)
                if not n or len(n.group(1)) <= len(m.group(1)):
                    continue
            out.append(ln)
        if out == lines:
            return out
        lines = out


def _card_row(rel, shorts):
    """manifest 這行是那些卡的路徑嗎（檔已經不在了也認得：檔名結尾 -<短編號>.md）。"""
    return rel.startswith(cards.CARDS_DIR + "/") and any(rel.endswith(f"-{s}.md") for s in shorts)


def _scrub_rows(vault, files, rows, ids, prefix, mask, counts, shorts, datas, state):
    alias, spec = inventory.split_uri(prefix)
    card_dir = f"{cards.CARDS_DIR}/{alias}/{spec}/".casefold()
    for row in rows:
        if not isinstance(row, dict) or row.get("action") == "purged" or not isinstance(row.get("path"), str):
            continue
        rel = row["path"]
        f = files / rel
        if not _under_files(files, f):
            continue  # 紀錄不信（B-080）：解出來不在這個回合的 files/ 底下就不碰
        if _card_row(rel, shorts) or rel.casefold().startswith(card_dir):
            # 那些卡的每一行（create 也算）都換掉：路徑本身就帶夾名與卡標題（第三輪修）
            try:
                if f.is_file():
                    cid = _card_id(f)
                    if cid in ids:
                        counts["card_ids"].add(cid)
                    f.unlink()
                    counts["backups"] += 1
            except OSError:
                counts["left"]["backups"] += 1
                continue
            row["action"], row["path"] = "purged", PURGED
            state["dirty"] = True
            continue
        if not f.is_file():
            continue
        try:
            _scrub_row(row, rel, f, ids, prefix, counts, shorts, datas, state, mask)
        except (OSError, UnicodeDecodeError):
            counts["left"]["backups"] += 1


def _scrub_row(row, rel, f, ids, prefix, counts, shorts, datas, state, mask=None):
    """manifest 一行：那些卡的舊版、引用它們的綜合卡舊版刪掉並標 purged；狀態檔舊版拿掉那些條目；總目錄舊版拿掉那些行。"""
    gone = False
    if rel.startswith(cards.CARDS_DIR + "/") and rel.endswith(".md"):
        cid = _card_id(f)
        if cid in ids:
            counts["card_ids"].add(cid)
            gone = True
        elif mask is not None:  # 別張卡的舊版：「原檔」行等文字裡那夾的路徑換掉（第五輪；第六輪起只換路徑）
            _mask_file(f, mask, counts)
    elif _is_synthesis(rel):
        gone = _refs(f.read_text(encoding="utf-8"), ids)
    elif rel in JSON_DROPS:
        data = _read_json(f)
        datas[rel] = data
        if data is not None and JSON_DROPS[rel](data, ids, prefix):
            write_json_atomic(f, data)
            counts["backups"] += 1
    elif rel == cards.INDEX_POSIX:
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        alias, spec = inventory.split_uri(prefix)
        kept = _drop_index_node(lines, [alias] + spec.split("/"))
        kept = [ln for ln in kept if not any(f"-{s}.md)" in ln for s in shorts)]
        kept = _drop_empty_nodes(kept)
        if kept != lines:
            write_text_atomic(f, "".join(kept))
            counts["backups"] += 1
    elif mask is not None and rel != sources_lib.SOURCES_REL and f.suffix.casefold() in TEXT_SUFFIXES \
            and not any(rel == md for _j, md, _r in MD_RENDERS):
        # 別的檔的舊版（其他文字檔）：文字裡那夾的路徑換掉；字詞（編號、標題）只套工作日誌（第六輪）
        _mask_file(f, mask, counts)
    if gone:
        f.unlink()
        row["action"], row["path"] = "purged", PURGED
        counts["backups"] += 1
        state["dirty"] = True


MD_RENDERS = ((pending.PENDING_POSIX, pending.MD_POSIX, lambda vault, data: _pending_md(data)),
              (agenda.AGENDA_POSIX, agenda.MD_POSIX, lambda vault, data: agenda.render_text(vault, data)),
              (rulings.RULINGS_POSIX, rulings.MD_POSIX,
               lambda vault, data: rulings.render_text(data, agenda.card_titles(vault))))


def _scrub_md(vault, files, rows, mask, counts, datas, state):
    """待處理.md、日程.md、裁示.md 的舊版：同一回合有 JSON 舊版就照改過的重渲染，沒有而內容指到那夾就刪掉、標 purged。"""
    for json_rel, md_rel, render in MD_RENDERS:
        md = files / md_rel
        if not _under_files(files, md) or not md.is_file():
            continue  # files/ 底下巢狀的 junction 不跟（第三輪修）
        try:
            _scrub_one_md(vault, md, md_rel, json_rel, render, rows, mask, counts, datas, state)
        except (OSError, UnicodeDecodeError):
            counts["left"]["backups"] += 1


def _scrub_one_md(vault, md, md_rel, json_rel, render, rows, mask, counts, datas, state):
    text = md.read_text(encoding="utf-8")
    data = datas.get(json_rel)
    if isinstance(data, dict):
        try:
            new = render(vault, data)
        except Exception:  # noqa: BLE001  舊版的 JSON 形狀不對：照下一條（提到就刪）
            new = None
        if new is not None:
            if new != text:
                write_text_atomic(md, new)
                counts["backups"] += 1
            return
    if mask(text) != text:
        md.unlink()
        for row in rows:
            if isinstance(row, dict) and row.get("path") == md_rel:
                row["action"], row["path"] = "purged", PURGED
        counts["backups"] += 1
        state["dirty"] = True


def _scrub_moves(vdir, prefix, counts):
    try:
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
    except (OSError, UnicodeDecodeError):
        counts["left"]["backups"] += 1


def _temp(vault, ids, hashes, counts):
    """.system/temp 的轉出檔與 office 工作區的同名副本（檔名照記的雜湊比）。刪不掉的算進還剩的。回刪了幾份。"""
    root = Path(vault) / TEMP_POSIX
    if not root.is_dir() or _is_link(root):
        return 0
    n = 0
    hits = []
    for p in root.iterdir():
        m = _PNG.match(p.name)
        stem = p.name[:-3] if p.name.endswith(".md") else (m.group("id") if m else None)
        if stem in ids and p.is_file():
            hits.append(p)
    office = root / OFFICE
    if office.is_dir() and not _is_link(office):
        for dirpath, dirnames, filenames in os.walk(office):
            dirnames[:] = [d for d in dirnames if not _is_link(os.path.join(dirpath, d))]  # 連結與 junction 不跟
            hits += [Path(dirpath) / f for f in filenames if name_hash(f) in hashes]
    for p in hits:
        try:
            p.unlink()
            n += 1
        except OSError:
            counts["left"]["temp"] += 1
    return n


def _scrub_text(vault, rel, mask):
    """ledger.tsv、hook-errors.log：鎖住讀改寫（hook 追加時不掉行）。檔不在回 True；寫不了回 False。"""
    p = Path(vault) / rel
    if not p.exists():
        return True
    try:
        with FileLock(p):
            text = p.read_text(encoding="utf-8")
            new = "\n".join(mask(ln) for ln in text.split("\n"))
            if new != text:
                write_text_atomic(p, new)
    except (OSError, UnicodeDecodeError):
        return False
    return True


TEXT_SUFFIXES = (".md", ".txt", ".json", ".jsonl", ".tsv", ".csv")
PENDING_POSIX = ".system/purge-pending.json"  # 還沒清完的那幾條排除的卡標題（不寫進 sources.json，清完刪掉；第六輪）


def load_pending(vault):
    try:
        with open(Path(vault) / PENDING_POSIX, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"schema_version": 1, "items": {}}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"schema_version": 1, "items": {}}
    return data


def recorded_titles(vault, alias, spec):
    item = load_pending(vault)["items"].get(f"{alias}/{spec}") or {}
    return {t for t in item.get("titles") or [] if isinstance(t, str)}


def record_titles(vault, alias, spec, titles, turn_id, undo=None):
    """卡標題跟範圍一起留到清完（重跑時卡與舊版可能已經刪了，標題就取不到）。有變才備進回合、寫。"""
    data = load_pending(vault)
    key = f"{alias}/{spec}"
    have = set((data["items"].get(key) or {}).get("titles") or [])
    want = sorted(have | {t for t in titles if t})
    if want == sorted(have) and key in data["items"]:
        return False
    data["items"][key] = {"titles": want}
    if undo is not None:
        undo.file(PENDING_POSIX)
    turn.backup(vault, PENDING_POSIX, turn_id)
    write_json_atomic(Path(vault) / PENDING_POSIX, data)
    return True


def clear_titles(vault, alias, spec, turn_id):
    """清完：拿掉那一條；空了整個檔刪掉（先備進回合）。"""
    p = Path(vault) / PENDING_POSIX
    data = load_pending(vault)
    if data["items"].pop(f"{alias}/{spec}", None) is None:
        return
    if data["items"]:
        turn.backup(vault, PENDING_POSIX, turn_id)
        write_json_atomic(p, data)
    elif p.exists():
        turn.backup(vault, PENDING_POSIX, turn_id, action="delete")
        p.unlink()


def word_titles(vault, titles):
    """當字詞比的卡標題：3 字以上，而且不是設定檔的卡型名、不是實體的名稱或別名（第六輪：「報價單」「信義王」這種標題
    當字詞比會把無關的回合整欄換掉，這種只靠編號與路徑認）。"""
    from kb._lib import profile
    skip = set()
    try:
        prof = profile.load(vault) or {}
        skip |= {str(c.get("name", "")).casefold() for c in prof.get("card_types") or []}
    except ValueError:
        pass
    try:
        for e in entities.load(vault)["entities"]:
            skip |= {str(w).casefold() for w in [e.get("name")] + list(e.get("aliases") or []) if w}
    except ValueError:
        pass
    return {t for t in titles if t and len(t) >= TITLE_MIN and t.casefold() not in skip}


def _read_any(f):
    """先 UTF-8、再 cp950；都解不開回 (None, None)（第六輪：讀不懂的備份跳過，不算沒清完）。"""
    raw = f.read_bytes()
    for enc in ("utf-8", "cp950"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return None, None


def _write_enc(f, text, enc):
    if enc == "utf-8":
        write_text_atomic(f, text)
        return
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_bytes(text.encode(enc))
    os.replace(tmp, f)


def _mask_file(f, mask, counts):
    text, enc = _read_any(f)
    if text is None:
        counts["undecodable"] += 1
        return
    new = mask(text)
    if new != text:
        _write_enc(f, new, enc)
        counts["backups"] += 1
TITLE_MIN = 3  # 太短的標題（兩個字）當字詞比對會誤遮一大片，不比


def word_masker(path_mask, ids, titles):
    """路徑之外，那些卡的編號與卡標題也換「（已排除）」（第五輪：回答裡列了卡標題、編號的回合要認得出來）。"""
    words = sorted({i for i in ids if i} | {t for t in titles if t and len(t) >= TITLE_MIN}, key=len, reverse=True)
    wx = re.compile("|".join(re.escape(w) for w in words)) if words else None

    def mask(text):
        if not isinstance(text, str):
            return text
        out = path_mask(text)
        return wx.sub(PURGED, out) if wx else out
    return mask


LEFT_WORDS = (("state", "狀態檔 {n} 個"), ("backups", "備份 {n} 份"), ("temp", "暫存 {n} 份"),
              ("journal", "工作日誌 {n} 個月"), ("logs", "留痕 {n} 個"))


def remaining_text(left):
    return "還沒清完：" + "、".join(w.format(n=left[k]) for k, w in LEFT_WORDS if left.get(k))


def purge(vault, alias, spec, root, turn_id):
    """清那夾的殘留（呼叫端持 sources.json 的鎖、已確認是 pending 的 hidden 排除）。還有綜合卡引用就丟 Blocked。
    先把範圍記進那條排除，再清；回 {"cards","backups","temp","journal","findings","remaining"}，remaining 有東西就是沒清完
    （呼叫端不標完成）。"""
    sc = scope(vault, alias, spec)
    ids = sc["ids"]
    names_blocking = blocking(vault, ids)
    if names_blocking:
        raise Blocked(names_blocking)
    record_scope(vault, alias, spec, sc, turn_id)
    titles = recorded_titles(vault, alias, spec) | (set(_titles(vault, ids).values()) if ids else set())
    record_titles(vault, alias, spec, titles, turn_id)  # 重跑時卡與舊版已經刪了，標題靠記下的
    counts = {"card_ids": set(), "backups": 0, "temp": 0, "journal": 0, "findings": 0, "undecodable": 0,
              "left": {k: 0 for k, _w in LEFT_WORDS}}
    prefix = folder_uri(alias, spec)
    mask = masker(vault, alias, spec, root)  # 備份、ledger、hook-errors 只換路徑（第六輪）
    words = word_masker(mask, ids, word_titles(vault, titles))  # 工作日誌的 result、note 另比編號與標題
    _current_state(vault, ids, turn_id, counts)
    for vdir in _version_dirs(vault):
        _scrub_version(vault, vdir, ids, prefix, mask, counts)
    counts["temp"] = _temp(vault, ids, sc["hashes"], counts)
    try:
        counts["journal"] = journal.scrub(vault, words, mention=lambda text: words(text) != text)
    except OSError:
        counts["left"]["journal"] += 1
    for rel in (ledger.LEDGER_REL, ledger.ERROR_LOG_REL):
        if not _scrub_text(vault, rel, mask):
            counts["left"]["logs"] += 1
    return {"cards": len(counts["card_ids"]), "backups": counts["backups"], "temp": counts["temp"],
            "journal": counts["journal"], "findings": counts["findings"], "undecodable": counts["undecodable"],
            "remaining": {k: v for k, v in counts["left"].items() if v}}


def mark_done(vault, alias, spec, turn_id):
    """全部清完：那條排除的 purge 改成今天、拿掉記的範圍（呼叫端持 sources.json 的鎖）；先備進回合。回有沒有寫。"""
    def change(ex):
        if ex.get("purge") != sources_lib.PURGE_PENDING:
            return False
        ex["purge"] = date.today().isoformat()
        ex.pop("purge_ids", None)
        ex.pop("purge_names", None)
        return True
    done = _write_row(vault, alias, spec, turn_id, change)
    clear_titles(vault, alias, spec, turn_id)
    return done


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
