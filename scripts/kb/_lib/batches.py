"""整批製卡的派工帳（card-maker，D-195～D-200）：.system/batches.json schema 1。

jobs：一次整理一筆（這個 session 有 open 的就沿用），turn 是派工那一句的回合；同一個 job 的批次、子代理的寫入都算這個回合。
  選填：source（dispatch --source，之後再派照它）、stopped_turn（cards stop 那一句，這一句不再派）、
  failed（{key: {"kind","reason"}}，這個 job 裡做過、回報沒做成的，這個 job 不再切）。
batches：一批一筆，kind 是 cards｜agenda｜synthesis，items 是項目的 key（卡的清冊編號、日程條目編號、實體編號或綜合卡路徑）；
  status open｜done｜released；選填 failed（{key: 原因}，SubagentStop 從回報抄的）。
  認領 = open、同一個 session、建立不到 card_batch_expire_minutes 分鐘；過期但記上的子代理還沒停，認領到三倍時間為止
  （不讓兩個子代理寫同一張卡）。過期的讀到時當放掉（不改檔）。
agents：SubagentStart 記的子代理（agent_id → session、回合），守門照它把子代理的寫入算回 job 回合；SubagentStop 記 stopped。
failures：跨 job 的失敗紀錄 {key: {"fp","jobs","reason"}}；同一份（指紋沒變）在兩個 job 都沒做成就照讀不到記進待處理，
  檔案改了（指紋變了）重新算（card-maker 第一輪修 S2）。
寫入都在 batches.json 的鎖裡（dispatch 連算 todo 都在鎖裡），寫前備進 job 回合（by program）；壞檔丟 Unreadable 帶檔名。
結束超過 card_batch_expire_minutes 的 job、批次、子代理寫入時清掉（每個 session 最後一個關了的 job 留著）。
派得出去的項目（split）：todo 的 new、stale、有檔頭的 broken、unlinked（cards）；agenda_unscanned、agenda_recheck（agenda）；
synthesis_broken、synthesis_stale、確認過的 synthesis_new（synthesis）。主對話自己做的（main）：link 條目（線上文件，子代理讀不到）、
檔名像密碼的（含裁示可以讀的）、待處理記過「有密碼」（還開著或用密碼打開了）的；manage 第二版還沒清的排除夾（purge pending）裡的不切也不算。
"""
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from kb._lib import config, inventory, turn
from kb._lib.io import FileLock, read_json, write_json_atomic
from kb._lib.paths import plugin_root

BATCHES_POSIX = ".system/batches.json"
SCHEMA_VERSION = 1
AGENT = "wiki-with-willy:card-maker"
KINDS = ("cards", "agenda", "synthesis")
KIND_TEXT = {"cards": "做卡", "agenda": "日程", "synthesis": "綜合卡"}
CARD_BUCKETS = ("new", "stale", "broken", "unlinked")
AGENDA_BUCKETS = ("agenda_recheck", "agenda_unscanned")  # 先重對再抽（D-140）
SYNTHESIS_BUCKETS = ("synthesis_broken", "synthesis_stale", "synthesis_new")
LOCKED_HEAD = "有密碼"
# 還沒解開（open），或主對話用密碼打開了（done、how 是 convert 的那句）；客戶交代過的（resolve）、說先跳過的（dismissed）不算（第一輪修）
STOPPED = "這批已經停了或做完了"
NO_SOURCE = "沒有登記這個來源：{}"
APPROVAL_QUESTION = "這次要更新 {n} 張綜合卡，比平常多；要繼續嗎？"
PROMPT = "批次 {batch}：{what}。第一步跑 {prefix} cards batch {batch} --json，照它做，做完照你的說明回報。"
DESCRIPTION = "整理第 {n} 批（{what}）"  # 給 Agent 工具的 description：繁中，主對話轉述也不會冒英文（S6）
SAY_FIRST = "交給製卡助手在背景整理，好了跟你說；這期間照常問我"
DEFAULT_FAIL = "製卡助手沒做成"
TWICE_FAIL = "製卡助手兩次沒做成：{}"
CARDING = "skills/ingest/references/carding.md"
READ = {"cards": [f"{CARDING} 批次、材料的欄位、一～七、九節"], "agenda": [f"{CARDING} 批次、材料的欄位、第九節"],
        "synthesis": ["rules/synthesis.md", f"{CARDING} 批次、材料的欄位、第八節"]}
