"""kb init <路徑>：從 skeleton/ 建客戶端總資料夾的五區骨架加 .system/sources.json（D-077、D-080），
再加客戶設定檔兩份 .system/profile.json 與 系統/客戶設定檔.md（預設值與渲染，D-111）。

缺的才建，既有檔一律不碰（I-066）；.gitkeep 只為了讓 git 留住空夾，不複製；.system/ 不在骨架裡，由這裡建。
目標的上層已是知識庫就拒絕（I-054，用 guard.find_vault 找）。CLAUDE.md、.claude/、.system/ 在 Windows 設隱藏屬性，設不成只印一句。
不開回合、不備份、不寫 ledger：init 之前還沒有知識庫。
有建東西就寫一段工作日誌程式段（D-085），代號用現在時間、不碰 turns/；日誌自己的檔不在骨架計畫裡，不算項數。
既有知識庫的 profile.json 是 schema 1（實體與綜合卡第 11 點）：md 沒改過就寫遷移後的 schema 2、重渲染 md（補第四、五節）；
md 改過就不動，請客戶先 kb profile apply。缺 entities.json、agenda.json 不建（第一次寫時才建）；
缺 系統/日程.md 渲染一份空的（日程第 11 點）。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import agenda, profile
from kb._lib import sources as sources_lib
from kb._lib.io import read_text, write_json_atomic, write_text_atomic
from kb._lib.paths import plugin_root

SKELETON_REL = "skeleton"
PROFILE_FILES = (profile.PROFILE_POSIX, profile.MD_POSIX)  # 不住 skeleton：由 profile 的預設值與渲染寫（D-067）
GENERATED_FILES = PROFILE_FILES + (agenda.MD_POSIX,)  # 日程.md 也由程式渲染，缺的才補
GITKEEP = ".gitkeep"
HIDDEN_ITEMS = ("CLAUDE.md", ".claude", ".system")
EMPTY_SOURCES = {"schema_version": 1, "sources": []}
FILE_ATTRIBUTE_HIDDEN = 0x2
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
UPGRADE_LINE = "設定檔升級：補第四、五節"
UPGRADE_BLOCKED = "設定檔有沒套用的改動，先 kb profile apply 再跑 kb init 升級"


def add_arguments(sub):
    sub.add_argument("path", help="要建的總資料夾")
    sub.add_argument("--check", action="store_true", help="只印計畫（會建／已有），什麼都不寫")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def skeleton_root():
    return plugin_root() / SKELETON_REL


def plan_items():
    """骨架樹的項目 [(rel, kind)]，kind 是 dir 或 file；資料夾在前、各自照字母。跳過 .gitkeep，它的父夾算空夾；加 .system/ 與 sources.json。"""
    root = skeleton_root()
    dirs, files = {".system"}, []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            dirs.add(rel)
        elif p.name == GITKEEP:
            dirs.add(p.parent.relative_to(root).as_posix())
        else:
            files.append(rel)
    files.append(sources_lib.SOURCES_REL)
    files.extend(GENERATED_FILES)
    return [(d, "dir") for d in sorted(dirs)] + [(f, "file") for f in sorted(files)]


def _fill_profile(p, have_json, have_md, notes):
    """補客戶設定檔（客戶設定檔與型別第 3 點），回 (建了的 rel, 只改了 md_sha256 的 rel)：
    兩份都缺：寫預設 JSON、渲染 md、回填 sha；只缺 md：從 JSON 渲染回來、更新 sha；
    只缺 JSON（半份）：寫預設 JSON、不動 md、md_sha256 留 null，下次開場就會問要不要套用。"""
    if have_json and have_md:
        return [], []
    if have_md:
        write_json_atomic(p / profile.PROFILE_POSIX, profile.defaults())
        return [profile.PROFILE_POSIX], []
    if have_json:
        try:
            data = profile.load(p)
        except ValueError as e:
            notes.append(f"{profile.PROFILE_POSIX} 讀不到，沒補 {profile.MD_POSIX}：{e}")
            return [], []
    else:
        data = profile.defaults()
    (p / profile.PROFILE_POSIX).parent.mkdir(parents=True, exist_ok=True)
    data["md_sha256"] = profile.sha256_text(profile.render(p, data))
    write_json_atomic(p / profile.PROFILE_POSIX, data)
    if have_json:
        return [profile.MD_POSIX], [profile.PROFILE_POSIX]
    return [profile.PROFILE_POSIX, profile.MD_POSIX], []


def _profile_upgrade(p):
    """兩份設定檔都在、JSON 是 schema 1：md 沒改過回 upgrade，改過回 blocked；其他回 None。"""
    if not ((p / profile.PROFILE_POSIX).is_file() and (p / profile.MD_POSIX).is_file()):
        return None
    if profile.stored_schema(p) != 1:
        return None
    try:
        data = profile.load(p)
    except ValueError:
        return None
    return "upgrade" if profile.md_sha256(p) == data.get("md_sha256") else "blocked"


def _upgrade_profile(p):
    """寫遷移後的 schema 2 JSON、重渲染 md、回填 sha（不開回合、不備份：md 沒改過，內容照 JSON 重寫）。"""
    data = profile.load(p)
    data["md_sha256"] = profile.sha256_text(profile.render(p, data))
    write_json_atomic(p / profile.PROFILE_POSIX, data)


def _vault_above(p):
    """p 的上層有沒有知識庫（p 自己有算「補齊」，不算）。回原始大小寫的路徑或 None。"""
    found = guard.find_vault(str(p.parent))
    return Path(os.path.realpath(str(found))) if found is not None else None


def _hide(path):
    """Windows 設隱藏屬性；非 Windows 跳過。回 None 或失敗原因。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetFileAttributesW.restype = ctypes.c_uint32
        attrs = kernel32.GetFileAttributesW(str(path))
        if attrs == INVALID_FILE_ATTRIBUTES:
            raise ctypes.WinError()
        if not kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN):
            raise ctypes.WinError()
    except Exception as e:  # noqa: BLE001  隱藏只是方便，設不成不算失敗
        return f"{type(e).__name__}: {e}"
    return None


