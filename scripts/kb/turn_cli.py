"""kb turn current／approve／note／redact：看目前回合；客戶同意後解除這一回合的綜合卡張數上限（D-075）；
note 給這一句的「做了」一句白話（D-084，AI 覆寫程式地板）；redact 把這一句的「問」換成遮好的句子（D-086、B-027）。
CLI 由模型呼叫時拿不到 session_id，用 .system/turns/current.json（最近一句話的回合，B-019）。
note、redact 進來的字一律再過 mask_secrets（多遮不改）；工作日誌那行存在才改，改了就重渲染。
"""
import argparse
import json
import os
from pathlib import Path

from kb import guard
from kb._lib import credentials, journal, turn
from kb._lib.io import read_json, write_json_atomic

NOT_IN_VAULT = "不在知識庫裡：這裡往上找不到 .system/sources.json"


def add_arguments(sub):
    subs = sub.add_subparsers(dest="turn_command", metavar="<子子命令>", required=True)
    cur = subs.add_parser("current", help="印目前回合：代號、問、已備份檔數、上限狀態", description="印目前回合。")
    cur.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    ap = subs.add_parser("approve", help="客戶同意後解除這一回合的綜合卡張數上限", description="解除這一回合的綜合卡張數上限。")
    ap.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")
    no = subs.add_parser("note", help="給這一句的「做了」一句白話，寫進工作日誌", description="給這一句的「做了」一句白話。")
    no.add_argument("text", help="白話一句")
    rd = subs.add_parser("redact", help="把這一句的「問」換成遮好的句子", description="把這一句的「問」換成遮好的句子。")
    rd.add_argument("text", help="遮好的句子")


def _note(vault, text):
    t = turn.current(vault)
    note = credentials.mask_secrets(str(text))
    turn.set_note(vault, t.get("session_id"), note)
    journal.update_turn(vault, t["turn"], note=note, note_by="ai")
    print("記下了")
    return 0


def _redact(vault, text):
    t = turn.current(vault)
    head = turn.prompt_head(vault, text)
    turn.set_prompt_head(vault, t.get("session_id"), head)
    meta = Path(vault) / turn.VERSIONS_REL / t["turn"] / "turn.json"
    if meta.is_file():
        data = read_json(meta, {}) or {}
        data["prompt_head"] = head
        write_json_atomic(meta, data)
    journal.update_turn(vault, t["turn"], prompt=head)
    print(f"這一句的問已改成：「{head}」")
    return 0


def run(args):
    vault = guard.find_vault(os.getcwd())
    if vault is None:
        print(NOT_IN_VAULT)
        return 2
    as_json = getattr(args, "json", False)
    if args.turn_command == "note":
        return _note(vault, args.text)
    if args.turn_command == "redact":
        return _redact(vault, args.text)
    if args.turn_command == "approve":
        t = turn.approve(vault)
        if as_json:
            print(json.dumps({"turn": t["turn"], "approved": True}, ensure_ascii=False))
        else:
            print("這一題解除綜合卡張數上限")
        return 0
    t = turn.current(vault)
    n = len(turn.entries(vault, t["turn"]))
    if as_json:
        print(json.dumps({"turn": t["turn"], "prompt_head": t.get("prompt_head", ""), "backed_up": n,
                          "approved": bool(t.get("approved"))}, ensure_ascii=False))
    else:
        limit = "已解除上限" if t.get("approved") else "未解除上限"
        print(f"{t['turn']}｜問：「{t.get('prompt_head', '')}」｜已備份 {n} 檔｜{limit}")
    return 0
