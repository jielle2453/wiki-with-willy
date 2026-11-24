"""網頁對話（#74 第 2 份，D-351～D-353、D-355 第 6 點）：小伺服器背後叫她自己登入、沒改過的 claude（D-350），一條對話一個常駐程式。

只用標準函式庫。一個知識庫一個 Chat（小伺服器起來時建）：
- 開程式：claude -p 加 stream-json 進出、--permission-mode auto、--permission-prompt-tool stdio；有要接回的段加 --resume。
  環境從目前的複製一份、拿掉 ANTHROPIC_API_KEY、設 WIKI_WITH_WILLY_WEB=1（開場 hook 認它帶最近的工作日誌）；
  WIKI_WITH_WILLY_WEB_PLUGIN_DIR 有值加 --plugin-dir（只給開發與驗收，不寫進任何狀態檔，D-019）。工作目錄＝知識庫。
- 送話：一行 user JSON 排進那個程式的寫入佇列，由它自己的寫入執行緒寫 stdin（不拿對話的鎖，卡 60 秒當斷了；測試第 1 輪 #1 死結）；
  AI 在回時照樣直接送（併進同一回合，B-547）。帶 page 時前面加一行「（她正在看：…）」，畫面只顯示她打的字。
- 讀事件（背景執行緒）：init 記 session_id；assistant 的 text 區塊一個一則（子代理的 parent_tool_use_id 有值，不顯示）；
  result 結束回答中、算長度（input＋cache_creation＋cache_read，全 0 的不算）；task_started（is_backgrounded）／task_notification
  算背景工作；control_request 的 can_use_tool 出卡片（AskUserQuestion 選項題、其他允許卡），其他 subtype 回 error；
  control_cancel_request 收掉那張卡。stdout 讀到結尾而不是我們關的 → 系統訊息「AI 那邊斷了…」，下一句 resume 接回；
  是 --resume 開的、還沒 init 就結束（接不回上一段）→ 開新的一段把那幾句自動重送一次，系統訊息「接不回上一段，開了新的一段」。
- 閒置與換段（D-351）：不忙（沒在回、沒背景工作、沒卡片在等）且最後一個事件超過 web_idle_close_minutes → 關（關 stdin、等 10 秒、
  還在就 taskkill /T /F）；她送話時程式沒開：最後的活動超過 web_segment_idle_minutes 且這一段長了 web_segment_growth_tokens 以上
  → 新的一段，否則 resume。她說「重新開始」→ 關掉、下一句開新的一段。
- 狀態檔 .system/local/web/chat.json；畫面紀錄 .system/local/web/chat/YYYY-MM.jsonl（遮密碼、只記回完的、留 web_chat_keep_days 天，
  單人版放本機不進 git）；排除與 purge 時 scrub 一起清。知識庫不見了（.system 被刪）不寫任何檔。
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime
from pathlib import Path

from kb._lib.io import FileLock, read_json, write_json_atomic, write_text_atomic

STATE_REL = ".system/local/web/chat.json"
LOG_REL = ".system/local/web/chat"
STDERR_REL = ".system/local/web/claude-stderr.log"
STDERR_KEEP = 200 * 1024
CLOSE_WAIT = 10
WRITE_WAIT = 60  # 寫給 claude 的一筆卡超過這麼久（它不讀 stdin 了）當斷了
HISTORY = 100
EVENTS_KEEP = 500
BASE_ARGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
             "--permission-mode", "auto", "--permission-prompt-tool", "stdio"]
NO_CLAUDE = "主機上還沒裝好 Claude，請服務人員來裝（建置步驟『網頁』）"
BROKEN = "AI 那邊斷了，再說一句就會接上"
RESUME_LOST = "接不回上一段，開了新的一段"
NOT_STARTED = "AI 那邊開不起來（已記下），請服務人員看一下"
RESTART_WORD = "重新開始"
RESTARTED = "好，下一句開始新的對話"
ERROR_DEFAULT = "AI 回答出錯了"
ALLOW_PREFIX = "需要客戶按允許才會執行："
DEFAULT_REASON = "AI 要做一件需要你同意的事"
DENY_MESSAGE = "她按了不允許"
PAGE_LINE = "（她正在看：{}）\n"
MULTI_JOIN = "、"
_MONTH = re.compile(r"^\d{4}-\d{2}$")

_time = time.time  # 測試換掉它


class CardGone(Exception):
    """卡片已經被別台回過（或程式已經關了）：網頁收 409、重抓。"""


class Closed(Exception):
    """小伺服器正在關：不再送話、不再開 claude（網頁收 503）。"""


# ---------- 小工具 ----------

def _iso(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _stamp(text):
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


def _log(vault, text):
    """寫 hook-errors；知識庫不見了（.system 被刪）寫到 %TEMP% 那份，不把 .system 又建出來。"""
    from kb._lib import ledger
    ledger.log_error(vault if (Path(vault) / ".system").is_dir() else None, text, source="web")


def find_claude():
    """PATH 上的 claude（跳過目前目錄，同 upgrade._which）優先，再找桌面版內建的 claude.exe；都沒有回 None。"""
    from kb._lib import upgrade
    return upgrade._which("claude") or upgrade.find_claude()


def claude_command():
    """要跑的指令前段；測試用 WIKI_WITH_WILLY_WEB_CLAUDE 指到假的 claude（tests/fake_claude.py）。找不到回 None。"""
    fake = os.environ.get("WIKI_WITH_WILLY_WEB_CLAUDE")
    if fake:
        return [sys.executable, fake]
    found = find_claude()
    return [found] if found else None


def card_reason(request):
    """允許卡的理由：decision_reason 去掉「需要客戶按允許才會執行：」，沒有用 description，再沒有用預設句。不給 input.command。"""
    reason = str(request.get("decision_reason") or "").strip()
    if reason.startswith(ALLOW_PREFIX):
        reason = reason[len(ALLOW_PREFIX):].strip()
    return reason or str(request.get("description") or "").strip() or DEFAULT_REASON


def _answers(questions, answer):
    """網頁送的選項題答案 → claude 要的 answers（題目文字 → 選到的 label；多選用「、」接；我自己說用她打的字）。不對丟 ValueError。"""
    if not isinstance(answer, dict) or not answer:
        raise ValueError("答案要是 {題目: 答案}")
    by_q = {q.get("question"): q for q in questions if isinstance(q, dict) and isinstance(q.get("question"), str)}
    out = {}
    for key, value in answer.items():
        q = by_q.get(key)
        if q is None:
            raise ValueError(f"沒有這題：{key}")
        labels = [o.get("label") for o in q.get("options") or [] if isinstance(o, dict)]
        if isinstance(value, str) and value in labels:
            out[key] = value
        elif (isinstance(value, list) and value and q.get("multiSelect") is True
              and all(isinstance(v, str) and v in labels for v in value)):
            out[key] = MULTI_JOIN.join(value)
        elif isinstance(value, dict) and isinstance(value.get("text"), str) and value["text"].strip():
            out[key] = value["text"].strip()
        else:
            raise ValueError(f"答案不對：{key}")
    if set(out) != set(by_q):
        raise ValueError("還有題目沒回")
    return {k: out[k] for k in by_q}


def _usage_total(usage):
    """這一段現在的長度：input＋cache_creation＋cache_read。result 的 usage 是整個回合每次呼叫加起來（用了工具就重複算好幾次），
    有 iterations 就用最後一筆（最後一次呼叫＝現在的長度），沒有才用加總。"""
    if not isinstance(usage, dict):
        return 0
    its = usage.get("iterations")
    if isinstance(its, list) and its and isinstance(its[-1], dict):
        usage = its[-1]
    total = 0
    for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        v = usage.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            total += v
    return total


# ---------- 畫面紀錄 ----------

def log_dir(vault):
    return Path(vault) / LOG_REL


def _lock(vault):
    d = log_dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    return FileLock(d)  # 鎖檔 .system/local/web/chat.lock


def _month_files(vault):
    d = log_dir(vault)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.jsonl") if _MONTH.match(p.stem))


def _rows(path):
    out = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue  # 壞行、寫到一半的行不顯示
        if isinstance(row, dict) and isinstance(row.get("seq"), int):
            out.append(row)
    return sorted(out, key=lambda r: r["seq"])


def read_rows(vault, after=None, limit=HISTORY):
    """after 沒給：最近 limit 則（跨月）；給了：seq 比它大的全部。由舊到新。"""
    out = []
    for p in reversed(_month_files(vault)):
        rows = _rows(p)
        if after is None:
            out = rows + out
            if len(out) >= limit:
                break
        else:
            newer = [r for r in rows if r["seq"] > after]
            out = newer + out
            if len(newer) < len(rows):
                break
    return out[-limit:] if after is None else out


def last_seq(vault):
    for p in reversed(_month_files(vault)):
        rows = _rows(p)
        if rows:
            return rows[-1]["seq"]
    return 0


def prune(vault, days, now=None):
    """刪掉超過 days 天的行；整個月都過期的檔直接刪。讀不懂的行留著。"""
    cutoff = (_time() if now is None else now) - days * 86400
    files = _month_files(vault)
    if not files:
        return
    with _lock(vault):
        for p in files:
            y, m = int(p.stem[:4]), int(p.stem[5:])
            month_end = datetime(y + (m == 12), m % 12 + 1, 1).astimezone().timestamp()
            if month_end <= cutoff:
                p.unlink()
                continue
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            keep = []
            for ln in lines:
                try:
                    row = json.loads(ln)
                except ValueError:
                    keep.append(ln)
                    continue
                t = _stamp(row.get("t")) if isinstance(row, dict) else None
                if t is None or t >= cutoff:
                    keep.append(ln)
            if not keep:
                p.unlink()
            elif len(keep) != len(lines):
                write_text_atomic(p, "\n".join(keep) + "\n")


def _mask_deep(obj, mask):
    if isinstance(obj, str):
        return mask(obj)
    if isinstance(obj, list):
        return [_mask_deep(x, mask) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in ("card_id", "kind", "answered_at", "multiSelect") else _mask_deep(v, mask)) for k, v in obj.items()}
    return obj


def _scrub_row(row, mask, mention):
    from kb._lib import journal
    row = dict(row)
    card = dict(row["card"]) if isinstance(row.get("card"), dict) else None
    blob = str(row.get("text") or "") + (json.dumps(card, ensure_ascii=False) if card else "")
    if mention is not None and mention(blob):
        row["text"] = journal.PURGED_TEXT
        if card:
            for k in ("reason", "answer"):
                if card.get(k):
                    card[k] = journal.PURGED_TEXT
            if "questions" in card:
                card["questions"] = []
    else:
        if isinstance(row.get("text"), str):
            row["text"] = mask(row["text"])
        if card:
            card = _mask_deep(card, mask)
    if card is not None:
        row["card"] = card
    return row


def scrub(vault, mask, mention=None, keep=()):
    """排除與 purge（D-353）：寫法照 journal.scrub。每則的字（與卡片的題目、理由、答案）讓 mention 說有提到的整則換「（已排除）」，
    其他照 mask 換。keep（排除那一句、清那一句的回合）在畫面紀錄對不上回合，收著不用。壞行照舊留著。回改到幾則。"""
    files = _month_files(vault)
    if not files:
        return 0
    total = 0
    with _lock(vault):
        for p in files:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            out, hits = [], 0
            for ln in lines:
                try:
                    row = json.loads(ln)
                except ValueError:
                    out.append(ln)
                    continue
                if not isinstance(row, dict):
                    out.append(ln)
                    continue
                new = _scrub_row(row, mask, mention)
                if new != row:
                    hits += 1
                    out.append(json.dumps(new, ensure_ascii=False))
                else:
                    out.append(ln)
            if hits:
                write_text_atomic(p, "\n".join(out) + "\n")
                total += hits
    return total


# ---------- 對話 ----------

class Chat:
    """鎖的分工（死結修正，測試第 1 輪 #1、#10）：self.lock 只保護記憶體（程式、卡片、事件、狀態），畫面紀錄的一行也在裡面寫（要跟 seq 同順序）；
    寫 claude 的 stdin 一律丟進那個程式的佇列、由它自己的寫入執行緒寫（卡超過 WRITE_WAIT 秒當斷了）；開程式（Popen）在 send_lock 裡、
    self.lock 外；chat.json 在鎖裡只記「要寫」，鎖外由 _flush 寫。順序：send_lock → save_lock → self.lock，持 self.lock 時不拿另外兩把。"""

    def __init__(self, vault):
        self.vault = Path(vault)
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.send_lock = threading.Lock()  # 送話（與接不回時重送）一次一個：只有拿著它的才開程式
        self.save_lock = threading.Lock()  # 寫 chat.json 一次一個
        self.dirty = False
        self.proc = None
        self.answering = False
        self.tasks = set()
        self.cards = {}  # card_id → {"request_id", "input", "public"}
        self.new_segment = False
        self.closed = False
        self.last_event = _time()
        self.events = deque(maxlen=EVENTS_KEEP)
        self.event_n = 0
        self.stoppers = []
        self.pruned_on = None
        self.state = self._load_state()
        self._maybe_prune()
        saved = self.state.get("seq")
        self.seq = max(last_seq(self.vault), saved if isinstance(saved, int) and not isinstance(saved, bool) else 0)

    # ----- 狀態 -----

    def _load_state(self):
        st = {"session_id": None, "baseline_tokens": None, "last_tokens": None, "last_activity": None, "seq": 0}
        try:
            data = read_json(self.vault / STATE_REL)
        except (OSError, ValueError) as e:
            _log(self.vault, f"網頁對話的 chat.json 讀不了，當成沒有：{type(e).__name__}: {e}")
            data = None
        if isinstance(data, dict):
            st.update({k: data.get(k) for k in st})
        return st

    def _changed(self):
        """（持鎖）狀態改了：只記一下，鎖外的 _flush 才寫 chat.json。"""
        self.dirty = True

    def _flush(self):
        """（不持 self.lock）有改就寫 chat.json。知識庫不見了不寫（不把刪掉的 .system 又建出來）。"""
        with self.save_lock:
            with self.lock:
                if not self.dirty:
                    return
                self.dirty = False
                state = dict(self.state)
            if self._gone():
                return
            try:
                p = self.vault / STATE_REL
                p.parent.mkdir(parents=True, exist_ok=True)
                write_json_atomic(p, state)
            except OSError as e:
                _log(self.vault, f"網頁對話的 chat.json 寫不了：{type(e).__name__}: {e}")

    def _gone(self):
        """知識庫不見了（.system 被刪）：小伺服器下一輪檢查就退，這之間對話不寫任何檔。"""
        return not (self.vault / ".system").is_dir()

    def _setting(self, key):
        from kb._lib import config
        return config.get(self.vault, key)

    def _maybe_prune(self):
        today = date.today()
        if self.pruned_on == today:
            return
        self.pruned_on = today
        try:
            prune(self.vault, self._setting("web_chat_keep_days"))
        except (OSError, ValueError) as e:
            _log(self.vault, f"網頁對話紀錄清舊的出錯：{type(e).__name__}: {e}")

    # ----- 給網頁的 -----

    def idle(self):
        """不忙：沒有 AI 在回、沒有背景助手在跑、沒有卡片在等她。"""
        with self.lock:
            return not (self.answering or self.tasks or self.cards)

    def running(self):
        with self.lock:
            return self.proc is not None and self.proc.poll() is None

    def snapshot(self, after=None):
        with self.lock:
            cards = [dict(c["public"]) for c in self.cards.values()]
            busy = self.answering
        return {"messages": read_rows(self.vault, after), "cards": cards, "busy": busy}

    def event_count(self):
        with self.lock:
            return self.event_n

    def events_after(self, n, timeout):
        """n 之後的事件；沒有就等最多 timeout 秒。回 (目前的編號, [事件])。"""
        with self.cond:
            if self.event_n <= n and timeout and not self.closed:
                self.cond.wait_for(lambda: self.event_n > n or self.closed, timeout)
            return self.event_n, [e for k, e in self.events if k > n]

    def send(self, text, page=None):
        text = str(text or "")
        if not text.strip():
            raise ValueError("沒有字")
        self._maybe_prune()
        try:
            with self.send_lock:
                if text.strip() == RESTART_WORD:
                    with self.lock:
                        if self.closed:
                            raise Closed()
                        self._record("her", text)
                        stop = self._detach()
                        self.new_segment = True
                        self._record("system", RESTARTED)
                    if stop is not None:
                        self._stop_later(stop)
                    return
                content = (PAGE_LINE.format(page) if page else "") + text
                self._deliver([{"type": "user", "message": {"role": "user", "content": content}}], her=text)
        finally:
            self._flush()

    def _deliver(self, lines, her=None, lost=False):
        """（持 send_lock、不持 self.lock）把 user 行交給程式：開著就直接排進去（AI 在回也照送）；沒開先開（Popen 在 self.lock 外，
        讀事件與網頁不被擋）。lost：接不回上一段、這幾行是重送，先記系統訊息。"""
        cmd = claude_command()
        limits = (self._setting("web_segment_idle_minutes"), self._setting("web_segment_growth_tokens"))
        with self.lock:
            if self.closed:
                raise Closed()
            if lost:
                self._record("system", RESUME_LOST)
            start = bool(cmd) and not self._alive()
            resume = self._plan_segment(*limits) if start else None  # 換段那行時間在她這句前面
            if her is not None:
                self._record("her", her)
            if not cmd:
                self._record("system", NO_CLAUDE)
                return
            if not start:
                self._push(self.proc, lines)
                self._touch()
                return
            self._set_answering(True)  # 開程式的這一兩秒也算在回（換版不交棒）
        try:
            proc = self._spawn(cmd, resume)
        except OSError as e:
            _log(self.vault, f"網頁對話開不了 claude：{type(e).__name__}: {e}")
            with self.lock:
                self._set_answering(False)
                self._record("system", NOT_STARTED)
            return
        with self.lock:
            late = self.closed  # 開程式的這段時間小伺服器關了
            if not late:
                self._attach(proc)
                self._push(proc, lines)
                self._touch()
        if late:
            try:
                proc.stdin.close()  # 還沒接寫入執行緒：直接關
            except OSError:
                pass
            self._stop(proc)
            raise Closed()

    def answer(self, card_id, answer):
        with self.lock:
            if self.closed:
                raise Closed()
            card = self.cards.get(card_id)
            if card is None or self.proc is None:
                raise CardGone(card_id)
            public = card["public"]
            if public["kind"] == "question":
                answers = _answers(public["questions"], answer)
                body = {"behavior": "allow", "updatedInput": {"questions": card["input"].get("questions"), "answers": answers}}
                shown = "；".join(answers.values())
                text = f"你選了：{shown}"
            else:
                if answer not in ("allow", "deny"):
                    raise ValueError("允許卡的答案是 allow 或 deny")
                if answer == "allow":
                    body = {"behavior": "allow", "updatedInput": card["input"]}
                else:
                    body = {"behavior": "deny", "message": DENY_MESSAGE}
                shown = "允許" if answer == "allow" else "不允許"
                text = f"你按了{shown}"
            self._queue(self.proc, {"type": "control_response",
                                    "response": {"subtype": "success", "request_id": card["request_id"], "response": body}})
            del self.cards[card_id]
            self.last_event = _time()
            row = self._record("card", text, card=dict(public, answer=shown, answered_at=_iso(self.last_event)), event="card_done")
        self._flush()
        return row

    def tick(self, now=None):
        """每分鐘（小伺服器的定時檢查）：每天第一次清舊的紀錄；不忙又閒置太久就關程式。"""
        now = _time() if now is None else now
        self._maybe_prune()
        limit = self._setting("web_idle_close_minutes") * 60
        with self.lock:
            if self.proc is None or not self.idle():
                return
            if now - self.last_event <= limit:
                return
            proc = self._detach()
        self._stop(proc)

    def close(self):
        with self.lock:
            self.closed = True
            proc = self._detach() if self.proc is not None else None
            self.cond.notify_all()
        if proc is not None:
            self._stop(proc)
        for t in list(self.stoppers):
            t.join(CLOSE_WAIT + 10)
        self._flush()

    # ----- 程式 -----

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _touch(self):
        """（持鎖）她剛送了話：算活動、在回答中。"""
        self.last_event = _time()
        self.state["last_activity"] = _iso(self.last_event)
        self._changed()
        self._set_answering(True)

    def _plan_segment(self, idle_minutes, growth_tokens):
        """（持鎖）程式沒開、她要送話：回要 resume 的 session_id，或 None（開新的一段，基準重算）。"""
        st = self.state
        sid = st.get("session_id")
        new = self.new_segment or not sid
        if not new:
            last = _stamp(st.get("last_activity"))
            grown = (st.get("last_tokens") or 0) - (st.get("baseline_tokens") or 0)
            new = last is not None and _time() - last > idle_minutes * 60 and grown > growth_tokens
        if not new:
            return sid
        if sid or self.new_segment:
            self._record("system", "", segment=True)
        self.new_segment = False
        st.update(session_id=None, baseline_tokens=None, last_tokens=None)
        self._changed()
        return None

    def _spawn(self, cmd, resume):
        """（不持鎖）開程式；還沒接上讀寫的執行緒（_attach 才接）。"""
        argv = list(cmd) + BASE_ARGS + (["--resume", resume] if resume else [])
        plugin_dir = os.environ.get("WIKI_WITH_WILLY_WEB_PLUGIN_DIR")
        if plugin_dir:
            argv += ["--plugin-dir", plugin_dir]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)  # 用她自己登入的訂閱，不准走 API 金鑰（D-350）
        env["WIKI_WITH_WILLY_WEB"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if self._gone():
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    cwd=str(self.vault), env=env, creationflags=flags)
        else:
            err_path = self.vault / STDERR_REL
            err_path.parent.mkdir(parents=True, exist_ok=True)
            _trim(err_path)
            with open(err_path, "ab") as err:
                proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                        cwd=str(self.vault), env=env, creationflags=flags)
        proc.willy_closing = False
        proc.willy_resumed = bool(resume)
        proc.willy_init = False
        proc.willy_stuck = False
        proc.willy_sent = []  # init 之前送進去的 user 行：接不回上一段時拿來重送
        proc.willy_q = queue.Queue()
        return proc

    def _attach(self, proc):
        """（持鎖）接上目前的程式與它的讀、寫執行緒。讀的執行緒要等這段鎖放掉才處理，所以 willy_sent 一定先填好。"""
        self.proc = proc
        self.last_event = _time()
        threading.Thread(target=self._read, args=(proc,), daemon=True).start()
        threading.Thread(target=self._writer, args=(proc,), daemon=True).start()

    def _push(self, proc, lines):
        """（持鎖）user 行排進去；init 前的留一份給重送。"""
        for line in lines:
            if not proc.willy_init:
                proc.willy_sent.append(line)
            self._queue(proc, line)

    @staticmethod
    def _queue(proc, obj):
        """（持鎖也可以）排進那個程式的寫入佇列，不會卡。"""
        proc.willy_q.put((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def _writer(self, proc):
        """每個程式一條：照順序寫 stdin，不拿 self.lock（死結修正 #1）。一筆卡超過 WRITE_WAIT 秒（它不讀了）→ 當斷了殺掉，
        讀的執行緒讀到結尾照斷線處理。收到 None（_stop）→ 寫完前面的就關 stdin。"""
        q = proc.willy_q
        while True:
            data = q.get()
            if data is None:
                break
            timer = threading.Timer(WRITE_WAIT, self._stuck, (proc,))
            timer.daemon = True
            timer.start()
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError):
                return  # 程式沒了：讀的那邊讀到結尾會處理
            finally:
                timer.cancel()
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass

    def _stuck(self, proc):
        proc.willy_stuck = True
        _log(self.vault, f"網頁對話寫給 claude 卡了 {WRITE_WAIT} 秒（pid {proc.pid}），當成斷了")
        self._kill(proc)

    def _detach(self):
        """（持鎖）把目前的程式拿下來交給 _stop 關：在回、背景工作、在等的卡片都清掉（卡片各台消失）。回那個程式或 None。"""
        proc, self.proc = self.proc, None
        if proc is not None:
            proc.willy_closing = True
        self._clear_live()
        return proc

    def _clear_live(self):
        self._set_answering(False)
        self.tasks.clear()
        for cid in list(self.cards):
            del self.cards[cid]
            self._emit({"type": "card_done", "card_id": cid})

    def _stop(self, proc):
        """（不持鎖）關 stdin（寫入執行緒寫完前面的才關）、等 10 秒，還在就 taskkill /T /F（連子行程）。"""
        proc.willy_q.put(None)
        try:
            proc.wait(CLOSE_WAIT)
            return
        except subprocess.TimeoutExpired:
            pass
        self._kill(proc)

    def _kill(self, proc):
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=CLOSE_WAIT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                proc.kill()
            proc.wait(CLOSE_WAIT)
        except (OSError, subprocess.SubprocessError) as e:
            _log(self.vault, f"網頁對話關不掉 claude（pid {proc.pid}）：{type(e).__name__}: {e}")

    def _stop_later(self, proc):
        t = threading.Thread(target=self._stop, args=(proc,), daemon=True)
        self.stoppers = [s for s in self.stoppers if s.is_alive()] + [t]
        t.start()

    def _read(self, proc):
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                with self.lock:
                    if self.proc is not proc:
                        continue  # 我們拿下來在關的：後面的事件不管
                    try:
                        self._on_event(proc, ev)
                    except Exception as e:  # noqa: BLE001  一個事件出錯不讓讀的執行緒停掉
                        _log(self.vault, f"網頁對話處理事件出錯：{type(e).__name__}: {str(e)[:150]}")
                self._flush()
        except (OSError, ValueError):
            pass
        retry = None
        with self.lock:
            if self.proc is proc:  # 程式自己結束了（不是我們關的）
                self.proc = None
                self._clear_live()
                if proc.willy_resumed and not proc.willy_init:
                    self.new_segment = True  # 接不回那一段（例如紀錄不見了）：開新的一段，不一直卡在接不回
                    if proc.willy_sent and not proc.willy_stuck and not self.closed:
                        retry = list(proc.willy_sent)  # 她那句沒被處理：新的一段自動重送一次（測試第 1 輪 #2）
                if retry is None:
                    self._record("system", BROKEN)
        if retry is not None:
            try:
                with self.send_lock:
                    self._deliver(retry, lost=True)
            except Closed:
                pass
        self._flush()
        try:
            proc.wait(CLOSE_WAIT)
        except subprocess.TimeoutExpired:
            pass

    def _on_event(self, proc, ev):
        self.last_event = _time()
        kind, sub = ev.get("type"), ev.get("subtype")
        if kind == "system":
            if sub == "init":
                proc.willy_init = True
                proc.willy_sent = []
                sid = ev.get("session_id")
                if isinstance(sid, str) and sid and sid != self.state.get("session_id"):
                    self.state["session_id"] = sid
                    self._changed()
            elif sub == "task_started" and ev.get("is_backgrounded") is True and ev.get("task_id"):
                self.tasks.add(ev["task_id"])
            elif sub == "task_notification" and ev.get("task_id"):
                self.tasks.discard(ev["task_id"])
        elif kind == "assistant":
            if ev.get("parent_tool_use_id"):
                return  # 背景助手（子代理）自己的話
            self._set_answering(True)  # 背景助手做完後 claude 自己開的新回合也算在回
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip():
                    self._record("ai", block["text"])
        elif kind == "result":
            self._set_answering(False)
            total = _usage_total(ev.get("usage"))
            if total > 0:  # 背景回合後那個 usage 全 0 的 result 不算
                if self.state.get("baseline_tokens") is None:
                    self.state["baseline_tokens"] = total
                self.state["last_tokens"] = total
            self.state["last_activity"] = _iso(self.last_event)
            self._changed()
            if ev.get("is_error") is True and not (proc.willy_resumed and not proc.willy_init):
                # --resume 還沒 init 就出錯＝接不回上一段（真 claude 會先吐一個空的出錯 result 再結束）：不說「出錯了」，結束時重送
                self._record("system", str(ev.get("result") or "").strip() or ERROR_DEFAULT)
        elif kind == "control_request":
            self._on_request(proc, ev)
        elif kind == "control_cancel_request":
            rid = ev.get("request_id")
            for cid, card in list(self.cards.items()):
                if card["request_id"] == rid:
                    del self.cards[cid]
                    self._emit({"type": "card_done", "card_id": cid})

    def _on_request(self, proc, ev):
        rid = ev.get("request_id")
        req = ev.get("request") if isinstance(ev.get("request"), dict) else {}
        if req.get("subtype") != "can_use_tool":
            self._queue(proc, {"type": "control_response", "response": {"subtype": "error", "request_id": rid, "error": "不支援"}})
            return
        inp = req.get("input") if isinstance(req.get("input"), dict) else {}
        cid = uuid.uuid4().hex[:12]
        if req.get("tool_name") == "AskUserQuestion":
            questions = [q for q in inp.get("questions") or [] if isinstance(q, dict)]
            public = {"card_id": cid, "kind": "question", "questions": questions}
            text = "；".join(str(q.get("question") or "") for q in questions)
        else:
            reason = self._mask(card_reason(req))
            public = {"card_id": cid, "kind": "permission", "reason": reason}
            text = reason
        self.cards[cid] = {"request_id": rid, "input": inp, "public": public}
        row = self._record("card", text, card=dict(public, answer="", answered_at=""), event="card")
        public["seq"] = row["seq"]

    # ----- 紀錄與事件 -----

    def _set_answering(self, value):
        if self.answering != value:
            self.answering = value
            self._emit({"type": "busy", "busy": value})

    def _emit(self, ev):
        self.event_n += 1
        self.events.append((self.event_n, ev))
        self.cond.notify_all()

    def _mask(self, text):
        from kb._lib import journal
        return journal._mask(text, self.vault)

    def _record(self, who, text, card=None, segment=False, event="message"):
        """（持鎖）記一則進畫面紀錄（遮密碼）並推給各台。回那一行。知識庫不見了只推不寫。"""
        self.seq += 1
        self.state["seq"] = self.seq  # 紀錄被清光、寫不進去也不重用編號（開著的網頁認 seq）
        self._changed()
        row = {"seq": self.seq, "t": _iso(_time()), "who": who, "text": self._mask(text) if text else ""}
        if card is not None:
            row["card"] = _mask_deep(card, self._mask)
        if segment:
            row["segment"] = True
        if not self._gone():
            try:
                with _lock(self.vault):
                    p = log_dir(self.vault) / f"{row['t'][:7]}.jsonl"
                    with open(p, "a", encoding="utf-8", newline="\n") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
            except OSError as e:
                _log(self.vault, f"網頁對話紀錄寫不了：{type(e).__name__}: {e}")
        ev = {"type": event, "message": row}
        if event == "card":
            ev["card"] = dict(self.cards[card["card_id"]]["public"], seq=row["seq"])
        elif event == "card_done":
            ev["card_id"] = card["card_id"]
        self._emit(ev)
        return row


def _trim(path):
    """stderr 紀錄只留最後 200 KB（每次開程式前修一次）。"""
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= STDERR_KEEP:
        return
    try:
        with open(path, "rb") as f:
            f.seek(size - STDERR_KEEP)
            tail = f.read()
        with open(path, "wb") as f:
            f.write(tail)
    except OSError:
        pass
