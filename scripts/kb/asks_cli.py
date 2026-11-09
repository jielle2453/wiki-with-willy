"""kb asks [--about <名稱>…] [--all] [--json]、kb asks set-aside <key>（日程第 2 點，D-134、D-135；回答末尾的問法 lint 題，D-225、D-226；
放寬乙 D-310(4)、D-322 第 6 件、D-334(7)(8)：一次一件、同一題一個對話只問一次、沒回的到期關掉）。

asks 跟 kb cards todo --json 裡的同一支（plan.build，跑一次 cards.todo）再加上 pending.json 裡沒有整理題的那幾種（plan.pending_asks）；
每題帶 key（穩定的鍵）、about（對象的實體與檔）、urgent（有時間性）。先放著的不列（有時間性且快到期的照列；逾期的先放著就不再拿出來）。
沒帶 --all：asks 只放跟這一句有關的（--about 給的實體，別名也認；加上這一回合碰過的：回合帳改到的卡與綜合卡、kb lookup／convert／cards
在對話裡記的 touched）與有時間性的，照急的排、最多 ASKS_MAX（1）題；later 是跟這一句無關、最急的一題（沒有 null），給 AI 在一段事說完時帶一句；
others 是其餘的件數（不含先放著）。同一個 session（--session）回過的題（asks、later）記進 .system/asks_seen.json
（{key: {first, sessions}}，寫前不備進回合，跟 set_aside.json 同一把鎖），之後同 session 不再回傳；終端機沒有 session 不記。答了（題目自己消失）就從 asks_seen 拿掉。
first 超過 ask_expire_days 還在的題不再回傳並關掉：p- 開頭的交 pending 標 closed（reason「沒回，照推測」）、其他的記進 set_aside.json（by expired）；
逾期日程的那筆資料不動。--json 的 expired 是這次關掉的鍵。
--all（「看待處理」「有沒有要我確認的」，answer）：全部照急的排（同 session 回過的照列），set_aside 是先放著的件數，沒有 later、others。
set-aside <key>：p- 開頭的交 kb pending set-aside；其他的（名字確認這類不住在 pending.json 的）記進 .system/set_aside.json
（{key: {at, by, question}}，寫前備進回合）；鍵含問句的指紋，對象變了鍵就變，自然回到前面。找不到「沒有這個問題」exit 3。
逾期、快到期的也收（放寬乙）；快到期的照 D-226 到 set_aside_wake_days 再拿出來。
tell 只帶這一回合做了什麼（D-141）。人讀照編號印問題與選項，沒有就印「沒有要問的」。
"""
import argparse
import json
from datetime import date
from pathlib import Path

from kb import journal_cli
from kb._lib import cards, config as config_lib, entities, pending, plan, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NOTHING = "沒有要問的"
ASKS_MAX = 1  # 回答末尾一次最多幾題（D-135；放寬乙改成一件）
SET_ASIDE_COUNT = "另外有 {n} 件先放著"
SET_ASIDE_LINE = "先放著了：{}；有變化或快到期會再拿出來"
NO_SUCH = "沒有這個問題"
ASKS_SEEN_POSIX = ".system/asks_seen.json"
EXPIRED_BY = "expired"
CLI_LOCK_TIMEOUT = 30


def add_arguments(sub):
    sub.add_argument("action", nargs="?", choices=["set-aside"], help="set-aside <key>：這題先放著")
    sub.add_argument("key", nargs="?", help="題目的 key（kb asks --json 每題的 key）")
    sub.add_argument("--about", action="append", default=[], help="這一句講到的實體名稱（別名也認），可以重複")
    sub.add_argument("--all", action="store_true", help="全部列出（看待處理、有沒有要我確認的）")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _collect(vault):
    """(全部題（照急的排，先放著的不在裡面）, 藏了幾題, tell)。"""
    data = cards.todo(vault, sources_lib.load(vault), tell_all=False)
    extra, hidden = plan.pending_asks(vault)
    return plan.order(data["asks"] + extra), data.get("set_aside", 0) + hidden, data["tell"]


def _scope(vault, names):
    """這一句的範圍：--about 的實體（別名也認）、這一回合 touched 的實體與檔、回合帳改到的卡（編號）與綜合卡（實體）。"""
    ents, files = set(), set()
    data = entities.load(vault)
    for name in names:
        e, _hit = entities.find(data, name.strip())
        if e is not None:
            ents.add(e["id"])
    now = turn.now(vault)  # 只讀，不開回合
    if now:
        got = turn.touched(now)
        ents |= got["entities"]
        files |= got["files"]
        by_card = {entities.card_rel(e, vault).casefold(): e["id"] for e in data["entities"]
                   if e.get("type") and e.get("status") == "active"}
        for row in turn.entries(vault, now["turn"]):
            rel = row["path"]
            if rel.casefold() in by_card:
                ents.add(by_card[rel.casefold()])
            elif rel.startswith(cards.CARDS_DIR + "/") and rel.endswith(".md"):
                try:
                    fid = cards.parse_front((Path(vault) / rel).read_text(encoding="utf-8")).get("編號")
                except (OSError, UnicodeDecodeError, ValueError):
                    fid = None
                if isinstance(fid, str):
                    files.add(fid)
    return ents, files


