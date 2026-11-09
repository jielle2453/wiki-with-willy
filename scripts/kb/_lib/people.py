"""這台是誰、幾個人用、誰的（多人共用第 2 份，D-286、D-287、D-289）。

幾個人用記在設定檔的 people（整數，預設 1）；2 人以上算多人。這台的人名記在 env.json 這台那一筆的 person（不進 git）。
單人（或沒有設定檔）一律當單人：me 回 None、visible 全看得到。只讀這些，不寫（寫由 kb who、kb profile 做）。
"""
import getpass
import subprocess

TEAM = "team"
TEAM_WORDS = ("全隊", "全員", "大家", "team")


def count(vault):
    """幾個人用；沒設定檔、沒這個欄、不是正整數都當 1。"""
    try:
        from kb._lib import profile

        n = (profile.load(vault) or {}).get("people", 1)
    except Exception:  # noqa: BLE001  設定檔讀不到當單人，別拖垮呼叫端
        return 1
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else 1


def multi(vault):
    return count(vault) >= 2


def me(vault):
    """多人時回這台的名字（還沒設回 None）；單人回 None。"""
    if not multi(vault):
        return None
    from kb._lib import env

    return env.person(vault)


def default_name(vault=None):
    """問名字時的預設：git config user.name，沒有 git 或沒設就拿登入的使用者名稱（新定 3）。"""
    try:
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, encoding="utf-8", timeout=3)
        name = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        name = ""
    if name:
        return name
    try:
        return getpass.getuser() or None
    except Exception:  # noqa: BLE001
        return None


def members(vault):
    """團隊成員：第五節「本人」那幾列的名稱。"""
    try:
        from kb._lib import profile

        return [a["name"] for a in (profile.load(vault) or {}).get("about") or [] if a.get("kind") == "person"]
    except Exception:  # noqa: BLE001
        return []


def parse_whose(text):
    """--whose 的字 → "team" 或名字清單。空字串、全隊類字眼都是 team。"""
    names = [x.strip() for x in str(text or "").replace("、", ",").replace("，", ",").split(",") if x.strip()]
    if not names or any(n in TEAM_WORDS for n in names):
        return TEAM
    return names


def visible(whose, mine):
    """whose 是 "team"、名字清單或 None；mine 是這台的人（單人是 None）。單人、全隊、或我在清單裡都看得到。"""
    if mine is None or whose in (None, TEAM, [], ""):
        return True
    if isinstance(whose, str):
        return whose == mine
    return mine in whose


def whose_label(whose):
    return "全隊" if whose in (None, TEAM, [], "") else (whose if isinstance(whose, str) else "、".join(whose))
