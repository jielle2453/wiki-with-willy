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
    got = _ok(vault, "rev-parse", "--absolute-git-dir")
    return Path(got) if got else None


def _head_from_files(d):
    """讀 .git/HEAD → 分支檔或 packed-refs 的 sha；讀不懂回 None（呼叫端改叫 git）。"""
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
    return got or _ok(vault, "rev-parse", "HEAD")


def changed_since(vault, old, new):
    """git diff --name-only old new；old 空或不認得就回 None（呼叫端當全部都要比）。"""
    if not old or not new:
        return None
    got = _ok(vault, "diff", "--name-only", "-z", old, new)
    if got is None:
        return None
    return [x for x in got.split("\0") if x]


def changed_by_others(vault, old, new):
    """old..new 之間，不是這台的人（git config user.name）寫的 commit 動到的檔（pull 進來的）；自己 commit 的不算。
    old 空或不認得回 None。"""
    if not old or not new:
        return None
    mine = _ok(vault, "config", "user.name")
    rc, out, _e = run(vault, "-c", "core.quotepath=false", "log", "--no-merges", "--format=\x01%an", "--name-only", f"{old}..{new}")
    if rc != 0:
        return None
    got, author = [], None
    for ln in out.splitlines():
        if ln.startswith("\x01"):
            author = ln[1:]
        elif ln.strip() and author != mine and ln not in got:
            got.append(ln)
    return got


def unmerged(vault):
    got = _ok(vault, "diff", "--name-only", "--diff-filter=U", "-z")
    return [x for x in (got or "").split("\0") if x]


def merge_in_progress(vault):
    d = _git_dir(vault)
    return bool(d and (d / "MERGE_HEAD").exists())


def rebase_in_progress(vault):
    d = _git_dir(vault)
    return bool(d and ((d / "rebase-merge").is_dir() or (d / "rebase-apply").is_dir()))


def show_stage(vault, path, stage):
    """衝突檔的第 2（自己）或第 3（對方）版內容；沒有回 None。"""
    rc, out, _e = run(vault, "show", f":{int(stage)}:{path}")
    return out if rc == 0 else None


def show_stage_bytes(vault, path, stage):
    if not available():
        return None
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = subprocess.run(["git", "-C", str(vault), "show", f":{int(stage)}:{path}"], capture_output=True, env=env,
                       stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout if r.returncode == 0 else None


def checkout_theirs(vault, path):
    rc, _o, _e = run(vault, "checkout", "--theirs", "--", path)
    return rc == 0


def untrack(vault, paths):
    """不再追蹤（git rm --cached，工作區的檔留著）；沒追蹤的跳過。回有沒有成功。"""
    if not paths:
        return True
    rc, _o, _e = run(vault, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", *paths)
    return rc == 0


def add(vault, paths):
    if not paths:
        return True
    rc, _o, _e = run(vault, "add", "--", *paths)
    return rc == 0


def commit_merge(vault):
    rc, _o, _e = run(vault, "commit", "--no-edit")
    return rc == 0


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
