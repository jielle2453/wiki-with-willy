"""成品進知識庫的唯一一條路（produce 第 2 點，D-164、D-168、D-170）：kb output save／link／drop-draft 的本體。

save：目的地要在 AI 產出底下、不是 .md（md 用檔案工具寫，hook 會備份登記）、檔名不像密碼或金鑰；同名加「 (2)」。
來源（實作回報裁定 2）：工作區（.system/temp/）的檔用搬的；知識庫外的檔（下載夾、Docs 匯出）用複製的、不動原處；
知識庫裡工作區以外的檔一律拒絕。turn.backup（create）→ 搬或複製 → 清冊登記（register_output，--from 記 derived_from；
--link 掛到那筆 link 條目）→ --draft 把草稿備進回合（delete）後拿掉、清冊那筆改 deleted。清冊不另備份：退回照 manifest
的 create 把成品改 turn_reverted，草稿照 delete 還原、對帳回 active（versions 既有的帳就夠）。
link：只收 https://claude.ai/ 的網址，清冊記一筆 kind link（不算指紋、掃描不走），實體要登記過、依據要是寫好卡的。
drop-draft：草稿要在 AI 產出底下、檔名是（草稿）.md。拒絕一律丟 Refused，訊息就是給人看的那句（一字不差）。
"""
import os
import re
import shutil
from pathlib import Path

from kb._lib import cards, credentials, inventory, turn

