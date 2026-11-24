"""willy 自己跑 git 的唯一出口（多人共用 3 新定 1，D-293～D-295）：只在 kb 程式裡跑，AI 不經 Bash 跑 git。

統一包 subprocess：逾時殺整棵、不跳帳密視窗、`index.lock` 存在時重試（Obsidian git 外掛同時在跑，spike：5 次、每次隔 2 秒）。
`git` 不在 PATH 或這裡不是 repo 時，每個函式回「不適用」（None／False／空），呼叫端照單機走。
認領標籤（spike）：每台各自新建一個帶這台代號的 commit 當標籤指向，兩台同名標籤才會因為指到不同物件而被遠端拒絕。
"""
import getpass
import os
import re
import subprocess
from pathlib import Path

LOCK_RETRIES = 5
LOCK_WAIT = 2.0  # 秒
TIMEOUT = 30
_SLEEP = None  # 測試換掉它免得真的等


def _sleep(sec):
    import time
    (_SLEEP or time.sleep)(sec)


def available():
    from shutil import which
    return which("git") is not None


def _kill(p):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        p.kill()


def _once(vault, args, timeout, env_extra=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never", LC_ALL="C")
    env.update(env_extra or {})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(["git", "-C", str(vault), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        p.communicate()
        return 124, "", "逾時"
    return p.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def run(vault, *args, timeout=TIMEOUT, env=None):
    """回 (結束碼, stdout, stderr)；git 不在 PATH 回 (127, "", "")。`index.lock` 被占時重試。"""
    if not available():
        return 127, "", ""
    for i in range(LOCK_RETRIES + 1):
        rc, out, err = _once(vault, args, timeout, env)
        if rc != 0 and "index.lock" in err and i < LOCK_RETRIES:
            _sleep(LOCK_WAIT)
            continue
        return rc, out, err
    return rc, out, err


def _ok(vault, *args, **kw):
    rc, out, _e = run(vault, *args, **kw)
    return out.strip() if rc == 0 else None


def _plain_git_dir(vault):
    """一般 clone（.git 是夾、裡面有 HEAD）→ 那個夾；worktree、submodule（.git 是檔）等回 None 交給 git 判斷。
    每句開頭都會問，讀檔不叫子行程（#44：多人每句先付 0.4 秒 git）。"""
    d = Path(vault) / ".git"
    try:
        ok = (d / "HEAD").is_file() and (d / "objects").is_dir() and (d / "refs").is_dir()  # 只有 HEAD 的壞夾交給 git 判斷
        return d.resolve() if ok else None
    except OSError:
        return None


def is_repo(vault):
    if not (Path(vault) / ".git").exists():  # 單人沒有 git 的知識庫：不叫子行程（每句開頭都問）
        return False
    if not available():
        return False
    return _plain_git_dir(vault) is not None or _ok(vault, "rev-parse", "--is-inside-work-tree") == "true"


def _git_dir(vault):
    d = _plain_git_dir(vault)
    if d is not None:
        return d
    root = outer_repo(vault)  # 子夾：上層是一般 clone 就讀檔，不叫子行程（每句開頭會問 rebase，D-554 實作疑義 2）
    d = _plain_git_dir(root) if root is not None else None
    if d is not None:
        return d
    got = _ok(vault, "rev-parse", "--absolute-git-dir")
    return Path(got) if got else None


def _head_from_files(d):
    """讀 .git/HEAD → 分支檔或 packed-refs 的 sha；讀不懂回 None（呼叫端改叫 git）；分支還沒有 commit（檔案式 ref，
    沒分支檔、packed-refs 也沒有）回 ""：確定沒有 HEAD，不必叫 git（家目錄是空 repo 時每句多付好幾次，code review 3 後實測）。"""
    try:
        text = (d / "HEAD").read_text(encoding="utf-8").strip()
        if not text.startswith("ref:"):
            return text if _is_sha(text) else None
        ref = text[4:].strip()
        f = d / ref
        if f.is_file():
            sha = f.read_text(encoding="utf-8").strip()
            return sha if _is_sha(sha) else None
        packed = d / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and _is_sha(parts[0]):
                    return parts[0]
        if not (d / "reftable").exists():
            return ""
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _is_sha(s):
    return len(s) in (40, 64) and all(c in "0123456789abcdef" for c in s)


def head(vault):
    if not is_repo(vault):
        return None
    d = _plain_git_dir(vault)
    got = _head_from_files(d) if d is not None else None
    return None if got == "" else (got or _ok(vault, "rev-parse", "HEAD"))


def changed_since(vault, old, new):
    """git diff --name-only old new；old 空或不認得就回 None（呼叫端當全部都要比）。"""
    if not old or not new:
        return None
    got = _ok(vault, "diff", "--name-only", "-z", old, new)
    if got is None:
        return None
    return [x for x in got.split("\0") if x]


def own_commits(vault):
    """這台自己做的 commit（HEAD 的 reflog 裡 commit…、commit (merge)… 那幾種；kb sync untrack 也記成 commit:）。
    pull、merge、reset 移過來的不算。分「別人的」不看作者名：一人多台、同步外掛都用同一個名字（D-554 實作疑義 3）。"""
    rc, out, _e = run(vault, "reflog", "show", "--format=%H %gs", "HEAD")
    if rc != 0:
        return set()
    got = set()
    for ln in out.splitlines():
        sha, _sp, subject = ln.partition(" ")
        if subject.startswith(("commit", "cherry-pick", "revert", "am:")) or _REPLAYED.match(subject) or _MERGED.search(subject):
            got.add(sha)
    return got


# pull --rebase／rebase 把這台的 commit 重放一次：新的 sha 記成 "pull -q --rebase origin main (pick): …"、衝突解完是
# "rebase (continue): …"，還是這台的；(start) 那行指的是對方的 commit，不算（git 2.50 實測）
# 這台 pull／merge 做出來的合併 commit（"pull --no-rebase: Merge made by the 'ort' strategy."）也是這台的：
# -m 對遠端那個 parent 比出的是這台自己的改動（code review 3）；fast-forward 的 sha 是對方的，不算
_MERGED = re.compile(r"^(pull|merge)\b[^:]*: Merge made by ")
_REPLAYED = re.compile(r"^(pull|rebase)\b[^()]*\((pick|reword|edit|squash|fixup|continue)\): ")


def _pulled_log(vault, old, new, extra, merges=False):
    """old..new 之間這台原本沒有、pull 進來的 commit（去掉 own_commits），各自動到的檔：回 [路徑]；不認得回 None。
    merges：合併 commit 也看（-m，對每個 parent 各比一次；只在別台的合併 commit 裡刪掉的也認得，測試 #3）。"""
    rc, out, _e = run(vault, "-c", "core.quotepath=false", "log", "-m" if merges else "--no-merges", "--format=\x01%H",
                      "--name-only", *extra, f"{old}..{new}")
    if rc != 0:
        return None
    mine = own_commits(vault)
    got, skip = [], False
    for ln in out.splitlines():
        if ln.startswith("\x01"):
            skip = ln[1:].strip() in mine
        elif ln.strip() and not skip and ln not in got:
            got.append(ln)
    return got


def changed_by_others(vault, old, new):
    """old..new 之間 pull 進來的 commit（這台自己 commit 的不算，照 reflog、不看作者名，D-554 實作疑義 3）動到的檔。
    old 空或不認得回 None。"""
    if not old or not new:
        return None
    return _pulled_log(vault, old, new, [])


def unmerged(vault, everywhere=False):
    """衝突中的檔，相對於知識庫夾、只限知識庫夾裡；everywhere 回整個 repo 的（子夾時看還有沒有別的衝突，D-554 實作疑義 1）。"""
    args = ["diff", "--name-only", "--diff-filter=U", "-z"] + ([] if everywhere else ["--relative", "--", "."])
    got = _ok(vault, *args)
    return [x for x in (got or "").split("\0") if x]


def merge_in_progress(vault):
    d = _git_dir(vault)
    return bool(d and (d / "MERGE_HEAD").exists())


def rebase_in_progress(vault):
    d = _git_dir(vault)
    return bool(d and ((d / "rebase-merge").is_dir() or (d / "rebase-apply").is_dir()))


def busy(vault):
    """合併、rebase、cherry-pick、revert、am 做到一半（kb sync untrack 不動 index，D-554）。"""
    d = _git_dir(vault)
    return bool(d and (merge_in_progress(vault) or rebase_in_progress(vault)
                       or (d / "CHERRY_PICK_HEAD").exists() or (d / "REVERT_HEAD").exists()))


def show_stage(vault, path, stage):
    """衝突檔的第 2（自己）或第 3（對方）版內容；沒有回 None。"""
    rc, out, _e = run(vault, "show", f":{int(stage)}:./{path}")  # ./：相對知識庫夾（子夾也對）
    return out if rc == 0 else None


def _bytes(vault, *args):
    if not available():
        return None
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "-C", str(vault), *args], capture_output=True, env=env,
                       stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout if r.returncode == 0 else None


def show_stage_bytes(vault, path, stage):
    return _bytes(vault, "show", f":{int(stage)}:./{path}")


def show_at(vault, rev, path):
    """某個版本裡這個檔（相對知識庫夾）的內容 bytes；沒有回 None（D-554）。"""
    if not rev:
        return None
    return _bytes(vault, "cat-file", "--filters", f"{rev}:./{path}")  # --filters：跟 checkout 一樣轉換行（autocrlf）


def checkout_theirs(vault, path):
    rc, _o, _e = run(vault, "checkout", "--theirs", "--", path)
    return rc == 0


BATCH_CHARS = 6000  # 一次命令列放多少字的路徑（Windows 命令列上限 32767，留餘裕）


def _batches(paths):
    cur, n = [], 0
    for p in paths:
        if cur and n + len(p) + 1 > BATCH_CHARS:
            yield cur
            cur, n = [], 0
        cur.append(p)
        n += len(p) + 1
    if cur:
        yield cur


def untrack(vault, paths, env=None, force=False):
    """不再追蹤（git rm --cached，工作區的檔留著）；沒追蹤的跳過。路徑多時分批、照字面不展開萬用字元（D-554）。
    force：暫存的內容跟 HEAD、工作區都不同也照拿掉（只動 index）。回有沒有全部成功。"""
    for chunk in _batches(list(paths or [])):
        rc, _o, _e = run(vault, "--literal-pathspecs", "rm", "-r", "--cached", "--quiet", "--ignore-unmatch",
                         *(["-f"] if force else []), "--", *chunk, env=env)
        if rc != 0:
            return False
    return True


def untrack_commit(vault, paths, message):
    """只把這些路徑停止追蹤並 commit（D-554 (3)）：別的已暫存改動不帶進這個 commit、照舊暫存著。
    做法：暫存的 index 從 HEAD 讀、rm --cached、write-tree、commit-tree（作者照 git 設定）、update-ref，再對真的 index rm --cached。
    回 "ok"｜"nothing"（HEAD 裡沒有這些，沒 commit）｜"error"（什麼都沒動）｜"partial"（commit 做好了、這台的 index 沒拿掉）；檔案一律留在硬碟上。"""
    d = _git_dir(vault)
    old = outer_head(vault)
    if d is None or not old or not paths:
        return "error"
    tmp = d / "willy-untrack.index"
    env = {"GIT_INDEX_FILE": str(tmp)}
    try:
        rc, _o, _e = run(vault, "read-tree", "HEAD", env=env)
        if rc != 0 or not untrack(vault, paths, env=env):
            return "error"
        tree = _ok(vault, "write-tree", env=env)
        if not tree:
            return "error"
        if tree == _ok(vault, "rev-parse", "HEAD^{tree}"):  # 沒東西可拿掉：不做空 commit（測試 #8）
            return "nothing"
        rc, out, _e = run(vault, "commit-tree", tree, "-p", old, "-m", message)
        if rc != 0 or not out.strip():
            return "error"
        rc, _o, _e = run(vault, "update-ref", "-m", "commit: " + message, "HEAD", out.strip(), old)  # reflog 記成這台的 commit
        if rc != 0:
            return "error"
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return "ok" if untrack(vault, paths, force=True) else "partial"  # commit 已做好、真的 index 沒拿掉：外掛下次會再加回


def add(vault, paths):
    if not paths:
        return True
    rc, _o, _e = run(vault, "add", "--", *paths)
    return rc == 0


def commit_merge(vault):
    rc, _o, _e = run(vault, "commit", "--no-edit")
    return rc == 0


def last_commit(vault, path):
    """最後動到這個檔（相對知識庫夾）的 commit；沒有回 None。"""
    return _ok(vault, "log", "-1", "--format=%H", "--", path)


def author_of(vault, rev, path=None):
    """git log -1 --format=%an <rev> [-- path]；沒有回 None。"""
    args = ["log", "-1", "--format=%an", rev]
    if path:
        args += ["--", path]
    got = _ok(vault, *args)
    return got or None


def tracked(vault, path):
    rc, _o, _e = run(vault, "ls-files", "--error-unmatch", "--", path)
    return rc == 0


def outer_repo(vault):
    """知識庫在哪個 repo 裡（自己的 .git 或上層的，D-554 實作疑義 1）：回 repo 根目錄；不在 repo、沒有 git 回 None。
    每句開頭都會問：只往上找 .git，不叫子行程。"""
    if not available():
        return None
    try:
        p = Path(os.path.abspath(str(vault)))
        ceiling = {os.path.normcase(os.path.abspath(c)) for c in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep) if c}
        for d in (p, *p.parents):
            g = d / ".git"
            if _plain_git_dir(d) is not None or g.is_file():  # 夾要有 HEAD、objects、refs（照 git 的判斷；家目錄只有 HEAD 的殘夾跳過往上找）；檔是 worktree／submodule
                return d
            if os.path.normcase(str(d)) in ceiling:  # 照 git 的規矩：不往這層以上找
                return None
    except OSError:
        pass
    return None


def outer_head(vault):
    """知識庫所在 repo 的 HEAD（子夾時是上層 repo 的）；不在 repo 回 None。一般 clone 讀檔，不叫子行程。"""
    root = outer_repo(vault)
    if root is None:
        return None
    d = _plain_git_dir(root)
    got = _head_from_files(d) if d is not None else None
    return None if got == "" else (got or _ok(root, "rev-parse", "HEAD"))


def orig_head(vault):
    return _ok(vault, "rev-parse", "-q", "--verify", "ORIG_HEAD")


def tracked_in_head(vault, paths):
    """HEAD 裡有的檔（相對知識庫夾），限這些路徑底下；只暫存還沒 commit 的不算（測試 #8）。不在 repo、沒有 git 回 []。"""
    if outer_repo(vault) is None:
        return []
    rc, out, _e = run(vault, "-c", "core.quotepath=false", "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths)
    return [x for x in out.split("\0") if x] if rc == 0 else []


def deleted_by_others(vault, old, new):
    """old..new 之間 pull 進來的 commit 刪掉過的檔（相對知識庫夾、只限知識庫夾裡）；不認得回 []。
    看每個 commit 而不是兩端比：old 時還沒追蹤、中間加了又刪的也算；這台自己的 commit（含 kb sync untrack）不算。"""
    if not old or not new:
        return []
    return _pulled_log(vault, old, new, merges=True, extra=["--relative", "--diff-filter=D", "--", "."]) or []


# ---------- 一次拿齊（確認 #2：git 次數不隨檔數增加） ----------

def prefix(vault):
    """知識庫夾在 repo 裡的位置（"" 或 "文件/總資料夾/"）；不在 repo 回 None。只看檔案。"""
    root = outer_repo(vault)
    if root is None:
        return None
    rel = os.path.relpath(os.path.abspath(str(vault)), str(root)).replace(os.sep, "/")
    return "" if rel == "." else rel.rstrip("/") + "/"


def unmerged_stages(vault):
    """衝突中的檔與各 stage 的物件：{路徑（相對知識庫夾）: {stage: sha}}，一次 ls-files -u。"""
    rc, out, _e = run(vault, "-c", "core.quotepath=false", "ls-files", "-u", "-z", "--", ".")
    got = {}
    for ent in (out.split("\0") if rc == 0 else []):
        meta, _tab, path = ent.partition("\t")
        parts = meta.split()
        if path and len(parts) == 3:
            got.setdefault(path, {})[int(parts[2])] = parts[1]
    return got


def cat_batch(vault, names):
    """一次讀很多物件（sha，或 rev:repo 根目錄算起的路徑），cat-file --batch：回 {名字: bytes}，沒有的不列；讀失敗回 None。
    不做換行轉換（--batch 加 --filters 收不了 rev:路徑，git 2.50 實測），寫回的是 commit 進去的樣子。"""
    names = list(dict.fromkeys(n for n in names if n))
    if not names or not available():
        return {}
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        r = subprocess.run(["git", "-C", str(vault), "cat-file", "--batch"], input="".join(n + "\n" for n in names).encode("utf-8"),
                           capture_output=True, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    got, buf, i = {}, r.stdout, 0
    for n in names:
        nl = buf.find(b"\n", i)
        if nl < 0:
            break
        head = buf[i:nl].decode("utf-8", errors="replace").split()
        i = nl + 1
        if len(head) != 3 or not head[2].isdigit():  # "<名字> missing"／"ambiguous"
            continue
        size = int(head[2])
        got[n] = buf[i:i + size]
        i += size + 1
    return got


def show_many_at(vault, rev, paths):
    """某個版本裡這些檔（相對知識庫夾）的內容：{路徑: bytes}，沒有的不列；一次 cat-file。"""
    pre = prefix(vault)
    if not rev or pre is None or not paths:
        return {}
    names = {f"{rev}:{pre}{p}": p for p in paths}
    return {names[k]: v for k, v in (cat_batch(vault, list(names)) or {}).items()}


def authors_between(vault, old, new):
    """old..new 每個檔最近一次是誰改的（相對知識庫夾）：{路徑: 人名}，一次 log。"""
    rc, out, _e = run(vault, "-c", "core.quotepath=false", "log", "--format=\x01%an", "--name-only", "--relative",
                      f"{old}..{new}", "--", ".")
    got, who = {}, None
    for ln in (out.splitlines() if rc == 0 else []):
        if ln.startswith("\x01"):
            who = ln[1:]
        elif ln.strip() and who:
            got.setdefault(ln, who)
    return got


def status_untracked_and_modified(vault):
    """git status --porcelain：回 [(狀態碼, 路徑)]（未追蹤 ?? 與修改）；不適用回 []。"""
    got = _ok(vault, "status", "--porcelain", "-z", "-uall")
    out = []
    for ent in (got or "").split("\0"):
        if len(ent) > 3:
            out.append((ent[:2], ent[3:]))
    return out


# ---------- 標籤認領 ----------

_NAME_OK = re.compile(r"^[A-Za-z0-9._/\-]+$")


def push_tag(vault, name, message):
    """認領：每台各自新建一個帶這台代號（message）的 commit，annotated tag 指向它，再推 refs/tags/<name>。
    回 "ok"｜"taken"（遠端已有同名）｜"offline"（連不上）｜"error"。推失敗就刪本機那個 tag。
    指到各自新 commit（時間與訊息不同）才會讓兩台同名標籤被遠端拒絕，不會因為都指同一個 HEAD 而兩邊都推成功。"""
    if not is_repo(vault) or not _NAME_OK.match(name):
        return "error"
    remote = _ok(vault, "remote")
    if not remote:
        return "offline"
    tree = _ok(vault, "rev-parse", "HEAD^{tree}")
    parent = head(vault)
    if not tree or not parent:
        return "error"
    ident = {"GIT_AUTHOR_NAME": message or "willy", "GIT_AUTHOR_EMAIL": "willy@local",
             "GIT_COMMITTER_NAME": message or "willy", "GIT_COMMITTER_EMAIL": "willy@local"}
    rc, out, _e = run(vault, "commit-tree", tree, "-p", parent, "-m", f"claim {name} by {message}", env=ident)
    if rc != 0:
        return "error"
    sha = out.strip()
    run(vault, "tag", "-d", name)  # 本機留著之前 fetch 來的同名標籤（tag_message 抓的）：真正有沒有人占著看遠端怎麼回
    rc, _o, _e = run(vault, "tag", "-a", name, sha, "-m", message or "willy", env=ident)
    if rc != 0:
        return "error"
    rc, _o, err = run(vault, "push", remote.split()[0], f"refs/tags/{name}")
    if rc == 0:
        return "ok"
    run(vault, "tag", "-d", name)
    low = err.lower()
    if "already exists" in low or "rejected" in low or "stale info" in low:
        return "taken"
    if any(s in low for s in ("could not resolve", "unable to access", "does not appear to be a git repository",
                              "could not read from remote", "connection", "timed out", "逾時", "no such")):
        return "offline"
    return "error"


def tags_local(vault, name):
    got = _ok(vault, "tag", "-l", name)
    return got.split() if got else []


def delete_remote_tag(vault, name):
    remote = _ok(vault, "remote")
    run(vault, "tag", "-d", name)
    if not remote:
        return False
    rc, _o, _e = run(vault, "push", remote.split()[0], f":refs/tags/{name}")
    return rc == 0


def list_remote_tags(vault, prefix):
    """遠端上以 prefix 開頭的標籤名；連不上回 None。"""
    remote = _ok(vault, "remote")
    if not remote:
        return None
    rc, out, _e = run(vault, "ls-remote", "--tags", remote.split()[0], f"refs/tags/{prefix}*")
    if rc != 0:
        return None
    names = []
    for line in out.splitlines():
        ref = line.split("\t")[-1].strip()
        if ref.startswith("refs/tags/") and not ref.endswith("^{}"):
            names.append(ref[len("refs/tags/"):])
    return names


def tag_message(vault, name):
    """標籤訊息（這台的人名）；本機沒有就先 fetch。"""
    remote = _ok(vault, "remote")
    if not tags_local(vault, name) and remote:
        run(vault, "fetch", remote.split()[0], "tag", name)
    got = _ok(vault, "tag", "-l", "--format=%(contents:subject)", name)
    return got or None


# ---------- 大檔 ----------

def _exclude_path(vault):
    d = _git_dir(vault)
    return d / "info" / "exclude" if d else None


def exclude_add(vault, path):
    p = _exclude_path(vault)
    if p is None:
        return False
    line = "/" + path.replace("\\", "/")
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    if line not in cur:
        cur.append(line)
        p.write_text("\n".join(cur) + "\n", encoding="utf-8")
    return True


def exclude_remove(vault, path):
    """不在 repo 裡或沒那行都是空操作。"""
    p = _exclude_path(vault) if available() and is_repo(vault) else None
    if p is None or not p.exists():
        return False
    line = "/" + path.replace("\\", "/")
    cur = p.read_text(encoding="utf-8").splitlines()
    if line not in cur:
        return False
    p.write_text("".join(x + "\n" for x in cur if x != line), encoding="utf-8")
    return True


def repo_size(vault):
    """git count-objects -vH 的 size-pack＋size 文字，例如 "12.3 MiB"；不適用回 None。"""
    got = _ok(vault, "count-objects", "-vH")
    if got is None:
        return None
    vals = dict(x.split(": ", 1) for x in got.splitlines() if ": " in x)
    return vals.get("size-pack") or vals.get("size")


def largest_tracked(vault, n=5):
    """追蹤中最大的 n 份：[(路徑, bytes)]。"""
    got = _ok(vault, "ls-files", "-z")
    root = Path(vault)
    sizes = []
    for rel in (got or "").split("\0"):
        if rel:
            try:
                sizes.append((rel, (root / rel).stat().st_size))
            except OSError:
                pass
    return sorted(sizes, key=lambda x: -x[1])[:n]


# ---------- 多人 ----------

def multi(vault):
    """多人用嗎（people.multi，多人共用 2）。"""
    from kb._lib import people
    return people.multi(vault)


def me(vault):
    """這台的人名（people.me，多人共用 2）；還沒記就用登入名。"""
    from kb._lib import people
    got = people.me(vault)
    if got:
        return got
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "willy"


def sync_active(vault):
    """多人而且是 git repo（認領、健檢分工、共用檔改寫用）。"""
    return multi(vault) and is_repo(vault)
