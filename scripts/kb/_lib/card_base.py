"""她手改了卡，系統跟著更新（D-270）：記程式與 Claude 最後寫的那一版（基準），再跟卡現在的內文比。

只比內文（檔頭 --- 之後）；檔頭的標籤行走 tag_edit（D-505），其他檔頭欄位是程式的。
基準記在 .system/card_base.json：{"schema_version": 1, "cards": {相對路徑: {"sha256", "body", "at"}}}；讀不到當沒有紀錄。
卡 = 檔案索引/ 底下的 .md（檔案卡）與 <型別夾>/<名稱>/<名稱>.md、檔頭有 型別 與 名稱 的綜合卡。

誰寫卡誰更新基準：output_log 的 Z5、Z1 驗過之後 record；rename／cards sync 搬卡 rekey；刪卡 forget；退回 after_revert。
記不了只記 log（record_safe 吞例外），不擋原本的寫入。沒有紀錄的卡（第一次用到、別的路徑長出來的卡）：
edits 把現在的內容當基準補上、不報手改；卡搬了但沒人 rekey（退回夾名）時，內文跟一筆沒對應檔的紀錄一樣就認作同一張。
"""
import difflib
import hashlib
from datetime import datetime
from pathlib import Path

from kb._lib import cards, inventory
from kb._lib.io import FileLock, read_json, write_json_atomic

BASE_POSIX = ".system/card_base.json"
SCHEMA = 1


def strip_files(body):
    """綜合卡「相關檔」那段（synthesis.file_section，程式的快取，放寬丙 7）拿掉：它不是她或 Claude 寫的，不算內文。
    記號要整行才算（同 synthesis._span，裁定 24）。後面接的是「## 」段（D-523：紀事在相關檔下面）就空一行接，
    跟 with_file_section 補上那段之前一樣（D-523 疑義裁定 1）；接的是她打的字照舊直接接，不多算一個空行。"""
    from kb._lib import synthesis
    span = synthesis._span(body)
    if span is None:
        return body
    tail = body[span[1]:].lstrip("\n")
    return body[:span[0]].rstrip("\n") + ("\n\n" if tail.startswith("## ") else "\n") + tail


def body_of(text):
    """檔頭之後的全文（綜合卡的「相關檔」那段不算），行尾統一 \\n；沒有檔頭就是整份。"""
    text = str(text).lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return strip_files("\n".join(lines[i + 1:]))
    return strip_files(text)


def sha(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _path(vault):
    return Path(vault) / BASE_POSIX


def load(vault):
    """{相對路徑: 紀錄}；檔不在、壞掉、格式不對都當沒有紀錄。"""
    try:
        data = read_json(_path(vault), None)
    except (ValueError, OSError):
        return {}
    got = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items() if isinstance(v, dict) and isinstance(v.get("sha256"), str)
            and isinstance(v.get("body"), str)}


def _save(vault, recs):
    write_json_atomic(_path(vault), {"schema_version": SCHEMA, "cards": recs})


