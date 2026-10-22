"""個案裁示 .system/rulings.json（schema 1，D-041、D-149、D-150；note 改知識第 2 點）與渲染視圖 系統/裁示.md（D-015）。

一條一個客戶說「只有這件這樣」的裁示：id（r- 加 8 碼 hex）、kind（KINDS 的值）、entities（登記的實體名稱）、
cards（[{id, fp}]，fp 是掛的時候那張檔案卡的指紋前 12 碼）、text（客戶原話）、date、turn、status（active｜revoked）、revoked（收回那天）。
密碼與守門（D-183、D-184）：第三種「可以讀」（credential_read）掛清冊條目 files（[id]，不是卡），選填 label（客戶怎麼稱呼這一種）；
守門與 kb convert 在憑證命中時查 readable_ids，那一份只放讀；跟著清冊條目走（搬、改名、改內容照樣認得）。
「可以記」拿掉（密碼存憑證管理員，D-180）。decline：客戶說某份檔名像密碼的不要讀，清冊記 credential_declined，之後不再問。
生效由程式帶：綜合卡的「整理到」收 r- 行，cards todo 把掛在實體或它的卡上的有效裁示附給寫綜合卡的人（for_entity）；
掛卡的裁示遇到卡指紋變了（原檔改版、卡重做）由 drifted 列出、asks 問現在可以信了嗎。封頂與升格交 lint。
每次寫都在 rulings.json 的 FileLock 裡：先 turn.backup 再原子寫，寫完重渲染 md（先備份，內容沒變不寫）。
"""
import secrets
from datetime import date
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

RULINGS_POSIX = ".system/rulings.json"
MD_POSIX = "系統/裁示.md"
SCHEMA_VERSION = 1
KINDS = {"口述為準": "spoken_wins", "不採用": "ruled_out", "可以讀": "credential_read"}
KIND_TEXT = {v: k for k, v in KINDS.items()}
ACTIVE_KINDS = ("spoken_wins", "ruled_out", "credential_read")
KIND_MSG = "裁示種類要是 口述為準、不採用、可以讀 之一"
READ_NEEDS_FILE = "可以讀要指定是哪一份檔（--file）"
READ_FILES_ONLY = "可以讀只掛檔（拿掉 --entity、--card）"
FILE_ONLY_FOR_READ = "只有可以讀才用 --file"
NO_FILE = "清冊沒有這份檔：{}"
NOT_CREDENTIAL = "這份本來就讀得到"
RULED_OUT_NEEDS_CARD = "不採用要指定是哪幾張卡（--card）"
RULED_OUT_NO_ENTITY = "不採用只掛卡，不掛實體（拿掉 --entity）"
SPOKEN_NEEDS_TARGET = "口述為準要掛實體或卡"
NOT_FOUND = "沒有這條裁示：{}"
REVOKED_MSG = "這條裁示已經收回了"
NO_CARDS_MSG = "這條裁示沒有掛卡，不用對新版"
NO_ENTITY = "實體清單沒有「{}」，先 kb entity add"
MISSING_CARD = "（找不到這張卡）"
MISSING_FILE = "（找不到這份檔）"
HEADER = ("# 裁示\n"
          "這是什麼：你說過「只有這件這樣」的個案，程式從 .system/rulings.json 整理出來。\n"
          "怎麼用：要改跟我說；直接改這個檔不會生效。\n"
          "由程式產生，每次有裁示加入或收回時重寫。\n")
EMPTY_SENTENCE = "目前沒有裁示"


class RulingRefused(ValueError):
    """kb ruling 的拒絕：訊息就是給人看的那句。"""


def _today():
    return date.today().isoformat()


def _path(vault):
    return Path(vault) / RULINGS_POSIX


def empty():
    return {"schema_version": SCHEMA_VERSION, "rulings": []}