HOLD_FACTOR = 3  # 過期但子代理沒停：認領到幾倍時間
_REPORT_LINE = re.compile(r"^\s*-\s*(\S+?)\s*[：:]\s*(.+?)\s*$")
_TIME = "%Y-%m-%dT%H:%M:%S"


class Unreadable(ValueError):
    pass


class Refused(Exception):
    pass


def _now():
    return datetime.now().replace(microsecond=0)


def _path(vault):
    return Path(vault) / BATCHES_POSIX


def empty():
    return {"schema_version": SCHEMA_VERSION, "jobs": [], "batches": [], "agents": []}


def load(vault):
    """沒檔回空的；讀不懂或形狀不對丟 Unreadable（訊息帶檔名）。"""
    try:
        data = read_json(_path(vault))
    except ValueError as e:
        raise Unreadable(f"{BATCHES_POSIX}：{e}") from None
    if data is None:
        return empty()
    if not (isinstance(data, dict) and all(isinstance(data.get(k), list) for k in ("jobs", "batches", "agents"))):
        raise Unreadable(f"{BATCHES_POSIX}：形狀不對")
    if not isinstance(data.get("failures", {}), dict):
        raise Unreadable(f"{BATCHES_POSIX}：形狀不對")
    return data


def lock(vault):
    _path(vault).parent.mkdir(parents=True, exist_ok=True)
    return FileLock(_path(vault))


def save(vault, data, turn_id):
    """寫前備進回合（by program：程式自己的帳，大改動不數），清掉結束很久的，再整檔原子寫。呼叫端拿著鎖。"""
    _cleanup(vault, data)
    turn.backup(vault, BATCHES_POSIX, turn_id, by="program")
    write_json_atomic(_path(vault), data)


def _new_id(prefix, taken):
    while True:
        got = f"{prefix}-{secrets.token_hex(4)}"
        if got not in taken:
            return got


def _parse(text):
    try:
        return datetime.strptime(str(text), _TIME)
    except (TypeError, ValueError):
        return None


def _minutes(vault):
    return config.get(vault, "card_batch_expire_minutes")


def _agents(data):
    return {a.get("agent_id"): a for a in data["agents"]}


def live(batch, now, minutes, agents=None):
    """認領中：open、建立不到 minutes 分鐘；過期了但記上的子代理還在跑（沒 stopped）就認領到 HOLD_FACTOR 倍時間
    （第一輪修：不讓別的子代理重切同一份）。建立時間讀不懂當過期。"""
    made = _parse(batch.get("created"))
    if batch.get("status") != "open" or made is None:
        return False
    age = now - made
    if age < timedelta(minutes=minutes):
        return True
    agent = (agents or {}).get(batch.get("agent_id"))
    return bool(agent and not agent.get("stopped")) and age < timedelta(minutes=minutes * HOLD_FACTOR)


def running(data, session_id, now, minutes):
    """這個 session 認領中的批次（換了 session 的舊認領不算）。"""
    agents = _agents(data)
    return [b for b in data["batches"] if live(b, now, minutes, agents) and _job_session(data, b) == session_id]


def _job_of(data, batch):
    return next((j for j in data["jobs"] if j.get("job") == batch.get("job")), None)


def _job_session(data, batch):
    job = _job_of(data, batch)
    return job.get("session_id") if job else None


def open_job(data, session_id):
    return next((j for j in data["jobs"] if j.get("session_id") == session_id and j.get("status") == "open"), None)


