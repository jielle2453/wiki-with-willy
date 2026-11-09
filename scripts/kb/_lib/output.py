"""成品進知識庫的唯一一條路（produce 第 2 點，D-164、D-168、D-170）：kb output save／link／drop-draft 的本體。

save：目的地不給就落暫存區（D-324），給了收任何准許的位置；不是 .md（md 用檔案工具寫，hook 會備份登記）、檔名不像密碼或金鑰；同名加「 (2)」。
來源（實作回報裁定 2）：工作區（.system/temp/）的檔用搬的；知識庫外的檔（下載夾、Docs 匯出）用複製的、不動原處；
知識庫裡工作區以外的檔一律拒絕；知識庫外的來源照守門同一套判斷（收尾 security-review）：排除的、檔名像密碼或金鑰的拒絕，
登記的來源裡的客戶原檔要先 kb office copy。--to 的檔名不收冒號（NTFS 資料流會繞過 .md 與同名檢查）。turn.backup（create）→ 搬或複製 → 清冊登記（register_output，--from 記 derived_from；
--link 掛到那筆 link 條目）→ --draft 把草稿備進回合（delete）後拿掉、清冊那筆改 deleted。清冊不另備份：退回照 manifest
的 create 把成品改 turn_reverted，草稿照 delete 還原、對帳回 active（versions 既有的帳就夠）。
link：只收 https://claude.ai/ 的網址，清冊記一筆 kind link（不算指紋、掃描不走），實體要登記過、依據要是寫好卡的；
寫清冊前先把 inventory.json 備進回合（退回這一句就拿掉這筆）；同網址已登記拒絕、不覆蓋；標題不收 $ 與反引號。
drop-draft：草稿要在暫存區底下、檔名是（草稿）.md。拒絕一律丟 Refused，訊息就是給人看的那句（一字不差）。
"""
import os
import re
import shutil
import zipfile
from pathlib import Path

from kb._lib import cards, credentials, inventory, turn

OUTPUT = inventory.HOLD  # 暫存區（D-324：原 AI 產出與收件匣合成一個）
ONLY_OUTPUT = "只能放進暫存區底下"  # output_rel（草稿判斷）還在用
NEEDS_FILE_NAME = "--to 要寫成 <夾>/<檔名>"
LINK_IN_OUTPUT = "--link 的成品要放在暫存區底下"
NO_MD = "md 檔用檔案工具寫就好"
NOT_FOUND = "找不到這個檔：{path}"
CREDENTIAL = "這個檔名像密碼或金鑰，不放"
DRAFT_OUTSIDE = "草稿不在暫存區：{path}"
BAD_URL = "網址要是 claude.ai 的 artifact"
UNKNOWN_LINK = "這個網址還沒登記；先跑 kb output link"
IN_VAULT = "只收工作區或知識庫外的檔，這份在知識庫裡"
NO_TITLE = "要給標題（--title）"
BAD_TYPE = "型別要是一個資料夾名，不含 \\ / : * ? \" < > |：{name}"
NO_ENTITY = "實體清單沒有「{name}」，先 kb entity add"
NO_SOURCE = "找不到這份原檔：{target}"
EXCLUDED = "這個位置你說過不讀"
ORIGINAL = "客戶原檔先用 kb office copy 拿副本"
COLON = "檔名不能有冒號"
LINKED_BEFORE = "這個網址已經登記過了：{title}"
TITLE_CHARS = "標題不能有 $ 或 ` 這兩種字元"
LOCKED = "檔案正開著，改視窗裡那份"  # D-529：蓋不回去，原檔不動、不留版本檔；AI 改走知識庫 CLAUDE.md 的附著改
NOT_EDIT_FOLDER ="這個夾不是 kb office copy --unpack 解開的，不知道原檔是哪份；用 copy --unpack 重拿一次"
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
    """目的地 → 總資料夾相對路徑（暫存區/…，正斜線）。寫法認 vault 相對（暫存區/…）、cwd 相對與絕對；要落在暫存區底下的檔。"""
    text = str(raw).replace("\\", "/")
    first = text.split("/", 1)[0]
    base = vault if first.casefold() == OUTPUT.casefold() else cwd
    target = _abs(raw, base)
    from kb._lib import sources as sources_lib
    hold = sources_lib.hold_root(vault)
    if hold and _inside(target, Path(hold)) and _real(target) != _real(Path(hold)):  # 登記成團隊雲端的暫存區：清冊的 uri 就是 file://暫存區/…
        rest = os.path.relpath(os.path.realpath(str(target)), os.path.realpath(str(hold))).replace("\\", "/")
        return f"{OUTPUT}/{rest}"
    root = Path(vault) / OUTPUT
    if not _inside(target, root) or _real(target) == _real(root):
        raise Refused(ONLY_OUTPUT)
    rel = os.path.relpath(os.path.realpath(str(target)), os.path.realpath(str(vault))).replace("\\", "/")
    head, _, rest = rel.partition("/")
    if head.casefold() != OUTPUT.casefold() or not rest:
        raise Refused(ONLY_OUTPUT)
    return f"{OUTPUT}/{rest}"


