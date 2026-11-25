"""kb refresh 的實作（多人共用 3 要做的事 2、5、7，D-293、D-295、D-301）：git pull 之後讓這台對上。

做的事：(a) git 合併做到一半 → 收拾衝突（卡留對方的、自己那版進 card_base 的 pending 照手改流程變知識、標重做；
其他檔留自己的、對方的另存「<名稱>（<對方名字>的版本）.<副檔名>」）；rebase 做到一半不動、回 rebase: true。
(b) 重畫這台的頁（日程、待處理、裁示、客戶設定檔、總目錄），不當成她手改。
(c) 知識庫 repo 裡的原檔：只對 git 這次 pull 動到的檔算指紋（pull 會把修改時間全更新）；雲端的原檔不在這裡比（交給每句開頭的檔案比對）。
(d) 大檔（超過 git_file_max_mb 還沒追蹤）加進 .git/info/exclude。
(e) 記這次的 HEAD（.system/local/last_head）與 pull 進來的檔（.system/local/pulled.json，手改基準用）。
(f) 停止追蹤（D-554 (3)）：每天查一次被追蹤的該忽略檔，有就開一件 sync_untrack 待處理；別台停止追蹤、pull 把這台的
本機狀態檔刪掉時，從 git 歷史寫回；合併衝突裡被 .gitignore 列的路徑留這台的、停止追蹤。
知識庫在別人的 repo 子夾時（D-554 實作疑義 1，部分修訂 D-546）只做 (e) 的 HEAD 與 (f)，衝突只碰知識庫夾裡被忽略的路徑；
(b)～(d) 與其他衝突只在知識庫自己有 .git 時做。git 不在或不在任何 repo：什麼都不做（回空結果）。
"""
import hashlib
import json
import os
from datetime import date
from pathlib import Path

from kb._lib import git
from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

LOCAL = ".system/local"
LAST_HEAD = LOCAL + "/last_head"
PULLED = LOCAL + "/pulled.json"
PULLED_KEEP = 200
SYSTEM_PREFIXES = (".system/", "系統/", ".obsidian/", "檔案索引/", ".git/")
UNTRACK_KEY = "sync_untrack"
UNTRACK_CHECK = "sync_untrack"  # 健檢那一項的名字（kb/lint/checks/sync_untrack.py）
UNTRACK_STAMP = LOCAL + "/untrack_checked"
UNTRACK_DO = "kb sync untrack"
CHECK_LINE = "有 {n} 個 willy 自己的檔被同步追蹤（本機狀態 {local} 個、可重算的副本 {copies} 個、渲染頁 {rendered} 個）；會讓別台的改動被蓋掉"
ASK_TEXT = "有 {n} 個 willy 自己的檔被同步追蹤了，會讓別台記的東西被蓋回舊的。要我停止追蹤嗎？檔案都留著。"
KEPT_LINE = "同事停止追蹤了 {n} 個檔，這台的本機狀態照留"
APPEND_EXT = (".tsv", ".log", ".jsonl")  # 只往後加的本機檔：pull 刪掉後又被寫了新的，舊的接在前面


def _log(vault, text):
    from kb._lib import ledger
    ledger.log_error(vault, text, source="refresh")


def empty():
    return {"rendered": [], "pulled": [], "changed": {"added": 0, "modified": 0, "missing": 0}, "unsure": 0,
            "conflicts": [], "big": [], "rebase": False, "kept_local": 0}


def read_last_head(vault):
    try:
        return (Path(vault) / LAST_HEAD).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def head_changed(vault):
    """每句開頭用：HEAD 跟記下的不同才要叫 refresh。不是 git 回 False。"""
    now = git.outer_head(vault)  # 子夾時照上層 repo 的 HEAD（D-554 實作疑義 2）
    return bool(now) and now != read_last_head(vault)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(vault, turn_id=None):
    """回 kb refresh --json 的結構；多一個 "skipped": True 表示同時有別人在跑。"""
    out = empty()
    own = git.is_repo(vault)
    if not own and git.outer_repo(vault) is None:
        return out
    (Path(vault) / LOCAL).mkdir(parents=True, exist_ok=True)
    try:
        lock = FileLock(Path(vault) / LOCAL / "refresh", timeout=0.5)
        lock.__enter__()
    except TimeoutError:
        return dict(out, skipped=True)
    try:
        return _run(vault, turn_id, out, own)
    finally:
        lock.__exit__(None, None, None)