def session_of_agent(vault, agent_id):
    """子代理是哪個 session 派的（agents 反查）；查不到回 None。"""
    try:
        data = load(vault)
    except ValueError:
        return None
    a = _agents(data).get(agent_id)
    return a.get("session_id") if a else None


def job_turns_of_session(vault, session_id):
    """這個 session 的 job 回合（cli 驗 --turn 用）。"""
    try:
        data = load(vault)
    except ValueError:
        return set()
    return {j.get("turn") for j in data["jobs"] if j.get("session_id") == session_id}


def _cleanup(vault, data):
    """結束超過 card_batch_expire_minutes 的拿掉：關了的 job（每個 session 最後一個留著，Stop 要換它的結果）、
    沒有 job 的批次、停了的子代理。failures 留著（跨 job 的紀錄）。"""
    now = _now()
    limit = timedelta(minutes=_minutes(vault))
    last_closed = {}
    for j in data["jobs"]:
        if j.get("status") == "closed":
            last_closed[j.get("session_id")] = j.get("job")

    def old(stamp):
        t = _parse(stamp)
        return t is not None and now - t > limit
    data["jobs"] = [j for j in data["jobs"] if not (j.get("status") == "closed" and old(j.get("closed"))
                                                    and last_closed.get(j.get("session_id")) != j.get("job"))]
    jobs = {j.get("job") for j in data["jobs"]}
    data["batches"] = [b for b in data["batches"] if b.get("job") in jobs]
    data["agents"] = [a for a in data["agents"] if not (a.get("stopped") and old(a.get("stopped")))]


# ---------- 項目 ----------

def key_of(bucket, item):
    if bucket == "synthesis_broken":
        return item["path"]
    if bucket in ("synthesis_stale", "synthesis_new"):
        return item["entity_id"]
    return item["id"]


def _entry_of_item(bucket, item, by_id):
    """項目對到的清冊條目（看來源、看是不是 link）：卡的 id；agenda_recheck 是出處卡；綜合卡沒有。"""
    if bucket == "agenda_recheck":
        return by_id.get(item.get("card"))
    if bucket in SYNTHESIS_BUCKETS:
        return None
    return by_id.get(item.get("id"))


def _locked_ids(vault):
    from kb._lib import pending
    try:
        items = pending.load(vault)["items"]
    except ValueError:
        return set()
    from kb import convert
    return {(i.get("file_ids") or [None])[0] for i in items
            if i.get("kind") == "unreadable" and str(i.get("reason") or "").startswith(LOCKED_HEAD)
            and (i.get("status") == "open" or (i.get("status") == "done" and i.get("how") == convert.OPENED_HOW))}


def _purge_prefixes_of(pend):
    from kb._lib import purge
    return [purge.folder_uri(p["alias"], p["spec"]) for p in pend or [] if p.get("spec")]


def _in_any(uri, prefixes):
    from kb._lib import purge
    return any(purge._in_folder(uri, p) for p in prefixes)


def main_only(entry, locked):
    """主對話自己做、不切進批次：link 條目、檔名像密碼的、待處理記過有密碼的。"""
    return entry is not None and (inventory.is_link(entry) or entry.get("read") == "credential"
                                  or entry.get("id") in locked)


def _by_id(vault):
    try:
        return {e.get("id"): e for e in inventory.load(vault)["entries"]}
    except (ValueError, OSError):
        return {}