def load(vault):
    """沒檔回空的；壞 JSON、不是物件、rulings 不是清單、元素不是物件、版本比這版新丟 ValueError，訊息帶檔名。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise ValueError(f"{RULINGS_POSIX}（JSON 壞了：{e}）") from e
    if data is None:
        return empty()
    if not isinstance(data, dict):
        raise ValueError(f"{RULINGS_POSIX}（不是物件）")
    items = data.get("rulings")
    if not isinstance(items, list):
        raise ValueError(f"{RULINGS_POSIX}（沒有 rulings 清單）")
    if any(not isinstance(i, dict) for i in items):
        raise ValueError(f"{RULINGS_POSIX}（有一條不是物件）")
    version = data.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise ValueError(f"{RULINGS_POSIX}（版本 {version!r} 比這版 plugin 新）")
    return {"schema_version": SCHEMA_VERSION, "rulings": items}


def find(data, rid):
    return next((r for r in data["rulings"] if r.get("id") == rid), None)


def is_active(r):
    return r.get("status") == "active" and r.get("kind") in ACTIVE_KINDS


def card_ids(r):
    return [c.get("id") for c in r.get("cards") or [] if isinstance(c, dict)]


def readable_ids(vault):
    """有效「可以讀」裁示掛的清冊編號（D-183、D-184）：守門與 kb convert 在憑證命中時查。壞檔丟 ValueError（呼叫端照擋）。"""
    return {fid for r in load(vault)["rulings"] if r.get("status") == "active" and r.get("kind") == "credential_read"
            for fid in r.get("files") or [] if isinstance(fid, str)}


def for_entity(data, name, mounts):
    """掛在 name 身上、或掛在它掛著的檔案卡上的有效裁示（mounts 是 entities.mounts 的結果），照清單順序。"""
    from kb._lib import entities
    k = entities.key(name)
    mine = {m["id"] for m in mounts.get(name, [])}
    return [r for r in data["rulings"] if is_active(r)
            and (any(entities.key(n) == k for n in r.get("entities") or []) or mine & set(card_ids(r)))]


# ---------- 寫 ----------

def _mutate(vault, turn_id, fn):
    """鎖住讀改寫：fn(data) 回 (結果, 有沒有改)；有改才備份、寫、渲染。回結果。"""
    p = _path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        data = load(vault)
        result, changed = fn(data)
        if changed:
            from kb._lib import turn
            turn.backup(vault, RULINGS_POSIX, turn_id)
            write_json_atomic(p, data)
            render(vault, turn_id, data)
    return result


def _new_id(data):
    taken = {r.get("id") for r in data["rulings"]}
    while True:
        new = "r-" + secrets.token_hex(4)
        if new not in taken:
            return new


def kind_of(kind):
    """中文或英文種類名 → 英文值；不認得的（含已拿掉的「可以記」）丟 RulingRefused。"""
    kind = KINDS.get(str(kind or "").strip(), str(kind or "").strip())
    if kind not in ACTIVE_KINDS:
        raise RulingRefused(KIND_MSG)
    return kind


def file_entry(vault, arg):
    """「可以讀」的 --file：清冊編號、uri（file://別名/相對路徑）或磁碟路徑 → active 的清冊條目；對不到丟 RulingRefused。"""
    from kb._lib import inventory
    from kb._lib import sources as sources_lib
    arg = str(arg or "").strip()
    entries = inventory.load(vault)["entries"]
    if arg.startswith("file://"):
        entry = inventory.entry_for(vault, arg, entries)
    elif arg.startswith(("f-", "out-")) and "/" not in arg and "\\" not in arg:
        entry = next((e for e in entries if e.get("id") == arg), None)
    else:
        uri = inventory.uri_of(vault, sources_lib.load(vault), arg)
        entry = inventory.entry_for(vault, uri, entries) if uri else None
    if entry is None or entry.get("status") != "active" or not entry.get("locations"):
        raise RulingRefused(NO_FILE.format(arg))
    return entry


def looks_like_credential(entry):
    """這份的檔名（來源根以下每一段）像密碼或金鑰：守門與清冊的同一套樣式（D-079 補）。"""
    from kb._lib import credentials, inventory
    _alias, rel = inventory.split_uri(entry["locations"][0]["uri"])
    return entry.get("read") == "credential" or credentials.match_segment([s for s in rel.split("/") if s]) is not None


def file_name(entry):
    return str(entry["locations"][0]["uri"]).rsplit("/", 1)[-1] if entry and entry.get("locations") else ""


def _names(vault, names):
    from kb._lib import entities
    data = entities.load(vault)
    out = []
    for name in names:
        entry, _hit = entities.find(data, name)
        if entry is None or entry.get("status") != "active":
            raise RulingRefused(NO_ENTITY.format(name))
        if entry["name"] not in out:
            out.append(entry["name"])
    return out


def card_of(vault, card_id):
    """回 {"path","front"}：要在清冊、原檔 active、檔案卡寫好了（同 agenda._card），不行丟 RulingRefused。"""
    from kb._lib import cards, inventory
    entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == card_id), None)
    if entry is None:
        raise RulingRefused(f"清冊沒有 {card_id}")
    if entry.get("status") != "active":
        raise RulingRefused("這張卡的原檔已不在")
    group = cards._scan_groups(vault)[0].get(card_id)
    if not group:
        raise RulingRefused("這份還沒有檔案卡")
    return group[0]


