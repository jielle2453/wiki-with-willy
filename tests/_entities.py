"""測試共用（實體與綜合卡）：tests/_cards 的知識庫加型別表（案子、業主）、實體清單 fixture、照 header 寫掛實體的檔案卡。"""
import json
import re
import shutil
from pathlib import Path

from tests import _cards
from tests._guard_fixture import FIXTURES
from kb._lib import cards, profile  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402

FIX = FIXTURES / "entities"
DAY = "2026-10-19"
XINYI = "e-5c1d0a7e"
WANG = "e-0f3e9a21"
SELF = "e-7a6b5c4d"
DAAN = "e-1a2b3c4d"
# tests/_cards 知識庫裡 read ok 的原檔（相對來源別名的 uri 去掉 file://）
QUOTE = "客戶案件/信義王/報價_v2.pdf"
ESTIMATE = "客戶案件/內湖李/估價單.pdf"
DRAFT = "客戶案件/大安陳/[草稿] 會議紀錄、第一次（修）.txt"
TEMPLATE = "報價與合約/合約範本.txt"
MEMO = "公司行政/舊檔/Big5備忘.txt"
CONTRACT = "知識庫/案子/信義王/合約.docx"


def set_profile(V, types=(("案子", "matter"), ("業主", "party")), about=()):
    """直接寫 profile.json（schema 2）與渲染的 md（sha 對得上），建型別夾。about 是 [(名稱, kind, [別名])]。"""
    data = profile.load(V) or profile.defaults(DAY)
    data["types"] = [{"name": n, "category": c, "note": "", "source": "interview", "date": DAY} for n, c in types]
    data["synthesis_sections"] = profile.default_sections(data["types"], DAY)
    data["about"] = [{"name": n, "kind": k, "aliases": list(a), "source": "interview", "date": DAY} for n, k, a in about]
    text = profile.render_text(data)
    (Path(V) / profile.MD_POSIX).write_text(text, encoding="utf-8", newline="\n")
    data["md_sha256"] = profile.sha256_text(text)
    (Path(V) / profile.PROFILE_POSIX).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for n, _c in types:
        (Path(V) / n).mkdir(exist_ok=True)


def make_entity_vault(with_entities=False):
    """回 (tmp, V, raw)：_cards 的知識庫、型別表案子與業主。with_entities：放 fixture 的 entities.json 與兩張綜合卡。"""
    tmp, V, raw = _cards.make_card_vault()
    set_profile(V)
    if with_entities:
        install(V)
    return tmp, V, raw


def install(V):
    shutil.copy2(FIX / "entities.json", Path(V) / ".system" / "entities.json")
    for name in ("信義王", "大安陳"):
        d = Path(V) / "案子" / name
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(FIX / f"{name}.md", d / f"{name}.md")


def put_entities(V, entries):
    (Path(V) / ".system" / "entities.json").write_text(
        json.dumps({"schema_version": 1, "entities": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def entity(eid, name, type_="案子", **kw):
    e = {"id": eid, "type": type_, "name": name, "aliases": [], "self": False, "status": "active", "source": "confirmed",
         "date": DAY, "card": False, "created_turn": "20261019-100000-test"}
    e.update(kw)
    return e


def new_item(V, rel):
    fid = _cards.entry_of(V, rel)["id"]
    data = cards.todo(V, sources_lib.load(V))
    return next(i for i in data["new"] + data["stale"] if i["id"] == fid)


def with_entities(text, names=(), pending=()):
    text = re.sub(r"^實體: \[.*\]$", f"實體: [{', '.join(names)}]", text, count=1, flags=re.M)
    return re.sub(r"^待補型別: \[.*\]$", f"待補型別: [{', '.join(pending)}]", text, count=1, flags=re.M)


def write_card(V, rel, title, names=(), pending=(), date="2026-10-01"):
    """照 todo 的 header 寫一張第二版檔案卡、掛 names。回卡的相對路徑。"""
    item = new_item(V, rel)
    p = _cards.card_path(V, item, title)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(with_entities(_cards.card_text(item, title, date=date), names, pending), encoding="utf-8", newline="\n")
    return p.relative_to(V).as_posix()


def to_v1(V, rel):
    """把一張第二版卡改成第一版（拿掉 實體、待補型別 兩行，版本 1）。"""
    p = Path(V) / rel
    text = p.read_text(encoding="utf-8")
    text = re.sub(r"^實體: .*\n", "", text, count=1, flags=re.M)
    text = re.sub(r"^待補型別: .*\n", "", text, count=1, flags=re.M)
    p.write_text(text.replace("範本版本: 2", "範本版本: 1"), encoding="utf-8", newline="\n")


def synthesis_text(header, name, body="一句講這是什麼。\n\n## 現況\n- 一點〔f-00000000〕\n"):
    return header + f"# {name}\n\n{body}"