def split(vault, out, source=None):
    """todo 的結果分成 {"cards","agenda","synthesis","main"}，每項 (bucket, key, item)。
    source 給了只留那個來源的項目（綜合卡不受限）；purge pending 那夾的不切也不列；broken 沒有 header 的不列（照舊不處理）。"""
    by_id = _by_id(vault)
    locked = _locked_ids(vault)
    prefixes = _purge_prefixes_of(out.get("purge_pending"))
    got = {k: [] for k in KINDS + ("main",)}
    seen = set()

    def place(kind, bucket, item):
        key = key_of(bucket, item)
        if (kind, key) in seen:
            return
        entry = _entry_of_item(bucket, item, by_id)
        uri = entry["locations"][0]["uri"] if entry and entry.get("locations") else None
        if uri and prefixes and _in_any(uri, prefixes):
            return
        if source is not None and kind != "synthesis" and inventory.split_uri(uri or "")[0] != source:
            return
        seen.add((kind, key))
        if kind == "cards" and main_only(entry, locked):
            got["main"].append((bucket, key, item))
        else:
            got[kind].append((bucket, key, item))  # 日程照卡抽，主對話做的卡也交得出去
    for bucket in CARD_BUCKETS:
        for item in out.get(bucket) or []:
            if bucket == "broken" and not (item.get("id") and item.get("header")):
                continue
            place("cards", bucket, item)
    for bucket in AGENDA_BUCKETS:
        for item in out.get(bucket) or []:
            place("agenda", bucket, item)
    for bucket in SYNTHESIS_BUCKETS:
        for item in out.get(bucket) or []:
            if bucket == "synthesis_new" and not item.get("confirmed"):
                continue  # 名字沒確認的等 asks
            place("synthesis", bucket, item)
    return got


def waiting(vault, out):
    """要整理的份數（plan 的門檻與 announce）：派得出去的 new、stale、unlinked 加 agenda_unscanned（跟 dispatch 同一套排除，
    第一輪修）；主對話的同一種加密 PDF 由 plan 另外加。"""
    parts = split(vault, out)
    return (sum(1 for b, _k, _i in parts["cards"] if b != "broken")
            + sum(1 for b, _k, _i in parts["agenda"] if b == "agenda_unscanned"))


def _collect(vault):
    from kb._lib import cards
    from kb._lib import sources as sources_lib
    return cards.collect(vault, sources_lib.load(vault))


def _settings(vault):
    return {k: config.get(vault, k) for k in ("card_batch_size", "card_parallel", "synthesis_batch_size",
                                               "card_batch_expire_minutes", "big_change_synthesis_max")}


def prefix():
    return f'python "{plugin_root().as_posix()}/scripts/kb.py"'


def _what(kind, n):
    return f"{KIND_TEXT[kind]} {n} 張" if kind == "synthesis" else f"{KIND_TEXT[kind]} {n} 份"


def check_source(vault, source):
    """--source 的別名要登記過；不是丟 Refused（一字不差的拒絕句，第一輪修）。"""
    from kb._lib import sources as sources_lib
    if source is not None and source not in {s["alias"] for s in sources_lib.load(vault).entries}:
        raise Refused(NO_SOURCE.format(source))


# ---------- 沒做成的（S2） ----------

def _record_failures(vault, data, job, parts_all, now):
    """這個 job 做完（done）的批次裡、現在還在待做的項目記進 job.failed（原因取回報抄的，沒有用 DEFAULT_FAIL）；
    做卡的項目另記進 failures（跨 job），同一份指紋沒變、兩個 job 都沒做成就照讀不到記進待處理。回有沒有改到。"""
    pending_now = {(k, key) for k in KINDS for _b, key, _i in parts_all[k]}
    failed = job.setdefault("failed", {})
    changed = False
    by_id = None
    for b in data["batches"]:
        if b.get("job") != job["job"] or b.get("status") != "done":
            continue
        for key in b.get("items") or []:
            if key in failed or (b["kind"], key) not in pending_now:
                continue
            reason = (b.get("failed") or {}).get(key) or DEFAULT_FAIL
            failed[key] = {"kind": b["kind"], "reason": reason}
            changed = True
            if b["kind"] == "cards":
                by_id = by_id if by_id is not None else _by_id(vault)
                _cross_job(vault, data, job, key, reason, by_id.get(key))
    return changed