def _draft_rel(vault, raw, cwd):
    """草稿：暫存區底下、存在、檔名是（草稿）.md；不是就丟 DRAFT_OUTSIDE（帶客戶給的寫法）。"""
    try:
        rel = output_rel(vault, raw, cwd)
    except Refused:
        raise Refused(DRAFT_OUTSIDE.format(path=raw)) from None
    if not rel.endswith(cards.DRAFT_SUFFIX) or not (Path(vault) / rel).is_file():
        raise Refused(DRAFT_OUTSIDE.format(path=raw))
    return rel


class Conflict(Refused):
    """同名：帶 conflict 列（name、dest、same_content、existing），--json 與 results 用（放寬甲）。"""

    def __init__(self, text, rows):
        super().__init__(text)
        self.rows = rows


def conflict(place, same, rename="加 --name <新名>"):
    """目的地已有同名檔：回 Conflict（句子與列同 source-edit 的格式；rename 是「要另取名怎麼做」那一小段）。"""
    from kb._lib import source_edit as se
    row = {"name": place["name"], "dest": place["dest_rel"], "same_content": bool(same), "existing": place["uri_shown"]}
    text = se.CONFLICT.format(dest=place["dest_rel"].rpartition("/")[2] or place["dest_rel"], name=place["name"],
                              same="一樣" if same else "不一樣").replace("加 --name <新名>", rename)
    return Conflict(text, [row])


def resolve_dest(vault, sources, raw, cwd):
    """--to 給的夾（路徑、uri、別名/相對路徑、知識庫相對、cwd 相對）→ 絕對路徑，可以還不存在（存檔時才建）。"""
    from kb._lib import source_edit as se
    text = str(raw).strip()
    if text.startswith("file://"):
        from kb._lib import inventory as inv
        got = inv.path_of(vault, sources, text)
        if got is not None:
            return Path(got)
    if os.path.isabs(text):
        return Path(os.path.abspath(text))
    alias = se._alias_form(vault, sources, text)
    if alias is not None:
        return Path(alias)
    first = text.replace("\\", "/").split("/", 1)[0]
    if first and (Path(vault) / first).exists() and not (Path(cwd) / first).exists():
        return Path(os.path.abspath(Path(vault) / text))
    return Path(os.path.abspath(Path(cwd) / text))


def place_at(vault, sources, dest_dir, name):
    """放到 dest_dir/name 的位置資訊：kind（vault｜source｜outside）、target、rel（知識庫相對，source 與 outside 是 None）、
    uri（清冊的位置，暫存區照舊給 None 讓清冊自己組）、bkey（回合備份的鍵）、dest_rel（conflict 的 dest）、uri_shown。
    不收 willy 的區與不讀的夾（source_edit.check_to 同一套）；檔名像密碼的在登記的來源裡拒絕。"""
    from kb._lib import source_edit as se
    se_dest = Path(dest_dir)
    try:
        se.check_to(vault, sources, se_dest, str(dest_dir), make=True)
    except se.Refused as e:
        raise Refused(str(e)) from None
    target = se_dest / name
    parts = se._parts(vault, target)
    uri = se.uri_of(vault, sources, target)
    dest_uri = se.uri_of(vault, sources, se_dest)
    if parts is not None:
        rel = "/".join(parts)
        legacy = parts[0].casefold() == OUTPUT.casefold()
        kind, bkey = "vault", rel
    elif uri:
        if se._credential(sources, target):
            raise Refused(CREDENTIAL)
        alias, urel = inventory.split_uri(uri)
        rel, legacy, kind, bkey = None, False, "source", f"_src/{alias}/{urel}"
    else:
        rel, legacy, kind, bkey = None, False, "outside", None
    return {"kind": kind, "target": target, "rel": rel, "uri": None if legacy else uri, "bkey": bkey, "name": name,
            "dest_rel": se._dest_rel(dest_uri), "uri_shown": uri, "legacy": legacy}