def add(vault, kind, entities, cards, text, turn_id, files=(), label=None):
    """驗完才寫：種類、口述為準至少掛一個實體或卡、不採用只收卡且至少一張、實體登記且 active、原話同口述的檢查、
    卡在清冊 active 且有檔案卡（記下它現在的指紋前 12 碼）。可以讀只收 --file（清冊 active、檔名像密碼），label 照原話的檢查。回條目。"""
    from kb._lib import agenda
    kind = kind_of(kind)
    entities, cards = list(dict.fromkeys(entities or [])), list(dict.fromkeys(cards or []))
    files = list(dict.fromkeys(f for f in files or [] if str(f).strip()))
    if kind == "credential_read":
        return _add_readable(vault, files, entities, cards, text, label, turn_id)
    if files:
        raise RulingRefused(FILE_ONLY_FOR_READ)
    if kind == "ruled_out" and not cards:
        raise RulingRefused(RULED_OUT_NEEDS_CARD)
    if kind == "ruled_out" and entities:
        raise RulingRefused(RULED_OUT_NO_ENTITY)
    if kind == "spoken_wins" and not entities and not cards:
        raise RulingRefused(SPOKEN_NEEDS_TARGET)
    names = _names(vault, entities)
    from kb._lib import statements
    text = statements.check_text(text, "裁示", RulingRefused)  # 同口述：一句、200 字、不收密碼與四種字元
    hooked = [{"id": cid, "fp": agenda.card_fp(card_of(vault, cid)["front"])} for cid in cards]

    def fn(data):
        it = {"id": _new_id(data), "kind": kind, "entities": names, "cards": hooked, "text": text, "date": _today(),
              "turn": turn_id, "status": "active", "revoked": None}
        data["rulings"].append(it)
        return it, True
    return _mutate(vault, turn_id, fn)


def _add_readable(vault, files, entities, cards, text, label, turn_id):
    """「可以讀」（D-183）：掛清冊條目，不掛卡與實體；檔名不像密碼就不用裁示。"""
    from kb._lib import statements
    if not files:
        raise RulingRefused(READ_NEEDS_FILE)
    if entities or cards:
        raise RulingRefused(READ_FILES_ONLY)
    entries = [file_entry(vault, f) for f in files]
    if any(not looks_like_credential(e) for e in entries):
        raise RulingRefused(NOT_CREDENTIAL)
    text = statements.check_text(text, "裁示", RulingRefused)
    label = statements.check_text(label, "種類名稱", RulingRefused) if label not in (None, "") else None
    ids = list(dict.fromkeys(e["id"] for e in entries))

    def fn(data):
        it = {"id": _new_id(data), "kind": "credential_read", "entities": [], "cards": [], "files": ids, "text": text,
              "date": _today(), "turn": turn_id, "status": "active", "revoked": None}
        if label:
            it["label"] = label
        data["rulings"].append(it)
        return it, True
    return _mutate(vault, turn_id, fn)


def decline(vault, arg, turn_id):
    """客戶說這份檔名像密碼的不要讀（或 AI 判斷不是同一種）：清冊那筆記 credential_declined（今天），之後 asks 不再問。回條目。"""
    from kb._lib import inventory
    entry = file_entry(vault, arg)
    if not looks_like_credential(entry):
        raise RulingRefused(NOT_CREDENTIAL)
    inventory.set_credential_declined(vault, entry["id"], _today(), turn_id)
    return entry


def _need_active(data, rid):
    it = find(data, rid)
    if it is None:
        raise RulingRefused(NOT_FOUND.format(rid))
    if it.get("status") != "active":
        raise RulingRefused(REVOKED_MSG)
    return it


def remove(vault, rid, turn_id):
    """收回：狀態改 revoked、記日期（紀錄留著）。回條目。"""
    def fn(data):
        it = _need_active(data, rid)
        it["status"], it["revoked"] = "revoked", _today()
        return it, True
    return _mutate(vault, turn_id, fn)


def recheck(vault, rid, turn_id):
    """客戶說「還是別信」：掛的每張卡改記它現在的指紋前 12 碼（卡不見的照舊）。回條目。"""
    from kb._lib import agenda, cards
    groups = cards._scan_groups(vault)[0]

    def fn(data):
        it = _need_active(data, rid)
        if not it.get("cards"):
            raise RulingRefused(NO_CARDS_MSG)
        dirty = False
        for c in it["cards"]:
            group = groups.get(c.get("id"))
            fp = agenda.card_fp(group[0]["front"]) if group else None
            if fp and fp != c.get("fp"):
                c["fp"] = fp
                dirty = True
        return it, dirty
    return _mutate(vault, turn_id, fn)