def _cross_job(vault, data, job, key, reason, entry):
    from kb._lib import cards, pending
    fp = cards.current_fp(entry) if entry else None
    rec = data.setdefault("failures", {}).get(key)
    if rec is None or rec.get("fp") != fp:
        rec = {"fp": fp, "jobs": [], "reason": reason}
    if job["job"] not in rec["jobs"]:
        rec["jobs"].append(job["job"])
    rec["reason"] = reason
    data["failures"][key] = rec
    if len(rec["jobs"]) >= 2 and entry is not None:
        label = pending.file_label(entry["locations"][0]["uri"]) if entry.get("locations") else f"「{key}」"
        pending.add(vault, "unreadable", [key], f"{label}讀不到", TWICE_FAIL.format(reason), job["turn"])


# ---------- dispatch ----------

def dispatch(vault, session_id, turn_id, source=None):
    """派工（kb cards dispatch，D-196、D-197）：沿用或新開這個 session 的 job，從沒被認領、這個 job 沒失敗過的項目切批，
    最多到 card_parallel 個 open；順序 cards → agenda → synthesis，前一種有剩或還在跑就不建後一種；synthesis 前算大改動門檻
    （D-075，綜合卡 stale 與 broken 都算），超過又沒同意回 approval。什麼都不剩也沒在跑：job 關掉、回 note_cmd。
    回 {"job","turn","batches","running","left","approval","done","note_cmd","stopped","failed","say"}。
    job 記著 source：之後不帶 --source 的再派照它。這一句剛跑過 cards stop：不派、stopped 是 true（D-200）。
    算 todo 也在鎖裡：子代理剛做完的不會被重切（第一輪修）。"""
    check_source(vault, source)
    st = _settings(vault)
    now = _now()
    with lock(vault):
        data = load(vault)
        mine = [j for j in data["jobs"] if j.get("session_id") == session_id]
        if mine and mine[-1].get("status") == "closed" and mine[-1].get("stopped_turn") == turn_id:
            return {"job": None, "turn": turn_id, "batches": [], "running": 0, "left": {k: 0 for k in KINDS},
                    "approval": None, "done": False, "note_cmd": None, "stopped": True, "failed": [], "say": None}
        job = open_job(data, session_id)
        fresh = job is None
        if fresh:
            job = {"job": _new_id("j", {j.get("job") for j in data["jobs"]}), "session_id": session_id, "turn": turn_id,
                   "created": now.strftime(_TIME), "status": "open", "source": source}
        elif source is None:
            source = job.get("source")
        out = _collect(vault)
        parts_all = split(vault, out)
        parts = split(vault, out, source) if source is not None else parts_all
        changed = False if fresh else _record_failures(vault, data, job, parts_all, now)
        failed = job.get("failed") or {}
        jt = job["turn"]
        run = running(data, session_id, now, st["card_batch_expire_minutes"])
        claimed = {(b["kind"], k) for b in run for k in b.get("items") or []}
        left = {k: [x for x in parts[k] if (k, x[1]) not in claimed and x[1] not in failed] for k in KINDS}
        slots = max(0, st["card_parallel"] - len(run))
        made, approval = [], None
        for kind in KINDS:
            busy = any(b["kind"] == kind for b in run)
            if not left[kind]:
                if busy:
                    break
                continue
            if kind == "synthesis":
                approval = _approval(vault, jt, left[kind], st)
                if approval:
                    break
            size = st["synthesis_batch_size"] if kind == "synthesis" else st["card_batch_size"]
            while slots and left[kind]:
                chunk, left[kind] = left[kind][:size], left[kind][size:]
                bid = _new_id("b", {b.get("batch") for b in data["batches"]})
                made.append({"batch": bid, "job": job["job"], "kind": kind, "items": [x[1] for x in chunk],
                             "source": source, "created": now.strftime(_TIME), "agent_id": None, "status": "open"})
                data["batches"].append(made[-1])
                slots -= 1
            break
        counts = {k: len(v) for k, v in left.items()}
        done = not made and not run and approval is None and not any(counts.values())
        if fresh and (made or approval):
            data["jobs"].append(job)
            changed = True
        if made:
            changed = True
        if done and not fresh:
            job["status"] = "closed"
            job["closed"] = now.strftime(_TIME)
            changed = True
        if changed:
            save(vault, data, jt)
    pre = prefix()
    first = fresh and bool(made)
    n0 = sum(1 for b in data["batches"] if b.get("job") == job["job"]) - len(made)
    shown = [{"batch": b["batch"], "kind": b["kind"], "items": len(b["items"]), "agent": AGENT,
              "description": DESCRIPTION.format(n=n0 + i + 1, what=_what(b["kind"], len(b["items"]))),
              "prompt": PROMPT.format(batch=b["batch"], what=_what(b["kind"], len(b["items"])), prefix=pre)}
             for i, b in enumerate(made)]
    has_job = not (fresh and not (made or approval))
    return {"job": job["job"] if has_job else None, "turn": jt, "batches": shown, "running": len(run), "left": counts,
            "approval": approval, "done": done,
            "note_cmd": f'kb turn note --turn {jt} "{{做了什麼}}"' if done and not fresh else None, "stopped": False,
            "failed": [{"id": k, "kind": v["kind"], "reason": v["reason"]} for k, v in failed.items()],
            "say": SAY_FIRST if first else None}


