"""kb who [set <名字>] [--json]（多人共用 2，D-286、D-287）：這台是誰、幾個人用、團隊成員。

單人：說單人、不問名字。set：把名字記進 env.json 這台那一筆（不進 git），第五節「本人」沒有這個名字就加一列（走 kb profile add 的同一條路）。
"""
import argparse
import json
from types import SimpleNamespace

from kb import profile_cli
from kb._lib import env, people
from kb.inventory_cli import NOT_IN_VAULT, find_vault

NEEDS_NAME = "要給名字：kb who set <名字>"
NOT_MULTI = "現在是單人，不用記這台是誰；多人用先 kb profile set people <N>"


def add_arguments(sub):
    sub.add_argument("action", nargs="?", choices=("set",), help="set＝記這台是誰")
    sub.add_argument("name", nargs="?", help="名字")
    sub.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
    sub.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def payload(vault):
    return {"me": people.me(vault), "default": people.default_name(vault), "people": people.count(vault),
            "multi": people.multi(vault), "members": people.members(vault)}


def _show(vault, as_json):
    p = payload(vault)
    if as_json:
        print(json.dumps(p, ensure_ascii=False))
        return 0
    if not p["multi"]:
        print("單人用，不記這台是誰")
        return 0
    print(f"這台是：{p['me'] or '還沒記（預設 ' + str(p['default']) + '）'}；幾個人用：{p['people']}；"
          f"團隊成員：{'、'.join(p['members']) or '（還沒有）'}")
    return 0


def _set(vault, name, as_json):
    name = (name or "").strip()
    if not name:
        print(NEEDS_NAME)
        return 3
    if not people.multi(vault):
        print(NOT_MULTI)
        return 3
    if name not in people.members(vault):
        ns = SimpleNamespace(vault=str(vault), json=False, profile_command="add", section="關於你",
                             fields=[f"名稱={name}", "是什麼=本人"], check=False, whose=None)
        code = profile_cli.run(ns)
        if code != 0:
            return code
    env.set_person(vault, name)
    if as_json:
        print(json.dumps(dict(payload(vault), applied=True), ensure_ascii=False))
    else:
        print(f"記下了：這台是「{name}」")
    return 0


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    as_json = getattr(args, "json", False)
    if args.action == "set":
        return _set(vault, args.name, as_json)
    return _show(vault, as_json)