def _label(rel, kind):
    return rel + "/" if kind == "dir" else rel


def run(args):
    as_json = getattr(args, "json", False)
    p = Path(args.path).resolve()
    if p.exists() and not p.is_dir():
        print(f"{p} 是檔案，不能當總資料夾")
        return 3
    above = _vault_above(p)
    if above is not None:
        print(f"上層 {above} 已經是知識庫，不能在知識庫裡再建一個")
        return 3
    existed = (p / sources_lib.SOURCES_REL).is_file()
    status = [(rel, kind, (p / rel).exists()) for rel, kind in plan_items()]
    upgrade = _profile_upgrade(p)
    upgrade_line = {"upgrade": UPGRADE_LINE, "blocked": UPGRADE_BLOCKED}.get(upgrade)
    if args.check:
        if as_json:  # L8
            print(json.dumps({"vault": str(p), "would_create": [rel for rel, _k, have in status if not have],
                              "existing": [rel for rel, _k, have in status if have], "profile_upgrade": upgrade},
                             ensure_ascii=False))
            return 0
        for rel, kind, have in status:
            print(f"{'已有' if have else '會建'} {_label(rel, kind)}")
        if upgrade_line:
            print(upgrade_line)
        return 0
    for rel, kind, have in status:
        if have and (p / rel).is_dir() != (kind == "dir"):
            print(f"{p / rel} 已存在但不是{'資料夾' if kind == 'dir' else '檔案'}，請先移開")
            return 3
    created, existing, notes = [], [], []
    for rel, kind, have in status:
        if have:
            existing.append(rel)
            continue
        if rel in GENERATED_FILES:
            continue  # 設定檔兩份互相牽動、日程.md 要渲染，下面各自補
        target = p / rel
        if kind == "dir":
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if rel == sources_lib.SOURCES_REL:
                write_text_atomic(target, json.dumps(EMPTY_SOURCES, ensure_ascii=False) + "\n")
            else:
                write_text_atomic(target, read_text(skeleton_root() / rel))
        created.append(rel)
    have = {rel: h for rel, _k, h in status}
    made, touched = _fill_profile(p, have[profile.PROFILE_POSIX], have[profile.MD_POSIX], notes)
    created += made
    if not have[agenda.MD_POSIX]:
        try:
            agenda.render(p)
            created.append(agenda.MD_POSIX)
        except ValueError as e:  # agenda.json 壞了：骨架照建，日程.md 之後 kb agenda render 補
            notes.append(f"{agenda.MD_POSIX} 沒補：{e}")
    if upgrade == "upgrade":
        _upgrade_profile(p)
        touched = [profile.PROFILE_POSIX, profile.MD_POSIX]
    for name in HIDDEN_ITEMS:
        if name in created:
            why = _hide(p / name)
            if why:
                notes.append(f"隱藏屬性沒設成：{name}，{why}")
    if created or upgrade == "upgrade":  # 程式段（D-085）：只數骨架項目；沒有缺的、也沒升級不寫；不開回合
        note = f"建立知識庫：建了 {len(created)} 項" if not existed else f"補齊了 {len(created)} 項"
        if not created:
            note = UPGRADE_LINE
        elif upgrade == "upgrade":
            note += "；" + UPGRADE_LINE
        changed = [{"path": rel, "action": "create"} for rel in created] + [{"path": rel, "action": "modify"}
                                                                             for rel in touched]
        journal_cli.record_program_or_warn(p, note, changed, "init", journal_cli.in_conversation(p))
    if as_json:
        print(json.dumps({"vault": str(p), "created": created, "existing": existing, "profile_upgrade": upgrade},
                         ensure_ascii=False))
    elif not existed:
        print(f"已建立知識庫 {p}：建了 {len(created)} 項")
    elif created:
        print(f"{p} 已是知識庫，補齊了 {len(created)} 項")
    else:
        print(f"{p} 已是知識庫，沒有缺的")
    if upgrade_line and not as_json:
        print(upgrade_line)
    for note in notes:
        print(note)
    return 0