def rename_entity(vault, mapping, turn_id, undo=None):
    """entities.rename／merge 呼叫：entities 裡 key 在 mapping 的名稱換成新名（去重，順序照原樣）。沒有 rulings.json 什麼都不做。
    undo 是 entities._Undo：寫之前記 rulings.json 與 md 的舊內容。回動到的條目。"""
    from kb._lib import entities
    if not _path(vault).exists():
        return []

    def fn(data):
        hits = []
        for it in data["rulings"]:
            names = it.get("entities") or []
            if not any(entities.key(n) in mapping for n in names):
                continue
            new = []
            for n in names:
                n = mapping.get(entities.key(n), n)
                if all(entities.key(x) != entities.key(n) for x in new):
                    new.append(n)
            it["entities"] = new
            hits.append(it)
        if hits and undo is not None:
            undo.file(RULINGS_POSIX)
            undo.file(MD_POSIX)
        return hits, bool(hits)
    return _mutate(vault, turn_id, fn)


# ---------- 給 plan ----------

def drifted(vault, data):
    """有效、掛卡、而且有卡的指紋跟記下的不同（原檔改版、卡重做過）的裁示：[{"ruling", "cards": [{"id","title","file","fp"}]}]，
    cards 只列變了的那幾張；卡不見的不列。"""
    from kb._lib import agenda, cards
    rows = [r for r in data["rulings"] if is_active(r) and r.get("cards")]
    if not rows:
        return []
    groups = cards._scan_groups(vault)[0]
    out = []
    for r in rows:
        moved = []
        for c in r["cards"]:
            group = groups.get(c.get("id"))
            if not group:
                continue
            front = group[0]["front"]
            fp = agenda.card_fp(front)
            if fp and fp != c.get("fp"):
                title = front.get("標題") if isinstance(front.get("標題"), str) else ""
                uris = front.get("原檔") if isinstance(front.get("原檔"), list) else []
                moved.append({"id": c["id"], "title": title, "file": str(uris[0]).rpartition("/")[2] if uris else title,
                              "fp": fp})
        if moved:
            out.append({"ruling": r, "cards": moved})
    return out


# ---------- 渲染 ----------

def file_names(vault):
    """清冊編號 → 檔名（可以讀的裁示顯示用）；清冊讀不到回空的。"""
    from kb._lib import inventory
    try:
        return {e.get("id"): file_name(e) for e in inventory.load(vault)["entries"]}
    except (ValueError, OSError):
        return {}


def targets(r, titles, names=None):
    """「信義王、「合約」」：掛的實體名稱，接著卡標題（加「」）；卡標題找不到寫 MISSING_CARD。可以讀的是檔名（加「」）。"""
    parts = list(r.get("entities") or [])
    parts += [f"「{titles.get(cid) or MISSING_CARD}」" for cid in card_ids(r)]
    parts += [f"「{(names or {}).get(fid) or MISSING_FILE}」" for fid in r.get("files") or []]
    return "、".join(parts)


def render_text(data, titles, names=None):
    """依種類分段（口述為準、不採用、可以讀），每行「- <掛的對象>：<原話>（<日期>）」，可以讀有 label 的接在對象後面加括號；
    只列有效的，沒有的種類整段不印，全空一句。"""
    blocks = []
    for kind in ACTIVE_KINDS:
        rows = [r for r in data["rulings"] if r.get("status") == "active" and r.get("kind") == kind]
        if rows:
            blocks.append(f"## {KIND_TEXT[kind]}\n" + "\n".join(
                f"- {targets(r, titles, names)}{'（' + r['label'] + '）' if r.get('label') else ''}：{r.get('text')}（{r.get('date')}）"
                for r in rows))
    return HEADER + "\n" + ("\n\n".join(blocks) if blocks else EMPTY_SENTENCE) + "\n"


def render(vault, turn_id=None, data=None):
    """寫 系統/裁示.md；內容沒變不寫；有 turn_id 先備進回合。回有沒有寫。"""
    from kb._lib import agenda
    data = data if data is not None else load(vault)
    text = render_text(data, agenda.card_titles(vault), file_names(vault))
    target = Path(vault) / MD_POSIX
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    if turn_id:
        from kb._lib import turn
        turn.backup(vault, MD_POSIX, turn_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(target, text)
    return True