OUTPUT = inventory.OUTPUT
ONLY_OUTPUT = "只能放進 AI 產出底下"
NO_MD = "md 檔用檔案工具寫就好"
NOT_FOUND = "找不到這個檔：{path}"
CREDENTIAL = "這個檔名像密碼或金鑰，不放"
DRAFT_OUTSIDE = "草稿不在 AI 產出：{path}"
BAD_URL = "網址要是 claude.ai 的 artifact"
UNKNOWN_LINK = "這個網址還沒登記；先跑 kb output link"
IN_VAULT = "只收工作區或知識庫外的檔，這份在知識庫裡"
NO_TITLE = "要給標題（--title）"
BAD_TYPE = "型別要是一個資料夾名，不含 \\ / : * ? \" < > |：{name}"
NO_ENTITY = "實體清單沒有「{name}」，先 kb entity add"
NO_SOURCE = "找不到這份原檔：{target}"
_URL = re.compile(r"^https://claude\.ai/[^\s\"'`$\\]+$")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f$`]')


class Refused(Exception):
    """拒絕：訊息就是給人看的那句。"""


def _real(path):
    return os.path.normcase(os.path.realpath(str(path)))


def _inside(path, root):
    p, r = _real(path), _real(root)
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _abs(path, base):
    p = Path(str(path))
    if not p.is_absolute():
        p = Path(base) / p
    return Path(os.path.abspath(p))


def output_rel(vault, raw, cwd):
    """目的地 → 總資料夾相對路徑（AI 產出/…，正斜線）。寫法認 vault 相對（AI 產出/…）、cwd 相對與絕對；要落在 AI 產出底下的檔。"""
    text = str(raw).replace("\\", "/")
    first = text.split("/", 1)[0]
    base = vault if first.casefold() == OUTPUT.casefold() else cwd
    target = _abs(raw, base)
    root = Path(vault) / OUTPUT
    if not _inside(target, root) or _real(target) == _real(root):
        raise Refused(ONLY_OUTPUT)
    rel = os.path.relpath(os.path.realpath(str(target)), os.path.realpath(str(vault))).replace("\\", "/")
    head, _, rest = rel.partition("/")
    if head.casefold() != OUTPUT.casefold() or not rest:
        raise Refused(ONLY_OUTPUT)
    return f"{OUTPUT}/{rest}"


def _draft_rel(vault, raw, cwd):
    """草稿：AI 產出底下、存在、檔名是（草稿）.md；不是就丟 DRAFT_OUTSIDE（帶客戶給的寫法）。"""
    try:
        rel = output_rel(vault, raw, cwd)
    except Refused:
        raise Refused(DRAFT_OUTSIDE.format(path=raw)) from None
    if not rel.endswith(cards.DRAFT_SUFFIX) or not (Path(vault) / rel).is_file():
        raise Refused(DRAFT_OUTSIDE.format(path=raw))
    return rel


def free_rel(vault, rel):
    """同名加「 (2)」「 (3)」…"""
    p = Path(vault) / rel
    if not p.exists():
        return rel
    stem, suffix = Path(rel).stem, Path(rel).suffix
    parent = rel.rsplit("/", 1)[0]
    n = 2
    while (Path(vault) / parent / f"{stem} ({n}){suffix}").exists():
        n += 1
    return f"{parent}/{stem} ({n}){suffix}"


def check_url(url):
    if not _URL.match(str(url or "")):
        raise Refused(BAD_URL)


def source_mode(vault, src):
    """來源在哪：工作區回 move、知識庫外回 copy、知識庫裡其他地方拒絕。"""
    if _inside(src, Path(vault) / ".system" / "temp") and _real(src) != _real(Path(vault) / ".system" / "temp"):
        return "move"
    if _inside(src, vault):
        raise Refused(IN_VAULT)
    return "copy"


def _link_entry(vault, url):
    key = inventory.uri_key(url)
    for e in inventory.load(vault)["entries"]:
        if e.get("status") == "active" and inventory.is_link(e) and any(
                inventory.uri_key(loc.get("uri", "")) == key for loc in e.get("locations") or []):
            return e
    raise Refused(UNKNOWN_LINK)


def _source_entry(vault, target, cwd):
    """--from 的原檔 → 清冊條目（卡編號、uri、路徑都認，同 kb convert）。"""
    from kb import convert
    from kb._lib import sources as sources_lib
    try:
        entry, _path, _uri = convert.resolve(vault, sources_lib.load(vault), target, cwd)
    except convert.Refused:
        entry = None
    if entry is None:
        raise Refused(NO_SOURCE.format(target=target))
    return entry


def label_of(vault, entry):
    """給人看的原檔名：卡標題，沒有卡用檔名主檔名。"""
    from kb._lib import agenda
    title = agenda.card_titles(vault).get(entry["id"])
    if title:
        return title
    uri = (entry.get("locations") or [{}])[0].get("uri", "")
    return Path(uri.rsplit("/", 1)[-1]).stem


def _drop(vault, rel, turn_id):
    """草稿備進回合（delete）後拿掉，清冊那筆改 deleted（退回時 versions 對帳會改回 active）。"""
    turn.backup(vault, rel, turn_id, action="delete")
    os.remove(Path(vault) / rel)
    key = inventory.uri_key("file://" + rel)
    ids = [e["id"] for e in inventory.load(vault)["entries"] if e.get("status") == "active"
           and any(inventory.uri_key(loc.get("uri", "")) == key for loc in e.get("locations") or [])]
    if ids:
        inventory.set_status(vault, ids, "deleted")


def save(vault, src, to, turn_id, cwd, from_=None, draft=None, link=None):
    """回 {"path", "id", "mode", "from", "from_label", "draft", "link"}。驗完才動檔。"""
    rel = output_rel(vault, to, cwd)
    if Path(rel).suffix.lower() == ".md":
        raise Refused(NO_MD)
    if credentials.match_segment(rel.split("/")[1:]) is not None:
        raise Refused(CREDENTIAL)
    src = _abs(src, cwd)
    if not src.is_file():
        raise Refused(NOT_FOUND.format(path=src))
    mode = source_mode(vault, src)
    draft_rel = _draft_rel(vault, draft, cwd) if draft else None
    link_entry = None
    if link:
        check_url(link)
        link_entry = _link_entry(vault, link)
    source = _source_entry(vault, from_, cwd) if from_ else None
    rel = free_rel(vault, rel)
    target = Path(vault) / rel
    turn.backup(vault, rel, turn_id, action="create")
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "move":
        shutil.move(str(src), str(target))
    else:
        shutil.copy2(str(src), str(target))
    if link_entry is not None:
        entry = inventory.attach_location(vault, link_entry["id"], rel, turn_id)
    else:
        entry = inventory.register_output(vault, rel, turn_id, derived_from=source["id"] if source else None)
    if draft_rel:
        _drop(vault, draft_rel, turn_id)
    return {"path": rel, "id": entry["id"] if entry else None, "mode": mode,
            "from": source["id"] if source else None, "from_label": label_of(vault, source) if source else None,
            "draft": draft_rel, "link": link}


def link(vault, url, title, turn_id, type_=None, names=(), basis=()):
    """回清冊那筆 link 條目。"""
    from kb._lib import entities
    check_url(url)
    title = str(title or "").strip()
    if not title:
        raise Refused(NO_TITLE)
    if type_ is not None and (not type_.strip() or _UNSAFE.search(type_) or type_.strip(" .") != type_):
        raise Refused(BAD_TYPE.format(name=type_))
    ent = entities.load(vault)
    found_names = []
    for name in names:
        e, _hit = entities.find(ent, name.strip())
        if e is None or e.get("status") != "active":
            raise Refused(NO_ENTITY.format(name=name.strip()))
        found_names.append(e["name"])
    for fid in basis:
        try:
            cards.card_of(vault, fid.strip())
        except cards.CardRefused as e:
            raise Refused(str(e)) from None
    return inventory.register_link(vault, url, turn_id, title=title, type_=type_,
                                   entities=found_names, basis=[b.strip() for b in basis])


def drop_draft(vault, raw, turn_id, cwd):
    rel = _draft_rel(vault, raw, cwd)
    _drop(vault, rel, turn_id)
    return rel
