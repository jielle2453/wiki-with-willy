"""kb init <路徑>：從 skeleton/ 建客戶端總資料夾的五區骨架加 .system/sources.json（D-077、D-080）。

缺的才建，既有檔一律不碰（I-066）；.gitkeep 只為了讓 git 留住空夾，不複製；.system/ 不在骨架裡，由這裡建。
目標的上層已是知識庫就拒絕（I-054，用 guard.find_vault 找）。CLAUDE.md、.claude/、.system/ 在 Windows 設隱藏屬性，設不成只印一句。
不開回合、不備份、不寫 ledger：init 之前還沒有知識庫。
有建東西就寫一段工作日誌程式段（D-085），代號用現在時間、不碰 turns/；日誌自己的檔不在骨架計畫裡，不算項數。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard, journal_cli
from kb._lib import sources as sources_lib
from kb._lib.io import read_text, write_text_atomic
from kb._lib.paths import plugin_root

SKELETON_REL = "skeleton"
GITKEEP = ".gitkeep"
HIDDEN_ITEMS = ("CLAUDE.md", ".claude", ".system")
EMPTY_SOURCES = {"schema_version": 1, "sources": []}
FILE_ATTRIBUTE_HIDDEN = 0x2
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


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
    return [(d, "dir") for d in sorted(dirs)] + [(f, "file") for f in sorted(files)]


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
    if args.check:
        if as_json:  # L8
            print(json.dumps({"vault": str(p), "would_create": [rel for rel, _k, have in status if not have],
                              "existing": [rel for rel, _k, have in status if have]}, ensure_ascii=False))
            return 0
        for rel, kind, have in status:
            print(f"{'已有' if have else '會建'} {_label(rel, kind)}")
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
    for name in HIDDEN_ITEMS:
        if name in created:
            why = _hide(p / name)
            if why:
                notes.append(f"隱藏屬性沒設成：{name}，{why}")
    if created:  # 程式段（D-085）：只數骨架項目；沒有缺的不寫；不開回合
        note = f"建立知識庫：建了 {len(created)} 項" if not existed else f"補齊了 {len(created)} 項"
        journal_cli.record_program_or_warn(p, note, [{"path": rel, "action": "create"} for rel in created], "init")
    if as_json:
        print(json.dumps({"vault": str(p), "created": created, "existing": existing}, ensure_ascii=False))
    elif not existed:
        print(f"已建立知識庫 {p}：建了 {len(created)} 項")
    elif created:
        print(f"{p} 已是知識庫，補齊了 {len(created)} 項")
    else:
        print(f"{p} 已是知識庫，沒有缺的")
    for note in notes:
        print(note)
    return 0