def _approval(vault, jt, items, st):
    """job 回合已改的既有綜合卡加這次要派的 synthesis_stale、synthesis_broken 張數超過上限、回合沒同意：回問句與指令（D-075）。
    broken 也算：寫回同一個路徑的也是改既有綜合卡，守門的 before_write 一樣會數（第一輪修）。"""
    data = turn.turn_data(vault, jt) or {}
    if data.get("approved"):
        return None
    n = len(data.get("ai_synthesis") or []) + sum(1 for bucket, _k, _i in items
                                                   if bucket in ("synthesis_stale", "synthesis_broken"))
    if n <= st["big_change_synthesis_max"]:
        return None
    return {"question": APPROVAL_QUESTION.format(n=n), "do": [f"kb turn approve --turn {jt}"]}


# ---------- batch ----------

def batch_view(vault, batch_id, agent_id=None):
    """card-maker 拿自己那批（kb cards batch）：項目照 todo 的欄位、多 bucket（來自哪一桶；不用 type，synthesis_new 的
    type 是實體型別）；做完了的項目不再列。批次不在、不是 open、或過期丟 Refused。agent_id 給了就記上這批（第一次記、之後不換）。"""
    st = _settings(vault)
    now = _now()
    data = load(vault)
    b = next((x for x in data["batches"] if x.get("batch") == batch_id), None)
    if b is None or not live(b, now, st["card_batch_expire_minutes"], _agents(data)):
        raise Refused(STOPPED)
    if agent_id and not b.get("agent_id"):
        with lock(vault):
            data = load(vault)
            b2 = next((x for x in data["batches"] if x.get("batch") == batch_id), None)
            if b2 is not None and not b2.get("agent_id"):
                b2["agent_id"] = agent_id
                job = _job_of(data, b2) or {}
                save(vault, data, job.get("turn") or turn.current(vault)["turn"])
    out = _collect(vault)
    want = list(b.get("items") or [])
    buckets = {"cards": CARD_BUCKETS, "agenda": AGENDA_BUCKETS, "synthesis": SYNTHESIS_BUCKETS}[b["kind"]]
    found = {}
    for bucket in buckets:
        for item in out.get(bucket) or []:
            k = key_of(bucket, item)
            if k in want and k not in found:
                found[k] = dict(item, bucket=bucket)
    root = plugin_root().as_posix()
    return {"batch": batch_id, "kind": b["kind"], "prefix": prefix(), "read": [f"{root}/{r}" for r in READ[b["kind"]]],
            "items": [found[k] for k in want if k in found], "profile": out.get("profile"), "entities": out.get("entities")}


# ---------- status、stop ----------

def _done_count(data, job, out):
    """這次做好幾份：這個 job 的 cards 批次裡、現在已不在待做的卡。"""
    if job is None:
        return 0
    pending_keys = {key_of(b, i) for b in CARD_BUCKETS for i in out.get(b) or []}
    items = {k for b in data["batches"] if b.get("job") == job["job"] and b.get("kind") == "cards" for k in b["items"]}
    return len(items - pending_keys)


