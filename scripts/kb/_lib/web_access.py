"""手機從外面連（#74 第 4 份，D-357）：Cloudflare Access 的簽章驗證、Cloudflare 權杖、加減能登入的信箱。只用標準函式庫。

驗證（web.py 對 Host 是 public_host 的請求叫 verify）：標頭 Cf-Access-Jwt-Assertion 是 RS256 的 JWT；公鑰從
https://<access_team>/cdn-cgi/access/certs 拿（urllib、5 秒逾時、記憶體快取 1 小時；簽章對不上時強制重抓一次再判，
重抓（含抓失敗）至少隔 REFETCH_MIN 秒，免得假簽章一直打 Cloudflare；過了 1 小時重抓失敗，STALE_MAX 內照用舊的；
抓的時候不鎖住別的請求）。RSA 用 pow(簽章, e, n) 自己算，比 PKCS#1 v1.5 的
SHA-256 填充整段（逐位元組，不只比雜湊尾巴）。再查 aud 含 access_aud、exp 沒過、iss 是 https://<access_team>。
不過丟 Denied（原因只給程式看，網頁一律白話「請重新登入」）。公鑰網址可用環境變數 WIKI_WITH_WILLY_ACCESS_CERTS_URL 換（只給測試）。

權杖：建置時 kb web token set 從 stdin 存進 Windows 認證管理員（unlock 的後端，TargetName
wiki-with-willy/<知識庫編號>/網頁/Cloudflare）；kb unlock 只認密碼與 PDF 密碼兩種，看不到它。值不進任何檔、不印出來。

名單：Cloudflare 只能整份覆蓋一個 policy：GET /accounts/{cf_account}/access/apps/{cf_app}/policies/{cf_policy}
→ 只改 include 裡的信箱規則（{"email": {"email": …}}，其他規則原樣）→ PUT 同一網址整份寫回。
API 底址 https://api.cloudflare.com/client/v4（環境變數 WIKI_WITH_WILLY_CF_API 可換，只給測試）。
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import threading
import time

CERTS_ENV = "WIKI_WITH_WILLY_ACCESS_CERTS_URL"
CF_API_ENV = "WIKI_WITH_WILLY_CF_API"
CF_API = "https://api.cloudflare.com/client/v4"
CERTS_TIMEOUT = 5
CERTS_TTL = 3600
REFETCH_MIN = 10  # 秒：簽章對不上的強制重抓、抓失敗後再試，至少隔這麼久
STALE_MAX = 24 * 3600  # 秒：過期了重抓失敗時，舊的公鑰最多再用這麼久（主機的網路閃一下不讓手機全部被擋）
CF_TIMEOUT = 15
CERTS_MAX = 256 * 1024
CF_MAX = 1024 * 1024
JWT_MAX = 16 * 1024
HEADER = "Cf-Access-Jwt-Assertion"
# PKCS#1 v1.5 的 SHA-256 DigestInfo（RFC 8017 9.2 註 1）
DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_B64URL = re.compile(r"^[A-Za-z0-9_-]*$")

TOKEN_NAME = "Cloudflare"
TOKEN_MAX = 500
EMAIL_MAX = 254
_EMAIL = re.compile(r"^[^@\s\"'<>(),;:\\\[\]]+@[^@\s\"'<>(),;:\\\[\]]+\.[^@\s\"'<>(),;:\\\[\]]+$")
NOT_SET_UP = "手機登入還沒設好（少了 {}），請服務人員照建置步驟設"
NO_TOKEN = "這台還沒存 Cloudflare 權杖，請服務人員照建置步驟跑 kb web token set"
CF_FAILED = "Cloudflare 沒有改成功：{}"
CF_READ_FAILED = "讀不到 Cloudflare 上的名單：{}"
BAD_EMAIL = "信箱格式不對：{}"
BAD_TOKEN = "權杖不能空白、最多 500 字、中間不能有空白"
LAST_ONE = "至少要留一個人，不然誰都進不來"
ADDED = "已讓 {} 可以從手機登入（下次在手機輸入這個信箱就會收到驗證碼）"
ALREADY_IN = "{} 本來就在名單上，可以從手機登入"
REMOVED = "已讓 {} 不能再從手機登入（那支手機已經登入的，要等登入到期才會被擋；要馬上擋請服務人員在 Cloudflare 撤銷登入）"
NOT_IN = "{} 本來就沒有在名單上"
READ_ONLY_FIELDS = ("id", "uid", "created_at", "updated_at", "app_count", "reusable")


class Denied(Exception):
    """Access 驗不過：訊息只給程式看（記 log 用），網頁一律回白話「請重新登入」。"""


class Refused(ValueError):
    """kb web token／access 的拒絕：訊息就是給人看的那句（不含權杖）。"""


# ---------- 公鑰 ----------

def certs_url(team):
    return os.environ.get(CERTS_ENV) or f"https://{team}/cdn-cgi/access/certs"


_cache = {}  # 網址 → {"at": 抓的時間, "keys": [(kid, n, e)]}
_lock = threading.Lock()


def clear_cache():
    with _lock:
        _cache.clear()


def b64url(text):
    if not isinstance(text, str) or not _B64URL.match(text):
        raise Denied("base64url 有不認得的字")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as e:
        raise Denied(f"base64url 解不開：{e}") from None


def _int(text):
    return int.from_bytes(b64url(text), "big")


def _fetch(url):
    """抓公鑰：回 [(kid, n, e)]；抓不到或格式不對丟 Denied。"""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=CERTS_TIMEOUT) as r:
            data = json.loads(r.read(CERTS_MAX).decode("utf-8"))
    except (OSError, ValueError) as e:
        raise Denied(f"公鑰拿不到：{type(e).__name__}") from None
    out = []
    for k in (data.get("keys") if isinstance(data, dict) else None) or []:
        if not isinstance(k, dict) or k.get("kty") != "RSA" or not isinstance(k.get("n"), str) or not isinstance(k.get("e"), str):
            continue
        try:
            out.append((k.get("kid"), _int(k["n"]), _int(k["e"])))
        except Denied:
            continue
    if not out:
        raise Denied("公鑰清單是空的")
    return out


def keys(url, force=False, now=None):
    """快取 1 小時的公鑰；force（簽章對不上）時重抓。上次抓（或試著抓）不到 REFETCH_MIN 秒就用快取的；
    重抓失敗時舊的還在 STALE_MAX 內照用，沒有舊的丟 Denied。抓的時候不拿鎖（5 秒逾時不擋住別的請求）。"""
    now = time.time() if now is None else now
    with _lock:
        got = _cache.get(url)
        if got is not None:
            age, tried = now - got["at"], now - got.get("tried", got["at"])
            if (age < CERTS_TTL and not force) or tried < REFETCH_MIN:
                return got["keys"]
            got["tried"] = now
    try:
        found = _fetch(url)
    except Denied:
        if got is not None and now - got["at"] < STALE_MAX:
            return got["keys"]
        raise
    with _lock:
        _cache[url] = {"at": now, "tried": now, "keys": found}
    return found


# ---------- RS256 ----------

def rs256_ok(n, e, signing_input, sig):
    """RSASSA-PKCS1-v1_5 + SHA-256：pow(簽章, e, n) 後整段跟 00 01 FF…FF 00 DigestInfo 雜湊 逐位元組比。"""
    k = (n.bit_length() + 7) // 8
    t = DIGEST_INFO + hashlib.sha256(signing_input).digest()
    if len(sig) != k or k < len(t) + 11:
        return False
    s = int.from_bytes(sig, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _signed_by(found, kid, signing_input, sig):
    pool = [x for x in found if kid is not None and x[0] == kid] or (found if kid is None else [])
    return any(rs256_ok(n, e, signing_input, sig) for _kid, n, e in pool)


def verify(token, team, aud, now=None):
    """驗 Access 的 JWT；過了回 claims（dict），不過丟 Denied。"""
    if not token or not team or not aud:
        raise Denied("沒有標頭或沒設 Access")
    if len(token) > JWT_MAX:
        raise Denied("JWT 太長")
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise Denied("JWT 不是三段")
    try:
        header = json.loads(b64url(parts[0]).decode("utf-8"))
        claims = json.loads(b64url(parts[1]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise Denied(f"JWT 解不開：{type(e).__name__}") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise Denied("JWT 形狀不對")
    if header.get("alg") != "RS256":
        raise Denied("alg 不是 RS256")
    kid = header.get("kid") if isinstance(header.get("kid"), str) else None
    sig = b64url(parts[2])
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    url = certs_url(team)
    if not _signed_by(keys(url, now=now), kid, signing_input, sig):
        if not _signed_by(keys(url, force=True, now=now), kid, signing_input, sig):  # 公鑰可能換了：重抓一次再判
            raise Denied("簽章對不上")
    auds = claims.get("aud")
    auds = [auds] if isinstance(auds, str) else auds if isinstance(auds, list) else []
    if aud not in auds:
        raise Denied("aud 不對")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp <= (time.time() if now is None else now):
        raise Denied("過期了")
    if claims.get("iss") != f"https://{team}":
        raise Denied("iss 不對")
    return claims


# ---------- 權杖（認證管理員） ----------

def _target(vault):
    from kb._lib import unlock
    return f"{unlock._base(vault)}{unlock.KIND_WEB}/{TOKEN_NAME}"


def _target_if_any(vault):
    """只讀時用：知識庫還沒有編號（從沒存過）回 None，不建 vault.json。"""
    from kb._lib import unlock
    from kb._lib import vault as vault_lib
    vid = vault_lib.vault_id(vault)
    return f"{unlock.PREFIX}/{vid}/{unlock.KIND_WEB}/{TOKEN_NAME}" if vid else None


def _unlock_refused(fn):
    """unlock 的 Refused（不是 Windows、讀寫失敗）轉成這裡的 Refused，訊息照原樣。"""
    from kb._lib import unlock
    try:
        return fn()
    except unlock.Refused as e:
        raise Refused(str(e)) from None


def save_token(vault, value):
    value = str(value or "").strip()
    if not value or len(value) > TOKEN_MAX or any(c.isspace() for c in value):
        raise Refused(BAD_TOKEN)
    from kb._lib import unlock
    _unlock_refused(lambda: unlock._backend().write(_target(vault), value, TOKEN_NAME))


def forget_token(vault):
    """刪掉；回刪了嗎（本來就沒有回 False）。"""
    from kb._lib import unlock
    return _unlock_refused(lambda: unlock._backend().delete(_target(vault)))


def has_token(vault):
    """有沒有存（只列名稱，不讀值、不寫任何檔）；這台存不了（不是 Windows）回 None。"""
    from kb._lib import unlock
    try:
        backend = unlock._backend()
    except unlock.Refused:
        return None
    target = _target_if_any(vault)
    if target is None:
        return False
    try:
        return target in backend.targets(target)
    except unlock.Refused:
        return None


def _token(vault):
    from kb._lib import unlock
    value = _unlock_refused(lambda: unlock._backend().read(_target(vault)))
    if not value:
        raise Refused(NO_TOKEN)
    return value


# ---------- Cloudflare 名單 ----------

def check_email(text):
    email = str(text or "").strip()
    if not email or len(email) > EMAIL_MAX or not _EMAIL.match(email):
        raise Refused(BAD_EMAIL.format(email or "（空的）"))
    return email


def _policy_url(cfg, reusable=False):
    """應用底下那份 policy 的網址；reusable（Cloudflare 新版的共用 policy，應用那條路改不了）用帳號層級的
    /accounts/{a}/access/policies/{p}。"""
    missing = [name for key, name in (("cf_account", "帳號編號"), ("cf_app", "應用編號"), ("cf_policy", "policy 編號"))
               if not cfg.get(key)]
    if missing:
        raise Refused(NOT_SET_UP.format("、".join(missing)))
    base = (os.environ.get(CF_API_ENV) or CF_API).rstrip("/")
    if reusable:
        return f"{base}/accounts/{cfg['cf_account']}/access/policies/{cfg['cf_policy']}"
    return f"{base}/accounts/{cfg['cf_account']}/access/apps/{cfg['cf_app']}/policies/{cfg['cf_policy']}"


def _cf_message(data, fallback):
    errors = data.get("errors") if isinstance(data, dict) else None
    texts = [str(e.get("message")) for e in errors or [] if isinstance(e, dict) and e.get("message")]
    return "；".join(texts)[:300] or fallback


def _call(vault, cfg, method, body, failed, reusable=False):
    """叫 Cloudflare；回 result（dict）。HTTP 錯誤、success 不是 true、連不上 → Refused(failed.format(它的訊息))。"""
    import urllib.error
    import urllib.request
    url = _policy_url(cfg, reusable)
    headers = {"Authorization": f"Bearer {_token(vault)}", "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=CF_TIMEOUT) as r:
            raw = r.read(CF_MAX)
    except urllib.error.HTTPError as e:
        try:
            got = json.loads(e.read(CF_MAX).decode("utf-8"))
        except (OSError, ValueError):
            got = None
        raise Refused(failed.format(_cf_message(got, f"HTTP {e.code}"))) from None
    except (OSError, ValueError) as e:
        raise Refused(failed.format(f"連不上（{type(e).__name__}）")) from None
    try:
        got = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise Refused(failed.format("回的看不懂")) from None
    if not isinstance(got, dict) or got.get("success") is not True or not isinstance(got.get("result"), dict):
        raise Refused(failed.format(_cf_message(got, "回的不是成功")))
    return got["result"]


def _email_of(rule):
    inner = rule.get("email") if isinstance(rule, dict) else None
    value = inner.get("email") if isinstance(inner, dict) else None
    return value if isinstance(value, str) else None


def _policy(vault, cfg):
    policy = _call(vault, cfg, "GET", None, CF_READ_FAILED)
    include = policy.get("include")
    if not isinstance(include, list):
        raise Refused(CF_READ_FAILED.format("名單的格式看不懂"))
    return policy


def emails(policy):
    return [e for e in (_email_of(r) for r in policy.get("include") or []) if e]


def access_list(vault, cfg):
    """回 {"emails": [...], "other_rules": N}（include 裡信箱以外的規則數）。"""
    policy = _policy(vault, cfg)
    found = emails(policy)
    return {"emails": found, "other_rules": len(policy["include"]) - len(found)}


def access_change(vault, cfg, email, add):
    """加或減一個信箱：GET 整份 → 改 include → PUT 整份。回 (改了嗎, 給人看的那句)。"""
    email = check_email(email)
    policy = _policy(vault, cfg)
    include = policy["include"]
    same = [r for r in include if (_email_of(r) or "").casefold() == email.casefold()]
    if add:
        if same:
            return False, ALREADY_IN.format(email)
        new_include = include + [{"email": {"email": email}}]
    else:
        if not same:
            return False, NOT_IN.format(email)
        new_include = [r for r in include if r not in same]
        if not any(_email_of(r) for r in new_include):
            raise Refused(LAST_ONE)
    body = {k: v for k, v in policy.items() if k not in READ_ONLY_FIELDS}
    body["include"] = new_include
    _call(vault, cfg, "PUT", body, CF_FAILED, reusable=policy.get("reusable") is True)
    return True, (ADDED if add else REMOVED).format(email)
