"""健檢 runner（lint 題，D-221、D-222、D-227）：跑 checks.CHECKS 每一項，照結果的 kind 處理，再整理待處理、寫報告、記 lint.json。

模組可有 due(ctx)：回 False 這次不跑（不算跑過，它開的件不關）。
每項回 findings：[{check, kind: fix|decide|number|ingest, key, text, items, data, apply?, file_ids, entity_ids, once}]。
fix：runner 接著叫 apply(ctx)，回的那句記進這次的「做了」（回 None 是沒事可做；丟 Skipped 是這次做不了，那句寫進報告）；
decide：寫進待處理（kind lint，同 key 合成一件、已在的不重複；once 的答過不再開）；number：只進報告；ingest：只數，交整理。
每項各自 try：一項出錯不影響其他，報告寫「X這項這次沒查成」、hook-errors.log 記一行（source lint）。
之後整理待處理：關掉程式確認已不成立的（讀不到的檔不在了、寫法不同的卡不在了、要確認結案的實體已結案或移除、程式動到的檔不在清冊）、
放太久的收進先放著、該回來的拿回前面、待處理.md「整理時要問你的」那段換成現在的問題。
鎖：.system/lint.lock，同時只跑一個（搶不到丟 Busy）；清理那步另外搶 .system/revert.lock（sizes 那項自己處理）。
回合：她說「幫我檢查」用目前回合（退回得回）；背景健檢開自己的回合 versions/<時間>-lint（不碰 current.json，不算進她正在問的那句）。
報告：背景健檢在本月第一次跑時寫上個月的定稿報告、待處理開「報告出來了」那件（舊的那件自動關掉）；她說「幫我檢查」寫這個月到今天的
（同月重寫）。第一次跑（沒有 lint.json）不補報告。
opening 給開場 hook：距上次健檢超過 lint_background_days 天、今天還沒開過，就背景開 kb lint --background、不等它。
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from kb._lib import config, ledger, pending, turn
from kb._lib import io as kio
from kb.lint import state

LOCK_POSIX = ".system/lint.lock"
LOCK_WAIT = 1
LOCK_STALE = 3600
BUSY = "另一個健檢正在跑，等它做完"
FAILED_LINE = "{name}這項這次沒查成"
NOT_TRUE_HOW = pending.LINT_GONE_HOW
# 舊版開的 lint 件沒記 check：照 key 的開頭認是哪一項開的（條件消失時關，測試 T-1）
KEY_CHECKS = (("guard_stale", "guard"), ("versions_unwritable", "backup"), ("system_link", "system_dir"),
              ("inferred:", "inferred"), ("skeleton:", "versions"), ("synthesis_big:", "sizes"),
              ("redo_by_profile:", "stale_by_profile"), ("promote:", "rulings_promote"), ("preference:", "preferences"))
CLOSED_TEXT = "關掉 {n} 件已經不成立的待處理"
ASIDE_TEXT = "收起 {n} 件放超過 {days} 天沒動的待處理（先放著，有變化會再拿出來）"
OPENING_LOCK_WAIT = 0.5  # 開場記「今天開過了」只等這麼久，等不到這次不開（開場 hook 有時限）
NO_BACKGROUND_ENV = "WIKI_WITH_WILLY_NO_BACKGROUND"  # 測試設這個，開場不開背景子行程（黑箱測試的知識庫是暫存夾）
KINDS = ("fix", "decide", "number", "ingest")


class Busy(Exception):
    """另一個健檢正在跑。"""


class Skipped(Exception):
    """這次做不了的修（例如清理搶不到鎖）：args[0] 是寫進報告的那句。"""


def finding(check, kind, key, text, items=None, data=None, apply=None, file_ids=(), entity_ids=(), once=False):
    if kind not in KINDS:
        raise ValueError(f"健檢結果只收 {'、'.join(KINDS)}：{kind!r}")
    return {"check": check, "kind": kind, "key": key, "text": text, "items": list(items or []), "data": dict(data or {}),
            "apply": apply, "file_ids": list(file_ids), "entity_ids": list(entity_ids), "once": once}


class Context:
    """一次健檢共用的資料：要用才讀、讀一次。今天的日期集中在 today（測試換掉它）。"""

    def __init__(self, vault, today=None, turn_id=None, background=False):
        self.vault = Path(vault)
        self.today = today or date.today().isoformat()
        self.turn_id = turn_id
        self.background = background
        self.shared = True  # 這次有沒有跑共用的檢查（多人共用 3，D-304）；單人一律 True
        self._cache = {}

    def _get(self, name, fn):
        if name not in self._cache:
            self._cache[name] = fn()
        return self._cache[name]

    def config(self, key):
        return config.get(self.vault, key)

    def days_since(self, day):
        """day（YYYY-MM-DD 開頭）到今天幾天；解析不了回 None。"""
        try:
            return (date.fromisoformat(self.today) - date.fromisoformat(str(day)[:10])).days
        except ValueError:
            return None

    def entities(self):
        from kb._lib import entities
        return self._get("entities", lambda: entities.load(self.vault))

    def mounts(self):
        from kb._lib import entities
        return self._get("mounts", lambda: entities.mounts(self.vault, self.entities()))

    def cards(self):
        """{卡編號: {"path","front"}}。"""
        from kb._lib import cards
        return self._get("cards", lambda: cards.scan_cards(self.vault)[0])

    def synthesis(self):
        """[(實體, 相對路徑, 全文)]：有型別、active、不是自己、綜合卡在的；讀不了的跳過。"""
        def read():
            from kb._lib import entities
            out = []
            for e in self.entities()["entities"]:
                if e.get("status") != "active" or entities.is_self(e) or not e.get("type"):
                    continue
                rel = entities.locate(self.vault, e)
                if not rel:
                    continue
                try:
                    out.append((e, rel, (self.vault / rel).read_text(encoding="utf-8")))
                except (OSError, UnicodeDecodeError):
                    continue
            return out
        return self._get("synthesis", read)

    def statements(self):
        from kb._lib import statements
        return self._get("statements", lambda: statements.load(self.vault))

    def statements_archive(self):
        from kb._lib import statements
        return self._get("statements_archive", lambda: statements.load_archive(self.vault))

    def findings(self):
        from kb._lib import findings
        return self._get("findings", lambda: findings.load(self.vault))

    def findings_archive(self):
        from kb._lib import findings
        return self._get("findings_archive", lambda: findings.load_archive(self.vault))

    def rulings(self):
        from kb._lib import rulings
        return self._get("rulings", lambda: rulings.load(self.vault))

    def inventory(self):
        from kb._lib import inventory
        return self._get("inventory", lambda: inventory.load(self.vault)["entries"])

    def profile(self):
        from kb._lib import entities
        return self._get("profile", lambda: entities._profile(self.vault) or {})

    def profile_strict(self):
        """設定檔；沒檔用預設，讀不懂丟 ValueError（那一項算沒查成，不關它的件，第二輪 T-28）。"""
        from kb._lib import profile
        return self._get("profile_strict", lambda: profile.load(self.vault) or profile.defaults())

    def collect(self):
        """cards.collect 的結果（整理擱著的、整理題都從這裡算；不寫檔）。"""
        from kb._lib import cards
        from kb._lib import sources as sources_lib
        return self._get("collect", lambda: cards.collect(self.vault, sources_lib.load(self.vault)))

    def forget(self, *names):
        """修完之後這些資料變了：下一項重讀。"""
        for n in names:
            self._cache.pop(n, None)


def _program_turn(vault):
    """背景健檢自己的回合：versions/<時間>-lint/（turn.json 的問寫背景健檢），不碰 turns/current.json。"""
    now = datetime.now().replace(microsecond=0)
    tid = f"{now:%Y%m%d-%H%M%S}-lint"
    turn.turn_dir(vault, tid, {"started": now.strftime("%Y-%m-%dT%H:%M:%S"), "prompt_head": "（背景健檢）"})
    return tid


def _prev_month(month):
    first = date.fromisoformat(month + "-01")
    return (first - timedelta(days=1)).strftime("%Y-%m")


def run(vault, background=False, today=None):
    """跑一次健檢。回結果 dict（kb lint --json 的欄位，另帶 numbers、notes）。另一個健檢在跑丟 Busy(BUSY)。"""
    vault = Path(vault)
    # 等一秒就放棄；健檢在大的知識庫可能跑超過一般鎖的殘留秒數，殘留改算一小時（不然另一個會把跑到一半的鎖清掉）
    lock = kio.FileLock(vault / LOCK_POSIX, timeout=LOCK_WAIT, stale=LOCK_STALE)
    try:
        lock.__enter__()
    except TimeoutError:
        raise Busy(BUSY) from None
    try:
        return _run(vault, background, today)
    finally:
        lock.__exit__(None, None, None)


def _run(vault, background, today):
    from kb.lint import checks, report
    first_time = not state.exists(vault)
    turn_id = _program_turn(vault) if background else turn.current(vault)["turn"]
    ctx = Context(vault, today=today, turn_id=turn_id, background=background)
    done, notes, failed, numbers, ingest, decided = [], [], [], {}, {}, []
    ran, keys = set(), set()
    shared = claim_shared(vault, ctx.today)  # 多人共用 3（D-304）：共用的檢查每週只一台跑；沒搶到的只跑本機的
    ctx.shared = shared
    mods = [m for m in checks.CHECKS if shared or getattr(m, "SCOPE", "shared") == "local"]
    early = [m for m in mods if not getattr(m, "AFTER_UPKEEP", False)]
    late = [m for m in mods if getattr(m, "AFTER_UPKEEP", False)]  # 件數在關件之後算（測試 T-24）
    for mod in early:
        _run_check(ctx, mod, done, notes, failed, numbers, ingest, decided, ran, keys)
    if shared:
        closed = _close_gone(ctx, ran, keys)
        _upkeep(ctx, done, failed, closed)
        clean_old_claims(vault, ctx.today)
    ctx.forget("collect")
    for mod in late:
        _run_check(ctx, mod, done, notes, failed, numbers, ingest, decided, ran, keys)
    try:
        state.prune(vault, turn_id)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"lint.json 沒整理成：{type(e).__name__}: {e}", source="lint")
    got = _decisions(ctx)
    month = ctx.today[:7]
    st = state.load(vault)
    st["month_done"] = st["month_done"] + [{"date": ctx.today, "text": t} for t in done]
    counts = {"decide": got["decide"], "set_aside": got["set_aside"]}
    n_failed = len(failed)
    if background:
        prev = _prev_month(month)
        if first_time or not st.get("last_report"):
            st["last_report"] = prev
        elif st["last_report"] < prev:
            month_done = [d["text"] for d in st["month_done"] if str(d.get("date", ""))[:7] == prev]
            report.write(ctx, prev, month_done, got, numbers, ingest, notes, failed, _counts(st.get("last_counts")))
            pending.add_report(vault, prev, turn_id)
            st["last_counts"] = dict(counts, done=len(month_done))
            st["last_report"] = prev
            st["month_done"] = [d for d in st["month_done"] if str(d.get("date", ""))[:7] > prev]
    this_month = [d["text"] for d in st["month_done"] if str(d.get("date", ""))[:7] == month]
    summary = report.summary(got["decide"], len(this_month), _counts(st.get("last_counts")), failed=n_failed)
    rel = None
    if not background:
        rel = report.write(ctx, month, this_month, got, numbers, ingest, notes, failed, _counts(st.get("last_counts")))
    st["last_background"] = ctx.today
    if ctx.shared:
        st["last_shared"] = ctx.today  # 「健檢有 N 件要決定」只在跑了共用檢查的那台說
    state.save(vault, st, turn_id)
    _journal(vault, background, done)
    return {"month": month, "report": rel, "summary": summary, "done": done, "decide": got["decide"],
            "most_urgent": got["most_urgent"], "set_aside": got["set_aside"], "ingest": ingest, "failed_checks": failed,
            "numbers": numbers, "notes": notes}


def claim_shared(vault, today):
    """多人而且是 git repo：推 willy/lint/<年>-W<週> 標籤，ok 這台跑共用的檢查；別台搶到（taken）或連不上只跑本機的。
    標籤是自己這台推的（訊息是這台的人名）也算 ok——同一週每天第一句再跑不會被自己擋住。單人或不是 git 一律 True。"""
    from kb._lib import git
    try:
        if not git.sync_active(vault):
            return True
        y, w, _d = date.fromisoformat(today).isocalendar()
        name, me = f"willy/lint/{y}-W{w:02d}", git.me(vault)
        got = git.push_tag(vault, name, me)
        if got == "taken":
            return git.tag_message(vault, name) == me
        return got in ("ok", "error")
    except Exception as e:  # noqa: BLE001  標籤機制出錯不擋健檢
        ledger.log_error(vault, f"健檢分工標籤出錯：{type(e).__name__}: {str(e)[:100]}", source="lint")
        return True


CLAIM_KEEP_DAYS = 7


def clean_old_claims(vault, today):
    """健檢多一項：清 willy/claim/* 超過 7 天的標籤（名字尾巴是日期）。只在多人 git 時做；失敗只記 log。"""
    from kb._lib import git
    try:
        if not git.sync_active(vault):
            return 0
        n = 0
        for name in git.list_remote_tags(vault, "willy/claim/") or []:
            try:
                d = datetime.strptime(name.rsplit("-", 1)[1], "%Y%m%d").date()
            except (ValueError, IndexError):
                continue
            if (date.fromisoformat(today) - d).days > CLAIM_KEEP_DAYS and git.delete_remote_tag(vault, name):
                n += 1
        return n
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"清舊認領標籤出錯：{type(e).__name__}: {str(e)[:100]}", source="lint")
        return 0


def _counts(last):
    return last if isinstance(last, dict) else None


def _check_id(mod):
    """這一項的名字（finding 的 check 就是它）：模組檔名。"""
    return str(getattr(mod, "__name__", mod.NAME)).rsplit(".", 1)[-1]


def _run_check(ctx, mod, done, notes, failed, numbers, ingest, decided, ran, keys):
    """跑一項、處理它的結果；跑成功、結果也都處理成功才算進 ran（只有 ran 的才關它沒再回的件，測試 T-1）。
    模組有 due(ctx) 且回 False 的這次不跑、不算進 ran，它的件不關（每月一次的偏好月檢，D-504 裁定 2）。"""
    try:
        if hasattr(mod, "due") and not mod.due(ctx):
            return
        found = mod.run(ctx)
    except Exception as e:  # noqa: BLE001  一項出錯不影響其他（D-221）
        failed.append(mod.NAME)
        ledger.log_error(ctx.vault, f"健檢「{mod.NAME}」出錯：{type(e).__name__}: {e}", source="lint")
        return
    ok = True
    for f in found:
        try:
            _handle(ctx, f, done, notes, numbers, ingest, decided)
            if f["kind"] == "decide":
                keys.add(f["key"])
        except Exception as e:  # noqa: BLE001
            ok = False
            if mod.NAME not in failed:
                failed.append(mod.NAME)
            ledger.log_error(ctx.vault, f"健檢「{mod.NAME}」處理結果出錯：{type(e).__name__}: {e}", source="lint")
    if ok:
        ran.add(_check_id(mod))


def _owner(item):
    if item.get("check"):
        return item["check"]
    key = str(item.get("key") or "")
    return next((check for head, check in KEY_CHECKS if key.startswith(head)), None)


def _close_gone(ctx, ran, keys):
    """這次跑成功的檢查沒再回的 lint 件關掉；回關掉幾件（併進「關掉不成立的待處理」那句）。"""
    try:
        return pending.close_lint_gone(ctx.vault, _owner, ran, keys, ctx.turn_id)
    except Exception as e:  # noqa: BLE001
        ledger.log_error(ctx.vault, f"健檢「關掉不成立的待處理」出錯：{type(e).__name__}: {e}", source="lint")
        return 0


def _fp(items):
    """對象指紋：這件列的項目（她答過、項目沒變就不重開，測試 T-2）。"""
    return hashlib.sha256(json.dumps(sorted(str(x) for x in items), ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _handle(ctx, f, done, notes, numbers, ingest, decided):
    kind = f["kind"]
    if kind == "fix":
        try:
            text = f["apply"](ctx) if f.get("apply") else None
        except Skipped as e:
            notes.append(str(e.args[0]) if e.args else str(e))
            return
        if text:
            done.append(text)
    elif kind == "decide":
        from kb._lib import archive
        af, ae = ctx._get("archived", lambda: archive.sets(ctx.vault))
        if archive.hides(f["file_ids"], f["entity_ids"], af, ae):
            return  # 跟封存的檔或實體有關的不出題（放寬丙 12，D-332）
        pid, _created = pending.add_lint(ctx.vault, f["key"], f["text"], f["data"], ctx.turn_id, file_ids=f["file_ids"],
                                         entity_ids=f["entity_ids"], once=f["once"], check=f["check"], fp=_fp(f["items"]))
        decided.append(pid)
    elif kind == "number":
        numbers[f["key"]] = f["data"]
    else:
        ingest.update(f["data"])


def _upkeep(ctx, done, failed, closed=0):
    """待處理的整理：關掉不成立的（closed 是剛才關掉的 lint 件，一起算進那句）、收起放太久的、該回來的拿回前面、
    換掉「整理時要問你的」。各自 try。"""
    vault = ctx.vault
    steps = (("關掉不成立的待處理", lambda: _close_untrue(ctx, done, closed)),
             ("收起放太久的待處理", lambda: _aside_old(ctx, done)),
             ("先放著的拿回前面", lambda: pending.wake_due(vault, ctx.turn_id, ctx.today)),
             ("整理時要問你的", lambda: _refresh_asks(ctx)))
    for name, fn in steps:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            ledger.log_error(vault, f"健檢「{name}」出錯：{type(e).__name__}: {e}", source="lint")


def _untrue(ctx, item):
    """這件待處理是不是已經不成立（D-227：程式確認了才關）。"""
    kind = item.get("kind")
    by_id = {e.get("id"): e for e in ctx.inventory()}
    if kind == "unreadable":
        e = by_id.get((item.get("file_ids") or [None])[0])
        if e is None or e.get("status") != "active":
            return True
        card = ctx.cards().get(e["id"])  # 卡追上現在的檔才算讀得到（舊卡還在等重做的不算，測試 T-5）
        from kb._lib import cards as cards_lib
        return card is not None and cards_lib.card_state(ctx.vault, e, card) is None
    if kind == "conflict":
        return any(f not in ctx.cards() for f in item.get("file_ids") or [])
    if kind == "close":
        ents = {e.get("id"): e for e in ctx.entities()["entities"]}
        hits = [ents.get(i) for i in item.get("entity_ids") or []]
        return any(e is None or e.get("status") != "active" or e.get("progress") == "closed" for e in hits)
    if kind == "script_touched":
        return all(f not in by_id for f in item.get("file_ids") or [] if str(f).startswith("f-")) and \
            not any(not str(f).startswith("f-") for f in item.get("file_ids") or [])
    return False


def _close_untrue(ctx, done, closed=0):
    items = pending.load(ctx.vault)["items"]
    ids = [i["id"] for i in items if i.get("status") == "open" and _untrue(ctx, i)]
    n = (pending.close_ids(ctx.vault, ids, NOT_TRUE_HOW, ctx.turn_id) if ids else 0) + closed
    if n:
        done.append(CLOSED_TEXT.format(n=n))


def _aside_old(ctx, done):
    days = ctx.config("pending_set_aside_days")
    n = pending.set_aside_old(ctx.vault, days, ctx.turn_id, ctx.today)
    if n:
        done.append(ASIDE_TEXT.format(n=n, days=days))


def _refresh_asks(ctx):
    from kb._lib import plan
    pending.refresh(ctx.vault, ctx.turn_id, asks=plan.pending_view_questions(ctx.vault, out=ctx.collect()))


def _decisions(ctx):
    """要她決定的：件數、最急的一件（照 kb asks --all 的排法）、先放著的件數；算不出來給 0。"""
    from kb._lib import plan
    try:
        got = plan.build(ctx.collect(), ctx.vault, config, tell_all=False)
        extra, hidden = plan.pending_asks(ctx.vault)
        asks = [a for a in plan.order(got["asks"] + extra) if a["kind"] != "pending_report"]  # 報告那件不算（測試 T-20）
        return {"decide": len(asks), "most_urgent": asks[0]["question"] if asks else None,
                "set_aside": got.get("set_aside", 0) + hidden, "asks": asks}
    except Exception as e:  # noqa: BLE001
        ledger.log_error(ctx.vault, f"健檢算要你決定的出錯：{type(e).__name__}: {e}", source="lint")
        return {"decide": 0, "most_urgent": None, "set_aside": 0, "asks": []}


def _journal(vault, background, done):
    """工作日誌：背景的記一段程式段；她說「幫我檢查」的對話裡併進回合、終端機跑的記程式段（D-085）。"""
    from kb import journal_cli
    note = "健檢：" + ("；".join(done) if done else "沒有要修的")
    changed = [{"path": state.LINT_POSIX, "action": "modify"}]
    try:
        if background:
            from kb._lib import journal
            journal.record_program(vault, note, changed, "lint --background")
        else:
            journal_cli.record_program_or_warn(vault, note, changed, "lint", journal_cli.in_conversation(vault))
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"健檢的工作日誌沒記到：{type(e).__name__}: {e}", source="lint")


# ---------- 開場（背景健檢的觸發，D-221、D-227） ----------

def _popen(argv, **kw):
    """開背景子行程（不等它）；測試換掉它記下參數。"""
    return subprocess.Popen(argv, **kw)


def _spawn(vault):
    from kb._lib import paths
    argv = [sys.executable, str(paths.plugin_root() / "scripts" / "kb.py"), "lint", "--background"]
    kw = {"cwd": str(vault), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kw["start_new_session"] = True
    _popen(argv, **kw)


def _close_guard_stale(vault, today=None):
    """待處理還開著「守門好像沒在跑」、守門那項重判已經不成立：開場就關，不等下週的背景健檢（#63）。出錯只記 log，不擋開場。"""
    try:
        from kb.lint.checks import guard
        if not any(i.get("key") == "guard_stale" and i.get("status") == "open" for i in pending.load(vault)["items"]):
            return
        keys = {f["key"] for f in guard.run(Context(vault, today=today)) if f["kind"] != "number"}
        if "guard_stale" not in keys:
            pending.close_lint_gone(vault, _owner, {"guard"}, keys, _program_turn(vault))
    except Exception as e:  # noqa: BLE001
        ledger.log_error(vault, f"開場關「守門好像沒在跑」出錯：{type(e).__name__}: {e}", source="lint")


def opening(vault, today=None):
    """開場叫：先關掉已經不成立的「守門好像沒在跑」（#63）。沒跑過或距上次超過 lint_background_days 天、今天還沒開過，就先記今天再背景開 kb lint --background。不等它。
    回有沒有開。環境變數 WIKI_WITH_WILLY_NO_BACKGROUND 有值時不開（測試）。"""
    _close_guard_stale(vault, today)
    if os.environ.get(NO_BACKGROUND_ENV):
        return False
    today = today or date.today().isoformat()
    st = state.load(vault)
    if st.get("last_spawn") == today:
        return False
    last = st.get("last_background")
    if last:
        try:
            days = (date.fromisoformat(today) - date.fromisoformat(str(last)[:10])).days
            if 0 <= days <= config.get(vault, "lint_background_days"):  # 未來的日期（時鐘曾經錯）當過期（測試 T-15）
                return False
        except ValueError:
            pass
    try:
        state.change(vault, lambda d: d.update(last_spawn=today) or True, timeout=OPENING_LOCK_WAIT)
    except TimeoutError:
        return False  # 記不到今天開過了就不開（免得兩個對話各開一個），下次開場再試
    _spawn(vault)
    return True