def _entry(body, turn_id=None, pending=None):
    """pending：她改了還沒處理、Claude 又寫了這張卡時留下的 {"before": 基準, "after": 寫之前那一版}（Z5、Z1，D-270 裁定 2）。"""
    got = {"sha256": sha(body), "body": body, "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    if turn_id:
        got["turn"] = turn_id
    if pending:
        got["pending"] = pending
    return got


def _locked(vault, fn):
    """讀、改、寫整份基準，鎖住免得兩個程序互蓋；fn 回 False 就不寫。"""
    _path(vault).parent.mkdir(parents=True, exist_ok=True)
    with FileLock(_path(vault)):
        recs = load(vault)
        if fn(recs) is not False:  # fn 回 False = 什麼都沒改，不碰狀態檔
            _save(vault, recs)


# ---------- 哪些是卡 ----------

def _reserved(name):
    return name.startswith(".") or name.casefold() in inventory.VAULT_RESERVED_DIRS


def is_synthesis_text(text):
    try:
        front = cards.parse_front(text)
    except ValueError:
        return False
    return "名稱" in front and "型別" in front


def all_cards(vault, deadline=None):
    """[(相對路徑, 'file'|'synthesis')]，照路徑排。deadline（time.monotonic 的時刻）到了就不再讀綜合卡檔頭，回已找到的。"""
    import time
    out = [(rel, "file") for rel in cards._walk_cards(vault)]
    root = Path(vault)
    try:
        tops = sorted(p for p in root.iterdir() if p.is_dir() and not _reserved(p.name))
    except OSError:
        return out
    for top in tops:
        try:
            names = sorted(p for p in top.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            continue
        for d in names:
            if deadline is not None and time.monotonic() > deadline:
                return sorted(out)
            f = d / f"{d.name}.md"
            try:
                if f.is_file() and is_synthesis_text(f.read_text(encoding="utf-8")):
                    out.append((f.relative_to(root).as_posix(), "synthesis"))
            except (OSError, UnicodeDecodeError):
                continue
    return sorted(out)


def card_kind(vault, rel):
    """這個相對路徑是卡嗎：'file'、'synthesis' 或 None（檔要在）。"""
    parts = rel.split("/")
    p = Path(vault) / rel
    if parts[0] == cards.CARDS_DIR and rel.lower().endswith(".md") and not any(x.startswith(".") for x in parts):
        return "file" if p.is_file() else None
    if len(parts) == 3 and not _reserved(parts[0]) and not parts[1].startswith(".") and parts[2] == f"{parts[1]}.md":
        try:
            return "synthesis" if p.is_file() and is_synthesis_text(p.read_text(encoding="utf-8")) else None
        except (OSError, UnicodeDecodeError):
            return None
    return None


def _read_body(vault, rel):
    return body_of((Path(vault) / rel).read_text(encoding="utf-8"))


# ---------- 寫的人更新基準 ----------

def record(vault, rel):
    """把這張卡現在的內文記成基準（卡要在）。"""
    body = _read_body(vault, rel)
    _locked(vault, lambda recs: recs.__setitem__(rel, _entry(body, pending=(recs.get(rel) or {}).get("pending"))))


def record_write(vault, rel, turn_id, backup):
    """Z5、Z1 驗過之後記基準（Claude 寫的不算她改）。寫之前那一版（回合備份 backup）的內文跟基準不同＝她改了還沒處理：
    把「基準 → 寫之前那一版」留成 pending，kb cards edits 照列到 --done。同一回合 Claude 已經寫過（紀錄的 turn 就是這回合）時，
    備份是更早的版本，不比。回這次新發現的手改 hunks（沒有回 []）；記不了只記 log。"""
    try:
        if card_kind(vault, rel) is None:
            return []
        body = _read_body(vault, rel)
        found = []

        def fn(recs):
            got = recs.get(rel)
            pending = (got or {}).get("pending")
            if got and got.get("turn") != turn_id and backup is not None and Path(backup).is_file():
                try:
                    pre = body_of(Path(backup).read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    pre = None
                if pre is not None and pre != got["body"]:
                    pending = {"before": pending["before"] if pending else got["body"], "after": pre, "machine": _machine(vault)}
                    found.extend(hunks(got["body"], pre))
            recs[rel] = _entry(body, turn_id, pending)
        _locked(vault, fn)
        return found
    except BaseException as e:  # noqa: BLE001
        _log(vault, e)
        return []


def record_safe(vault, rel):
    """寫卡的人叫：記不了只記 log，不擋原本的寫入。回有沒有記成。"""
    try:
        if card_kind(vault, rel) is None:
            return False
        record(vault, rel)
        return True
    except BaseException as e:  # noqa: BLE001
        _log(vault, e)
        return False


def in_sync(vault, rel):
    """這張卡現在的內文就是基準（或根本沒紀錄）：此時程式改它，基準可以跟著換；她有沒處理的手改就不換。"""
    try:
        got = load(vault).get(rel)
        return got is None or got["sha256"] == sha(_read_body(vault, rel))
    except (OSError, UnicodeDecodeError):
        return True


def rekey(vault, old, new, synced=True, transform=None):
    """卡搬家：紀錄換鍵。synced（搬之前沒有手改）時順便把基準換成新位置現在的內文，內文跟著程式改過（例：改名換標題）。
    沒 synced（她有沒處理的手改）時基準不換成現在的內容，但程式自己改的字照 transform(基準內文) 同樣改一遍，
    免得程式換的字被算進她的 hunks（例：實體改名換標題）。"""
    try:
        def fn(recs):
            got = recs.pop(old, None)
            if got is None:
                return False
            if synced and card_kind(vault, new):
                got = _entry(_read_body(vault, new), pending=got.get("pending"))
            elif transform is not None:
                got = dict(got, body=transform(got["body"]))
                got["sha256"] = sha(got["body"])
            recs[new] = got
        _locked(vault, fn)
    except BaseException as e:  # noqa: BLE001
        _log(vault, e)


def rekey_folder(vault, src, dst):
    """整夾搬（型別改名、型別合併、型別夾編號）：夾底下的卡紀錄照原樣換鍵（內文位元組不動，pending 也跟著）。"""
    try:
        a, b = src.strip("/") + "/", dst.strip("/") + "/"

        def fn(recs):
            moved = [k for k in recs if k.startswith(a)]
            if not moved:
                return False
            for k in moved:
                recs[b + k[len(a):]] = recs.pop(k)
        _locked(vault, fn)
    except BaseException as e:  # noqa: BLE001
        _log(vault, e)


def scrub(vault, mention):
    """purge（不想讓 AI 看到的夾排除後清殘留）：基準與 pending 存的是卡的全文，提到那夾的內容（mention(text) 為真）就換成卡現在的內容、
    pending 拿掉；卡已經不在的紀錄刪掉。回洗了幾筆。"""
    n = [0]

    def fn(recs):
        for rel in list(recs):
            got = recs[rel]
            pend = got.get("pending") if isinstance(got.get("pending"), dict) else {}
            texts = [got["body"]] + [v for v in pend.values() if isinstance(v, str)]
            try:
                if not (Path(vault) / rel).is_file():
                    recs.pop(rel)
                elif any(mention(t) for t in texts):
                    recs[rel] = _entry(_read_body(vault, rel))
                else:
                    continue
            except (OSError, UnicodeDecodeError):
                recs.pop(rel)
            n[0] += 1
        return bool(n[0])
    _locked(vault, fn)
    return n[0]


def forget(vault, *rels):
    try:
        _locked(vault, lambda recs: any([recs.pop(r, None) is not None for r in rels]))
    except BaseException as e:  # noqa: BLE001
        _log(vault, e)


def after_revert(vault, restored, removed):
    """kb versions revert：還原回來的卡基準換成那一版，被拿掉的卡刪紀錄；不算她改的。"""
    try:
        have = load(vault)
        removed = [r for r in removed if r in have]
        restored = [r for r in restored if r in have and card_kind(vault, r)]  # 沒紀錄的卡以後補基準，退回不新建狀態檔
        if not removed and not restored:  # 退回的不是卡：不碰狀態檔
            return

        def fn(recs):
            for rel in removed:
                recs.pop(rel, None)
            for rel in restored:
                if card_kind(vault, rel):
                    recs[rel] = _entry(_read_body(vault, rel), pending=(recs.get(rel) or {}).get("pending"))
        _locked(vault, fn)
    except BaseException as e:  # noqa: BLE001
        _log(vault, e)


# ---------- 認出她改的 ----------

def hunks(before, after):
    """逐行比對的改動塊：新增 before 是 ""、刪掉 after 是 ""。"""
    a, b = before.split("\n"), after.split("\n")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append({"before": "\n".join(a[i1:i2]), "after": "\n".join(b[j1:j2])})
    return out


def _item(vault, rel, kind, hunk_list):
    try:
        front = cards.parse_front((Path(vault) / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        front = {}
    if kind == "file":
        ents = front.get("實體")
        item = {"path": rel, "kind": "file", "id": front.get("編號") or "", "title": front.get("標題") or "",
                "entities": [x for x in ents if isinstance(x, str)] if isinstance(ents, list) else []}
    else:
        item = {"path": rel, "kind": "synthesis", "entity": front.get("名稱") or rel.split("/")[1]}
    item["hunks"] = hunk_list
    return item


def _pulled_same_fn(vault):
    """回 f(rel)：這張卡現在的全文是不是 pull 進來的那一版（.system/local/pulled.json 記的 sha）。讀不到都是 False。"""
    def f(rel):
        try:
            from kb._lib import refresh
            return refresh.pulled_same(vault, rel, (Path(vault) / rel).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return False
    return f


def _machine(vault):
    """這台的流水檔代號；拿不到回 None（pending 就不記是哪台，照舊兩台都報）。"""
    try:
        from kb._lib import env
        return env.machine_id(vault)
    except Exception:  # noqa: BLE001
        return None


def _others_pending(vault, pend, mine):
    """這個 pending 是別台留的、那台還在寫流水檔（代號換了、找不到那台的檔就照報，免得永遠沒人處理）。"""
    m = pend.get("machine")
    if not m or m == mine:
        return False
    from kb._lib import streams
    return (streams.stream_dir(vault, "card_base") / f"{m}.jsonl").is_file()


def edits(vault, deadline=None, persist=True):
    """比每張卡現在的內文跟基準。回 {"edits": [...], "more": 時限內沒比完}。
    沒紀錄的卡（含搬了沒換鍵的）補基準、不報；persist=False 時不寫狀態檔。被刪的卡不列。"""
    import time
    recs = load(vault)
    found = all_cards(vault, deadline)
    cut = deadline is not None and time.monotonic() > deadline  # 找卡就用完時限：後面的卡沒看到，不能拿「沒對應檔」當搬了
    live = {rel for rel, _k in found}
    orphans = {}  # sha → 沒對應檔的紀錄路徑：卡搬了但沒人換鍵
    for rel, got in recs.items():
        if rel not in live and not cut:
            orphans.setdefault(got["sha256"], rel)
    out, added, drop, more = [], {}, [], cut
    pulled = _pulled_same_fn(vault)
    mine = None
    for rel, kind in found:
        if deadline is not None and time.monotonic() > deadline:
            more = True
            break
        try:
            body = _read_body(vault, rel)
        except (OSError, UnicodeDecodeError):
            continue
        got = recs.get(rel)
        if got is None:
            s = sha(body)
            old = orphans.pop(s, None)
            if old is not None:
                drop.append(old)
            added[rel] = _entry(body)
        else:
            pend = got.get("pending")
            hs = hunks(pend["before"], pend["after"]) if isinstance(pend, dict) else []
            if hs and pend.get("machine"):  # card_base 是共用流水檔：別台留的 pending 那台會處理，這台不報（#45，新定 3）
                mine = mine or _machine(vault)
                if _others_pending(vault, pend, mine):
                    hs = []
            if got["sha256"] != sha(body):
                if pulled(rel):  # 多人共用 3 問 1：內容跟 pull 進來時一樣＝同事改的，改的那台會記；這台把基準對上、不報
                    added[rel] = _entry(body, pending=pend)
                else:
                    hs = hs + hunks(got["body"], body)
            if hs:
                out.append(_item(vault, rel, kind, hs))
    if persist and (added or drop):
        try:
            def fn(cur):
                for rel, ent in added.items():
                    if rel in recs:
                        cur[rel] = ent  # pull 進來的卡：基準換成現在的內文
                    else:
                        cur.setdefault(rel, ent)
                for rel in drop:
                    cur.pop(rel, None)
            _locked(vault, fn)
        except BaseException as e:  # noqa: BLE001  補不了基準只記 log
            _log(vault, e)
    return {"edits": out, "more": more}


class DoneRefused(ValueError):
    pass


def done(vault, paths, turn_id=None):
    """Claude 處理完了：這幾張的基準換成現在的內容（pending 清掉）。路徑不是卡或不存在就整個拒絕。
    turn_id：這一回合；之後同一回合 Claude 再寫那張卡，record_write 認得「這回合處理過了」，不拿回合備份比出反向的假通知。"""
    rels = []
    for p in paths:
        rel = str(p).replace("\\", "/").strip("/")
        if card_kind(vault, rel) is None:
            raise DoneRefused(f"「{p}」不是卡（要寫知識庫相對路徑，例：檔案索引/…/xxx.md）或檔案不存在")
        rels.append(rel)
    bodies = {rel: _read_body(vault, rel) for rel in rels}
    _locked(vault, lambda recs: [recs.__setitem__(r, _entry(b, turn_id)) for r, b in bodies.items()])
    return rels


def _log(vault, e):
    try:
        from kb._lib import ledger
        ledger.log_error(vault, f"card_base {type(e).__name__}: {str(e)[:200]}", source="card_base")
    except BaseException:  # noqa: BLE001
        pass