def _related(ask, ents, files):
    about = ask.get("about") or {}
    return bool(ents & set(about.get("entities") or []) or files & set(about.get("files") or []))


def _pick(asks, ents, files):
    """有時間性的先、再來有關的，最多 ASKS_MAX 題；其餘的（照急的排）第一題是 later。回 (chosen, later, 其餘件數)。"""
    first = [a for a in asks if a.get("urgent")] + [a for a in asks if not a.get("urgent") and _related(a, ents, files)]
    chosen = first[:ASKS_MAX]
    rest = [a for a in asks if a not in chosen]
    return chosen, (rest[0] if rest else None), len(rest)


def _seen_path(vault):
    return Path(vault) / ASKS_SEEN_POSIX


def _read_seen(vault):
    try:
        data = kio.read_json(_seen_path(vault))
    except (ValueError, OSError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _seen_lock(vault):
    p = Path(vault) / plan.SET_ASIDE_POSIX  # 跟 set_aside.json 同一把鎖
    p.parent.mkdir(parents=True, exist_ok=True)
    return kio.FileLock(p)


def _aside_now(vault):
    """現在算先放著的鍵（pending 的 set_aside 還沒該回來、set_aside.json 記的鍵）。"""
    try:
        items = pending.load(vault)["items"]
    except ValueError:
        items = []
    back = pending.due_back(vault, items) if any(i.get("set_aside") for i in items) else set()
    return {i["id"] for i in items if i.get("status") == "open" and i.get("set_aside") and i["id"] not in back} | set(plan.set_aside_keys(vault))


def _drop_aside_overdue(asks, vault):
    """逾期的（與 p- 的有時間性待處理）先放著了就不再拿出來；快到期的照 D-226 到 set_aside_wake_days 再拿出來。回 (留下的, 藏了幾題)。"""
    aside = _aside_now(vault)
    keep = [a for a in asks if not (a.get("urgent") and a.get("key") in aside and (a.get("kind") == "overdue" or str(a.get("key")).startswith("p-")))]
    return keep, len(asks) - len(keep)


def _expire(vault, asks, seen):
    """first 超過 ask_expire_days 的題關掉。回 (留下的, 這次關掉的鍵)。"""
    days = config_lib.get(vault, "ask_expire_days")
    today = date.today()
    keep, gone = [], []
    for a in asks:
        first = (seen.get(a.get("key")) or {}).get("first")
        try:
            old = bool(first) and (today - date.fromisoformat(first)).days > days
        except (TypeError, ValueError):
            old = False
        (gone if old else keep).append(a)
    if gone:
        turn_id = turn.current(vault)["turn"]
        conversation = journal_cli.in_conversation(vault)
        changed = []
        for a in gone:
            key = a["key"]
            if key.startswith("p-"):
                if pending.close_expired(vault, key, turn_id):
                    changed = [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]
            else:
                _write_key(vault, key, a.get("question", ""), turn_id, by=EXPIRED_BY)
                changed.append({"path": plan.SET_ASIDE_POSIX, "action": "modify"})
        if changed:
            journal_cli.record_program_or_warn(vault, f"{days} 天沒回的題關掉、照推測：{len(gone)} 件", changed, "asks expired", conversation)
    return keep, [a["key"] for a in gone]


def _record_seen(vault, shown, current_keys, sid):
    """回過的題記進 asks_seen（沒 session 不記）；題目已不在（答了）的拿掉。寫前不備進回合。"""
    with _seen_lock(vault):
        seen = _read_seen(vault)
        new = {k: json.loads(json.dumps(v)) for k, v in seen.items() if k in current_keys}
        if sid:
            today = date.today().isoformat()
            for a in shown:
                row = new.setdefault(a["key"], {"first": today, "sessions": []})
                if sid not in row.setdefault("sessions", []):
                    row["sessions"].append(sid)
        if new != seen:
            kio.write_json_atomic(_seen_path(vault), new)


def _set_aside(vault, key, as_json):
    key = (key or "").strip()
    if not key:
        print(NO_SUCH)
        return 3
    hit = None
    if not key.startswith("p-"):  # 住在 pending.json 的不用跑整理（第二輪 T-27：狀態檔壞了也收得了）
        asks, _hidden, _tell = _collect(vault)
        hit = next((a for a in asks if a.get("key") == key), None)
    turn_id = turn.current(vault)["turn"]
    conversation = journal_cli.in_conversation(vault)
    if key.startswith("p-"):
        try:
            item = pending.set_aside(vault, key, "customer", turn_id)
        except KeyError:
            print(NO_SUCH)
            return 3
        text = item.get("text", "") + (f"：{item['reason']}" if item.get("kind") == "unreadable" and item.get("reason") else "")
        changed = [{"path": pending.PENDING_POSIX, "action": "modify"}, {"path": pending.MD_POSIX, "action": "modify"}]
    else:
        stored = plan.set_aside_keys(vault)
        if hit is None and key not in stored:
            print(NO_SUCH)
            return 3
        text = hit["question"] if hit else stored[key].get("question", "")
        _write_key(vault, key, text, turn_id)
        changed = [{"path": plan.SET_ASIDE_POSIX, "action": "modify"}]
    journal_cli.record_program_or_warn(vault, f"先放著：{text}", changed, "asks set-aside", conversation)
    line = SET_ASIDE_LINE.format(text)
    print(json.dumps({"key": key, "line": line}, ensure_ascii=False) if as_json else line)
    return 0


def _write_key(vault, key, question, turn_id, by="customer"):
    p = Path(vault) / plan.SET_ASIDE_POSIX
    p.parent.mkdir(parents=True, exist_ok=True)
    with kio.FileLock(p):
        keys = plan.set_aside_keys(vault)
        if key in keys:
            return
        turn.backup(vault, plan.SET_ASIDE_POSIX, turn_id)
        keys[key] = {"at": date.today().isoformat(), "by": by, "question": question}
        kio.write_json_atomic(p, {"schema_version": 1, "keys": keys})


def _load(vault):
    """全部題（先放著的與到期的拿掉）：(asks, 藏了幾題, tell, 現在的 key, asks_seen, 這次關掉的 key)。"""
    asks, hidden, tell = _collect(vault)
    current_keys = {a["key"] for a in asks}
    seen = _read_seen(vault)
    asks, expired = _expire(vault, asks, seen)
    asks, more_aside = _drop_aside_overdue(asks, vault)
    return asks, hidden + more_aside, tell, current_keys, seen, expired


def about(vault, names):
    """沒帶 --all 的那一路（kb asks --about，與 kb lookup 順帶的，#46）：{"asks","later","others","tell"}（有關掉的多 expired）；
    回過的題記進 asks_seen（同 session 不再回）。"""
    asks, _hidden, tell, current_keys, seen, expired = _load(vault)
    sid = turn.SESSION or ""
    ents, files = _scope(vault, names)
    told = {a["key"] for a in asks if sid and sid in (seen.get(a["key"]) or {}).get("sessions", [])}
    chosen, later, others = _pick([a for a in asks if a["key"] not in told], ents, files)
    shown = chosen + ([later] if later else [])
    plan.renumber(shown)
    _record_seen(vault, shown, current_keys - set(expired), sid)
    out = {"asks": chosen, "later": later, "others": others, "tell": tell}
    if expired:
        out["expired"] = expired
    return out


def end_lines(out):
    """kb lookup 人讀末尾那段（#46）：有要問、要說的才印，沒有回 []。"""
    lines = []
    for ask in out["asks"]:
        lines.append(ask["question"])
        lines.extend(f"  {r['n']}. {r['label']}" for r in ask["results"])
    lines = (["回答末尾要問："] + lines) if lines else []
    if out.get("later"):
        lines.append(f"一段事說完再問：{out['later']['question']}")
    if out["tell"]:
        lines += ["回答末尾要說："] + list(out["tell"])
    return lines

def _print(asks, tail):
    lines = []
    for ask in asks:
        lines.append(ask["question"])
        lines.extend(f"  {r['n']}. {r['label']}" for r in ask["results"])
    print("\n".join(lines or [NOTHING]))
    for line in tail:
        print(line)


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    try:
        if args.action == "set-aside":
            return _set_aside(vault, args.key, as_json)
        if args.all:
            asks, hidden, tell, _keys, _seen, expired = _load(vault)
            plan.renumber(asks)
            out = {"asks": asks, "set_aside": hidden, "tell": tell}
            tail = [SET_ASIDE_COUNT.format(n=hidden)] if hidden else []
            if expired:
                out["expired"] = expired
        else:
            out, tail = about(vault, args.about), []
    except ValueError as e:
        print(f"狀態檔讀不到：{e}")
        return 3
    except TimeoutError:
        print("等不到鎖（別的指令正在改），沒有寫入；請稍後再試")
        return 3
    except OSError as e:
        print(f"讀不到：{type(e).__name__}: {e}")
        return 3
    if as_json:
        print(json.dumps(out, ensure_ascii=False))
        return 0
    _print(out["asks"], tail)
    if out.get("later"):
        print(f"一段事說完再問：{out['later']['question']}")
    if out["tell"]:
        print("回答末尾要說：")
        print("\n".join(out["tell"]))
    return 0