def backup_before(vault, sources, place, turn_id, action, link_from=None):
    """寫之前把位置備進回合：知識庫裡 turn.backup；登記的來源 turn.backup_source；沒登記的地方沒有備份。
    link_from：版本檔（D-529），回合備份硬連結到它。"""
    if place["kind"] == "vault":
        turn.backup(vault, place["rel"], turn_id, action=action, link_from=link_from)
    elif place["kind"] == "source":
        turn.backup_source(vault, place["target"], None, turn_id, sources, link_from=link_from)


_CN = "零一二三四五六七八九"


def _cn(n):
    """1～99 寫成中文數字（版本一、十二、二十）；更大的照阿拉伯數字。"""
    if n < 10:
        return _CN[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return ("" if tens == 1 else _CN[tens]) + "十" + (_CN[ones] if ones else "")
    return str(n)


def version_name(target):
    """原檔同夾的下一個「<主檔名>（版本 N）.<副檔名>」：現有最大號加一（她刪了中間的也不回頭補）。"""
    target = Path(target)
    taken = {p.name.casefold() for p in target.parent.iterdir()}
    name = lambda n: f"{target.stem}（版本{_cn(n)}）{target.suffix}"  # noqa: E731
    return name(max((n for n in range(1, 1000) if name(n).casefold() in taken), default=0) + 1)


def _keep_version(vault, sources, place, turn_id):
    """蓋回原檔前（D-529）：現在的原檔複製成版本檔（回合記 create），原檔的回合備份硬連結到它（不多存一份）。回版本檔的位置。"""
    vplace = place_at(vault, sources, place["target"].parent, version_name(place["target"]))
    backup_before(vault, sources, vplace, turn_id, "create")
    shutil.copy2(str(place["target"]), str(vplace["target"]))
    backup_before(vault, sources, place, turn_id, "modify", link_from=vplace["target"])
    return vplace


def _register_version(vault, vplace, turn_id):
    """版本檔記進清冊、標不整理（skip_kind，D-113 現有欄位），整理不做卡、lookup 不當成現行那份。"""
    from kb._lib import cards
    if vplace["kind"] == "outside":
        return  # 沒登記的地方清冊不管，版本檔照留
    entry = inventory.register_output(vault, vplace["rel"] or vplace["bkey"], turn_id, uri=vplace["uri"],
                                      target=vplace["target"] if vplace["kind"] == "source" else None,
                                      backup_key=vplace["bkey"])
    if entry:
        inventory.set_skip(vault, [entry["id"]], cards.SKIP_AI)


def shown_path(place):
    """給人看與記日誌的位置：知識庫裡是相對路徑，來源是 別名/相對路徑，沒登記的是絕對路徑。"""
    if place["kind"] == "vault":
        return place["rel"]
    if place["kind"] == "source":
        return place["uri_shown"][len("file://"):]
    return str(place["target"])


def check_url(url):
    if not _URL.match(str(url or "")):
        raise Refused(BAD_URL)


def source_mode(vault, src):
    """來源在哪：工作區回 move、知識庫外回 copy、知識庫裡其他地方拒絕。知識庫外的照守門判斷：排除的（Z10、Z10b）、
    檔名像密碼或金鑰的（守門第一道）拒絕，登記的來源（Z9）要先 kb office copy。"""
    office_root = Path(vault) / ".system" / "temp" / "office"  # 工作區只算 office/（測試後修正裁定 20 L-3）
    if _inside(src, office_root) and _real(src) != _real(office_root):
        return "move"
    if _inside(src, vault):
        raise Refused(IN_VAULT)
    from kb import guard
    from kb._lib import sources as sources_lib
    from kb._lib.paths import normalize
    sources = sources_lib.load(vault)
    path_n, vault_n = normalize(os.path.realpath(str(src)), None), normalize(str(vault), None)
    zone, _alias = guard.classify(path_n, vault_n, sources)
    if zone in ("Z10", "Z10b"):
        raise Refused(EXCLUDED)
    if guard.credential_hit(path_n, zone, vault_n, sources) is not None:
        raise Refused(CREDENTIAL)
    if zone == "Z9":
        raise Refused(ORIGINAL)
    return "copy"


def _find_link(vault, url):
    key = inventory.uri_key(url)
    for e in inventory.load(vault)["entries"]:
        if e.get("status") == "active" and inventory.is_link(e) and any(
                inventory.uri_key(loc.get("uri", "")) == key for loc in e.get("locations") or []):
            return e
    return None


def _link_entry(vault, url):
    e = _find_link(vault, url)
    if e is None:
        raise Refused(UNKNOWN_LINK)
    return e


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


def save(vault, src, to, turn_id, cwd, from_=None, draft=None, link=None, replace=False):
    """回 {"path", "id", "mode", "from", "from_label", "draft", "link", "in_vault"}。驗完才動檔。
    --to 是 <夾>/<檔名>：收任何准許的位置（放寬甲），放在暫存區以外照樣登記、出處類別 AI 產出；同名回 Conflict，--replace 舊的存成同夾（版本 N）再覆蓋（D-529、#47）；xlsx 有公式先重算、驗。"""
    from kb._lib import sources as sources_lib
    sources = sources_lib.load(vault)
    checked = None
    if _abs(src, cwd).is_dir():  # copy --unpack 解開的夾：程式照副本打包、驗了才蓋回原檔（簡化一 C，D-529）
        src, to, from_, checked = _pack_folder(vault, _abs(src, cwd), to, from_)
        replace = replace or checked["overwrite"]
    if not to:  # 沒給 --to：落暫存區（登記成團隊雲端的夾就落那裡，多人共用 2），檔名照原檔（D-324）
        parent_text, name = str(sources_lib.hold_root(vault) or Path(vault) / OUTPUT), Path(str(src)).name
    else:
        text = str(to).replace("\\", "/")
        parent_text, _sep, name = text.rpartition("/")
    if not name or not parent_text:
        raise Refused(NEEDS_FILE_NAME)
    if ":" in name or _UNSAFE.search(name):
        raise Refused(COLON)
    if Path(name).suffix.lower() == ".md":
        raise Refused(NO_MD)
    place = place_at(vault, sources, resolve_dest(vault, sources, parent_text, cwd), name)
    if place["rel"] and credentials.match_segment(place["rel"].split("/")[1:]) is not None:
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
        if place["kind"] != "vault" or place["rel"].split("/")[0].casefold() != OUTPUT.casefold():
            raise Refused(LINK_IN_OUTPUT)
    source = _source_entry(vault, from_, cwd) if from_ else None
    target = place["target"]
    existing = os.path.lexists(target)
    if existing and not replace:
        raise conflict(place, target.is_file() and inventory.sha256_of(target) == inventory.sha256_of(src),
                       rename="改 --to 的檔名")
    if checked is None:  # 單一檔的 Excel：一樣重算＋驗（#47）；放在會拒絕的檢查之後、第一次寫入之前
        src, mode, checked = _excel_checked(vault, src, mode, turn_id)
    overwrite = existing and target.is_file()  # 蓋過既有的檔：存夾、存檔 --replace 都同夾留版本檔（D-529、#47）
    vplace = None
    if overwrite:
        vtarget = target.with_name(version_name(target))
        try:
            vplace = _keep_version(vault, sources, place, turn_id)
        except OSError:  # 夾唯讀、磁碟滿：不留半份版本檔，原檔還沒動
            try:
                os.unlink(str(vtarget))
            except OSError:
                pass
            raise
    else:
        backup_before(vault, sources, place, turn_id, "modify" if existing else "create")
    target.parent.mkdir(parents=True, exist_ok=True)
    if existing:  # 蓋過去：先寫同夾的暫存檔再整份換上，開著或沒權限時原檔不動、不留半份（D-529）
        tmp = target.with_name(f".~kb-{target.name}")
        try:
            shutil.copy2(str(src), str(tmp))
            os.replace(str(tmp), str(target))
        except OSError as e:
            for leftover in (tmp, vplace and vplace["target"]):
                try:
                    if leftover:
                        os.unlink(str(leftover))
                except OSError:
                    pass
            if overwrite and isinstance(e, PermissionError):
                raise Refused(LOCKED) from None
            raise
        if mode == "move":
            os.unlink(str(src))
    elif mode == "move":
        shutil.move(str(src), str(target))
    else:
        shutil.copy2(str(src), str(target))
    if link_entry is not None:
        entry = inventory.attach_location(vault, link_entry["id"], place["rel"], turn_id)
    elif place["kind"] == "outside":
        entry = None  # 沒登記的地方清冊不管
    else:
        entry = inventory.register_output(vault, place["rel"] or place["bkey"], turn_id,
                                          derived_from=source["id"] if source else None, uri=place["uri"],
                                          target=target if place["kind"] == "source" else None, backup_key=place["bkey"])
    if vplace:
        _register_version(vault, vplace, turn_id)
    if draft_rel:
        _drop(vault, draft_rel, turn_id)
    return {"path": shown_path(place), "id": entry["id"] if entry else None, "mode": mode,
            "from": source["id"] if source else None, "from_label": label_of(vault, source) if source else None,
            "draft": draft_rel, "link": link, "in_vault": place["kind"] == "vault", "checked": checked,
            "version": shown_path(vplace) if vplace else None}


def _excel_checked(vault, src, mode, turn_id):
    """存單一檔是 xlsx／xlsm：交 office.check_excel（重算、錯誤值擋、沒工具照放、xlsm 不算）。有公式要重算、檔又不在工作區：
    先複製一份進這一句的工作區算、放那一份（原來那份不動）。回 (src, mode, checked 或 None)。"""
    from kb._lib import office
    low = src.suffix.lower()
    if low not in (".xlsx", ".xlsm"):
        return src, mode, None
    try:
        copy = mode != "move" and low == ".xlsx" and office.formula_report(src)[0]
    except (zipfile.BadZipFile, OSError):  # 不是 zip、讀不到：不驗照放（跟存夾前不擋一樣）
        return src, mode, None
    made = None
    try:
        if copy:
            ws = office.workspace(vault, turn_id)
            made, n = ws / src.name, 2
            while made.exists():
                made, n = ws / f"{src.stem} ({n}){src.suffix}", n + 1
            shutil.copy2(str(src), str(made))
            src, mode = made, "move"
        info = office.check_excel(vault, src)
    except BaseException as e:  # 驗不過、複製或重算出錯：拿掉複製的那份，不放
        if made is not None:
            try:
                os.unlink(str(made))
            except OSError:
                pass
        if isinstance(e, office.Refused):
            raise Refused(str(e)) from None
        raise
    return src, mode, dict(info, overwrite=False)


def _pack_folder(vault, folder, to, from_):
    """解開的夾 → (打包好的檔, --to, --from, 驗的結果)。沒給 --to 蓋回原檔（舊的先存成版本檔）；給了 --to 另存，--from 預設原檔。"""
    from kb._lib import office
    meta = office.edit_meta(vault, folder)
    if meta is None:
        raise Refused(NOT_EDIT_FOLDER)
    try:
        packed, info = office.pack_checked(vault, folder, meta)
    except (office.Refused, office.Unavailable) as e:
        raise Refused(str(e)) from None
    overwrite = not to
    if overwrite:  # 蓋回原檔（D-529）：位置照清冊、檔名不變；舊的存成版本檔，不記 derived_from（改的就是它自己）
        entry = next((e for e in inventory.load(vault)["entries"] if e.get("id") == meta.get("from")), None)
        if not entry or not entry.get("locations"):
            raise Refused(NO_SOURCE.format(target=meta["name"]))
        to, from_ = entry["locations"][0]["uri"], None
    else:
        from_ = from_ or meta.get("from")
    return str(packed), to, from_, dict(info, file=packed.name, overwrite=overwrite)


def link(vault, url, title, turn_id, type_=None, names=(), basis=()):
    """回清冊那筆 link 條目。"""
    from kb._lib import entities
    check_url(url)
    title = str(title or "").strip()
    if not title:
        raise Refused(NO_TITLE)
    if any(c in title for c in "$`"):  # 同型別名、實體名稱的 SHELL_CHARS（測試後修正裁定 20 L-4）
        raise Refused(TITLE_CHARS)
    old = _find_link(vault, url)
    if old is not None:  # 同網址再 link 不覆蓋（測試後修正裁定 14）
        raise Refused(LINKED_BEFORE.format(title=old.get("title") or url))
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
    turn.backup(vault, inventory.INVENTORY_POSIX, turn_id)  # 進回合帳：說退回拿掉的就是這筆（測試後修正裁定 13）
    return inventory.register_link(vault, url, turn_id, title=title, type_=type_,
                                   entities=found_names, basis=[b.strip() for b in basis])


def drop_draft(vault, raw, turn_id, cwd):
    rel = _draft_rel(vault, raw, cwd)
    _drop(vault, rel, turn_id)
    return rel
