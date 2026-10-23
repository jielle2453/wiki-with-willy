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
檔名像密碼的（含裁示可以讀的）、待處理記過「有密碼」（還開著或用密碼打開了）的、找不到是哪個實體的壞綜合卡（第二輪修 R1）；
manage 第二版還沒清的排除夾（purge pending）裡的不切也不算。
壞的綜合卡（synthesis_broken）派給製卡助手時，batch 給跟 synthesis_stale 一樣齊的材料：該有的檔頭（synthesis.header_for）、
該放的路徑（target）、掛的檔案卡、有效口述、裁示、推算、段落（第二輪修 R1，之前只給 path 與 reason，十次全做不成）。
dispatch 每次都給 say：第一次派出去、之後每次收通知再派的進度、做完的收尾，主對話對客戶只說它（第二輪修 R2）。
壞的與要更新的綜合卡（synthesis_broken、synthesis_stale），實體還沒跟客戶確認名字（source inferred）的不切：
plan 的 asks 出名字題（unconfirmed_names），客戶確認之後下一次 dispatch 才派（第三輪修 R5）；AI 判斷登記的（ai_judged，D-202）算確認。
build（第 6 點）：dispatch --ids 只派那幾份（建置抽樣；job 記著 ids，之後再派照它）；第四種工作 adopt（dispatch_adopt）：
build adopt --extract 切好的段（.system/temp/adopt/*.txt）分批交給 card-maker 讀、回報候選；adopt 的 job 帶 kind adopt，
跟整理的 job 分開（open_job 不認它），段讀完（批次 done）就不再派，回報裡「- <段>：<原因>」的記成沒讀成。
build 第一輪修：release_session（接手建置時把前一個對話的 job 與 open 批次放掉，同 stop、不記 stopped_turn，S1）；
close_ids_job（done sample 收掉抽樣的 ids job，S3）；dispatch 帶 --ids 時 job 開著也驗編號（S3）。
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
KIND_TEXT = {"cards": "做卡", "agenda": "日程", "synthesis": "綜合卡", "adopt": "舊 wiki"}
ADOPT = "adopt"
ADOPT_POSIX = ".system/temp/adopt"
ADOPT_BATCH_CHUNKS = 4  # 一批讀幾段（每段最多 adopt_chunk_chars 字）：一個子代理讀四段約五萬字，不會被摺疊
NO_IDS = "這幾份不在要整理的清單裡：{}"
NO_CHUNKS = "還沒切段，先跑 kb build adopt <舊 wiki 的路徑> --extract"
SAY_ADOPT_FIRST = "交給製卡助手在背景讀舊的 wiki，好了跟你說；這期間照常問我"
SAY_ADOPT_PROGRESS = "讀好 {done} 段了{failed}；還在讀 {running} 批"
SAY_ADOPT_DONE = "舊的 wiki 讀完了{failed}"
SAY_ADOPT_FAILED = "，{n} 段沒讀成"
ADOPT_REPORT = ("批次 <批次>：讀了 N 段、沒讀成 M\n"
                "口述｜<實體名稱或「無」>｜<YYYY-MM-DD 或「未載」>｜<一句>\n"
                "規則｜<一句>\n"
                "日程｜<YYYY-MM-DD>｜<實體名稱或「無」>｜<一句>\n"
                "待確認｜<一句>\n"
                "- <段名>：<沒讀成的原因一句>")
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
SAY_PROGRESS = "做好 {done} 份了{failed}；還在做 {running} 批"
SAY_RUNNING = "還在做 {running} 批"
SAY_DONE = "整理完了：做好 {done} 份{failed}"
SAY_DONE_NONE = "整理完了：{n} 份沒做成"
SAY_FAILED = "，{n} 份沒做成"
DEFAULT_FAIL = "製卡助手沒做成"
TWICE_FAIL = "製卡助手兩次沒做成：{}"
CARDING = "skills/ingest/references/carding.md"
READ = {"cards": [f"{CARDING} 批次、材料的欄位、一～七、九節"], "agenda": [f"{CARDING} 批次、材料的欄位、第九節"],
        "synthesis": ["rules/synthesis.md", f"{CARDING} 批次、材料的欄位、第八節"],
        "adopt": [f"{CARDING} 批次、第十二節"]}
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
    """這個 session open 的整理 job（adopt 的不算）。"""
    return next((j for j in data["jobs"] if j.get("session_id") == session_id and j.get("status") == "open"
                 and j.get("kind") != ADOPT), None)


def _open_adopt(data, session_id):
    return next((j for j in data["jobs"] if j.get("session_id") == session_id and j.get("status") == "open"
                 and j.get("kind") == ADOPT), None)


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


class _SynthesisContext:
    """壞綜合卡要的材料：實體清單、掛載、設定檔、口述裁示推算、編號 → 綜合卡檔（懶載入，只在有 synthesis_broken 時讀）。"""

    def __init__(self, vault):
        from kb._lib import cards, entities, synthesis
        self.vault = vault
        self.data = entities.load(vault)
        self.mounts = entities.mounts(vault, self.data)
        self.prof = cards._profile_or_default(vault)[0]
        self.know = synthesis.knowledge(vault)
        self.files = entities.synthesis_files(vault)

    def entity(self, entity_id):
        return next((e for e in self.data["entities"] if e.get("id") == entity_id), None)

    def owner(self, path):
        """這張綜合卡是誰的：該放在這個路徑的實體，或檔頭編號指到的實體；有型別、還在（不是 merged）才算。"""
        from kb._lib import synthesis
        key = str(path).casefold()
        live = [e for e in self.data["entities"] if e.get("type") and e.get("status") != "merged"]
        hit = next((e for e in live if synthesis.card_rel(e).casefold() == key), None)
        if hit is not None:
            return hit
        by_id = {e.get("id"): e for e in live}
        for eid, rels in self.files.items():
            if any(str(r).casefold() == key for r in rels) and eid in by_id:
                return by_id[eid]
        return None

    def material(self, item):
        from datetime import date
        from kb._lib import synthesis
        e = self.owner(item["path"])
        if e is None:
            return None
        ms = self.mounts.get(e["name"], [])
        stmts, ruls, finds = synthesis.basis(self.know, e["name"], self.mounts)
        return dict(item, entity_id=e["id"], name=e["name"], type=e["type"],
                    category=synthesis.category_text(self.prof, e["type"]), target=synthesis.card_rel(e),
                    header=synthesis.header_for(self.vault, e, ms, date.today().isoformat(), self.data, self.mounts,
                                                self.prof, self.know),
                    sections=synthesis.sections_for(self.prof, e["type"]), cards=[synthesis._brief(m) for m in ms],
                    statements=[synthesis.statement_brief(x) for x in stmts],
                    rulings=[synthesis.ruling_brief(self.vault, self.know, r) for r in ruls],
                    findings=[synthesis.finding_brief(self.vault, self.know, c, self.mounts) for c in finds])


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
    ctx = []  # 有 synthesis_broken 才建 _SynthesisContext

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
        elif bucket == "synthesis_broken" and not _fixable(vault, ctx, item):
            got["main"].append((bucket, key, item))  # 找不到是誰的綜合卡：製卡助手沒材料修，留給主對話
        elif bucket in ("synthesis_broken", "synthesis_stale") and _unconfirmed(vault, ctx, bucket, item) is not None:
            return  # 名字還沒確認：asks 先問（R5）
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


def _fixable(vault, ctx, item):
    try:
        return _context(vault, ctx).owner(item["path"]) is not None
    except ValueError:  # 實體清單讀不到：照舊交出去，製卡助手回報做不成
        return True


def _context(vault, ctx):
    if not ctx:
        ctx.append(_SynthesisContext(vault))
    return ctx[0]


def _unconfirmed(vault, ctx, bucket, item):
    """壞的或要更新的綜合卡，它的實體還沒確認名字（source inferred）就回那個實體，否則 None；實體清單讀不到回 None。"""
    try:
        c = _context(vault, ctx)
    except ValueError:
        return None
    e = c.entity(item.get("entity_id")) if bucket == "synthesis_stale" else c.owner(item["path"])
    return e if e is not None and e.get("source") == "inferred" else None


NAME_QUESTION = "我要幫「{name}」（{type}，{mounted}）整理一頁現況：名字這樣叫對嗎？它跟其他{type}是同一件嗎？"
MOUNTED = "掛了 {n} 份檔"
NO_CARDS_YET = "還沒做卡"  # 掛 0 份（卡還沒做或都拿掉了）不說「掛了 0 份檔」（build 交界）


def unconfirmed_names(vault, out):
    """synthesis_broken、synthesis_stale 裡實體還沒確認名字的：[{"entity_id","name","type","question"}]，同一個實體一題，
    synthesis_new 已經在問的不重複（plan 的名字題，第三輪修 R5）。問法跟 synthesis_new 的同一句。"""
    asked = {i.get("entity_id") for i in out.get("synthesis_new") or [] if not i.get("confirmed")}
    ctx, got = [], []
    for bucket in ("synthesis_broken", "synthesis_stale"):
        for item in out.get(bucket) or []:
            e = _unconfirmed(vault, ctx, bucket, item)
            if e is None or e["id"] in asked:
                continue
            asked.add(e["id"])
            n = len(_context(vault, ctx).mounts.get(e["name"], []))
            got.append({"entity_id": e["id"], "name": e["name"], "type": e.get("type"),
                        "question": NAME_QUESTION.format(name=e["name"], type=e.get("type"),
                                                         mounted=MOUNTED.format(n=n) if n else NO_CARDS_YET)})
    return got


def waiting(vault, out):
    """要整理的份數（plan 的門檻與 announce）：new、stale、unlinked 加 agenda_unscanned，扣掉還沒清的排除夾裡的；
    主對話自己做的（線上文件、可以讀的密碼檔、用密碼打開的）照算，不然它們永遠不會被提醒（22 第一輪驗後）。
    記成有密碼還沒打開、記過讀不到的連結，todo 本來就不列。同一種的加密 PDF 由 plan 另外加。"""
    parts = split(vault, out)
    return (sum(1 for b, _k, _i in parts["cards"] + parts["main"] if b in ("new", "stale", "unlinked"))
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
    if kind == ADOPT:
        return f"讀{KIND_TEXT[kind]} {n} 段"
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

def _only_ids(parts, ids):
    """--ids：只留 cards 裡那幾份，其他種類不派（建置抽樣，build 第 6 點）。"""
    want = set(ids)
    got = {k: [] for k in parts}
    got["cards"] = [x for x in parts["cards"] if x[1] in want]
    return got


def dispatch(vault, session_id, turn_id, source=None, ids=None):
    """派工（kb cards dispatch，D-196、D-197）：沿用或新開這個 session 的 job，從沒被認領、這個 job 沒失敗過的項目切批，
    最多到 card_parallel 個 open；順序 cards → agenda → synthesis，前一種有剩或還在跑就不建後一種；synthesis 前算大改動門檻
    （D-075，綜合卡 stale 與 broken 都算），超過又沒同意回 approval。什麼都不剩也沒在跑：job 關掉、回 note_cmd。
    回 {"job","turn","batches","running","left","approval","done","note_cmd","stopped","failed","say"}。
    job 記著 source：之後不帶 --source 的再派照它。這一句剛跑過 cards stop：不派、stopped 是 true（D-200）。
    算 todo 也在鎖裡：子代理剛做完的不會被重切（第一輪修）。
    ids（build 抽樣）：只派這幾份做卡，不在 todo 可派的做卡項目裡的丟 Refused；job 記著，之後不帶 --ids 的再派照它。"""
    check_source(vault, source)
    explicit_ids = bool(ids)
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
            inherited = _same_turn_failed(data, session_id, turn_id)
            if inherited:
                job["failed"] = inherited
            if ids:
                job["ids"] = list(ids)
        else:
            if source is None:
                source = job.get("source")
            if ids is None:
                ids = job.get("ids")
        out = _collect(vault)
        parts_all = split(vault, out)
        parts = split(vault, out, source) if source is not None else parts_all
        if ids:
            if explicit_ids:  # 這一句給的 --ids：job 開著也驗（做過的那幾份算認得，build 第一輪修 S3）
                known = {x[1] for x in parts["cards"]} | {k for b in data["batches"] if b.get("job") == job["job"]
                                                          for k in b.get("items") or []}
                missing = [i for i in ids if i not in known]
                if missing:
                    raise Refused(NO_IDS.format("、".join(missing)))
            parts = _only_ids(parts, ids)
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
    say = None if (fresh and not made) else _say(first, done, approval, made, run, _done_count(data, job, out), len(failed))
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
            "say": say}


def _say(first, done, approval, made, run, done_n, failed_n):
    """給客戶的那一句（繁中，第二輪修 R2）：第一次派出去說交給製卡助手；之後每次（收通知再派）說進度；做完說收尾；
    要問大改動、或什麼都沒做的不說（approval 有自己的問句）。"""
    tail = SAY_FAILED.format(n=failed_n) if failed_n else ""
    if first:
        return SAY_FIRST
    if approval is not None:
        return None
    if done:
        if not done_n:
            return SAY_DONE_NONE.format(n=failed_n) if failed_n else None
        return SAY_DONE.format(done=done_n, failed=tail)
    if not (done_n or failed_n):
        return SAY_RUNNING.format(running=len(run) + len(made))
    return SAY_PROGRESS.format(done=done_n, failed=tail, running=len(run) + len(made))


def _same_turn_failed(data, session_id, turn_id):
    """同一個 session、同一個派工回合裡已經關掉的 job 的 failed（22 第二輪驗後）：同一句話做完又叫一次 dispatch 不算第二次整理，
    沿用它們、不重切、不再記失敗；換了回合才算第二次。"""
    got = {}
    for j in data["jobs"]:
        if j.get("session_id") == session_id and j.get("turn") == turn_id and j.get("status") == "closed":
            got.update(j.get("failed") or {})
    return got


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


# ---------- adopt（build 第 6 點，D-193） ----------

def adopt_chunks(vault):
    """build adopt --extract 切好的段：檔名（001.txt…）照順序。"""
    d = Path(vault) / ADOPT_POSIX
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix == ".txt")


def dispatch_adopt(vault, session_id, turn_id):
    """第四種工作（kb cards dispatch --adopt）：段分批交給 card-maker 讀，回報口述、規則、日程、待確認的候選。
    跟 dispatch 同一套認領、同時幾個；做完（批次 done）的段不再派，回報「- <段>：<原因>」的記進 failed。
    回 dispatch 同樣的鍵（left 是 {"adopt": n}）。這一句剛跑過 cards stop：不派。"""
    names = adopt_chunks(vault)
    if not names:
        raise Refused(NO_CHUNKS)
    st = _settings(vault)
    now = _now()
    with lock(vault):
        data = load(vault)
        mine = [j for j in data["jobs"] if j.get("session_id") == session_id and j.get("kind") == ADOPT]
        if mine and mine[-1].get("status") == "closed" and mine[-1].get("stopped_turn") == turn_id:
            return {"job": None, "turn": turn_id, "batches": [], "running": 0, "left": {ADOPT: 0}, "approval": None,
                    "done": False, "note_cmd": None, "stopped": True, "failed": [], "say": None}
        job = _open_adopt(data, session_id)
        fresh = job is None
        if fresh:
            job = {"job": _new_id("j", {j.get("job") for j in data["jobs"]}), "session_id": session_id, "turn": turn_id,
                   "created": now.strftime(_TIME), "status": "open", "kind": ADOPT}
        run = running(data, session_id, now, st["card_batch_expire_minutes"])
        mine_b = [b for b in data["batches"] if b.get("job") == job["job"]]
        read = {k for b in mine_b if b.get("status") == "done" for k in b.get("items") or []}
        failed = {k: v for b in mine_b if b.get("status") == "done" for k, v in (b.get("failed") or {}).items()}
        claimed = {k for b in run if b.get("kind") == ADOPT for k in b.get("items") or []}
        left = [n for n in names if n not in read and n not in claimed]
        slots = max(0, st["card_parallel"] - len(run))
        made = []
        while slots and left:
            chunk, left = left[:ADOPT_BATCH_CHUNKS], left[ADOPT_BATCH_CHUNKS:]
            bid = _new_id("b", {b.get("batch") for b in data["batches"]})
            made.append({"batch": bid, "job": job["job"], "kind": ADOPT, "items": chunk, "source": None,
                         "created": now.strftime(_TIME), "agent_id": None, "status": "open"})
            data["batches"].append(made[-1])
            slots -= 1
        busy = [b for b in run if b.get("kind") == ADOPT and b.get("job") == job["job"]]
        done = not made and not busy and not left
        changed = bool(made)
        if fresh and made:
            data["jobs"].append(job)
        if done and not fresh:
            job["status"], job["closed"] = "closed", now.strftime(_TIME)
            job["failed"] = {k: {"kind": ADOPT, "reason": v} for k, v in failed.items()}
            changed = True
        if changed:
            save(vault, data, job["turn"])
    tail = SAY_ADOPT_FAILED.format(n=len(failed)) if failed else ""
    if fresh and made:
        say = SAY_ADOPT_FIRST
    elif done and not fresh:
        say = SAY_ADOPT_DONE.format(failed=tail)
    elif fresh:
        say = None
    else:
        say = SAY_ADOPT_PROGRESS.format(done=len(read - set(failed)), failed=tail, running=len(busy) + len(made))
    pre = prefix()
    n0 = len(mine_b)
    shown = [{"batch": b["batch"], "kind": ADOPT, "items": len(b["items"]), "agent": AGENT,
              "description": DESCRIPTION.format(n=n0 + i + 1, what=_what(ADOPT, len(b["items"]))),
              "prompt": PROMPT.format(batch=b["batch"], what=_what(ADOPT, len(b["items"])), prefix=pre)}
             for i, b in enumerate(made)]
    return {"job": job["job"] if not (fresh and not made) else None, "turn": job["turn"], "batches": shown,
            "running": len(busy), "left": {ADOPT: len(left)}, "approval": None, "done": done and not fresh,
            "note_cmd": f'kb turn note --turn {job["turn"]} "{{做了什麼}}"' if done and not fresh else None,
            "stopped": False, "failed": [{"id": k, "kind": ADOPT, "reason": v} for k, v in failed.items()], "say": say}


def _adopt_view(vault, b):
    d = (Path(vault) / ADOPT_POSIX).resolve()
    items = [{"name": n, "chunk": (d / n).as_posix()} for n in b.get("items") or [] if (d / n).is_file()]
    root = plugin_root().as_posix()
    return {"batch": b["batch"], "kind": ADOPT, "prefix": prefix(), "read": [f"{root}/{r}" for r in READ[ADOPT]],
            "items": items, "report": ADOPT_REPORT.replace("<批次>", b["batch"])}


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
    if b["kind"] == ADOPT:
        return _adopt_view(vault, b)
    out = _collect(vault)
    want = list(b.get("items") or [])
    buckets = {"cards": CARD_BUCKETS, "agenda": AGENDA_BUCKETS, "synthesis": SYNTHESIS_BUCKETS}[b["kind"]]
    found = {}
    ctx = None
    for bucket in buckets:
        for item in out.get(bucket) or []:
            k = key_of(bucket, item)
            if k in want and k not in found:
                if bucket == "synthesis_broken":
                    ctx = ctx or _SynthesisContext(vault)
                    item = ctx.material(item) or item
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
        adopt = _open_adopt(data, session_id)
        if adopt is not None:  # 讀舊 wiki 的也一起停
            adopt["status"], adopt["closed"], adopt["stopped_turn"] = "closed", now.strftime(_TIME), turn_id
        job = job or adopt
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


def release_session(vault, session_id, turn_id):
    """接手建置（build 第一輪修 S1）：前一個對話（session_id）open 的 job 關掉、open 批次放掉（released），項目回到可派；
    不記 stopped_turn（不是那一句說了停）。回放掉的批數；沒有 batches.json 回 0。"""
    if not session_id or not _path(vault).is_file():
        return 0
    now = _now()
    with lock(vault):
        data = load(vault)
        n = 0
        for b in data["batches"]:
            if b.get("status") == "open" and _job_session(data, b) == session_id:
                b["status"] = "released"
                n += 1
        jobs = [j for j in data["jobs"] if j.get("session_id") == session_id and j.get("status") == "open"]
        for j in jobs:
            j["status"], j["closed"], j["released_by_takeover"] = "closed", now.strftime(_TIME), True
        if n or jobs:
            save(vault, data, turn_id)
    return n


def close_ids_job(vault, session_id, turn_id):
    """done sample（build 第一輪修 S3）：這個 session 用 --ids 開、還開著的 job 收掉，下一次 dispatch 開新的 job 全量切。回有沒有收。"""
    if not _path(vault).is_file():
        return False
    with lock(vault):
        data = load(vault)
        job = open_job(data, session_id)
        if job is None or not job.get("ids"):
            return False
        job["status"], job["closed"] = "closed", _now().strftime(_TIME)
        save(vault, data, turn_id)
    return True


def ids_running(vault, session_id):
    """這個 session 用 --ids 開的 job 還在跑的批數（done sample 的前提）。"""
    try:
        data = load(vault)
    except ValueError:
        return 0
    job = open_job(data, session_id)
    if job is None or not job.get("ids"):
        return 0
    run = running(data, session_id, _now(), _minutes(vault))
    return sum(1 for b in run if b.get("job") == job["job"])


def given_up(vault):
    """製卡助手回報沒做成的（每個 job 的 failed）：[{"id","kind","reason"}]，同一個只列一次（建置報告，S7）。"""
    try:
        data = load(vault)
    except ValueError:
        return []
    got = {}
    for j in data["jobs"]:
        for k, v in (j.get("failed") or {}).items():
            got[k] = {"id": k, "kind": v.get("kind"), "reason": v.get("reason")}
    return list(got.values())


# ---------- 子代理 ----------

def agent_start(vault, session_id, agent_id):
    """SubagentStart：記 agent_id → 回合（這個 session open 的 job 的回合，沒有 job 取 session 的目前回合）。回那個回合。"""
    with lock(vault):
        data = load(vault)
        job = open_job(data, session_id) or _open_adopt(data, session_id)
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


def adopt_agent(vault, agent_id):
    """這個子代理目前認領的是讀舊 wiki 的批次嗎（守門擋它寫檔，build 第一輪修 S8）；讀不到回 False。"""
    try:
        data = load(vault)
    except ValueError:
        return False
    return any(b.get("agent_id") == agent_id and b.get("status") == "open" and b.get("kind") == ADOPT
               for b in data["batches"])


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
    """開場那行（D-161、B-415）：(還沒整理的份數, 要跟著搬的卡)。份數是 todo 的 new＋stale 扣掉還沒清的排除夾裡的；
    主對話自己做的照算（記成有密碼還沒打開、記過讀不到的連結 todo 本來就不列，22 第一輪驗後）；搬的是 moved。只讀，不寫檔。"""
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
    n = 0
    for item in out["new"] + out["stale"]:
        e = by_id.get(item["id"])
        uri = e["locations"][0]["uri"] if e and e.get("locations") else ""
        if prefixes and _in_any(uri, prefixes):
            continue
        n += 1
    return n, len(out["moved"])