def status(vault, session_id):
    """只讀：{"job","running":[{"batch","kind","items"}],"done","left":{kind: n},"waiting","failed"}。"""
    st = _settings(vault)
    now = _now()
    data = load(vault)
    out = _collect(vault)
    parts = split(vault, out)
    job = open_job(data, session_id)
    run = running(data, session_id, now, st["card_batch_expire_minutes"]) if job else []
    claimed = {(b["kind"], k) for b in run for k in b.get("items") or []}
    failed = (job or {}).get("failed") or {}
    left = {k: sum(1 for x in parts[k] if (k, x[1]) not in claimed and x[1] not in failed) for k in KINDS}
    return {"job": job["job"] if job else None,
            "running": [{"batch": b["batch"], "kind": b["kind"], "items": len(b["items"])} for b in run],
            "done": _done_count(data, job, out), "left": left, "waiting": waiting(vault, out), "failed": len(failed)}


def stop(vault, session_id, turn_id=None):
    """停（D-200）：這個 session 的 open 批次改 released、job 關掉（記 stopped_turn：這一句之後收到的通知不再派）；
    回 {"job","agents","done","left"}，沒有 job 回 None。
    agents 是要停的子代理：這些批次記的 agent_id，加上這個 job 回合裡還沒收到 SubagentStop 的。"""
    now = _now()
    with lock(vault):
        out = _collect(vault)
        data = load(vault)
        job = open_job(data, session_id)
        if job is None:
            return None
        agents = []
        for b in data["batches"]:
            if b.get("status") == "open" and _job_session(data, b) == session_id:
                b["status"] = "released"
                if b.get("agent_id"):
                    agents.append(b["agent_id"])
        agents += [a["agent_id"] for a in data["agents"] if a.get("session_id") == session_id
                   and a.get("turn") == job["turn"] and not a.get("stopped")]
        job["status"] = "closed"
        job["closed"] = now.strftime(_TIME)
        job["stopped_turn"] = turn_id
        save(vault, data, job["turn"])
    parts = split(vault, out)
    return {"job": job["job"], "turn": job["turn"], "agents": list(dict.fromkeys(agents)),
            "done": _done_count(data, job, out), "left": sum(len(parts[k]) for k in KINDS)}


# ---------- 子代理 ----------

def agent_start(vault, session_id, agent_id):
    """SubagentStart：記 agent_id → 回合（這個 session open 的 job 的回合，沒有 job 取 session 的目前回合）。回那個回合。"""
    with lock(vault):
        data = load(vault)
        job = open_job(data, session_id)
        if job is not None:
            tid = job["turn"]
        else:
            now_turn = turn._read_turn(turn._session_file(vault, session_id)) if session_id else None
            tid = (now_turn or turn.current(vault, session_id))["turn"]
        if any(a.get("agent_id") == agent_id for a in data["agents"]):
            return next(a["turn"] for a in data["agents"] if a.get("agent_id") == agent_id)
        data["agents"].append({"agent_id": agent_id, "session_id": session_id, "turn": tid,
                               "started": _now().strftime(_TIME)})
        save(vault, data, tid)
    return tid


def report_failures(message):
    """card-maker 的回報裡「- <編號>：<原因>」那幾行 → {編號: 原因}（原因不轉述給客戶以外的地方，只給 tell）。"""
    got = {}
    for line in str(message or "").splitlines():
        m = _REPORT_LINE.match(line)
        if m:
            got[m.group(1)] = m.group(2)[:200]
    return got