def _run(vault, turn_id, out, own=True):
    from kb._lib import turn
    if turn_id is None:
        turn_id = turn.current(vault)["turn"]
    if git.rebase_in_progress(vault):
        _log(vault, "同步卡住了（rebase 到一半），請聯絡服務人員")
        out["rebase"] = True
        return out
    failed = False  # 寫回、衝突收拾出錯：不記這次的 HEAD，下次再試（補驗 1）
    if git.merge_in_progress(vault) and git.unmerged(vault):
        try:
            out["conflicts"] = resolve_conflicts(vault, turn_id, own=own)
        except Exception as e:  # noqa: BLE001
            _log(vault, f"合併衝突收拾失敗：{type(e).__name__}: {str(e)[:100]}，下次再試")
            failed = True
    head = git.outer_head(vault)
    old = read_last_head(vault)
    out["kept_local"], gone_pages, ok = _keep_local(vault, old, head)
    failed = failed or not ok
    try:
        restore_env(vault)
    except Exception as e:  # noqa: BLE001
        _log(vault, f"換回這台的 env.json 失敗：{type(e).__name__}: {str(e)[:100]}，下次再試")
    if own:
        pulled = git.changed_by_others(vault, old, head) or []  # 別人寫的 commit 動到的檔；自己（外掛）commit 的不算
        out["pulled"] = pulled
        out["rendered"] = _render_pages(vault, turn_id, also=gone_pages)
        _originals(vault, pulled, out)
        out["big"] = check_big(vault)
    elif gone_pages:  # 子夾：只補畫被同事停止追蹤刪掉的頁（D-546 其餘不重畫）
        out["rendered"] = _render_pages(vault, turn_id, also=gone_pages, only=True)
    _daily_untrack(vault, turn_id)
    if head and not failed:
        write_text_atomic(Path(vault) / LAST_HEAD, head + "\n")
    if own:
        _remember_pulled(vault, out["pulled"])
    return out


# ---------- (f) 停止追蹤 ----------

OWN_ROOTS = (".system", "系統", "總目錄.md")  # willy 自己的檔只會在這幾處（測試 #1）


