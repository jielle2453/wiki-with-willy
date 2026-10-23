"""kb profile check／apply（客戶設定檔與型別第 2 點，D-110～D-113）與 show／add／remove／type（note 改規則第 2 點，D-144）。

check：系統/客戶設定檔.md 跟 .system/profile.json 比，印差異與型別夾對帳，不寫任何檔；讀不懂 exit 3，其餘 0。
apply：check 的判斷之後，刪了有東西的型別夾那列擋（D-058）；沒有改不寫；否則先建缺的型別夾（只建不刪、建夾不備份；
建不成就整份不套用，測試後修正裁定 11）→ merge → 兩檔備進回合後寫（D-074）→ 表少了的型別、夾是空的就一起拿掉（確認後修正裁定 21）
→ 第三節拿掉的「不整理」種類把清冊的 skip_kind 拿掉（清冊先備份）→ 第五節關於你對實體清單（entities.sync_self，先備份）。
建的夾與拿掉的空夾記進回合的資料夾帳（D-142），退回時改得回來。
apply --from <路徑>（build 第 4 點，D-052）：事先填好的設定檔 md 先照 apply 同一支解析驗過（讀不懂回節與列、什麼都不寫），
再把 系統/客戶設定檔.md 備進回合、換成那份，照 apply 那條路做；apply 拒絕（exit 3）就把原本的 md 寫回去。
show：六節的表（--json 給每節的欄、能填什麼、主鍵、列與指令範本，加 pending_edit）。只讀。
add／remove：note 在對話裡改一節的一列（profile.edited）→ render_text → parse_md（跟 apply 同一支驗證）→ 跟 apply 同一條路寫；
md 有手改未套用時一律拒絕、不動檔、給結果清單（D-110 補）；偏好的條數與字數上限在 edited 擋（D-137）。
type rename／merge／remove：會搬夾的型別操作（D-139、D-143）；--check 只算影響、回問題與結果清單，不寫任何檔；
不帶 --check 才做，中途出錯照 entities._Undo 還原，夾的動作做完才記進資料夾帳。
範本（produce 第 6 點，D-167）：add 範本 文件=<名> 底=<卡編號或卡標題> 先把底換成清冊 active 的卡編號，找不到拒絕。
鎖 profile.json 30 秒。終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json
import os

from kb import journal_cli
from kb._lib import agenda, cards, config, entities, inventory, pending, profile, synthesis, turn
from kb._lib import io as kio
from kb._lib import sources as sources_lib
from kb._lib.plan import cmd
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
MISSING = "還沒有客戶設定檔，跑 kb init 補"
LOCK_BUSY = "等不到鎖，沒有寫入"
NO_CHANGE = "客戶設定檔沒有改"
NOT_APPLIED = "客戶設定檔沒有改，沒有套用"
NO_CONTENT_DIFF = "客戶設定檔有改，但沒有內容上的差異"
NONEMPTY = "型別「{name}」底下還有 {n} 項，先搬走再拿掉這列"
UNLISTED = "第一層夾不在型別表：{names}（請在設定檔第一節補一列）"
CHANGED = [{"path": profile.PROFILE_POSIX, "action": "modify"}, {"path": profile.MD_POSIX, "action": "modify"}]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="profile_command", metavar="<子子命令>", required=True)
    ck = subs.add_parser("check", help="看客戶設定檔改了什麼", description="設定檔 md 跟 JSON 比，印差異與型別夾對帳；不寫檔。")
    ap = subs.add_parser("apply", help="套用客戶設定檔", description="把改過的設定檔套進 JSON、建缺的型別夾；寫前先備份。")
    ap.add_argument("--from", dest="from_md", help="事先填好的設定檔 md（建置那天用）：先換成它再套用")
    sh = subs.add_parser("show", help="看客戶設定檔六節", description="印六節的表；--json 給欄位、能填什麼與指令範本。不寫檔。")
    ad = subs.add_parser("add", help="加或改一列", description="加一列；主鍵已有就改那一列。寫前先備份。")
    rm = subs.add_parser("remove", help="拿掉一列", description="照主鍵拿掉一列。寫前先備份。")
    for p in (ad, rm):
        p.add_argument("section", help="節：型別、卡型、不整理與敏感、每類段落、關於你、偏好、範本")
        p.add_argument("fields", nargs="*", help="欄=值（例：偏好=回答先講結論）")
    ty = subs.add_parser("type", help="型別改名、合併、拿掉", description="會搬資料夾的型別操作；先 --check 看影響。")
    tys = ty.add_subparsers(dest="type_command", metavar="<動作>", required=True)
    tr = tys.add_parser("rename", help="型別改名", description="型別改名：資料夾、實體、綜合卡、檔案卡一起改。")
    tr.add_argument("--to", required=True, help="新名")
    tm = tys.add_parser("merge", help="型別併進另一個", description="型別併進另一個：實體與資料夾搬過去，這一類拿掉。")
    tm.add_argument("--into", required=True, help="併進哪個型別")
    td = tys.add_parser("remove", help="拿掉型別", description="拿掉沒有實體、資料夾是空的型別。")
    for p in (tr, tm, td):
        p.add_argument("name", help="型別")
        p.add_argument("--check", action="store_true", help="只算影響、不動手")
    for p in (ck, ap, sh, ad, rm, tr, tm, td):
        p.add_argument("--vault", help="總資料夾；沒給就從目前目錄往上找")
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 輸出")


def _detail_lines(st):
    d, cats = st["diff"], {v: k for k, v in profile.CATEGORIES.items()}
    acts = {v: k for k, v in profile.ACTIONS.items()}
    new = st["new"]
    old = st["data"]
    new_types = {t["name"]: t for t in new["types"]}
    nonempty = dict(st["removed_nonempty"])
    lines = [f"- 型別 多了 {n}（{cats.get(new_types[n]['category'], '')}）" for n in d["types"]["added"]]
    for n in d["types"]["removed"]:
        why = f"夾裡還有 {nonempty[n]} 項，套用會被擋" if n in nonempty else "空夾會一起拿掉"
        lines.append(f"- 型別 少了 {n}（{why}）")
    lines += [f"- 型別 {n} 改了" for n in d["types"]["changed"]]
    for word, key in (("多了", "added"), ("少了", "removed"), ("改了", "changed")):
        lines += [f"- 卡型 {c} 的段落 {s} {word}" for c, s in d["card_types"][key]]
    new_ex = {e["kind"]: e for e in new["exclusions"]}
    old_ex = {e["kind"]: e for e in old["exclusions"]}
    lines += [f"- 不整理與敏感 多了 {k}（{acts.get(new_ex[k]['action'], '')}）" for k in d["exclusions"]["added"]]
    lines += [f"- 不整理與敏感 少了 {k}（{acts.get(old_ex[k].get('action'), '')}）" for k in d["exclusions"]["removed"]]
    lines += [f"- 不整理與敏感 {k} 改成 {acts.get(new_ex[k]['action'], '')}" for k in d["exclusions"]["changed"]]
    for word, key in (("多了", "added"), ("少了", "removed"), ("改了", "changed")):
        lines += [f"- 每類段落 {t} 的 {s} {word}" for t, s in d["synthesis_sections"][key]]
    kinds = {v: k for k, v in profile.KINDS.items()}
    new_about = {a["name"]: a for a in new["about"]}
    lines += [f"- 關於你 多了 {n}（{kinds.get(new_about[n]['kind'], '')}）" for n in d["about"]["added"]]
    lines += [f"- 關於你 少了 {n}" for n in d["about"]["removed"]]
    lines += [f"- 關於你 {n} 改了" for n in d["about"]["changed"]]
    lines += [f"- 偏好 多了 {n}" for n in d["preferences"]["added"]]
    lines += [f"- 偏好 少了 {n}" for n in d["preferences"]["removed"]]
    lines += [f"- 範本 多了 {n}" for n in d["templates"]["added"]]
    lines += [f"- 範本 少了 {n}" for n in d["templates"]["removed"]]
    lines += [f"- 範本 {n} 換了底" for n in d["templates"]["changed"]]
    return lines


def _unlisted_line(st):
    return [UNLISTED.format(names="、".join(st["unlisted"]))] if st["unlisted"] else []


def _public(st):
    return {"state": st["state"], "diff": st["diff"], "error": st["error"], "unlisted": st["unlisted"],
            "removed_empty": st["removed_empty"], "removed_nonempty": [list(x) for x in st["removed_nonempty"]],
            "to_create": st["to_create"]}


def check_lines(st):
    """回 (行, exit code)。"""
    if st["state"] == "missing":
        return [MISSING], 0
    if st["state"] == "error":
        return [f"客戶設定檔讀不懂：{st['error']}"], 3
    if st["state"] == "ok":
        return [NO_CHANGE] + _unlisted_line(st), 0
    if not profile.has_diff(st["diff"]):
        return [NO_CONTENT_DIFF] + _unlisted_line(st), 0
    return [f"客戶設定檔有改：{profile.summary_full(st['diff'])}"] + _detail_lines(st) + _unlisted_line(st), 0


def _check(vault, as_json):
    st = profile.status(vault)
    lines, code = check_lines(st)
    if as_json:
        print(json.dumps(_public(st), ensure_ascii=False))
    else:
        print("\n".join(lines))
    return code


def _refuse(as_json, st, line):
    if as_json:
        print(json.dumps(dict(_public(st), applied=False, created_dirs=[], unskipped=0, message=line), ensure_ascii=False))
    else:
        print(line)


def _remove_empty_dirs(vault, names):
    """表少了一列、夾是空的：寫完設定檔後一起拿掉（確認後修正裁定 21）；rmdir 失敗（剛好被佔用）就留著。回 (拿掉的, 留著的)。"""
    removed, kept = [], []
    for name in names:
        path = vault / name
        if not path.is_dir():
            continue
        try:
            os.rmdir(path)
        except OSError:
            kept.append(name)
            continue
        removed.append(name)
    return removed, kept


def _with_dirs(summary, removed, kept):
    """「型別 -1」後面接「（拿掉了空夾 X；Y 夾是空的，會留著）」。"""
    notes = (["拿掉了空夾 " + "、".join(removed)] if removed else []) + [f"{n} 夾是空的，會留著" for n in kept]
    if not notes or not summary.startswith("型別 "):
        return summary
    head, sep, rest = summary.partition("、")
    return f"{head}（{'；'.join(notes)}）{sep}{rest}"


def _apply_locked(vault, as_json, conversation):
    st = profile.status(vault)
    if st["state"] == "missing":
        _refuse(as_json, st, MISSING)
        return 3
    if st["state"] == "error":
        _refuse(as_json, st, f"客戶設定檔讀不懂：{st['error']}")
        return 3
    if st["state"] == "ok":  # 沒有內容差異也補跑不整理種類與關於你（冪等；上次寫完設定檔才失敗的會補上，測試後修正裁定 12、19）
        turn_id = turn.current(vault)["turn"]
        unskipped = inventory.unskip_kinds(vault, _stale_skip_kinds(vault, st["data"]), turn_id)
        about = entities.sync_self(vault, st["data"].get("about") or [], turn_id)
        lines = ([f"{len(unskipped)} 份回到待製卡"] if unskipped else []) + _about_lines(about)
        if lines:
            changed = ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if unskipped else []) + \
                ([{"path": entities.ENTITIES_POSIX, "action": "modify"}] if _about_lines(about) else [])
            journal_cli.record_program_or_warn(vault, "；".join(lines), changed, "profile apply", conversation)
        if as_json:
            print(json.dumps(dict(_public(st), applied=False, created_dirs=[], unskipped=len(unskipped),
                                  message=NOT_APPLIED, about=about), ensure_ascii=False))
        else:
            print("\n".join([NOT_APPLIED] + lines))
        return 0
    if st["removed_nonempty"]:
        name, n = st["removed_nonempty"][0]
        _refuse(as_json, st, NONEMPTY.format(name=name, n=n))
        return 3
    blocked = [n for n in st["to_create"] if (vault / n).exists()]
    if blocked:
        _refuse(as_json, st, f"「{blocked[0]}」已經有同名的檔，不能建成型別夾；先改名或搬走")
        return 3
    created = []
    for name in st["to_create"]:  # 先建夾再寫檔（測試後修正裁定 11）：建不成就整份不套用，已建的夾留著
        try:
            (vault / name).mkdir(exist_ok=True)
        except OSError as e:
            line = f"建不了資料夾「{name}」：{e.strerror or e}；設定檔沒有套用"
            _refuse(as_json, st, line)
            if created and not as_json:
                print("已建的資料夾留著：" + "、".join(created))
            return 3
        created.append(name)
    old, new = st["data"], st["new"]
    turn_id = turn.current(vault)["turn"]
    for name in created:  # 建的夾記進回合（D-142）：退回時空的就拿掉
        turn.record_folder(vault, turn_id, "make", name)
    merged = profile.merge(old, new, first=old.get("applied") is None)
    data = dict(old, **merged, applied=profile.now())
    profile.write_locked(vault, data, turn_id)
    removed_dirs, kept_dirs = _remove_empty_dirs(vault, st["removed_empty"])
    for name in removed_dirs:
        turn.record_folder(vault, turn_id, "remove", name)
    try:
        with _written():
            kinds = set(profile.unskipped_kinds(old, merged)) | _stale_skip_kinds(vault, merged)
            unskipped = inventory.unskip_kinds(vault, kinds, turn_id)
            about = entities.sync_self(vault, merged["about"], turn_id)
    except Refused as e:
        if as_json:
            print(json.dumps(dict(_public(st), created_dirs=created, removed_dirs=removed_dirs, message=e.line,
                                  **e.payload), ensure_ascii=False))
        else:
            print(e.line)
        return 3
    about_lines = _about_lines(about)
    summary = _with_dirs(profile.summary_short(st["diff"]), removed_dirs, kept_dirs)
    st["unlisted"] = [n for n in st["unlisted"] if n not in removed_dirs]
    parts = [summary] if summary else []
    if created:
        parts.append("建了資料夾 " + "、".join(created))
    if unskipped:
        parts.append(f"{len(unskipped)} 份回到待製卡")
    body = "；".join(parts) if summary else "沒有內容上的差異，重寫了設定檔" + "".join("；" + p for p in parts)
    sentence = f"已套用客戶設定檔：{body}"
    changed = CHANGED + ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if unskipped else [])
    if about_lines:
        changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
    journal_cli.record_program_or_warn(vault, sentence[1:], changed, "profile apply", conversation)
    if as_json:
        print(json.dumps(dict(_public(st), applied=True, created_dirs=created, removed_dirs=removed_dirs,
                              unskipped=len(unskipped), about=about), ensure_ascii=False))
        return 0
    print(sentence)
    for line in about_lines + _unlisted_line(st):
        print(line)
    return 0


def _about_lines(about):
    """第五節對實體清單動到的（實體與綜合卡第 10 點）：登記了誰、誰改記成你自己、誰不再記成你自己。"""
    lines = [f"關於你：登記了 {'、'.join(about['added'])}"] if about["added"] else []
    lines += [f"關於你：{note}" for note in about["notes"]]
    if about["released"]:
        lines.append(f"關於你：{'、'.join(about['released'])} 不再記成你自己")
    return lines


def _stale_skip_kinds(vault, data):
    """清冊裡記了 skip_kind、但設定檔第三節已經不是「不整理」的種類（上次寫完設定檔、清冊那步沒做完留下的）。"""
    keep = set(profile.no_card_kinds(data))
    return {e["skip_kind"] for e in inventory.load(vault)["entries"] if e.get("skip_kind")} - keep


def _apply(vault, as_json, conversation, from_md=None):
    with profile.lock(vault):
        if from_md is None:
            return _apply_locked(vault, as_json, conversation)
        return _apply_from(vault, from_md, as_json, conversation)


FROM_UNREADABLE = "讀不到填好的設定檔：{}"


def _apply_from(vault, from_md, as_json, conversation):
    """--from：先用 apply 同一支解析驗那份（讀不懂回節與列、不寫），過了才備份、換掉 系統/客戶設定檔.md、照 apply 做；
    apply 拒絕就把原本的 md 寫回去。"""
    st = profile.status(vault)
    if st["state"] == "missing":
        _refuse(as_json, st, MISSING)
        return 3
    try:
        text = open(from_md, encoding="utf-8").read()
    except UnicodeDecodeError:
        _refuse(as_json, st, "客戶設定檔讀不懂：檔案不是 UTF-8")
        return 3
    except OSError as e:
        _refuse(as_json, st, FROM_UNREADABLE.format(e.strerror or e))
        return 3
    data = st["data"]
    try:
        stored = profile.stored_schema(vault)
        profile.parse_md(text, config.get(vault, "card_title_max_chars"),
                         legacy=stored if stored in profile.SECTIONS_OF else False,
                         known_types=[t["name"] for t in data["types"]], **profile.preference_limits(vault),
                         resolve=profile.template_resolver(vault, data))
    except profile.ProfileError as e:
        _refuse(as_json, st, f"客戶設定檔讀不懂：{e.message}")
        return 3
    md = vault / profile.MD_POSIX
    old = md.read_text(encoding="utf-8") if md.is_file() else None
    turn.backup(vault, profile.MD_POSIX, turn.current(vault)["turn"])
    kio.write_text_atomic(md, text)
    code = _apply_locked(vault, as_json, conversation)
    if code != 0 and old is not None:
        kio.write_text_atomic(md, old)
    return code


# ---------- note 改設定檔：show／add／remove（D-144） ----------

PENDING = "設定檔有你改過還沒套用的地方（{summary}），先套用再改"
PENDING_ASK = "設定檔有你改過還沒套用的地方（{summary}），要先套用嗎？"
PENDING_ERROR = "設定檔有你改過但讀不懂：{error}，改好之前我記不了"
BAD_FIELD = "「{arg}」要寫成 欄=值"
WRITTEN_BUT = "設定檔已改好，但後面沒做完（{why}）；跑 kb profile apply 會補上"
SECTION_HEADS = [(num, title, head) for num, title, head in zip(profile.NUMERALS, profile.TITLES, profile.TABLE_HEADS)]


class Refused(Exception):
    """add／remove／type 的拒絕：line 是給人看的那句，payload 是 --json 多帶的欄。"""

    def __init__(self, line, payload=None):
        super().__init__(line)
        self.line, self.payload = line, payload or {}


def _pending_edit(st):
    """md 有手改未套用（D-110 補）：{"summary", "error"}；沒有回 None。只差空白、沒有內容差異的不算（寫了也不會蓋掉什麼）。"""
    if st["state"] == "error":
        return {"summary": None, "error": st["error"]}
    if st["state"] == "changed" and profile.has_diff(st["diff"]):
        return {"summary": profile.summary_short(st["diff"]), "error": None}
    return None


def _result(n, label, do=(), strict=False):
    """plan.cmd 給不出引號時是 None：那一項不給指令、帶 note（同 plan._result）。"""
    from kb._lib import plan
    do = list(do)
    if any(d is None for d in do):
        return {"n": n, "label": label, "do": [], "strict": strict, "fill": [], "note": plan.NO_CMD_NOTE}
    return {"n": n, "label": label, "do": do, "strict": strict, "fill": []}


def _guard_pending(st, again):
    """寫入前的共同關卡：沒設定檔、md 有手改未套用就丟 Refused（不動任何檔）。again 是原本那條指令（kb 開頭）。"""
    if st["state"] == "missing":
        raise Refused(MISSING)
    pend = _pending_edit(st)
    if pend is None:
        return
    if pend["error"]:
        line = PENDING_ERROR.format(error=pend["error"])
        raise Refused(line, {"question": line, "results": [_result(1, "先不記")]})
    raise Refused(PENDING.format(summary=pend["summary"]),
                  {"question": PENDING_ASK.format(summary=pend["summary"]),
                   "results": [_result(1, "先套用我改的，再記這句", ["kb profile apply", again]), _result(2, "先不記")]})


def _limits(vault):
    return profile.preference_limits(vault)


def _show(vault, as_json):
    st = profile.status(vault)
    if st["state"] == "missing":
        print(MISSING)
        return 3
    data = st["data"]
    lim = _limits(vault)
    if as_json:
        titles = profile.card_titles(vault, data)
        print(json.dumps({"sections": profile.sections_schema(data, lim["preference_max"], lim["preference_max_chars"],
                                                              titles),
                          "pending_edit": _pending_edit(st)}, ensure_ascii=False))
        return 0
    lines = []
    titles = profile.card_titles(vault, data)
    for number, (num, title, head) in enumerate(SECTION_HEADS, 1):
        rows = profile.flat_rows(data, number, titles)
        cols = (("序號",) if number == 6 else ()) + head
        lines += [f"## {num}、{title}", profile._row(cols), "|" + "---|" * len(cols)]
        lines += [profile._row([r.get(c, "") for c in cols]) for r in rows]
        lines.append("")
    pend = _pending_edit(st)
    if pend:
        lines.append(PENDING.format(summary=pend["summary"]) if pend["summary"] else PENDING_ERROR.format(error=pend["error"]))
    print("\n".join(lines).rstrip("\n"))
    return 0


def _fields(raw):
    out = {}
    for arg in raw:
        k, sep, v = arg.partition("=")
        if not sep or not k.strip():
            raise Refused(BAD_FIELD.format(arg=arg))
        out[k.strip()] = v
    return out


def _make_dirs(vault, names, turn_id):
    """加型別建夾（D-112）：先全部檢查同名的檔，再建、記 make。回建了的名稱。"""
    for name in names:
        if (vault / name).exists():
            raise Refused(f"「{name}」已經有同名的檔，不能建成型別夾；先改名或搬走")
    made = []
    for name in names:
        (vault / name).mkdir(exist_ok=True)
        turn.record_folder(vault, turn_id, "make", name)
        made.append(name)
    return made


def _edit_locked(vault, op, args, as_json, conversation):
    section = args.section.strip()
    again = cmd("profile", op, section, *args.fields)
    st = profile.status(vault)
    _guard_pending(st, again)
    fields = _fields(args.fields)
    old = st["data"]
    lim = _limits(vault)
    if section == "範本" and op == "add" and str(fields.get("底") or "").strip():
        fields["底"] = _template_card(vault, fields["底"].strip())
    try:
        sections, res = profile.edited(old, section, op, fields, **lim)
        if not res["changed"]:
            _out(as_json, [res["message"]], {"applied": False, "section": section, "op": op, "row": res["row"],
                                            "message": res["message"], "created_dirs": [], "unskipped": 0})
            return 0
        merged = dict(old, **sections)
        new = profile.parse_md(profile.render_text(merged, profile.card_titles(vault, merged)),
                               config.get(vault, "card_title_max_chars"),
                               known_types=[t["name"] for t in old["types"]], **lim,
                               resolve=profile.template_resolver(vault, merged))
    except profile.ProfileError as e:
        raise Refused(e.message) from e
    turn_id = turn.current(vault)["turn"]
    created = _make_dirs(vault, profile.folder_check(vault, new["types"], old["types"])["to_create"], turn_id)
    profile.write_locked(vault, dict(old, **new), turn_id)
    with _written():
        unskipped = inventory.unskip_kinds(vault, profile.unskipped_kinds(old, new), turn_id)
        about = entities.sync_self(vault, new["about"], turn_id)
    message = res["message"] + (f"；建了資料夾 {'、'.join(created)}" if created else "") + \
        (f"；{len(unskipped)} 份回到待製卡" if unskipped else "")
    changed = CHANGED + ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if unskipped else [])
    about_lines = _about_lines(about)
    if about_lines:
        changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
    journal_cli.record_program_or_warn(vault, message, changed, f"profile {op}", conversation)
    _out(as_json, [message] + about_lines, {"applied": True, "section": section, "op": op, "row": res["row"],
                                            "message": message, "created_dirs": created, "unskipped": len(unskipped)})
    return 0


NO_CARD = "找不到這張卡：{base}"


def _template_card(vault, base):
    """kb profile add 範本 的「底」：卡編號或卡標題 → 清冊 active、寫好卡的那張的編號（D-167）；找不到、同名多張丟 Refused。"""
    resolve = profile.template_resolver(vault)
    try:
        return resolve("", base, 0)
    except profile.ProfileError as e:
        line = NO_CARD.format(base=base) if "找不到" in e.message else e.message.split("的以哪份為底", 1)[-1]
        raise Refused(line) from e


class _written:
    """profile.json 已寫入之後的步驟（unskip_kinds、sync_self）失敗（測試後修正裁定 19）：不說「沒有寫入」，
    丟 Refused 帶「設定檔已改好，但後面沒做完…」；apply 沒有內容差異時會補跑這兩步。"""

    def __enter__(self):
        return self

    def __exit__(self, kind, e, _tb):
        if e is None or not isinstance(e, (OSError, ValueError, entities.EntityRefused)):
            return False
        why = "等不到鎖" if isinstance(e, TimeoutError) else str(e)
        # 已寫入：--json 回 applied true、incomplete true（測試第二輪後裁定 28）
        raise Refused(WRITTEN_BUT.format(why=why), {"applied": True, "incomplete": True}) from e


def _out(as_json, lines, payload):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("\n".join(lines))


# ---------- 型別改名、合併、拿掉（D-139、D-143） ----------

AI_DIR = "AI 產出"
NO_TYPE = "型別表沒有「{name}」"


def _type_row(data, name):
    return next((t for t in data["types"] if profile._key(t["name"]) == profile._key(name)), None)


def _need_type(data, name):
    row = _type_row(data, name)
    if row is None:
        raise Refused(NO_TYPE.format(name=name))
    return row


def _members(vault, type_name):
    """這個型別底下 active、不是客戶自己的實體。"""
    return [e for e in entities.load(vault)["entities"]
            if e.get("type") and entities.key(e["type"]) == entities.key(type_name) and e.get("status") == "active"
            and not entities.is_self(e)]


def _file_cards(vault, type_name):
    """原檔在 知識庫/<型別>/ 底下、有檔案卡的份數（型別夾搬了，這些卡跟著搬）。"""
    groups, _broken = cards._scan_groups(vault)
    prefix = f"file://{inventory.KNOWLEDGE}/{type_name}/".casefold()
    return sum(1 for e in inventory.load(vault)["entries"]
               if e.get("status") == "active" and e.get("id") in groups and e.get("locations")
               and str(e["locations"][0].get("uri", "")).casefold().startswith(prefix))


def _impact(vault, row, members, folders, change=None):
    synth = sum(1 for e in members if e.get("card") and entities.locate(vault, e))
    return {"entities": len(members), "file_cards": _file_cards(vault, row["name"]), "synthesis": synth,
            "folders": folders, "category_change": change}


def _counts_text(imp):
    return f"{imp['entities']} 個實體、{imp['file_cards']} 張檔案卡、{imp['synthesis']} 張綜合卡"


def _category_change(vault, src, dst, members):
    """大類不同時的影響（D-143）：事 → 別的，狀態不再顯示（資料留著）、期間日程照留；別的 → 事，沒有狀態的會補「進行中（推測）」。"""
    if src.get("category") == dst.get("category"):
        return None, ""
    cats = profile._REVERSED[0]
    a, b = cats.get(src["category"], ""), cats.get(dst["category"], "")
    hidden = sum(1 for e in members if e.get("progress")) if src["category"] == "matter" else 0
    added = sum(1 for e in members if not e.get("progress")) if dst["category"] == "matter" else 0
    periods = 0
    if src["category"] == "matter":
        names = {entities.key(e["name"]) for e in members}
        try:
            items = agenda.load(vault)["items"]
        except ValueError:
            items = []
        periods = sum(1 for i in items if i.get("kind") == "period" and i.get("status") == "open"
                      and any(entities.key(n) in names for n in i.get("entities") or []))
    change = {"from": a, "to": b, "progress_hidden": hidden, "progress_added": added, "periods": periods}
    head = f"{src['name']}是『{a}』、{dst['name']}是『{b}』，"
    if src["category"] == "matter":
        tail = f"併過去後 {hidden} 個的狀態不再顯示（資料留著）、{periods} 條期間日程照留。"
    elif dst["category"] == "matter":
        tail = f"併過去後 {added} 個會多出狀態『進行中（推測）』。"
    else:
        tail = f"併過去後照『{b}』的段落寫綜合卡，舊的段落照留。"
    return change, head + tail


def _name_refusal(vault, data, old, new):
    max_chars = config.get(vault, "card_title_max_chars")
    why = profile.type_name_problem(new)
    if not why and profile._WINDOWS_RESERVED.match(new):
        why = "Windows 保留名"
    if not why and len(new) > max_chars:
        why = f"太長，最多 {max_chars} 字"
    if why:
        return f"型別名「{new}」不能用（{why}）"
    other = _type_row(data, new)
    if other is not None and other is not old:
        return f"型別「{other['name']}」已經有了，不能改成這個名字；要併在一起用合併"
    if other is old and old["name"] == new:
        return f"型別「{new}」本來就叫這個名字"
    if profile._key(new) != profile._key(old["name"]):
        for rel in (new, f"{AI_DIR}/{new}"):
            if (vault / rel).exists() and (rel == new or (vault / AI_DIR / old["name"]).is_dir()):
                return f"「{rel}」已經有同名的資料夾或檔，沒有改名；先把它搬走"
    keys = {profile._key(w) for a in data.get("about") or [] for w in [a["name"]] + list(a.get("aliases") or [])}
    if profile._key(new) in keys:
        return f"「{new}」是關於你裡的名稱，不能當型別名"
    return None


def _plan_rename(vault, data, name, new):
    row = _need_type(data, name)
    why = _name_refusal(vault, data, row, new)
    if why:
        raise Refused(why)
    members = _members(vault, row["name"])
    folders = [{"from": rel, "to": f"{rel[:-len(row['name'])]}{new}"}
               for rel in (row["name"], f"{AI_DIR}/{row['name']}") if (vault / rel).is_dir()]
    imp = _impact(vault, row, members, folders)
    move = f"，資料夾「{row['name']}」會改成「{new}」" if (vault / row["name"]).is_dir() else ""
    question = f"要把「{row['name']}」改名叫「{new}」嗎？{_counts_text(imp)}會跟著搬{move}。"
    results = [_result(1, f"改名叫{new}", [cmd("profile", "type", "rename", row["name"], "--to", new)]),
               _result(2, "先不要")]
    return row, members, imp, question, results


def _plan_merge(vault, data, name, target):
    row = _need_type(data, name)
    dst = _need_type(data, target)
    if row is dst:
        raise Refused(f"{row['name']}跟{dst['name']}是同一個")
    members = _members(vault, row["name"])
    for e in members:
        if (vault / dst["name"] / e["name"]).exists():
            raise Refused(f"「{dst['name']}」底下已經有「{e['name']}」，沒有合併；先改名或搬走")
    if (vault / AI_DIR / row["name"]).is_dir() and (vault / AI_DIR / dst["name"] / row["name"]).exists():
        raise Refused(f"「{AI_DIR}/{dst['name']}」底下已經有「{row['name']}」，沒有合併；先改名或搬走")
    folders = [{"from": f"{row['name']}/{e['name']}", "to": f"{dst['name']}/{e['name']}"} for e in members
               if (vault / row["name"] / e["name"]).is_dir()]
    if (vault / AI_DIR / row["name"]).is_dir():
        folders.append({"from": f"{AI_DIR}/{row['name']}", "to": f"{AI_DIR}/{dst['name']}/{row['name']}"})
    if (vault / row["name"]).is_dir():
        folders.append({"from": row["name"], "to": None})
    change, sentence = _category_change(vault, row, dst, members)
    imp = _impact(vault, row, members, folders, change)
    question = (f"要把「{row['name']}」併進「{dst['name']}」嗎？{_counts_text(imp)}會跟著搬，「{row['name']}」這一類會拿掉。"
                + sentence)
    results = [_result(1, f"併進{dst['name']}", [cmd("profile", "type", "merge", row["name"], "--into", dst["name"])], True),
               _result(2, "先不要")]
    return row, dst, members, imp, question, results


def _plan_remove(vault, data, name, checking):
    row = _need_type(data, name)
    members = _members(vault, row["name"])
    folder = vault / row["name"]
    folders = [{"from": row["name"], "to": None}] if folder.is_dir() else []
    imp = _impact(vault, row, members, folders)
    if members:
        if not checking:
            raise Refused(f"「{row['name']}」底下還有 {len(members)} 個實體，先併到別的型別")
        others = [t["name"] for t in data["types"] if t is not row]
        results = [_result(i, f"併到{o}", [cmd("profile", "type", "merge", row["name"], "--into", o)], True)
                   for i, o in enumerate(others, 1)]
        results.append(_result(len(results) + 1, "先不刪"))
        return row, imp, f"「{row['name']}」底下還有 {len(members)} 個實體，要併到哪一類？", results
    n = _item_count(folder) if folder.is_dir() else 0
    if n:
        raise Refused(NONEMPTY.format(name=row["name"], n=n))
    results = [_result(1, f"刪掉{row['name']}這一類", [cmd("profile", "type", "remove", row["name"])], True),
               _result(2, "先不刪")]
    return row, imp, f"要刪掉「{row['name']}」這一類嗎？", results


def _item_count(path):
    try:
        return sum(1 for _ in os.scandir(path))
    except OSError:
        return 0


def _check_out(as_json, op, name, target, imp, question, results):
    if as_json:
        print(json.dumps({"op": op, "type": name, "target": target, "impact": imp, "question": question,
                          "results": results, "refusal": None}, ensure_ascii=False))
        return 0
    lines = [f"- {_counts_text(imp)}"]
    lines += [f"- {f['from']} → {f['to']}" if f["to"] else f"- 拿掉 {f['from']}" for f in imp["folders"]]
    lines.append(question)
    lines += [f"{r['n']}. {r['label']}" for r in results]
    print("\n".join(lines))
    return 0


def _move(vault, undo, turn_id, src, dst):
    """整夾搬（os.rename），記進 undo 與它的資料夾帳（做完才寫進回合）。
    目的地的上層夾沒有就建、記 make（AI 產出/<目標>/，測試後修正裁定 18）：退回時夾空了就拿掉。"""
    missing = [p for p in reversed((vault / dst).parents) if vault in p.parents and not p.exists()]
    (vault / dst).parent.mkdir(parents=True, exist_ok=True)
    for p in missing:
        undo.folder(turn_id, "make", p.relative_to(vault).as_posix())
    os.rename(vault / src, vault / dst)
    undo.moved(src, dst)
    undo.folder(turn_id, "move", src, dst)


def _write_profile(vault, data, turn_id, undo):
    undo.file(profile.PROFILE_POSIX)
    undo.file(profile.MD_POSIX)
    profile.write_locked(vault, data, turn_id)
    return list(CHANGED)


def _follow(vault, turn_id, undo):
    """型別夾搬完：清冊照搬動更新（只掃知識庫，同 entity rename）、檔案卡跟著原檔搬（cards.sync）、重寫總目錄。回 (changed, 動到的實體名)。"""
    changed = []
    report = inventory.scan(vault, sources_lib.load(vault), only_roots=[inventory.KNOWLEDGE])
    if inventory.has_changes(vault, report):
        for rel in (inventory.INVENTORY_POSIX, pending.PENDING_POSIX, pending.MD_POSIX):
            undo.file(rel)
        inventory.apply(vault, report, turn_id)
        changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    got = cards.sync(vault, sources_lib.load(vault), turn_id, undo)
    changed += got["changed"]
    if got["moved"] or got["updated"] or got["dropped"]:
        undo.file(cards.INDEX_POSIX)
        cards.render_index(vault, turn_id)
        changed.append({"path": cards.INDEX_POSIX, "action": "modify"})
    return changed, got["touched"]


def _refresh(vault, names, turn_id):
    """最後重渲染受影響實體的綜合卡快取；出錯只記 log（同 cards.refresh_synthesis）。"""
    return [{"path": rel, "action": "modify"} for rel in cards.refresh_synthesis(vault, names, turn_id)]


def _rename_type(vault, data, row, new, members, imp, turn_id):
    old = row["name"]

    def act(undo):
        today = profile._today()
        types = [dict(t, name=new, source="corrected", date=today) if t is row else t for t in data["types"]]
        synth = [dict(s, type=new) if profile._key(s["type"]) == profile._key(old) else s
                 for s in data["synthesis_sections"]]
        changed = _write_profile(vault, dict(data, types=types, synthesis_sections=synth), turn_id, undo)
        # 綜合卡檔頭在搬夾之前改：備份記在舊位置，退回時先把夾改回去再還原，不會在新名底下多出檔
        changed += entities.retype(vault, old, new, turn_id, undo)
        for f in imp["folders"]:
            _move(vault, undo, turn_id, f["from"], f["to"])
        more, touched = _follow(vault, turn_id, undo)
        return changed + more, touched
    changed, touched = entities._guarded(vault, act)
    changed += _refresh(vault, [e["name"] for e in members] + touched, turn_id)
    return f"已把型別「{old}」改名叫「{new}」：{imp['entities']} 個實體、{imp['file_cards']} 張檔案卡跟著搬；不對可以說退回", changed


def _merge_type(vault, data, row, dst, members, imp, turn_id):
    src = row["name"]

    def act(undo):
        changed = []
        for e in members:
            loc = entities.locate(vault, e) if e.get("card") else None
            folder = f"{src}/{e['name']}"
            if not loc and (vault / folder).is_dir():  # 沒有綜合卡的實體夾也整夾搬（rename 只搬有卡的）
                _move(vault, undo, turn_id, folder, f"{dst['name']}/{e['name']}")
            changed += entities.rename(vault, e["id"], e["name"], dst["name"], turn_id, undo=undo)["changed"]
        if (vault / AI_DIR / src).is_dir():
            _move(vault, undo, turn_id, f"{AI_DIR}/{src}", f"{AI_DIR}/{dst['name']}/{src}")
        types = [t for t in data["types"] if t is not row]
        synth = [s for s in data["synthesis_sections"] if profile._key(s["type"]) != profile._key(src)]
        changed += _write_profile(vault, dict(data, types=types, synthesis_sections=synth), turn_id, undo)
        changed += entities.retype(vault, src, dst["name"], turn_id, undo)  # 已移除、已合併的條目也換掉舊型別
        left = _item_count(vault / src) if (vault / src).is_dir() else 0
        if (vault / src).is_dir() and not left:
            os.rmdir(vault / src)
            undo.rmdir(src)
            undo.folder(turn_id, "remove", src)
        more, touched = _follow(vault, turn_id, undo)
        return changed + more, touched, left
    changed, touched, left = entities._guarded(vault, act)
    changed += _refresh(vault, [e["name"] for e in members] + touched, turn_id)
    tail = f"；「{src}」夾裡還有 {left} 項，夾留著" if left else ""
    return (f"已把「{src}」併進「{dst['name']}」：{imp['entities']} 個實體、{imp['file_cards']} 張檔案卡跟著搬；"
            f"「{src}」這一類拿掉了{tail}"), changed


def _remove_type(vault, data, row, turn_id):
    name = row["name"]

    def act(undo):
        types = [t for t in data["types"] if t is not row]
        synth = [s for s in data["synthesis_sections"] if profile._key(s["type"]) != profile._key(name)]
        changed = _write_profile(vault, dict(data, types=types, synthesis_sections=synth), turn_id, undo)
        if (vault / name).is_dir():
            os.rmdir(vault / name)
            undo.rmdir(name)
            undo.folder(turn_id, "remove", name)
        return changed
    return f"已拿掉型別「{name}」", entities._guarded(vault, act)


def _type_locked(vault, args, as_json, conversation):
    op, name = args.type_command, args.name.strip()
    target = (args.to if op == "rename" else args.into if op == "merge" else None)
    target = target.strip() if target else None
    flag = {"rename": "--to", "merge": "--into"}.get(op)
    again = cmd("profile", "type", op, name, *((flag, target) if flag else ()))
    st = profile.status(vault)
    _guard_pending(st, again)
    data = st["data"]
    if op == "rename":
        row, members, imp, question, results = _plan_rename(vault, data, name, target)
    elif op == "merge":
        row, dst, members, imp, question, results = _plan_merge(vault, data, name, target)
    else:
        row, imp, question, results = _plan_remove(vault, data, name, args.check)
    if args.check:
        return _check_out(as_json, op, row["name"], target, imp, question, results)
    turn_id = turn.current(vault)["turn"]
    if op == "rename":
        message, changed = _rename_type(vault, data, row, target, members, imp, turn_id)
    elif op == "merge":
        message, changed = _merge_type(vault, data, row, dst, members, imp, turn_id)
    else:
        message, changed = _remove_type(vault, data, row, turn_id)
    journal_cli.record_program_or_warn(vault, message, changed, f"profile type {op}", conversation)
    _out(as_json, [message], {"applied": True, "op": op, "type": row["name"], "target": target, "message": message,
                              "impact": imp})
    return 0


def _refuse_payload(as_json, e, check=False, extra=None):
    if as_json:
        payload = dict({"applied": False, "message": e.line}, **(extra or {}))
        if check:
            payload = dict(extra or {}, refusal=e.line, results=[])
        payload.update(e.payload)
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(e.line)
    return 3


def _locked(vault, fn, *a):
    with profile.lock(vault):
        return fn(vault, *a)


def run(args):
    vault = find_vault(args)
    if vault is None:
        print(NOT_IN_VAULT)
        return 3
    kio.LOCK_TIMEOUT = CLI_LOCK_TIMEOUT
    as_json = getattr(args, "json", False)
    command = args.profile_command
    try:
        if command == "check":
            return _check(vault, as_json)
        if command == "show":
            return _show(vault, as_json)
        conversation = journal_cli.in_conversation(vault)
        if command in ("add", "remove"):
            try:
                return _locked(vault, _edit_locked, command, args, as_json, conversation)
            except Refused as e:
                return _refuse_payload(as_json, e)
        if command == "type":
            try:
                return _locked(vault, _type_locked, args, as_json, conversation)
            except Refused as e:
                check = args.check and not e.payload
                return _refuse_payload(as_json, e, check=check,
                                       extra={"op": args.type_command, "type": args.name} if check else None)
        return _apply(vault, as_json, conversation, getattr(args, "from_md", None))
    except entities.EntityRefused as e:  # 型別合併時逐個實體換型別的拒絕（含還原失敗的 PartialWrite）
        print(str(e))
        return 3
    except TimeoutError:
        print(LOCK_BUSY)
        return 3
    except ValueError as e:  # profile.json 或清冊壞了
        print(f"狀態檔讀不到：{e}")
        return 3
    except OSError as e:
        print(f"沒有寫入：{type(e).__name__}: {e}")
        return 3