def agent_stop(vault, session_id, agent_id, message=None):
    """SubagentStop：這個子代理記上的 open 批次改 done（回報裡沒做成的原因抄進批次的 failed）、agents 那筆記 stopped。
    沒認領過批次就停了（第一次 cards batch 之前死掉）：這個 session 還沒人認領的批次比還在跑、沒認領的子代理多出來的，
    從新的放掉（released），不用等過期（第一輪修）。回它的回合（沒記過回 None）。"""
    reasons = report_failures(message)
    now = _now()
    with lock(vault):
        data = load(vault)
        agent = next((a for a in data["agents"] if a.get("agent_id") == agent_id
                      and (not session_id or a.get("session_id") == session_id)), None)
        if agent is None:
            return None
        claimed = False
        for b in data["batches"]:
            if b.get("agent_id") == agent_id and b.get("status") == "open":
                b["status"] = "done"
                b["failed"] = {k: v for k, v in reasons.items() if k in (b.get("items") or [])}
                claimed = True
        agent["stopped"] = now.strftime(_TIME)
        if not claimed:
            _release_orphans(data, agent.get("session_id"), now, _minutes(vault))
        save(vault, data, agent["turn"])
    return agent["turn"]


def _release_orphans(data, session_id, now, minutes):
    agents = _agents(data)
    mine = [b for b in data["batches"] if _job_session(data, b) == session_id and live(b, now, minutes, agents)]
    holders = {b.get("agent_id") for b in mine if b.get("agent_id")}
    waiting_agents = [a for a in data["agents"] if a.get("session_id") == session_id and not a.get("stopped")
                      and a.get("agent_id") not in holders]
    unclaimed = [b for b in mine if not b.get("agent_id")]
    extra = len(unclaimed) - len(waiting_agents)
    for b in reversed(unclaimed):
        if extra <= 0:
            break
        b["status"] = "released"
        extra -= 1


def job_turns(vault, session_id):
    """主對話 Stop 要補「改了」的 job：這個 session open 的 job，加上最近一個關了、還沒換過結果的（回合檔沒記 job_result）。
    回 [(job, 關了嗎)]。"""
    try:
        data = load(vault)
    except ValueError:
        return []
    mine = [j for j in data["jobs"] if j.get("session_id") == session_id]
    got = [(j, False) for j in mine if j.get("status") == "open"]
    closed = [j for j in mine if j.get("status") == "closed"]
    if closed:
        last = closed[-1]
        if not (turn.turn_data(vault, last["turn"]) or {}).get("job_result"):
            got.append((last, True))
    return got


def active_job(vault, session_id, within_minutes=None):
    """這個 session 有在整理（open 的 job），或 within_minutes 分鐘內剛關的嗎（日誌判斷「對話裡」、按停止偵測用）。"""
    try:
        data = load(vault)
    except ValueError:
        return False
    now = _now()
    for j in data["jobs"]:
        if j.get("session_id") != session_id:
            continue
        if j.get("status") == "open":
            return True
        closed = _parse(j.get("closed"))
        if within_minutes and closed is not None and now - closed < timedelta(minutes=within_minutes):
            return True
    return False


# ---------- 開場數字 ----------

def opening_counts(vault, sources, deadline=None):
    """開場那行（D-161、B-415）：(還沒整理的份數, 要跟著搬的卡)。份數是 todo 的 new＋stale，跟 dispatch 同一套排除
    （link、檔名像密碼的、記過有密碼的、還沒清的排除夾，第一輪修）；搬的是 moved。只讀，不寫檔。"""
    from kb._lib import cards, entities
    from kb._lib import sources as sources_lib
    entries = inventory.load(vault)["entries"]
    out, _groups, _data = cards._sort_files(vault, sources, entities.load(vault), entries, light=True, deadline=deadline)
    by_id = {e.get("id"): e for e in entries}
    prefixes = []
    pend = sources_lib.pending_purges(sources)
    if pend:
        from kb._lib import purge
        prefixes = [purge.folder_uri(p["alias"], p["spec"]) for p in pend]
    locked = _locked_ids(vault)
    n = 0
    for item in out["new"] + out["stale"]:
        e = by_id.get(item["id"])
        uri = e["locations"][0]["uri"] if e and e.get("locations") else ""
        if main_only(e, locked) or (prefixes and _in_any(uri, prefixes)):
            continue
        n += 1
    return n, len(out["moved"])