def _rule(pattern):
    """.gitignore 一行 → (放行嗎, 只對資料夾嗎, regex)；空行、註解回 None。夠用就好：*、**、?、開頭 /、結尾 /、!。"""
    import re
    pat = pattern.rstrip("\r\n").rstrip()
    if not pat or pat.startswith("#"):
        return None
    neg = pat.startswith("!")
    pat = pat[1:] if neg else pat
    dir_only = pat.endswith("/")
    pat = pat.rstrip("/")
    anchored = "/" in pat
    pat = pat.lstrip("/")
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pat.startswith("/**", i):
            out, i = out + "/.*", i + 3
        elif pat[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pat[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pat[i]), i + 1
    return neg, dir_only, re.compile(("" if anchored else "(?:.*/)?") + out + r"\Z")


def _rules(text):
    return [r for r in (_rule(ln) for ln in (text or "").splitlines()) if r]


def _ignored_by(rules, rel):
    """照這份規則 rel 被忽略嗎（git 的意思：上層資料夾被忽略，底下的放行不回來）。"""
    parts = rel.split("/")
    for k in range(1, len(parts) + 1):
        sub, is_dir = "/".join(parts[:k]), k < len(parts)
        hit = None
        for neg, dir_only, rx in rules:
            if (is_dir or not dir_only) and rx.match(sub):
                hit = not neg
        if hit and is_dir:
            return True
        if k == len(parts):
            return bool(hit)
    return False


_ALLOWED = []


def _skeleton_allowed():
    """骨架 .gitignore 用 ! 放行的（這些不是 willy 不同步的檔）；一個程序讀一次。"""
    if _ALLOWED:
        return _ALLOWED[0]
    _ALLOWED.append(_read_allowed())
    return _ALLOWED[0]


def _read_allowed():
    from kb._lib import paths
    try:
        text = (paths.plugin_root() / "skeleton" / ".gitignore").read_text(encoding="utf-8")
    except OSError:
        return []
    return [(False, d, rx) for neg, d, rx in _rules(text) if neg]


def classify(rel):
    """willy 自己的檔是哪一類：copies（流水檔合起來的副本）、rendered（渲染頁）、local（本機狀態）；
    不是 willy 自己的回 None，一律不碰（測試 #1：名單由 willy 定，不靠 git 的忽略規則）。"""
    from kb._lib import streams
    if rel.startswith("系統/") or rel == "總目錄.md":
        return "rendered"
    if not rel.startswith(".system/") or rel.startswith(".system/streams/"):
        return None
    allowed = _skeleton_allowed()
    if allowed and _ignored_by(allowed, rel):
        return None
    copies = {v[0] for v in streams.KINDS.values()} | {v[1] for v in streams.VIEWS.values()}
    return "copies" if rel in copies else "local"


def own_ignored(vault, rels):
    """這些路徑裡 willy 自己的、又被知識庫自己的 .gitignore 列的（上層 repo 的 .gitignore、info/exclude、
    全域 excludesFile 都不算，測試 #1）。只讀檔，不叫 git。"""
    try:
        rules = _rules((Path(vault) / ".gitignore").read_text(encoding="utf-8-sig", errors="replace"))  # 記事本存的會帶 BOM；
        # 舊記事本、PowerShell 5.1 存的中文註解不是 UTF-8：換成 �，規則照讀（補驗 1）
    except OSError:
        return []
    return [r for r in rels if classify(r) and _ignored_by(rules, r)]


def own_tracked(vault):
    """HEAD 裡被追蹤、又該忽略的 willy 檔（kb sync untrack 要停止追蹤的那些）。"""
    return own_ignored(vault, git.tracked_in_head(vault, OWN_ROOTS))


def untrack_summary(vault, paths=None):
    """{"paths","n","local","copies","rendered"}：kb sync untrack --check、selftest、每日偵測、健檢共用。"""
    paths = own_tracked(vault) if paths is None else list(paths)
    got = {"paths": paths, "n": len(paths), "local": 0, "copies": 0, "rendered": 0}
    for rel in paths:
        kind = classify(rel)
        if kind:
            got[kind] += 1
    return got


def check_line(summary):
    return CHECK_LINE.format(**summary)


def ask_text(summary):
    return ASK_TEXT.format(n=summary["n"])


def ask_data(summary):
    return dict({k: summary[k] for k in ("n", "local", "copies", "rendered")}, do=UNTRACK_DO)


def _daily_untrack(vault, turn_id):
    """每天最多查一次（日期記在 .system/local/untrack_checked），有就開一件 sync_untrack 待處理。出錯只記 log。"""
    stamp = Path(vault) / UNTRACK_STAMP
    today = date.today().isoformat()
    try:
        if stamp.read_text(encoding="utf-8").strip() == today:
            return
    except OSError:
        pass
    try:
        write_text_atomic(stamp, today + "\n")
        got = untrack_summary(vault)
        if got["n"]:
            from kb._lib import pending
            pending.add_lint(vault, UNTRACK_KEY, ask_text(got), ask_data(got), turn_id, check=UNTRACK_CHECK)
    except Exception as e:  # noqa: BLE001
        _log(vault, f"查同步追蹤失敗：{type(e).__name__}: {str(e)[:100]}")


def _write_bytes(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.willy-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


def _old_contents(vault, rels, orig=None):
    """被 git 刪掉的檔刪掉前這台的內容 {路徑: bytes}：先看 ORIG_HEAD（pull 前的這台），沒有的再看上次記的 HEAD。
    每個版本一次 cat-file，不隨檔數增加（確認 #2）。不看「歷史上刪掉前的那一版」：新 clone 會拿到別台的 env.json（review 1）。"""
    got, left = {}, list(rels)
    for rev in (orig if orig is not None else git.orig_head(vault), read_last_head(vault)):
        if not left or not rev:
            continue
        got.update(git.show_many_at(vault, rev, left))
        left = [r for r in left if r not in got]
    return got


def _pulled_deleted(vault, head=None):
    """這台上次記的 HEAD（沒有就 pull 前的 ORIG_HEAD）到現在之間，pull 進來的 commit 刪掉、現在被 .gitignore 列的檔：
    (base, orig, [路徑])。這台自己的 commit 不算（實作疑義 3）；新 clone 沒有 base，回空。"""
    head = head or git.outer_head(vault)
    orig = git.orig_head(vault)
    base = read_last_head(vault) or orig
    if not base or not head or base == head:
        return base, orig, []
    deleted = git.deleted_by_others(vault, base, head)
    return base, orig, own_ignored(vault, deleted)


ENV_BACK_LINE = "同步帶來別台的 env.json，已換回這台的"


def restore_env(vault):
    """env.json 的這台代號跟 .system/local/env.mine.json 不同（別台還在追蹤、commit 了它的 env.json，pull 時 git 照蓋被忽略的檔）：
    用這台的備份寫回、記 log，回 True。沒有備份（舊版升上來第一次）不動（#78 第二段第 6 點）。"""
    import json
    from kb._lib import env
    from kb._lib.io import FileLock
    mp = Path(vault) / env.MINE_REL
    if not mp.is_file():
        return False
    p = Path(vault) / env.ENV_REL
    with FileLock(p):  # 備份在鎖裡讀：換代號的人也拿這把鎖（review 8）
        try:
            raw = mp.read_bytes()
            mine = env.mine_id(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError):
            return False
        if not mine:
            return False
        try:
            cur = env.mine_id(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            cur = None
        if cur == mine:
            return False
        _write_bytes(p, raw)
    _log(vault, ENV_BACK_LINE)
    return True


def recover_local(vault, rel):
    """被 pull 進來的 commit 刪掉（別台停止追蹤）、現在被 .gitignore 列的本機檔（相對知識庫夾）寫回原位；
    檔還在、不是 pull 刪的、拿不回回 False（env._load 在 env.json 不見時叫，確認 #1）。"""
    target = Path(vault) / rel
    if target.exists() or git.outer_repo(vault) is None:
        return False
    _base, orig, gone = _pulled_deleted(vault)
    if rel not in gone:
        return False
    data = _old_contents(vault, [rel], orig).get(rel)
    if data is None:
        return False
    _write_bytes(target, data)
    return True


def _keep_local(vault, old, head):
    """別台停止追蹤、這次 pull 把這台的本機狀態檔刪掉：寫回原位（寫回後已被忽略，不會再進 git）。
    副本、渲染頁不寫回（讀的時候、_render_pages 重算）。只往後加的檔（ledger、log、journal）pull 之後又被寫了新的：舊的接在前面。
    git 叫的次數固定（log、reflog、check-ignore、rev-parse、cat-file 一兩次），不隨檔數增加。
    回 (寫回幾個, 被刪掉的渲染頁, 沒出錯嗎)；渲染頁交給呼叫端重畫（_render_pages 平常只畫還在的頁）。出錯記 log，呼叫端不記 HEAD。"""
    try:
        orig = git.orig_head(vault)
        base = old or orig
        if not base or base == head:
            return 0, [], True
        deleted = git.deleted_by_others(vault, base, head)
        deleted = own_ignored(vault, deleted)
        pages = [r for r in deleted if classify(r) == "rendered" and not (Path(vault) / r).exists()]
        need = [r for r in deleted if classify(r) == "local" and (not (Path(vault) / r).exists() or r.endswith(APPEND_EXT))]
        old_data = _old_contents(vault, need, orig) if need else {}
        n = 0
        for rel in need:
            target, data = Path(vault) / rel, old_data.get(rel)
            if data is None:
                continue
            if not target.exists():
                _write_bytes(target, data)
                n += 1
                continue
            cur = target.read_bytes()
            if not cur.startswith(data):
                _write_bytes(target, data + (b"" if data.endswith(b"\n") else b"\n") + cur)
                n += 1
        if n:
            _log(vault, KEPT_LINE.format(n=n))
        return n, pages, True
    except Exception as e:  # noqa: BLE001
        _log(vault, f"寫回本機狀態檔失敗：{type(e).__name__}: {str(e)[:100]}，下次再試")
        return 0, [], False


# ---------- 背景對帳（確認 #2：每句開頭不在 hook 裡跑 refresh） ----------

RESULT_CACHE = LOCAL + "/cache/refresh-lines.json"
SPAWNED = LOCAL + "/refresh-spawned"
SPAWN_STALE = 600  # 開過的背景 10 分鐘還沒對完（被砍、當掉）：同一個狀態可以再開


def _merge_head(vault):
    d = git._git_dir(vault)
    try:
        return (d / "MERGE_HEAD").read_text(encoding="utf-8").strip() if d else ""
    except OSError:
        return ""


def state_key(vault):
    """這台現在的同步狀態（HEAD、合併中的對方、rebase）：同一個狀態背景只開一次。只讀檔，不叫 git。"""
    return f"{git.outer_head(vault) or ''} {_merge_head(vault)} {int(git.rebase_in_progress(vault))}"


def needs_refresh(vault):
    """HEAD 變了、正在合併或 rebase 才要對帳（每句開頭問，不叫 git）。"""
    return git.rebase_in_progress(vault) or git.merge_in_progress(vault) or head_changed(vault)


def spawn_background(vault):
    """開 kb refresh --background（DETACHED、不等）；同一個狀態已經開過（或正在跑）就不再開，回有沒有開。"""
    import time
    key = state_key(vault)
    mark = Path(vault) / SPAWNED
    try:
        if mark.read_text(encoding="utf-8").strip() == key and time.time() - mark.stat().st_mtime < SPAWN_STALE:
            return False
    except OSError:
        pass
    mark.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(mark, key + "\n")
    if os.environ.get("WIKI_WITH_WILLY_NO_BACKGROUND"):  # 測試：不開子行程，就地跑完（暫存庫會被刪）
        run_background(vault)
        return True
    import subprocess
    import sys
    from kb._lib import paths
    kw = {"cwd": str(vault), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, str(paths.plugin_root() / "scripts" / "kb.py"), "refresh", "--background"], **kw)
    except OSError:
        try:
            mark.unlink()  # 沒開成：下一句再試
        except OSError:
            pass
        raise
    return True


def run_background(vault):
    """kb refresh --background 的本體：對帳，做完把要給 AI 的結果存起來（別人正在跑就不存，那邊會存）。"""
    got = run(vault)
    if not got.get("skipped"):
        save_result(vault, got)
    return got


def save_result(vault, got):
    """背景做完：要給 AI 的結果（衝突、pull 進來的檔、太大的檔、rebase）存起來，下一句開頭 take_result 拿；
    上一次的還沒被拿就併在一起。"""
    p = Path(vault) / RESULT_CACHE
    p.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(p):
        cur = read_json(p, None) or {}
        keep = {"rebase": bool(cur.get("rebase") or got.get("rebase"))}
        for k in ("conflicts", "pulled", "big"):
            keep[k] = list(cur.get(k) or []) + [x for x in got.get(k) or [] if x not in (cur.get(k) or [])]
        write_json_atomic(p, keep)


def take_result(vault):
    """拿背景存的結果並刪掉（給一次）；沒有回 None。"""
    p = Path(vault) / RESULT_CACHE
    if not p.exists():
        return None
    with FileLock(p):
        got = read_json(p, None)
        try:
            p.unlink()
        except OSError:
            pass
    return got if isinstance(got, dict) else None


# ---------- (b) 重畫 ----------

def _render_pages(vault, turn_id, also=(), only=False):
    """日程、待處理、裁示照 purge.py 的做法在 renders.suppressed 裡、hand={} 重畫；設定檔 md 她改過就不蓋；總目錄重畫。
    平常只畫還在的頁；also 是被同事停止追蹤、pull 刪掉的頁，不在也畫（D-554）。only：只畫 also 列的（子夾庫）。"""
    from kb._lib import agenda, cards, pending, profile, renders, rulings
    done = []
    also = set(also or ())

    def want(rel, always=False):
        return rel in also if only else (always or rel in also or (Path(vault) / rel).exists())

    def step(rel, fn):
        try:
            with renders.suppressed(rel):
                fn()
            done.append(rel)
        except Exception as e:  # noqa: BLE001  一頁畫不出來不擋其他頁
            _log(vault, f"重畫 {rel} 失敗：{type(e).__name__}: {str(e)[:100]}")

    if want(agenda.MD_POSIX):
        step(agenda.MD_POSIX, lambda: agenda.render(vault, turn_id, hand={}))
    if want(pending.MD_POSIX):
        step(pending.MD_POSIX, lambda: pending.render(vault, turn_id=turn_id))
    if want(rulings.MD_POSIX):
        step(rulings.MD_POSIX, lambda: rulings.render(vault, turn_id, hand={}))

    def prof():
        data = profile.load(vault)
        if data is None:
            raise LookupError("沒有設定檔")
        if data.get("md_sha256") and profile.md_sha256(vault) not in (None, data["md_sha256"]):
            raise LookupError("設定檔被她手改過，不蓋掉")  # 照現在先問
        with profile.lock(vault):
            profile.write_locked(vault, profile.load(vault), turn_id)
    if want(profile.MD_POSIX, always=True):
        try:  # 設定檔 md 不進 git（#42）：別台不再追蹤時 pull 會刪掉這台的，沒有也要畫
            prof()
            done.append(profile.MD_POSIX)
        except LookupError:
            pass
        except Exception as e:  # noqa: BLE001
            _log(vault, f"重畫設定檔失敗：{type(e).__name__}: {str(e)[:100]}")
    if want(cards.INDEX_POSIX, always=True):
        try:
            cards.render_index(vault, turn_id)
            done.append(cards.INDEX_POSIX)
        except Exception as e:  # noqa: BLE001
            _log(vault, f"重畫總目錄失敗：{type(e).__name__}: {str(e)[:100]}")
    return done


# ---------- (c) 知識庫 repo 裡的原檔 ----------

def _originals(vault, pulled, out):
    """只看 pull 動到的檔、而且在知識庫別名底下的原檔：算指紋，指紋真的變了才算 modified。
    不 apply（寫清冊交給每句開頭的檔案比對）；來不及比的記 unsure。"""
    cand = {p for p in pulled if not p.startswith(SYSTEM_PREFIXES) and not p.endswith(".md")}
    if not cand:
        return
    from kb._lib import inventory
    from kb._lib import sources as sources_lib
    try:
        rep = inventory.scan(vault, sources_lib.load(vault), quick=False, only_roots=[inventory.KNOWLEDGE])
    except Exception as e:  # noqa: BLE001
        _log(vault, f"refresh 比原檔失敗：{type(e).__name__}: {str(e)[:100]}")
        return

    def mine(uris):
        return [u for u in uris if inventory.split_uri(u)[1] in cand]
    out["changed"] = {"added": len(mine(rep.get("added") or [])), "modified": len(mine(rep.get("modified") or [])),
                      "missing": len(mine(rep.get("missing") or []))}
    out["unsure"] = 1 if rep.get("incomplete") else 0


# ---------- (d) 大檔 ----------

def check_big(vault):
    from kb._lib import config
    limit = float(config.get(vault, "git_file_max_mb")) * 1024 * 1024
    cloud = _cloud_alias(vault)
    big = []
    for st, rel in git.status_untracked_and_modified(vault):
        if st.strip() != "??" or rel.startswith(SYSTEM_PREFIXES) or rel.startswith("暫存區/"):
            continue
        try:
            size = (Path(vault) / rel).stat().st_size
        except OSError:
            continue
        if size > limit and not git.tracked(vault, rel):
            git.exclude_add(vault, rel)
            big.append({"path": rel, "mb": round(size / 1024 / 1024, 1), "cloud": cloud})
    return big


def _cloud_alias(vault):
    """sources 裡位置不在知識庫底下的來源別名（建置時登記的共用雲端夾），沒有回 None。"""
    try:
        from kb._lib import sources as sources_lib
        from kb._lib.paths import inside, normalize
        root = normalize(os.path.realpath(str(vault)), None)
        for s in sources_lib.load(vault).entries:  # entries 的位置在 root（解好的），沒有 spec（#45）
            where = s.get("root")
            if where and not inside(normalize(os.path.realpath(where), None), root) and s.get("alias") not in ("暫存區", "知識庫"):
                return s["alias"]
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------- (e) pulled ----------

def _remember_pulled(vault, pulled):
    """這次 pull 進來的卡與它們現在的 sha（問 1 (a)）：line_card_edits 遇到內容跟這裡一樣的卡就跳過。"""
    if not pulled:
        return
    p = Path(vault) / PULLED
    cur = read_json(p, {}) or {}
    for rel in pulled:
        f = Path(vault) / rel
        if f.is_file() and rel.endswith(".md"):
            try:
                cur[rel] = _sha(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                pass
    keep = dict(list(cur.items())[-PULLED_KEEP:])
    write_json_atomic(p, keep)


def pulled_same(vault, rel, text):
    """這張卡現在的內文跟 pull 進來時一樣嗎（一樣就不是這台的人手改）。"""
    got = read_json(Path(vault) / PULLED, {}) or {}
    return got.get(rel) == _sha(text)


# ---------- 5. 衝突 ----------

def _is_card(vault, rel):
    from kb._lib import card_base
    try:
        return card_base.card_kind(vault, rel) is not None or rel.startswith("檔案索引/")
    except Exception:  # noqa: BLE001
        return rel.startswith("檔案索引/")


def _copy_name(rel, who):
    stem, ext = os.path.splitext(rel)
    return f"{stem}（{who}的版本）{ext}"


def resolve_conflicts(vault, turn_id, own=True):
    """回 conflicts 清單，全部處理完 commit_merge。own False（知識庫在別人的 repo 子夾）只收拾知識庫夾裡被 .gitignore 列的路徑，
    其他衝突不碰；整個 repo 都沒有衝突了才 commit_merge（D-554 實作疑義 1）。"""
    from kb._lib import card_base
    from kb._lib.skeleton import UNTRACKED
    got = []
    stages = git.unmerged_stages(vault)  # 一次 ls-files -u 拿所有 stage（確認 #2：git 次數不隨檔數增加）
    todo = list(stages)
    ignored = set(own_ignored(vault, todo))
    drop = [r for r in todo if r in UNTRACKED or r in ignored]
    # 不進 git 的檔（#42 設定檔 md；D-554 被 .gitignore 列的）：一律不追蹤，留這台的；這台的版本一次 cat-file 讀
    mine_all = git.cat_batch(vault, [stages[r].get(2) for r in drop]) if drop else {}
    if mine_all is None:  # 讀不到這台那版：先不收拾，合併照留、下次再試（不能當成這台沒有而刪掉或留對方的）
        _log(vault, "合併衝突收拾不了：讀不到這台的版本，下次再試")
        return got
    for rel in drop:
        target = Path(vault) / rel
        mine = mine_all.get(stages[rel].get(2))
        if mine is not None:
            _write_bytes(target, mine)
        elif target.exists() and classify(rel) != "local":  # 這邊已不追蹤、對方改了：工作區是對方的，刪掉讓這台重畫、重算
            target.unlink()
    if drop:
        git.untrack(vault, drop, force=True)
    # 子夾庫：別人 repo 的衝突不碰，只收拾知識庫的流水檔（兩邊的行都留；#78 第二段第 5 點）
    rest = [r for r in todo if r not in drop and (own or r.startswith(".system/streams/"))]
    authors = git.authors_between(vault, "HEAD", "MERGE_HEAD") if any(not r.startswith(".system/streams/") for r in rest) else {}
    for rel in rest:
        other = authors.get(rel) or "同事"
        target = Path(vault) / rel
        if rel.startswith(".system/streams/"):
            _merge_streams(vault, rel)
            git.add(vault, [rel])
            _log(vault, f"流水檔衝突：{rel}，兩邊的行都留")
            continue
        if _is_card(vault, rel):
            mine = git.show_stage(vault, rel, 2)
            base = None
            try:
                base = card_base.load(vault).get(rel, {}).get("body")
            except Exception:  # noqa: BLE001
                pass
            theirs_ok = git.checkout_theirs(vault, rel)
            if mine is not None and theirs_ok:
                _record_pending(vault, rel, base, card_base.body_of(mine), turn_id)
            _mark_redo(vault, rel, turn_id)
            git.add(vault, [rel])
            got.append({"path": rel, "kind": "card", "other": other, "copy": None})
        else:
            from kb._lib import turn
            mine = git.show_stage_bytes(vault, rel, 2)
            theirs = git.show_stage_bytes(vault, rel, 3)
            copy_rel = _copy_name(rel, other)
            try:
                turn.backup(vault, rel, turn_id)
            except Exception:  # noqa: BLE001
                pass
            if mine is not None:
                target.write_bytes(mine)
            if theirs is not None:
                (Path(vault) / copy_rel).write_bytes(theirs)
            git.add(vault, [x for x in (rel, copy_rel) if (Path(vault) / x).exists()])
            got.append({"path": rel, "kind": "file", "other": other, "copy": copy_rel})
    if not own:
        if not git.unmerged(vault, everywhere=True):
            git.commit_merge(vault)
        return got
    if not git.unmerged(vault):
        # 標重做與 pending 改到的這台流水檔（.system/streams/<種類>/<代號>.jsonl）一起進這個合併 commit
        git.add(vault, [rel for _st, rel in git.status_untracked_and_modified(vault) if rel.startswith(".system/") and not rel.startswith(".system/local/")])
        git.commit_merge(vault)
    return got


def _record_pending(vault, rel, base, mine_body, turn_id):
    """自己那版進 card_base 的 pending（before 是基準、after 是自己的），之後照手改流程變成知識。
    卡已換成對方的版本，基準也換成對方的（pending 另留）。"""
    from kb._lib import card_base

    def fn(recs):
        cur = recs.get(rel) or {}
        before = base if base is not None else cur.get("body")
        theirs_body = card_base._read_body(vault, rel)
        pending = None
        if before is not None and before != mine_body:
            old = cur.get("pending")
            pending = {"before": old["before"] if old else before, "after": mine_body, "machine": card_base._machine(vault)}
        recs[rel] = card_base._entry(theirs_body, turn_id, pending)
    card_base._locked(vault, fn)


def _mark_redo(vault, rel, turn_id):
    """卡標重做：檔案卡用 cards.mark_redo；綜合卡把「整理到」每一行的指紋欄改成 redo。標不了只記 log。"""
    from kb._lib import cards, synthesis
    try:
        text = (Path(vault) / rel).read_text(encoding="utf-8")
        if rel.startswith(cards.CARDS_DIR + "/"):
            by_path = {c["path"]: fid for fid, grp in cards._scan_groups(vault)[0].items() for c in grp}
            fid = by_path.get(rel)
            if fid:
                cards.mark_redo(vault, fid, turn_id)
            return
        lines = cards.parse_front(text).get("整理到")
        if isinstance(lines, list):
            new = [f"{str(x).split(' ')[0]} {synthesis.REDO}" for x in lines]
            from kb._lib import turn
            turn.backup(vault, rel, turn_id, by="program")
            write_text_atomic(Path(vault) / rel, synthesis.replace_front(text, {"整理到": new}))
    except Exception as e:  # noqa: BLE001
        _log(vault, f"衝突的卡標不了重做 {rel}：{type(e).__name__}: {str(e)[:100]}")


def _merge_streams(vault, rel):
    """流水檔衝突：stage 2、stage 3，加上工作區裡衝突標記以外、讀得懂的行（衝突還在時照樣寫進來的，測試第 2 輪 #14），
    三邊取聯集、去掉重複的行。"""
    a = git.show_stage(vault, rel, 2) or ""
    b = git.show_stage(vault, rel, 3) or ""
    try:
        work = _work_lines((Path(vault) / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        work = []
    seen, lines = set(), []
    for ln in (a + "\n" + b).splitlines() + work:
        if ln.strip() and ln not in seen:
            seen.add(ln)
            lines.append(ln)
    write_text_atomic(Path(vault) / rel, "\n".join(lines) + "\n")


def _work_lines(text):
    """工作區那份裡讀得懂的流水行：衝突標記、壞行不算；diff3 的共同祖先段（||||||| 到 =======）也不收，免得救回兩邊都拿掉的行。"""
    out, base = [], False
    for ln in text.splitlines():
        if ln.startswith("|||||||"):
            base = True
            continue
        if ln.startswith(("<<<<<<<", "=======", ">>>>>>>")):
            base = False
            continue
        if base:
            continue
        try:
            if isinstance(json.loads(ln), dict):
                out.append(ln)
        except (ValueError, RecursionError):
            pass
    return out


def public(got):
    return json.loads(json.dumps(got, ensure_ascii=False))




# ---------- 6. 退回時別人改過的卡與檔（D-296 (2)(3)） ----------

AFTER = "after.jsonl"
COPY_SUFFIX = "退回前的版本"


def _file_sha(vault, rel):
    f = Path(vault) / rel
    try:
        return hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
    except OSError:
        return None


def write_after(vault, turn_id):
    """回合收尾（Stop）：這回合碰過的檔（manifest 裡知識庫內的路徑）現在的 sha 記進 versions/<turn>/after.jsonl，
    一行 {"path","sha256"}。之後 undo 比現在的 sha：不同＝這回合之後有人（pull 進來的、雲端同步的）改過。"""
    from kb._lib import turn
    seen, rows = set(), []
    for e in turn.entries(vault, turn_id):
        rel = e.get("path")
        if not rel or e.get("uri") or rel in seen or rel.startswith("_src/"):
            continue
        seen.add(rel)
        rows.append({"path": rel, "sha256": _file_sha(vault, rel)})
    if not rows:
        return 0
    d = Path(vault) / turn.VERSIONS_REL / turn_id
    d.mkdir(parents=True, exist_ok=True)
    write_text_atomic(d / AFTER, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return len(rows)


def changed_after(vault, turn_id, rel):
    """這個檔現在的 sha 跟那回合收尾時記的不同嗎（沒記就是沒有，回 False）。"""
    from kb._lib import turn
    p = Path(vault) / turn.VERSIONS_REL / turn_id / AFTER
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln) if ln.strip() else {}
            if r.get("path") == rel:
                return r.get("sha256") != _file_sha(vault, rel)
    except (OSError, ValueError):
        pass
    return False


def other_changed(vault, turn_id, rel, backup=None):
    """退回一項（檔）之前：這回合之後被別人改過就先替她做好一半，回 conflict 的補充欄 dict；沒被動過回 None。
    卡 → 不換回舊版，標重做，done: "redo"；產出、原檔 → 不換回，留的那份另存「<名稱>（退回前的版本）.<副檔名>」，copy: 路徑。
    who：卡取 git log -1 的人名，雲端的檔寫「同事」；via：git 追蹤的是 pull，其他是 cloud。"""
    if not changed_after(vault, turn_id, rel):
        return None
    tracked = git.is_repo(vault) and git.tracked(vault, rel)
    who = (git.author_of(vault, "HEAD", rel) if tracked else None) or "同事"
    out = {"side": "other", "who": who, "via": "pull" if tracked else "cloud", "done": None, "copy": None}
    if _is_card(vault, rel):
        _mark_redo(vault, rel, turn_id)
        out["done"] = "redo"
    elif backup and Path(backup).is_file():
        stem, ext = os.path.splitext(rel)
        dst = f"{stem}（{COPY_SUFFIX}）{ext}"
        try:
            (Path(vault) / dst).write_bytes(Path(backup).read_bytes())
            out["copy"] = dst
        except OSError:
            pass
    return out
