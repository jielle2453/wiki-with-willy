"""kb profile check／apply（客戶設定檔與型別第 2 點，D-110～D-113）與 show／add／remove／type（note 改規則第 2 點，D-144）。

check：系統/客戶設定檔.md 跟 .system/profile.json 比，印差異與型別夾對帳，不寫任何檔；讀不懂 exit 3，其餘 0。
apply：check 的判斷之後，刪了有東西的型別夾那列擋（D-058）；沒有改不寫；否則先建缺的型別夾（只建不刪、建夾不備份；
建不成就整份不套用，測試後修正裁定 11）→ merge → 兩檔備進回合後寫（D-074）→ 表少了的型別、夾是空的就一起拿掉（確認後修正裁定 21）
→ 第三節拿掉的「不整理」種類把清冊的 skip_kind 拿掉（清冊先備份）→ 第五節關於你對實體清單（entities.sync_self，先備份）。
建的夾與拿掉的空夾記進回合的資料夾帳（D-142），退回時改得回來。
apply --from <路徑>（build 第 4 點，D-052）：事先填好的設定檔 md 先照 apply 同一支解析驗過（讀不懂回節與列、什麼都不寫），
再把 系統/客戶設定檔.md 備進回合、換成那份，照 apply 那條路做；apply 拒絕（exit 3）就把原本的 md 寫回去。
show：八節的表（--json 給每節的欄、能填什麼、主鍵、列與指令範本，加 pending_edit）。只讀。
add／remove：note 在對話裡改一節的一列（profile.edited）→ render_text → parse_md（跟 apply 同一支驗證）→ 跟 apply 同一條路寫；
md 有手改未套用時一律拒絕、不動檔、給結果清單（D-110 補）；偏好的條數與字數上限在 edited 擋（D-137）。
type rename／merge／remove：會搬夾的型別操作（D-139、D-143）；--check 只算影響、回問題與結果清單，不寫任何檔；
type rename 的舊名不在型別表、只在檔案卡的 待補型別 裡（build 桌面版確認 A 段：抽樣時說「案子改叫工程」）：新名不在型別表就
帶 --category 加成新型別（沒帶回問題問大類），那些卡的待補型別換成新名、備進回合；之後整理照 types_added 把卡掛上實體。
不帶 --check 才做，中途出錯照 entities._Undo 還原，夾的動作做完才記進資料夾帳。
範本（produce 第 6 點，D-167）：add 範本 文件=<名> 底=<卡編號或卡標題> 先把底換成清冊 active 的卡編號，找不到拒絕。
標籤（標籤字典，D-233～D-237；正本在實體清單，D-505）：add 標籤 加欄或加詞、remove 標籤拒絕；加了欄或詞，那個型別每個 active 實體
記一筆健檢的「補標籤（…）」要更新（lint.state.add_fix；有綜合卡記卡路徑，沒有的記實體編號，裁定 4）。tag rename／merge／remove
改字典的詞或欄，先改正本（entities.retag_entities，by program），再改每張卡檔頭那一行（synthesis.retag），影響照正本數；
先 --check、做了照 entities._Undo 倒回、卡、實體清單與設定檔都備進回合。apply 遇到手改拿掉有實體用到的詞或欄就拒絕，
回結果清單指到 apply --rename-tag（同一欄正好一出一進）或 apply --drop-tags（實作疑義 2）。
數字欄（第九節）、記帳（第十節）與第五節的平常在哪，#65 整個拿掉：add／remove 數字欄 直接拒絕，設定檔沒有這幾處。
鎖 profile.json 30 秒。終端機跑的記一段工作日誌程式段（D-085），對話裡叫的併進回合。
"""
import argparse
import json
import os

from kb import journal_cli
from kb._lib import agenda, card_base, cards, config, entities, inventory, pending, profile, streams, synthesis, turn
from kb._lib import io as kio
from kb._lib.areas import HOLD_DIR
from kb._lib import sources as sources_lib
from kb._lib.plan import cmd
from kb.inventory_cli import NOT_IN_VAULT, find_vault

CLI_LOCK_TIMEOUT = 30
MISSING = "還沒有客戶設定檔，跑 kb init 補"
LOCK_BUSY = "等不到鎖，沒有寫入"
NO_CHANGE = "客戶設定檔沒有改"
NOT_APPLIED = "客戶設定檔沒有改，沒有套用"
NO_CONTENT_DIFF = "客戶設定檔有改，但沒有內容上的差異"
PENDING_TYPE = ("型別「{type}」還有 {n} 個實體要換型別：{names}；照使用者的話一個一個跑 kb entity rename <名稱> --to <名稱> "
                "--type <新型別>（看不出來才問），換完這個型別自動拿掉。")
PENDING_SHOWN = 8
APPLY_ERROR = "設定檔讀不懂：{error}；照錯誤用 Edit 改好再跑 kb profile apply，改不好就 kb versions 退回上一版。"
CHANGED = [{"path": profile.PROFILE_POSIX, "action": "modify"}, {"path": profile.MD_POSIX, "action": "modify"}]


def add_arguments(sub):
    subs = sub.add_subparsers(dest="profile_command", metavar="<子子命令>", required=True)
    ck = subs.add_parser("check", help="看客戶設定檔改了什麼", description="設定檔 md 跟 JSON 比，印差異與型別夾對帳；不寫檔。")
    ap = subs.add_parser("apply", help="套用客戶設定檔", description="把改過的設定檔套進 JSON、建缺的型別夾；寫前先備份。")
    ap.add_argument("--from", dest="from_md", help="事先填好的設定檔 md（建置那天用）：先換成它再套用")
    ap.add_argument("--drop-tags", action="store_true", help="手改拿掉的標籤詞或欄，卡上一起拿掉（apply 拒絕之後用）")
    ap.add_argument("--rename-tag", nargs=4, action="append", metavar=("型別", "欄", "舊詞", "新詞"),
                    help="手改把一個標籤詞改名，卡上一起換（apply 拒絕之後用，可以重複）")
    sh = subs.add_parser("show", help="看客戶設定檔八節", description="印八節的表；--json 給欄位、能填什麼與指令範本。不寫檔。")
    ad = subs.add_parser("add", help="加或改一列", description="加一列；主鍵已有就改那一列。寫前先備份。")
    ad.add_argument("--check", action="store_true",
                    help="只有偏好：不寫檔，回跟同組＋全部每一條的比對、相似的、加了會不會超過上限")
    ad.add_argument("--whose", help="只有偏好：全隊或名字（多人時預設是這台的人，單人不帶）")
    stp = subs.add_parser("set", help="改設定檔的單一值", description="kb profile set people <N>：幾個人用（2 人以上算多人）。")
    stp.add_argument("key", choices=("people",), help="people＝幾個人用")
    stp.add_argument("value", help="值")
    rm = subs.add_parser("remove", help="拿掉一列", description="照主鍵拿掉一列。寫前先備份。")
    for p in (ad, rm):
        p.add_argument("section", help="節：型別、卡型、不整理與敏感、每類段落、關於你、偏好、範本、標籤")
        p.add_argument("fields", nargs="*", help="欄=值（例：偏好=回答先講結論）")
    ty = subs.add_parser("type", help="型別改名、合併、拿掉", description="會搬資料夾的型別操作；先 --check 看影響。")
    tys = ty.add_subparsers(dest="type_command", metavar="<動作>", required=True)
    tr = tys.add_parser("rename", help="型別改名", description="型別改名：資料夾、實體、綜合卡、檔案卡一起改。")
    tr.add_argument("--to", required=True, help="新名")
    tr.add_argument("--category", help="舊名只在檔案卡的待補型別裡、新名要加成新型別時的大類：對象、事、物、主題")
    tm = tys.add_parser("merge", help="型別併進另一個", description="型別併進另一個：實體與資料夾搬過去，這一類拿掉。")
    tm.add_argument("--into", required=True, help="併進哪個型別")
    td = tys.add_parser("remove", help="拿掉型別", description="拿掉沒有實體、資料夾是空的型別。")
    for p in (tr, tm, td):
        p.add_argument("name", help="型別")
        p.add_argument("--check", action="store_true", help="只算影響、不動手")
    tg = subs.add_parser("tag", help="標籤字典的詞或欄改名、合併、拿掉", description="改第八節的字典，綜合卡檔頭跟著改；先 --check 看影響。")
    tgs = tg.add_subparsers(dest="tag_command", metavar="<動作>", required=True)
    gr = tgs.add_parser("rename", help="詞或欄改名", description="給了詞是詞改名，沒給詞是欄改名；每張卡那一行跟著改。")
    gr.add_argument("--to", required=True, help="新名")
    gm = tgs.add_parser("merge", help="詞併進另一個詞", description="前一個詞拿掉，卡上換成後一個，重複的只留一個。")
    gm.add_argument("--into", required=True, help="併進哪個詞")
    gd = tgs.add_parser("remove", help="拿掉詞或整欄", description="給了詞是拿掉那個詞，沒給詞是拿掉整欄；卡上一起拿掉。")
    for p in (gr, gm, gd):
        p.add_argument("type", help="型別")
        p.add_argument("field", help="欄名")
        p.add_argument("word", nargs=None if p is gm else "?", help="詞")
        p.add_argument("--check", action="store_true", help="只算影響、不動手")
    for p in (ck, ap, sh, ad, rm, stp, tr, tm, td, gr, gm, gd):
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
        why = f"還有 {nonempty[n]} 個實體，先留著、換完型別自動拿掉" if n in nonempty else "空夾會一起拿掉"
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
    new_prefs = {x["text"]: x for x in new["preferences"]}
    lines += [f"- 偏好 {n} 改成用在{new_prefs[n]['use']}" for n in d["preferences"]["changed"]]
    lines += [f"- 範本 多了 {n}" for n in d["templates"]["added"]]
    lines += [f"- 範本 少了 {n}" for n in d["templates"]["removed"]]
    lines += [f"- 範本 {n} 換了底" for n in d["templates"]["changed"]]
    new_tags = {(t["type"], t["field"]): t for t in new.get("tags") or []}
    lines += [f"- 標籤 {t}的{f} 多了（{'、'.join(new_tags[(t, f)]['words'])}）" for t, f in d["tags"]["added"]]
    lines += [f"- 標籤 {t}的{f} 少了" for t, f in d["tags"]["removed"]]
    lines += [f"- 標籤 {t}的{f} 改了" for t, f in d["tags"]["changed"]]
    return lines


def _pending_lines(pend):
    out = []
    for p in pend:
        names = p["entities"]
        shown = "、".join(names[:PENDING_SHOWN]) + (f"等 {len(names)} 個" if len(names) > PENDING_SHOWN else "")
        out.append(PENDING_TYPE.format(type=p["type"], n=len(names), names=shown))
    return out


def _public(st):
    return {"state": st["state"], "diff": st["diff"], "error": st["error"], "unlisted": st["unlisted"],
            "removed_empty": st["removed_empty"], "removed_nonempty": [list(x) for x in st["removed_nonempty"]],
            "pending_types": st["pending_types"],
            "to_create": st["to_create"], "renamed": st.get("renamed") or [],
            "renames": [{"from": a, "to": b} for a, b in st.get("moves") or []]}


def check_lines(st):
    """回 (行, exit code)。"""
    if st["state"] == "missing":
        return [MISSING], 0
    if st["state"] == "error":
        return [f"客戶設定檔讀不懂：{st['error']}"], 3
    renamed = profile.renamed_line(st.get("renamed"))  # 她改了型別夾名：跟開場同一句，不說沒有改（裁定 8）
    if st["state"] == "ok":
        return [renamed or NO_CHANGE], 0
    if not profile.has_change(st):
        return [renamed or NO_CONTENT_DIFF], 0
    summary = profile.summary_full(st["diff"]) or ORDER_SUMMARY
    return [f"客戶設定檔有改：{summary}"] + _detail_lines(st) + _move_lines(st), 0


ORDER_SUMMARY = "型別順序"


def _move_lines(st):
    """套用時型別夾會怎麼改名（D-247、D-248）：先講給她聽，說好才 apply。"""
    lines = [f"- 型別夾 {a} → {b}" for a, b in sorted(st.get("moves") or [])]
    if st.get("renamed"):
        lines.append(profile.renamed_line(st["renamed"], with_change=True))
    return lines


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
    """表少了一列、夾是空的：寫完設定檔後一起拿掉（確認後修正裁定 21）；rmdir 失敗（剛好被佔用）就留著。回 (拿掉的, 留著的)。
    names 是夾名（型別夾編號：照 folder_check 的 removed_dirs）。"""
    removed, kept = [], []
    for name in names:
        path = vault / name
        if not path.is_dir() or _item_count(path):  # 夾裡還有別的檔：留著當使用者的夾（D-307(2)），不提
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


def _apply_locked(vault, as_json, conversation, drop_tags=False, rename_tags=()):
    st = profile.status(vault)
    if st["state"] == "missing":
        _refuse(as_json, st, MISSING)
        return 3
    if st["state"] == "error":
        _refuse(as_json, st, APPLY_ERROR.format(error=st["error"]))
        return 3
    try:
        tag_plan = _tag_plan(vault, st, drop_tags, rename_tags)
    except Refused as e:
        if as_json:
            print(json.dumps(dict(_public(st), applied=False, created_dirs=[], unskipped=0, message=e.line, **e.payload),
                             ensure_ascii=False))
        else:
            print(e.line)
        return 3
    if st["refusal"]:  # 型別夾要改名卻撞到別的夾（D-247）
        _refuse(as_json, st, st["refusal"])
        return 3
    if st["state"] == "ok" and (st["renamed"] or st["moves"] or st["to_create"]):
        return _apply_dirs_only(vault, st, as_json, conversation)
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
    blocked = [n for n in st["to_create"] if (vault / n).exists()]
    if blocked:
        _refuse(as_json, st, f"「{blocked[0]}」已經有同名的檔，不能建成型別夾；先改名或搬走")
        return 3
    created = _create_dirs(vault, st, as_json)
    if created is None:
        return 3
    old, new = st["data"], st["new"]
    turn_id = turn.current(vault)["turn"]
    for name in created:  # 建的夾記進回合（D-142）：退回時空的就拿掉
        turn.record_folder(vault, turn_id, "make", name)
    merged = profile.merge(old, new, first=old.get("applied") is None)
    merged["types"] = _with_plan_dirs(merged["types"], st["plan_types"])
    pend = st["pending_types"]
    merged["types"] = profile.with_pending(merged["types"], old["types"], [p["type"] for p in pend])
    data = dict(old, **merged, applied=profile.now())
    try:
        retagged, dir_changed, touched = _write_with_tag_plan(vault, data, tag_plan, turn_id, st["moves"],
                                                              _renamed_pairs(st))
    except Refused as e:
        _refuse(as_json, st, e.line)
        return 3
    if dir_changed or touched:
        dir_changed += _refresh(vault, touched + _moved_names(vault, old, list(st["moves"]) + _renamed_pairs(st)),
                                turn_id)
    removed_dirs, kept_dirs = _remove_empty_dirs(vault, [st["removed_dirs"].get(n, n) for n in st["removed_empty"]])
    for name in removed_dirs:
        turn.record_folder(vault, turn_id, "remove", name)
    try:
        with _written():
            kinds = set(profile.unskipped_kinds(old, merged)) | _stale_skip_kinds(vault, merged)
            unskipped = inventory.unskip_kinds(vault, kinds, turn_id)
            about = entities.sync_self(vault, merged["about"], turn_id)
            marked = _mark_retag(vault, _retag_reasons(old.get("tags"), merged["tags"], tag_plan["renamed_to"]), turn_id)
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
    parts += _dir_lines(st, with_created=False)
    if unskipped:
        parts.append(f"{len(unskipped)} 份回到待製卡")
    if retagged:
        parts.append(f"{len(retagged)} 張綜合卡的標籤跟著改")
    if marked:
        parts.append(_marked_text(marked))
    pending_lines = _pending_lines(pend)
    body = "；".join(parts) if summary else "沒有內容上的差異，重寫了設定檔" + "".join("；" + p for p in parts)
    sentence = f"已套用客戶設定檔：{body}"
    changed = CHANGED + ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if unskipped else [])
    changed += [{"path": rel, "action": "modify"} for rel in retagged] + dir_changed
    if marked:
        changed.append({"path": LINT_POSIX, "action": "modify"})
    if about_lines:
        changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
    journal_cli.record_program_or_warn(vault, sentence[1:], changed, "profile apply", conversation)
    if as_json:
        print(json.dumps(dict(_public(st), applied=True, created_dirs=created, removed_dirs=removed_dirs,
                              unskipped=len(unskipped), about=about, pending_types=pend), ensure_ascii=False))
        return 0
    print(sentence)
    for line in about_lines + pending_lines:
        print(line)
    return 0


def _create_dirs(vault, st, as_json):
    """先建夾再寫檔（測試後修正裁定 11）：建不成就整份不套用、已建的夾留著，回 None；否則回建了的夾名。"""
    created = []
    for name in st["to_create"]:
        try:
            (vault / name).mkdir(exist_ok=True)
        except OSError as e:
            line = f"建不了資料夾「{name}」：{e.strerror or e}；設定檔沒有套用"
            _refuse(as_json, st, line)
            if created and not as_json:
                print("已建的資料夾留著：" + "、".join(created))
            return None
        created.append(name)
    return created


def _with_plan_dirs(types, plan_types):
    """merge 出的型別表（沒有 dir）照 folder_check 算好的每列 dir 補上（比對型別名）。"""
    dirs = {profile._key(t["name"]): t.get("dir") for t in plan_types or []}
    out = []
    for t in types:
        row = {k: v for k, v in t.items() if k != "dir"}
        if dirs.get(profile._key(t["name"])):
            row["dir"] = dirs[profile._key(t["name"])]
        out.append(row)
    return out


def _renamed_pairs(st):
    return [(r["from"], r["to"]) for r in st.get("renamed") or []]


RENAMED_DONE = "照你改的名字記下了：{pairs}"
ADOPTED_DONE = "{old}這個夾改叫 {new}了，跟其他型別放一起"
MOVED_DONE = "型別夾跟著改名：{pairs}"


def _dir_lines(fc, with_created=True):
    """型別夾的動作怎麼說（D-247、D-250）：她改的名字記下了、她自己建的夾收進來、照編號改名、建了的夾。"""
    lines = []
    if fc.get("renamed"):
        lines.append(RENAMED_DONE.format(pairs=profile.renamed_pairs(fc["renamed"])))
    adopted = {profile._key(a) for _t, a, _b in fc.get("adopted") or []}
    lines += [ADOPTED_DONE.format(old=a, new=b) for _t, a, b in fc.get("adopted") or []]
    moves = sorted((a, b) for a, b in fc.get("moves") or [] if profile._key(a) not in adopted)
    if moves:
        lines.append(MOVED_DONE.format(pairs=_arrow(moves)))
    if with_created and fc.get("to_create"):
        lines.append("建了資料夾 " + "、".join(fc["to_create"]))
    return lines


def _dir_work(vault, undo, turn_id, moves, renamed):
    """型別夾改名（照編號）與她自己改好的夾名：搬、路徑跟上、清冊與檔案卡跟上（_follow）。回 (changed, 動到的實體名)。"""
    if not moves and not renamed:
        return [], []
    changed = _move_dirs(vault, undo, turn_id, moves, renamed)
    more, touched = _follow(vault, turn_id, undo)
    return changed + more, touched


def _apply_dirs_only(vault, st, as_json, conversation):
    """設定檔內容沒改、只有型別夾要跟上（她改了夾名、預設的夾不在、編號對不上）：照 folder_check 記 dir、建夾、改名。"""
    blocked = [n for n in st["to_create"] if (vault / n).exists()]
    if blocked:
        _refuse(as_json, st, f"「{blocked[0]}」已經有同名的檔，不能建成型別夾；先改名或搬走")
        return 3
    created = _create_dirs(vault, st, as_json)
    if created is None:
        return 3
    old = st["data"]
    turn_id = turn.current(vault)["turn"]
    for name in created:
        turn.record_folder(vault, turn_id, "make", name)
    data = dict(old, types=st["plan_types"])

    def act(undo):
        changed = _write_profile(vault, data, turn_id, undo)
        more, touched = _dir_work(vault, undo, turn_id, st["moves"], _renamed_pairs(st))
        return changed + more, touched
    changed, touched = entities._guarded(vault, act)
    if touched or st["moves"] or st["renamed"]:
        changed += _refresh(vault, touched + _moved_names(vault, old, list(st["moves"]) + _renamed_pairs(st)), turn_id)
    unskipped = inventory.unskip_kinds(vault, _stale_skip_kinds(vault, old), turn_id)  # 同沒有內容差異那條（裁定 12、19）
    about = entities.sync_self(vault, old.get("about") or [], turn_id)
    if unskipped:
        changed.append({"path": inventory.INVENTORY_POSIX, "action": "modify"})
    if _about_lines(about):
        changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
    lines = _dir_lines(st) + ([f"{len(unskipped)} 份回到待製卡"] if unskipped else []) + _about_lines(about)
    journal_cli.record_program_or_warn(vault, "；".join(lines), changed, "profile apply", conversation)
    if as_json:
        print(json.dumps(dict(_public(st), applied=True, created_dirs=created, removed_dirs=[], unskipped=0,
                              message=lines[0] if lines else ""), ensure_ascii=False))
    else:
        print("\n".join(lines))
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
    keep = set(profile.no_card_kinds(data)) | {cards.SKIP_AI}  # AI 判斷不在設定檔裡，不算殘留
    return {e["skip_kind"] for e in inventory.load(vault)["entries"] if e.get("skip_kind")} - keep


def _apply(vault, as_json, conversation, from_md=None, drop_tags=False, rename_tags=()):
    with profile.lock(vault):
        if from_md is None:
            return _apply_locked(vault, as_json, conversation, drop_tags, rename_tags)
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
        _refuse(as_json, st, APPLY_ERROR.format(error="檔案不是 UTF-8"))
        return 3
    except OSError as e:
        _refuse(as_json, st, FROM_UNREADABLE.format(e.strerror or e))
        return 3
    data = st["data"]
    try:
        stored = profile.stored_schema(vault)
        profile.fill_missing(profile.parse_md(text, config.get(vault, "card_title_max_chars"),
                                              legacy=stored if stored in profile.SECTIONS_OF else False,
                                              known_types=[t["name"] for t in data["types"]], **profile.preference_limits(vault),
                                              resolve=profile.template_resolver(vault, data),
                                              uses=profile.known_uses(vault, data)),
                             data)
    except profile.ProfileError as e:
        _refuse(as_json, st, APPLY_ERROR.format(error=e.message))
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
    if st["state"] == "changed" and profile.has_change(st):
        return {"summary": profile.summary_short(st["diff"]) or ORDER_SUMMARY, "error": None}
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
        conflicts = [c for c in streams.conflicts_of(vault, "profile") if c["field"] != "md_sha256"]  # 機器算的欄不列（#45）
        print(json.dumps({"sections": profile.sections_schema(data, titles, profile.use_choices(vault), **lim),
                          "pending_edit": _pending_edit(st), "conflicts": conflicts}, ensure_ascii=False))
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


def _first_payload(first):
    return {"applied_first": True, "applied_first_summary": first["summary"]} if first else {}


def _apply_first(vault, pend, conversation):
    """profile add 遇手改（D-307(4)）：先照 apply 套用手改（同一回合、同一把鎖）；apply 失敗才回錯誤。回 {"summary", "lines"}。"""
    import contextlib
    import io
    if pend["error"]:
        raise Refused(APPLY_ERROR.format(error=pend["error"]))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = _apply_locked(vault, False, conversation)
    if code != 0:
        raise Refused(buf.getvalue().strip())
    return {"summary": pend["summary"], "lines": buf.getvalue().strip().splitlines()}


FIELDS_GONE = "數字欄這一節拿掉了，不用記；她講到的數字照一般口述記（kb statement add）"


def _edit_locked(vault, op, args, as_json, conversation):
    section = args.section.strip()
    if section == "數字欄":
        raise Refused(FIELDS_GONE)
    again = cmd("profile", op, section, *args.fields)
    st = profile.status(vault)
    first = None
    if op == "add" and _pending_edit(st) is not None:
        first = _apply_first(vault, _pending_edit(st), conversation)
        st = profile.status(vault)
    else:
        _guard_pending(st, again)
    fields = _fields(args.fields)
    old = st["data"]
    lim = _limits(vault)
    if section == "範本" and op == "add" and str(fields.get("底") or "").strip():
        fields["底"] = _template_card(vault, fields["底"].strip())
    try:
        sections, res = profile.edited(old, section, op, fields, uses=profile.use_choices(vault), **lim)
        if not res["changed"]:
            _out(as_json, (first["lines"] if first else []) + [res["message"]],
                 dict({"applied": False, "section": section, "op": op, "row": res["row"],
                       "message": res["message"], "created_dirs": [], "unskipped": 0}, **_first_payload(first)))
            return 0
        merged = dict(old, **sections)
        new = profile.parse_md(profile.render_text(merged, profile.card_titles(vault, merged)),
                               config.get(vault, "card_title_max_chars"),
                               known_types=[t["name"] for t in old["types"]], **lim,
                               resolve=profile.template_resolver(vault, merged), uses=profile.known_uses(vault, old))
        profile.fill_missing(new, merged)
    except profile.ProfileError as e:
        raise Refused(getattr(e, "plain", None) or e.message) from e  # 第八節照欄位講（裁定 21）
    profile.carry_whose(new["preferences"], sections["preferences"])
    turn_id = turn.current(vault)["turn"]
    for t in sections["types"]:  # 夾名= 的夾名，render／parse 帶不過來，這裡接回去
        if t.get("dir_given"):
            for n in new["types"]:
                if profile._key(n["name"]) == profile._key(t["name"]):
                    n["dir"], n["dir_given"] = t["dir"], True
    fc = profile.folder_check(vault, new["types"], old["types"])
    if fc["refusal"]:
        raise Refused(fc["refusal"])
    created = _make_dirs(vault, fc["to_create"], turn_id)
    data = dict(old, **dict(new, types=fc["plan_types"]))
    moves, renamed = fc["moves"], _renamed_pairs(fc)
    if moves or renamed:  # 型別夾跟著改名（D-247、D-248、D-250）：跟寫設定檔同一個 _Undo
        def act(undo):
            changed = _write_profile(vault, data, turn_id, undo)
            more, touched = _dir_work(vault, undo, turn_id, moves, renamed)
            return changed + more, touched
        dir_changed, touched = entities._guarded(vault, act)
        dir_changed += _refresh(vault, touched + _moved_names(vault, old, list(moves) + renamed), turn_id)
    else:
        profile.write_locked(vault, data, turn_id)
        dir_changed = []
    with _written():
        unskipped = inventory.unskip_kinds(vault, profile.unskipped_kinds(old, new), turn_id)
        about = entities.sync_self(vault, new["about"], turn_id)
        marked = _mark_retag(vault, _retag_reasons(old.get("tags"), new["tags"]), turn_id) if section == "標籤" else {}
    if section == "型別" and "序號" in fields and moves:  # 調順序（D-248）：講哪些夾改了名
        message = f"{res['message']}：{_arrow(sorted(moves))}；不對可以說退回"
    else:
        message = res["message"] + "".join("；" + x for x in _dir_lines(fc, with_created=False))
    message += (f"；建了資料夾 {'、'.join(created)}" if created else "") + \
        (f"；{len(unskipped)} 份回到待製卡" if unskipped else "") + (f"；{_marked_text(marked)}" if marked else "")
    changed = CHANGED + dir_changed + ([{"path": inventory.INVENTORY_POSIX, "action": "modify"}] if unskipped else [])
    if marked:
        changed.append({"path": LINT_POSIX, "action": "modify"})
    about_lines = _about_lines(about)
    if about_lines:
        changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
    journal_cli.record_program_or_warn(vault, message, changed, f"profile {op}", conversation)
    _out(as_json, (first["lines"] if first else []) + [message] + about_lines,
         dict({"applied": True, "section": section, "op": op, "row": res["row"],
               "message": message, "created_dirs": created, "unskipped": len(unskipped),
               "retag": sum(sum(v.values()) for v in marked.values())}, **_first_payload(first)))
    return 0


CHECK_ONLY_PREFERENCE = "--check 只有偏好能用：kb profile add 偏好 偏好=<那句> [用在哪=<值>] --check"
ORDER_QUESTION = "要把{name}排到第 {n} 個嗎？資料夾會改名：{pairs}。"
ORDER_NO_MOVE = "要把{name}排到第 {n} 個嗎？資料夾不用改名。"


def _type_order_check(vault, args, as_json):
    """kb profile add 型別 名稱=<型別> 序號=<n> --check（D-248）：不寫檔，回會改名的夾、問題與結果清單（同 type rename --check）。"""
    again = cmd("profile", "add", "型別", *args.fields)
    st = profile.status(vault)
    _guard_pending(st, again)
    fields = _fields(args.fields)
    old = st["data"]
    lim = _limits(vault)
    try:
        sections, res = profile.edited(old, "型別", "add", fields, uses=profile.use_choices(vault), **lim)
    except profile.ProfileError as e:
        raise Refused(e.message) from e
    if not res["changed"]:
        _out(as_json, [res["message"]], {"renames": [], "question": None, "results": [], "message": res["message"]})
        return 0
    fc = profile.folder_check(vault, sections["types"], old["types"])
    if fc["refusal"]:
        raise Refused(fc["refusal"])
    moves = sorted(fc["moves"])
    row = next(t for t in sections["types"] if profile._key(t["name"]) == profile._key(fields["名稱"]))
    n = sections["types"].index(row) + 1
    question = (ORDER_QUESTION.format(name=row["name"], n=n, pairs=_arrow(moves)) if moves
                else ORDER_NO_MOVE.format(name=row["name"], n=n))
    results = [_result(1, f"把{row['name']}排第 {n} 個", [again]), _result(2, "先不要")]
    payload = {"renames": [{"from": a, "to": b} for a, b in moves], "question": question, "results": results}
    lines = [f"- {a} → {b}" for a, b in moves] + [question] + [f"{r['n']}. {r['label']}" for r in results]
    _out(as_json, lines, payload)
    return 0
CHECK_WOULD = {"add": "會加成一條", "change": "會改第 {n} 條", "exists": "這句已經有了，不會加"}


def _preference_check(vault, args, as_json):
    """kb profile add 偏好 … --check（D-504 決策 4）：不鎖、不寫；句子或用在哪不能收照 add 的拒絕句。"""
    if args.section.strip() != "偏好":
        raise Refused(CHECK_ONLY_PREFERENCE)
    fields = {k: v.strip() for k, v in _fields(args.fields).items()}
    data = profile.status(vault)["data"]
    if data is None:
        raise Refused(MISSING)
    lim = _limits(vault)
    number = None
    if "序號" in fields:
        prefs = data.get("preferences") or []
        try:
            number = int(fields["序號"])
        except ValueError:
            number = 0
        if not 1 <= number <= len(prefs):
            raise Refused(f"沒有第 {fields['序號']} 條偏好（現在 {len(prefs)} 條）")
        old = prefs[number - 1]
        fields.setdefault("偏好", old["text"])
        fields.setdefault("用在哪", old.get("use") or profile.USE_ALL)
    text, use = fields.get("偏好", ""), fields.get("用在哪") or profile.USE_ALL
    if not text:
        raise Refused("少了「偏好」；這一節要給 偏好")
    why = profile.preference_problem(text) or (
        "偏好" + profile.use_problem(use, profile.use_choices(vault)) if profile.use_problem(use, profile.use_choices(vault))
        else None)
    if why:
        raise Refused(why)
    got = profile.preference_check(data, text, use, number, config.get(vault, "preference_similar_ratio"), **lim)
    lines = [f"{CHECK_WOULD[got['would']].format(n=number)}（用在哪：{got['use']}）；這組 {got['group_count']}／"
             f"{got['group_max']} 條、{got['group_chars']}／{got['group_max_chars']} 字"
             + ("，超過上限" if got["over_limit"] else "")]
    lines += [f"{c['序號']}. {c['偏好']}（{c['用在哪']}）相似 {c['similar']}" + ("　← 很像" if c["序號"] in got["similar"] else "")
              for c in got["compare"]]
    _out(as_json, lines, got)
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

AI_DIR = HOLD_DIR  # 暫存區底下的分類夾照型別名、不編號（D-251、D-324）
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


def _file_cards(vault, folder):
    """原檔在 知識庫/<型別夾>/ 底下、有檔案卡的份數（型別夾搬了，這些卡跟著搬）。"""
    groups, _broken = cards._scan_groups(vault)
    prefix = f"file://{inventory.KNOWLEDGE}/{folder}/".casefold()
    return sum(1 for e in inventory.load(vault)["entries"]
               if e.get("status") == "active" and e.get("id") in groups and e.get("locations")
               and str(e["locations"][0].get("uri", "")).casefold().startswith(prefix))


def _impact(vault, row, members, folders, change=None):
    synth = sum(1 for e in members if e.get("card") and entities.locate(vault, e))
    return {"entities": len(members), "file_cards": _file_cards(vault, _folder(row)), "synthesis": synth,
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


def _renamed_dir(row, new):
    """型別改名後的夾名：預設名字跟著換、編號不變（01 案子 → 01 工程）；她自訂的不動；升級前的（沒有 dir）是新型別名。"""
    d = row.get("dir")
    if not d:
        return new
    return f"{d[:2]} {new}" if profile.is_default_dir(row["name"], d) else d


def _name_refusal(vault, data, old, new):
    max_chars = config.get(vault, "card_title_max_chars") - profile.DIR_PREFIX_CHARS
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
        folder = _renamed_dir(old, new)
        if profile._key(folder) != profile._key(_folder(old)) and (vault / folder).exists():
            return f"「{folder}」已經有同名的資料夾或檔，沒有改名；先把它搬走"
        rel = f"{AI_DIR}/{new}"
        if (vault / rel).exists() and (vault / AI_DIR / old["name"]).is_dir():
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
    folder, to = _folder(row), _renamed_dir(row, new)
    folders = [{"from": folder, "to": to}] if folder != to and (vault / folder).is_dir() else []
    if (vault / AI_DIR / row["name"]).is_dir():  # AI 產出底下的分類夾照型別名、不編號（D-251）
        folders.append({"from": f"{AI_DIR}/{row['name']}", "to": f"{AI_DIR}/{new}"})
    imp = _impact(vault, row, members, folders)
    move = f"，資料夾「{folder}」會改成「{to}」" if folder != to and (vault / folder).is_dir() else ""
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
    src_dir, dst_dir = _folder(row), _folder(dst)
    for e in members:
        if (vault / dst_dir / e["name"]).exists():
            raise Refused(f"「{dst_dir}」底下已經有「{e['name']}」，沒有合併；先改名或搬走")
    if (vault / AI_DIR / row["name"]).is_dir() and (vault / AI_DIR / dst["name"] / row["name"]).exists():
        raise Refused(f"「{AI_DIR}/{dst['name']}」底下已經有「{row['name']}」，沒有合併；先改名或搬走")
    _merged_tags(data, row["name"], dst["name"])  # 第八節併過去超過詞數上限先擋（裁定 6）
    folders = [{"from": f"{src_dir}/{e['name']}", "to": f"{dst_dir}/{e['name']}"} for e in members
               if (vault / src_dir / e["name"]).is_dir()]
    if (vault / AI_DIR / row["name"]).is_dir():
        folders.append({"from": f"{AI_DIR}/{row['name']}", "to": f"{AI_DIR}/{dst['name']}/{row['name']}"})
    if (vault / src_dir).is_dir():
        folders.append({"from": src_dir, "to": None})
    renumber = _renumber(vault, data, [t for t in data["types"] if t is not row])
    folders += [{"from": a, "to": b} for a, b in renumber["moves"]]
    change, sentence = _category_change(vault, row, dst, members)
    imp = _impact(vault, row, members, folders, change)
    question = (f"要把「{row['name']}」併進「{dst['name']}」嗎？{_counts_text(imp)}會跟著搬，「{row['name']}」這一類會拿掉。"
                + sentence + (f"後面的夾會補號：{_arrow(renumber['moves'])}。" if renumber["moves"] else ""))
    results = [_result(1, f"併進{dst['name']}", [cmd("profile", "type", "merge", row["name"], "--into", dst["name"])], True),
               _result(2, "先不要")]
    return row, dst, members, imp, question, results, renumber


def _renumber(vault, data, types):
    """拿掉或併掉一個型別之後，後面還是預設名字的夾往前補號（D-247）：dir_plan 的結果；撞名丟 Refused。"""
    plan = profile.dir_plan(vault, types, data["types"])
    if plan["refusal"]:
        raise Refused(plan["refusal"])
    return plan


def _plan_remove(vault, data, name, checking):
    row = _need_type(data, name)
    members = _members(vault, row["name"])
    folder = vault / _folder(row)
    folders = [{"from": _folder(row), "to": None}] if folder.is_dir() else []
    renumber = _renumber(vault, data, [t for t in data["types"] if t is not row])
    folders += [{"from": a, "to": b} for a, b in renumber["moves"]]
    imp = _impact(vault, row, members, folders)
    if members:  # 還有實體：標 pending_remove，換完型別自動拿掉（D-307(2)）；後面的夾不補號
        renumber = _renumber(vault, data, list(data["types"]))
        others = [t["name"] for t in data["types"] if t is not row]
        results = [_result(i, f"併到{o}", [cmd("profile", "type", "merge", row["name"], "--into", o)], True)
                   for i, o in enumerate(others, 1)]
        results.append(_result(len(results) + 1, "先不刪"))
        return row, imp, f"「{row['name']}」底下還有 {len(members)} 個實體，要併到哪一類？", results, renumber
    if folder.is_dir() and _item_count(folder):  # 夾裡剩別的檔：夾留著當使用者的夾，後面的夾不補號
        renumber = _renumber(vault, data, list(data["types"]))
    results = [_result(1, f"刪掉{row['name']}這一類", [cmd("profile", "type", "remove", row["name"])], True),
               _result(2, "先不刪")]
    tail = f"後面的夾會補號：{_arrow(renumber['moves'])}。" if renumber["moves"] else ""
    return row, imp, f"要刪掉「{row['name']}」這一類嗎？{tail}", results, renumber


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
    card_base.rekey_folder(vault, src, dst)  # 夾底下的卡紀錄跟著換鍵（手改卡，D-270）


def _folder(row):
    """型別表一列的夾名（型別夾編號）：有 dir 用 dir，沒有（升級前）用型別名。"""
    return row.get("dir") or row["name"]


def _arrow(moves):
    return "、".join(f"{a} → {b}" for a, b in moves)


def _move_dirs(vault, undo, turn_id, moves, renamed=()):
    """照 dir_plan 改型別夾的名字（D-247、D-248）：目標剛好是另一個要改的夾現在的名字時先改成暫名再改回，不撞名；
    每一步經 _move（記資料夾帳、退回改得回來）。renamed 是她自己在檔案總管改好的 [(從, 到)]（不搬，只讓路徑跟上）。
    lint.json 的要更新綜合卡與 batches.json 裡指到舊夾的路徑跟著換。回 changed（日誌用）。"""
    sources = {profile._key(a) for a, _b in moves}
    later = []
    for i, (a, b) in enumerate(moves):
        if profile._key(a) != profile._key(b) and profile._key(b) in sources:
            tmp = f".kb-{i}-{b}"
            _move(vault, undo, turn_id, a, tmp)
            later.append((tmp, b))
        else:
            _move(vault, undo, turn_id, a, b)
    for tmp, b in later:
        _move(vault, undo, turn_id, tmp, b)
    return _follow_paths(vault, list(moves) + list(renamed), turn_id, undo)


def _follow_paths(vault, moves, turn_id, undo):
    """型別夾換了名字：lint.json 的 synthesis_fix 與 batches.json 裡「舊夾/…」的路徑換成「新夾/…」（寫前備進回合）。"""
    from kb._lib import batches
    from kb.lint import state
    mapping = [(a, b) for a, b in moves if a != b]
    if not mapping:
        return []
    changed = []
    if (vault / LINT_POSIX).is_file():
        undo.file(LINT_POSIX)
        if state.follow_dirs(vault, mapping, turn_id):
            changed.append({"path": LINT_POSIX, "action": "modify"})
    if (vault / batches.BATCHES_POSIX).is_file():
        undo.file(batches.BATCHES_POSIX)
        if batches.follow_dirs(vault, mapping, turn_id):
            changed.append({"path": batches.BATCHES_POSIX, "action": "modify"})
    return changed


def _moved_names(vault, data, moves):
    """夾改了名的型別底下的實體名稱（綜合卡快取要跟著重渲染）。data 是改名之前的設定檔。"""
    froms = {profile._key(a) for a, _b in moves}
    types = {profile._key(t["name"]) for t in data["types"] if profile._key(_folder(t)) in froms}
    try:
        ents = entities.load(vault)["entities"]
    except ValueError:
        return []
    return [e["name"] for e in ents if e.get("status") == "active" and e.get("type")
            and profile._key(e["type"]) in types]


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
        moved = dict(row, name=new, source="corrected", date=today)
        if row.get("dir"):
            moved["dir"] = _renamed_dir(row, new)  # 預設名字跟著換、編號不變；她自訂的不動（D-247）
        types = [moved if t is row else t for t in data["types"]]
        synth = [dict(s, type=new) if profile._key(s["type"]) == profile._key(old) else s
                 for s in data["synthesis_sections"]]
        tags = [dict(t, type=new) if profile._key(t["type"]) == profile._key(old) else t for t in data.get("tags") or []]
        changed = _write_profile(vault, dict(data, types=types, synthesis_sections=synth, tags=tags), turn_id, undo)
        # 綜合卡檔頭在搬夾之前改：備份記在舊位置，退回時先把夾改回去再還原，不會在新名底下多出檔
        changed += entities.retype(vault, old, new, turn_id, undo)
        for f in imp["folders"]:
            _move(vault, undo, turn_id, f["from"], f["to"])
        changed += _follow_paths(vault, [(f["from"], f["to"]) for f in imp["folders"]
                                         if not f["from"].startswith(AI_DIR + "/")], turn_id, undo)
        more, touched = _follow(vault, turn_id, undo)
        return changed + more, touched
    changed, touched = entities._guarded(vault, act)
    changed += _refresh(vault, [e["name"] for e in members] + touched, turn_id)
    return f"已把型別「{old}」改名叫「{new}」：{imp['entities']} 個實體、{imp['file_cards']} 張檔案卡跟著搬；不對可以說退回", changed


TAGS_TOO_MANY = "併過去之後「{type}／{field}」會有 {n} 個詞，超過 {max} 個；先拿掉一些再併"


def _merged_tags(data, src, dst):
    """型別合併時的第八節（裁定 6）：來源型別的列併進目標——同名欄併詞（目標的在前）、可多選任一有勾就勾，
    目標沒有的欄整列帶過去（型別換成目標）；卡上的標籤行照留就驗得過。併完超過 TAG_WORDS_MAX 個詞丟 Refused。"""
    out = [dict(t) for t in data.get("tags") or [] if profile._key(t["type"]) != profile._key(src)]
    for t in data.get("tags") or []:
        if profile._key(t["type"]) != profile._key(src):
            continue
        hit = next((x for x in out if profile._key(x["type"]) == profile._key(dst)
                    and profile._key(x["field"]) == profile._key(t["field"])), None)
        if hit is None:
            out.append(dict(t, type=dst))
            continue
        have = {profile._key(w) for w in hit["words"]}
        hit["words"] = list(hit["words"]) + [w for w in t["words"] if profile._key(w) not in have]
        hit["multi"] = bool(hit.get("multi") or t.get("multi"))
        if len(hit["words"]) > profile.TAG_WORDS_MAX:
            raise Refused(TAGS_TOO_MANY.format(type=dst, field=hit["field"], n=len(hit["words"]),
                                               max=profile.TAG_WORDS_MAX))
    return out


def _merge_type(vault, data, row, dst, members, imp, turn_id, renumber):
    src = row["name"]
    src_dir, dst_dir = _folder(row), _folder(dst)

    def act(undo):
        changed = []
        for e in members:
            loc = entities.locate(vault, e) if e.get("card") else None
            folder = f"{src_dir}/{e['name']}"
            if not loc and (vault / folder).is_dir():  # 沒有綜合卡的實體夾也整夾搬（rename 只搬有卡的）
                _move(vault, undo, turn_id, folder, f"{dst_dir}/{e['name']}")
            changed += entities.rename(vault, e["id"], e["name"], dst["name"], turn_id, undo=undo)["changed"]
        if (vault / AI_DIR / src).is_dir():
            _move(vault, undo, turn_id, f"{AI_DIR}/{src}", f"{AI_DIR}/{dst['name']}/{src}")
        synth = [s for s in data["synthesis_sections"] if profile._key(s["type"]) != profile._key(src)]
        tags = _merged_tags(data, src, dst["name"])
        changed += _write_profile(vault, dict(data, types=renumber["types"], synthesis_sections=synth, tags=tags), turn_id, undo)
        changed += entities.retype(vault, src, dst["name"], turn_id, undo)  # 已移除、已合併的條目也換掉舊型別
        left = _item_count(vault / src_dir) if (vault / src_dir).is_dir() else 0
        if (vault / src_dir).is_dir() and not left:
            os.rmdir(vault / src_dir)
            undo.rmdir(src_dir)
            undo.folder(turn_id, "remove", src_dir)
        changed += _follow_paths(vault, [(f"{src_dir}/{e['name']}", f"{dst_dir}/{e['name']}") for e in members],
                                 turn_id, undo)
        changed += _move_dirs(vault, undo, turn_id, renumber["moves"])  # 後面的預設名字往前補號（D-247）
        more, touched = _follow(vault, turn_id, undo)
        return changed + more, touched, left
    changed, touched, left = entities._guarded(vault, act)
    changed += _refresh(vault, [e["name"] for e in members] + touched + _moved_names(vault, data, renumber["moves"]),
                        turn_id)
    tail = f"；「{src_dir}」夾裡還有 {left} 項，夾留著" if left else ""
    tail += f"；後面的夾補號：{_arrow(renumber['moves'])}" if renumber["moves"] else ""
    return (f"已把「{src}」併進「{dst['name']}」：{imp['entities']} 個實體、{imp['file_cards']} 張檔案卡跟著搬；"
            f"「{src}」這一類拿掉了{tail}"), changed


def _remove_type(vault, data, row, turn_id, renumber):
    name, folder = row["name"], _folder(row)
    members = _members(vault, name)
    if members:  # 還有實體：只標 pending_remove
        types = [dict(t, pending_remove=True) if t["name"] == name else dict(t) for t in data["types"]]
        entities._guarded(vault, lambda undo: _write_profile(vault, dict(data, types=types), turn_id, undo))
        pend = [{"type": name, "entities": [m["name"] for m in members]}]
        return (f"型別「{name}」先留著（還有 {len(members)} 個實體）；" + _pending_lines(pend)[0]), list(CHANGED)
    keep_dir = (vault / folder).is_dir() and bool(_item_count(vault / folder))
    if keep_dir:  # 夾裡剩別的檔：夾留著，後面的夾不補號
        renumber = dict(renumber, types=[t for t in renumber["types"] if profile._key(t["name"]) != profile._key(name)],
                        moves=[])

    def act(undo):
        synth = [s for s in data["synthesis_sections"] if profile._key(s["type"]) != profile._key(name)]
        tags = [t for t in data.get("tags") or [] if profile._key(t["type"]) != profile._key(name)]
        changed = _write_profile(vault, dict(data, types=renumber["types"], synthesis_sections=synth, tags=tags), turn_id, undo)
        if (vault / folder).is_dir() and not keep_dir:
            os.rmdir(vault / folder)
            undo.rmdir(folder)
            undo.folder(turn_id, "remove", folder)
        if not renumber["moves"]:
            return changed, []
        changed += _move_dirs(vault, undo, turn_id, renumber["moves"])  # 後面的預設名字往前補號（D-247）
        more, touched = _follow(vault, turn_id, undo)
        return changed + more, touched
    changed, touched = entities._guarded(vault, act)
    if renumber["moves"]:
        changed += _refresh(vault, touched + _moved_names(vault, data, renumber["moves"]), turn_id)
        return f"已拿掉型別「{name}」；後面的夾補號：{_arrow(renumber['moves'])}", changed
    return f"已拿掉型別「{name}」", changed


def _type_locked(vault, args, as_json, conversation):
    op, name = args.type_command, args.name.strip()
    target = (args.to if op == "rename" else args.into if op == "merge" else None)
    target = target.strip() if target else None
    flag = {"rename": "--to", "merge": "--into"}.get(op)
    again = cmd("profile", "type", op, name, *((flag, target) if flag else ()))
    st = profile.status(vault)
    _guard_pending(st, again)
    data = st["data"]
    if op == "rename" and _type_row(data, name) is None and cards.pending_type_cards(vault, name):
        return _pending_rename(vault, data, name, target, (args.category or "").strip() or None, args.check, as_json,
                               conversation)
    if op == "rename":
        row, members, imp, question, results = _plan_rename(vault, data, name, target)
    elif op == "merge":
        row, dst, members, imp, question, results, renumber = _plan_merge(vault, data, name, target)
    else:
        row, imp, question, results, renumber = _plan_remove(vault, data, name, args.check)
    if args.check:
        return _check_out(as_json, op, row["name"], target, imp, question, results)
    turn_id = turn.current(vault)["turn"]
    if op == "rename":
        message, changed = _rename_type(vault, data, row, target, members, imp, turn_id)
    elif op == "merge":
        message, changed = _merge_type(vault, data, row, dst, members, imp, turn_id, renumber)
    else:
        message, changed = _remove_type(vault, data, row, turn_id, renumber)
    journal_cli.record_program_or_warn(vault, message, changed, f"profile type {op}", conversation)
    payload = {"applied": True, "op": op, "type": row["name"], "target": target, "message": message, "impact": imp}
    if op == "remove" and _type_row(profile.load(vault) or {}, row["name"]) is not None:
        payload["pending_types"] = [{"type": row["name"], "entities": [m["name"] for m in _members(vault, row["name"])]}]
    _out(as_json, [message], payload)
    return 0


def settle_pending(vault, turn_id, old_type):
    """entity rename --type 換掉最後一個實體之後：型別列標了 pending_remove 就在同一回合拿掉（夾空了跟著拿掉）。
    回句子；沒事回 None。"""
    data = profile.load(vault)
    row = _type_row(data or {}, old_type) if old_type else None
    if not row or not row.get("pending_remove") or _members(vault, row["name"]):
        return None
    with profile.lock(vault):
        data = profile.load(vault)
        row = _type_row(data, old_type)
        if not row:
            return None
        renumber = {"types": [t for t in data["types"] if t is not row], "moves": []}
        message, _changed = _remove_type(vault, data, row, turn_id, renumber)
    return message


PENDING_CATEGORY = "「{old}」只在 {n} 張檔案卡的待補型別裡、型別表沒有；「{new}」要加成新型別，先說它是哪一大類"
PENDING_QUESTION = "「{new}」是哪一大類？（「{old}」只在 {n} 張檔案卡的待補型別裡，改名後這幾張會換成「{new}」、之後整理掛上實體）"
PENDING_DONE = "已把 {n} 張檔案卡的待補型別「{old}」換成「{new}」{added}；下次整理會把這些卡掛上「{new}」的實體，不對可以說退回"


def _pending_rename(vault, data, old, new, category, checking, as_json, conversation):
    """型別名只在檔案卡的待補型別裡（build 桌面版確認 A 段）：新名不在型別表就照 --category 加一列，卡的待補型別換成新名。
    整段照 entities._guarded 做（中途出錯倒回），卡與設定檔都備進回合，說退回改得回來。"""
    pend = cards.pending_type_cards(vault, old)
    row_new = _type_row(data, new)
    if row_new is None and not category:
        results = [_result(i, f"{new}是{c}", [cmd("profile", "type", "rename", old, "--to", new, "--category", c)])
                   for i, c in enumerate(profile.CATEGORIES, 1)]
        results.append(_result(len(results) + 1, "先不要"))
        raise Refused(PENDING_CATEGORY.format(old=old, n=len(pend), new=new),
                      {"question": PENDING_QUESTION.format(old=old, n=len(pend), new=new), "results": results})
    if row_new is None and category not in profile.CATEGORIES:
        raise Refused(f"大類要是 {'、'.join(profile.CATEGORIES)} 之一")
    imp = {"entities": 0, "file_cards": len(pend), "synthesis": 0, "folders": [], "category_change": None}
    added = "" if row_new is not None else f"，「{new}」加進型別表（{category}）"
    if checking:
        do = ["profile", "type", "rename", old, "--to", new] + (["--category", category] if row_new is None else [])
        question = f"要把 {len(pend)} 張檔案卡待補型別的「{old}」換成「{new}」嗎？" + (added.lstrip("，") + "。" if added else "")
        return _check_out(as_json, "rename", old, new, imp, question, [_result(1, f"改名叫{new}", [cmd(*do)]),
                                                                        _result(2, "先不要")])
    turn_id = turn.current(vault)["turn"]
    plan = {}

    def act(undo):
        changed = []
        if row_new is None:
            lim = _limits(vault)
            sections, _res = profile.edited(data, "型別", "add", {"名稱": new, "大類": category}, **lim)
            merged = dict(data, **sections)
            try:
                parsed = profile.parse_md(profile.render_text(merged, profile.card_titles(vault, merged)),
                                          config.get(vault, "card_title_max_chars"),
                                          known_types=[t["name"] for t in data["types"]], **lim,
                                          resolve=profile.template_resolver(vault, merged))
                profile.fill_missing(parsed, merged)
            except profile.ProfileError as e:
                raise Refused(e.message) from e
            plan.update(profile.folder_check(vault, parsed["types"], data["types"]))
            if plan["refusal"]:
                raise Refused(plan["refusal"])
            changed += _write_profile(vault, dict(data, **dict(parsed, types=plan["plan_types"])), turn_id, undo)
            changed += _dir_work(vault, undo, turn_id, plan["moves"], _renamed_pairs(plan))[0]
        for rel in pend:
            undo.file(rel)
            turn.backup(vault, rel, turn_id)
            path = vault / rel
            kio.write_text_atomic(path, cards.rewrite_pending_types(path.read_text(encoding="utf-8"), old, new))
            changed.append({"path": rel, "action": "modify"})
        return changed
    changed = entities._guarded(vault, act)
    if row_new is None:  # 型別夾（同 profile add 型別，有編號），建了記資料夾帳，退回時空的拿掉
        _make_dirs(vault, [d for d in plan.get("to_create") or [] if not (vault / d).exists()], turn_id)
    message = PENDING_DONE.format(n=len(pend), old=old, new=new, added=added)
    journal_cli.record_program_or_warn(vault, message, changed, "profile type rename", conversation)
    _out(as_json, [message], {"applied": True, "op": "rename", "type": old, "target": new, "message": message,
                              "impact": imp})
    return 0


# ---------- 標籤字典：補標籤、改字典、手改拿掉（D-236、D-237，實作疑義 2） ----------

LINT_POSIX = ".system/lint.json"
TAG_REASON = "補標籤"
NO_FIELD = "第八節沒有「{field}」這個標籤欄；有：{have}"
NO_WORD = "「{field}」的字典沒有「{word}」；有：{have}"
WORD_TAKEN = "「{to}」已經在「{field}」的字典裡；要併在一起用 {cmd}"
FIELD_TAKEN = "「{type}」已經有「{to}」這個標籤欄"
SAME_WORD = "「{word}」跟「{to}」是同一個"
SAME_NAME = "「{word}」本來就叫這個名字"
LAST_WORD = "「{field}」只剩「{word}」這個詞；要拿掉整欄用 {cmd}"
HAND_DROP = "第八節拿掉了「{type}／{field}」的「{word}」，有 {n} 個{type}用到"
HAND_DROP_FIELD = "第八節拿掉了「{type}／{field}」這一欄，有 {n} 個{type}用到"
HAND_NOT_APPLIED = "沒有套用"
HAND_QUESTION = "{line}：是改名、要從卡上拿掉，還是要併到別的詞？"
HAND_QUESTION_MANY = "第八節拿掉了 {n} 個有用到的詞或欄：是改名、要從卡上拿掉，還是要併到別的詞？"
HAND_SINGLE = "{type}／{field} 有 {n} 個標了兩個以上的詞；要改成單選得先拿掉多的"
HAND_REVERT_NOTE = "用 Edit 改設定檔第 8 節（拿掉的詞或欄那一格改回原樣），再跑 kb profile apply；然後照使用者的話跑 kb profile tag merge 或 rename。"
DROP_NONE = "第八節沒有拿掉卡上用到的詞或欄，不用 --drop-tags"
STUCK = "{n} 張綜合卡的標籤行改不了（{names}），這次沒有改；先看那幾張的檔頭再試"
FROM_WITH_TAGS = "apply --from 不能跟 --drop-tags、--rename-tag 一起用"
RENAME_NONE = "--rename-tag {type} {field} {old} {new} 對不上第八節的改動（要拿掉一個有卡用到的詞、多一個詞）"
UNREADABLE_LINE = "另有 {n} 張綜合卡的檔頭讀不懂，卡上那行沒跟著改（實體清單改好了）：{names}"


def _k(name):
    return entities.key(name)


def _tag_row(tags, type_name, field):
    return next((t for t in tags or [] if _k(t["type"]) == _k(type_name) and _k(t["field"]) == _k(field)), None)


def _retag_reasons(old_tags, new_tags, skip=()):
    """第八節加了欄或詞：{型別: [「補標籤（欄）」或「補標籤（欄：詞／詞）」]}；skip 是 --rename-tag 換過去的 (型別, 欄, 詞)（key）。"""
    out = {}
    for t in new_tags or []:
        o = _tag_row(old_tags, t["type"], t["field"])
        if o is None:
            reason = f"{TAG_REASON}（{t['field']}）"
        else:
            have = {_k(w) for w in o.get("words") or []}
            more = [w for w in t["words"] if _k(w) not in have and (_k(t["type"]), _k(t["field"]), _k(w)) not in skip]
            if not more:
                continue
            reason = f"{TAG_REASON}（{t['field']}：{'／'.join(more)}）"
        out.setdefault(t["type"], []).append(reason)
    return out


def _mark_retag(vault, reasons, turn_id):
    """那個型別每個 active 實體記健檢的「補標籤」要更新（D-236、D-505 決策 4）：有綜合卡的記卡路徑，沒有的（掛了檔案卡的）記實體編號
    （指紋是正本 tags，裁定 4）。回 {型別: {"cards": 張數, "entities": 沒有綜合卡的個數}}（都 0 的不列）。"""
    from kb.lint import state
    marked, data, mounts = {}, entities.load(vault), None
    for type_name, rs in reasons.items():
        with_card = {e["id"]: rel for e, rel, _front in synthesis.tagged_cards(vault, type_name, data)}
        for e, _tags in synthesis.tagged_entities(vault, type_name, data):
            rel = with_card.get(e["id"])
            if rel is None:
                mounts = mounts if mounts is not None else entities.mounts(vault, data)
                if not mounts.get(e["name"]):
                    continue
            for r in rs:
                state.add_fix(vault, rel or e["id"], r, [], turn_id)
            slot = marked.setdefault(type_name, {"cards": 0, "entities": 0})
            slot["cards" if rel else "entities"] += 1
    return marked


def _marked_text(marked):
    parts = []
    for t, n in marked.items():
        if n["cards"]:
            parts.append(f"{n['cards']} 張{t}的綜合卡要補標籤")
        if n["entities"]:
            parts.append(f"{n['entities']} 個{t}還沒有綜合卡，也會補標籤")
    return "、".join(parts)


def _word_fn(word, to):
    """標籤行的值裡 word 換成 to（None 是拿掉），重複的只留一個；單選的一個詞照單選寫（拿光寫 []）。"""
    k = _k(word)

    def fn(v):
        out = []
        for w in synthesis.tag_words(v):
            w2 = to if _k(w) == k else w
            if w2 is not None and _k(w2) not in {_k(x) for x in out}:
                out.append(w2)
        return out if isinstance(v, list) else (out[0] if out else [])
    return fn


def _uses(front, field, word=None):
    return _used(synthesis.front_tag(front, field), word)


def _uses_tags(tags, field, word=None):
    """正本 tags 用到這一欄（或這一欄的這個詞）嗎。"""
    return _used(entities.tag_value(tags, field), word)


def _used(v, word):
    if v is None:
        return False
    return word is None or _k(word) in {_k(w) for w in synthesis.tag_words(v)}


def _retag_cards(vault, rels, field, fn, new_field, turn_id, undo):
    """每張卡檔頭 field 那一行照 fn 改（synthesis.retag，其他行與內文位元組不動）；改到的先備進回合（by program）。
    那張卡有健檢記的要更新，照新指紋再記一次（標了補標籤又改字典，記號不掉）。回改到的路徑。"""
    from kb.lint import state
    fixes = {f["path"]: f for f in state.fixes(vault)}
    written, stuck = [], []
    for rel in rels:
        p = vault / rel
        text = p.read_bytes().decode("utf-8")
        new = synthesis.retag(text, field, fn, new_field)
        if new == text:  # 算進影響卻改不了：整個不做（裁定 11），呼叫端的 _Undo 倒回已改的
            stuck.append(rel.split("/")[1] if rel.count("/") == 2 else rel)
            continue
        undo.file(rel)
        turn.backup(vault, rel, turn_id, by="program")
        kio.write_text_atomic(p, new)
        written.append(rel)
        if rel in fixes:
            undo.file(LINT_POSIX)
            state.add_fix(vault, rel, fixes[rel].get("reason"), fixes[rel].get("ids") or [], turn_id)
    if stuck:
        raise Refused(STUCK.format(n=len(stuck), names="、".join(stuck)))
    return written


def _have(rows):
    return "、".join(rows) if rows else "（還沒有）"


def _need_tag(data, type_name, field, word=None):
    _need_type(data, type_name)  # 型別不在型別表先講這個，不說欄不在（B-464 (1)）
    row = _tag_row(data.get("tags"), type_name, field)
    if row is None:
        raise Refused(NO_FIELD.format(field=field, have=_have([t["field"] for t in profile.tags_for(data, type_name)])))
    if word is not None and _k(word) not in {_k(w) for w in row["words"]}:
        raise Refused(NO_WORD.format(field=row["field"], word=word, have=_have(row["words"])))
    hit = next((w for w in row["words"] if word is not None and _k(w) == _k(word)), None)
    return row, hit


def _unreadable(vault, type_name, ids, cards):
    """要改的實體裡，有綜合卡檔、卻不在 tagged_cards（檔頭讀不懂）的名稱（B-464 (2)）：正本照改，卡上那行沒跟著改。
    綜合卡檔在不在照 tagged_cards 的認法。"""
    readable = {e["id"] for e, _rel, _front in cards}
    out = []
    for e, _tags in synthesis.tagged_entities(vault, type_name):
        if e["id"] not in ids or e["id"] in readable:
            continue
        rel = synthesis.card_rel(e, vault)
        if (vault / rel).is_file() or (e.get("card") and entities.locate(vault, e)):
            out.append(e["name"])
    return out


def _unreadable_lines(names):
    return [UNREADABLE_LINE.format(n=len(names), names="、".join(names))] if names else []


def _tag_validated(vault, data, tags):
    """新的第八節照 apply 同一支驗（render_text 後 parse_md）；錯了丟 Refused。回驗過的 tags。"""
    merged = dict(data, tags=tags)
    lim = _limits(vault)
    try:
        parsed = profile.parse_md(profile.render_text(merged, profile.card_titles(vault, merged)),
                                  config.get(vault, "card_title_max_chars"),
                                  known_types=[t["name"] for t in data["types"]], **lim,
                                  resolve=profile.template_resolver(vault, merged))
    except profile.ProfileError as e:
        raise Refused(getattr(e, "plain", None) or e.message) from e
    return [dict(t, source=old.get("source"), date=old.get("date")) if old is not None else t
            for t, old in zip(parsed["tags"], tags)]


def _tag_op_plan(vault, data, args):
    """kb profile tag 的計畫：回 (op, row, 新 tags, 要改的卡 [相對路徑], 要改的實體 [編號], fn, new_field, impact, question, results,
    done)。影響照正本數（D-505 決策 5）：用到的實體，有卡沒卡都算；卡上有那一行的才改卡。"""
    op, tname, field = args.tag_command, args.type.strip(), args.field.strip()
    word = args.word.strip() if args.word else None
    to = (args.to if op == "rename" else args.into if op == "merge" else None)
    to = to.strip() if to else None
    row, hit = _need_tag(data, tname, field, word)
    tname, field = row["type"], row["field"]
    today = profile._today()
    stamped = dict(row, source="corrected", date=today)
    cards = synthesis.tagged_cards(vault, tname)
    ents = synthesis.tagged_entities(vault, tname)
    new_field = None
    both = 0
    if op == "merge":
        _need_tag(data, tname, field, to)
        target = next(w for w in row["words"] if _k(w) == _k(to))
        if _k(target) == _k(hit):
            raise Refused(SAME_WORD.format(word=hit, to=target))
        words = [w for w in row["words"] if _k(w) != _k(hit)]
        used = [rel for _e, rel, f in cards if _uses(f, field, hit)]
        ids = [e["id"] for e, t in ents if _uses_tags(t, field, hit)]
        both = sum(1 for _e, t in ents if _uses_tags(t, field, hit) and _uses_tags(t, field, target))
        fn, to = _word_fn(hit, target), target
        impact_line = f"{tname}有 {len(ids)} 個標了{hit}，其中 {both} 個也標了{target}"
        question = f"{impact_line}；併起來 {len(ids)} 個都只剩{target}。要併嗎？"
        label, strict = f"併成{target}", True
        do = cmd("profile", "tag", "merge", tname, field, hit, "--into", target)
        done = f"已把「{hit}」併進「{target}」：{len(ids)} 個只剩{target}；不對可以說退回"
    elif op == "rename" and hit is not None:
        if _k(to) == _k(hit):
            raise Refused(SAME_NAME.format(word=hit))
        if _k(to) in {_k(w) for w in row["words"]}:
            raise Refused(WORD_TAKEN.format(to=to, field=field, cmd=cmd("profile", "tag", "merge", tname, field, hit,
                                                                           "--into", to)))
        words = [to if _k(w) == _k(hit) else w for w in row["words"]]
        used = [rel for _e, rel, f in cards if _uses(f, field, hit)]
        ids = [e["id"] for e, t in ents if _uses_tags(t, field, hit)]
        fn = _word_fn(hit, to)
        impact_line = f"{tname}有 {len(ids)} 個標了{hit}"
        question = f"{impact_line}；改名後都會改成{to}。要改嗎？"
        label, strict = f"改叫{to}", False
        do = cmd("profile", "tag", "rename", tname, field, hit, "--to", to)
        done = f"已把「{field}」的「{hit}」改叫「{to}」：{len(ids)} 個跟著改；不對可以說退回"
    elif op == "remove" and hit is not None:
        if len(row["words"]) == 1:
            raise Refused(LAST_WORD.format(field=field, word=hit, cmd=cmd("profile", "tag", "remove", tname, field)))
        words = [w for w in row["words"] if _k(w) != _k(hit)]
        used = [rel for _e, rel, f in cards if _uses(f, field, hit)]
        ids = [e["id"] for e, t in ents if _uses_tags(t, field, hit)]
        fn = _word_fn(hit, None)
        impact_line = f"{tname}有 {len(ids)} 個標了{hit}"
        question = f"{impact_line}；拿掉後這些{tname}的{hit}一起拿掉。要拿掉嗎？"
        label, strict = f"拿掉{hit}", True
        do = cmd("profile", "tag", "remove", tname, field, hit)
        done = f"已從「{field}」拿掉「{hit}」：{len(ids)} 個一起拿掉；不對可以說退回"
    elif op == "rename":
        if _k(to) == _k(field):
            raise Refused(SAME_NAME.format(word=field))
        if _tag_row(data.get("tags"), tname, to) is not None:
            raise Refused(FIELD_TAKEN.format(type=tname, to=to))
        words, new_field = row["words"], to
        stamped["field"] = to
        used = [rel for _e, rel, f in cards if _uses(f, field)]
        ids = [e["id"] for e, t in ents if _uses_tags(t, field)]
        fn = lambda v: v  # noqa: E731
        impact_line = f"{tname}有 {len(ids)} 個標了「{field}」"
        question = f"{impact_line}；改名後都會改成「{to}」。要改嗎？"
        label, strict = f"改叫{to}", False
        do = cmd("profile", "tag", "rename", tname, field, "--to", to)
        done = f"已把標籤欄「{field}」改叫「{to}」：{len(ids)} 個跟著改；不對可以說退回"
    else:
        words = None
        used = [rel for _e, rel, f in cards if _uses(f, field)]
        ids = [e["id"] for e, t in ents if _uses_tags(t, field)]
        fn = lambda v: synthesis.DROP  # noqa: E731
        impact_line = f"{tname}有 {len(ids)} 個標了「{field}」"
        question = f"{impact_line}；拿掉後這一欄一起拿掉，卡上那一行也拿掉。要拿掉嗎？"
        label, strict = f"拿掉「{field}」這一欄", True
        do = cmd("profile", "tag", "remove", tname, field)
        done = f"已拿掉標籤欄「{field}」：{len(ids)} 個一起拿掉；不對可以說退回"
    tags = [t for t in data.get("tags") or [] if t is not row] if words is None else \
        [dict(stamped, words=words) if t is row else t for t in data.get("tags") or []]
    if words is not None:
        tags = _tag_validated(vault, data, tags)
    results = [_result(1, label, [do], strict), _result(2, "先不要")]
    impact = {"entities": len(ids), "both": both}
    head = {"op": op, "type": tname, "field": field, "word": hit, "target": to}
    return head, tags, used, ids, fn, new_field, impact, impact_line, question, results, done


def _tag_locked(vault, args, as_json, conversation):
    flag = {"rename": "--to", "merge": "--into"}.get(args.tag_command)
    target = args.to if args.tag_command == "rename" else args.into if args.tag_command == "merge" else None
    again = cmd("profile", "tag", args.tag_command, args.type, args.field, *((args.word,) if args.word else ()),
                *((flag, target) if flag else ()))
    st = profile.status(vault)
    _guard_pending(st, again)
    data = st["data"]
    head, tags, used, ids, fn, new_field, impact, impact_line, question, results, done = _tag_op_plan(vault, data, args)
    unreadable = _unreadable(vault, head["type"], set(ids), synthesis.tagged_cards(vault, head["type"]))
    if args.check:
        if as_json:
            print(json.dumps(dict(head, impact=impact, question=question, results=results, refusal=None,
                                  unreadable=unreadable), ensure_ascii=False))
        else:
            print("\n".join([impact_line] + _unreadable_lines(unreadable) + [f"{r['n']}. {r['label']}" for r in results]))
        return 0
    turn_id = turn.current(vault)["turn"]

    def act(undo):
        changed = _write_profile(vault, dict(data, tags=tags), turn_id, undo)
        if entities.retag_entities(vault, head["type"], head["field"], fn, ids, turn_id, undo, new_field, synthesis.DROP):
            changed.append({"path": entities.ENTITIES_POSIX, "action": "modify"})
        written = _retag_cards(vault, used, head["field"], fn, new_field, turn_id, undo)
        return changed + [{"path": rel, "action": "modify"} for rel in written]
    changed = entities._guarded(vault, act)
    names = [e["name"] for e, rel, _f in synthesis.tagged_cards(vault, head["type"]) if rel in used]
    changed += _refresh(vault, names, turn_id)
    journal_cli.record_program_or_warn(vault, done, changed, f"profile tag {head['op']}", conversation)
    _out(as_json, [done] + _unreadable_lines(unreadable),
         dict(head, applied=True, message=done, impact=impact, unreadable=unreadable))
    return 0


def _tag_plan(vault, st, drop_tags, rename_tags):
    """apply 前的第八節對帳（實作疑義 2）：手改拿掉的詞或欄有卡用到 → 沒給旗標就丟 Refused（帶問題與結果清單）；
    --rename-tag 要對得上「拿掉一個有卡用到的詞、多一個詞」，--drop-tags 要有對應的拿掉。
    回 {"renames": [(型別, 欄, 舊, 新, [卡], [實體])], "drops": [(型別, 欄, 詞或 None, [卡], [實體])],
    "renamed_to": {(型別, 欄, 新詞) key}}。"""
    plan = {"renames": [], "drops": [], "renamed_to": set()}
    old = st["data"].get("tags") or [] if st["data"] else []
    new = (st["new"] or {}).get("tags") or [] if st["state"] == "changed" else old
    _check_single(vault, old, new)
    removals, pairs = _tag_removals(vault, old, new)
    left = list(removals)
    for type_name, field, was, now in rename_tags or ():
        hit = next((r for r in left if r["word"] is not None and _k(r["type"]) == _k(type_name)
                    and _k(r["field"]) == _k(field) and _k(r["word"]) == _k(was)), None)
        row = _tag_row(new, type_name, field)
        old_row = _tag_row(old, type_name, field)
        ok = hit is not None and row is not None and _k(now) in {_k(w) for w in row["words"]} \
            and _k(now) not in {_k(w) for w in old_row["words"]}
        if not ok:
            raise Refused(RENAME_NONE.format(type=type_name, field=field, old=was, new=now))
        word = next(w for w in row["words"] if _k(w) == _k(now))
        plan["renames"].append((hit["type"], hit["field"], hit["word"], word, hit["cards"], hit["entities"]))
        plan["renamed_to"].add((_k(hit["type"]), _k(hit["field"]), _k(word)))
        left.remove(hit)
    if drop_tags:
        if not left:
            raise Refused(DROP_NONE)
        plan["drops"] = [(r["type"], r["field"], r["word"], r["cards"], r["entities"]) for r in left]
        return plan
    if left:
        raise _hand_refusal(left, pairs)
    return plan


def _check_single(vault, old, new):
    """手改拿掉可多選的勾、卡上那一欄已有兩個以上的詞（裁定 7）：丟 Refused，結果清單只有先不套用；旗標也不放過。"""
    for t in new:
        was = _tag_row(old, t["type"], t["field"])
        if t.get("multi") or was is None or not was.get("multi"):
            continue
        n = sum(1 for _e, tags in synthesis.tagged_entities(vault, t["type"])
                if len(synthesis.tag_words(entities.tag_value(tags, t["field"]))) > 1)
        if n:
            line = HAND_SINGLE.format(type=t["type"], field=t["field"], n=n)
            raise Refused(line, {"question": line, "results": [_result(1, "先不套用")]})


def _tag_removals(vault, old, new):
    """手改第八節拿掉、正本有實體用到的（D-505 決策 5）：([{"type","field","word"（整欄是 None）,"entities","cards"}],
    {(型別key, 欄key): (舊詞, 新詞)})；cards 是卡上有那一行、要跟著改的綜合卡。pairs 是同一欄正好拿掉一個詞、多一個詞的欄
    （給 --rename-tag）。"""
    out, pairs, cache = [], {}, {}
    for t in old:
        if t["type"] not in cache:
            cache[t["type"]] = (synthesis.tagged_entities(vault, t["type"]), synthesis.tagged_cards(vault, t["type"]))
        ents, cards = cache[t["type"]]
        row = _tag_row(new, t["type"], t["field"])
        if row is None:
            ids = [e["id"] for e, tags in ents if _uses_tags(tags, t["field"])]
            if ids:
                out.append({"type": t["type"], "field": t["field"], "word": None, "entities": ids,
                            "cards": [rel for _e, rel, f in cards if _uses(f, t["field"])]})
            continue
        keep = {_k(w) for w in row["words"]}
        gone = [w for w in t["words"] if _k(w) not in keep]
        came = [w for w in row["words"] if _k(w) not in {_k(x) for x in t["words"]}]
        if len(gone) == 1 and len(came) == 1:
            pairs[(_k(t["type"]), _k(t["field"]))] = (gone[0], came[0])
        for w in gone:
            ids = [e["id"] for e, tags in ents if _uses_tags(tags, t["field"], w)]
            if ids:
                out.append({"type": t["type"], "field": t["field"], "word": w, "entities": ids,
                            "cards": [rel for _e, rel, f in cards if _uses(f, t["field"], w)]})
    return out, pairs


def _drop_line(r):
    if r["word"] is None:
        return HAND_DROP_FIELD.format(type=r["type"], field=r["field"], n=len(r["entities"]))
    return HAND_DROP.format(type=r["type"], field=r["field"], word=r["word"], n=len(r["entities"]))


def _hand_refusal(left, pairs):
    """手改拿掉有卡用到的詞或欄：一個一句「…；沒有套用」，好幾個一行一個、最後一行「沒有套用」（裁定 8）。"""
    first = left[0]
    if len(left) > 1:
        line = "\n".join([_drop_line(r) for r in left] + [HAND_NOT_APPLIED])
        question = HAND_QUESTION_MANY.format(n=len(left))
        drop_label = ("卡上的這幾個詞一起拿掉，照你改的套用" if all(r["word"] for r in left)
                      else "卡上的這幾個詞與欄一起拿掉，照你改的套用")
        revert_label = "其實是要併到別的詞：先把第八節那幾格改回去，再跟我說要併到哪個"
    elif first["word"] is None:
        line = f"{_drop_line(first)}；{HAND_NOT_APPLIED}"
        question = HAND_QUESTION.format(line=_drop_line(first))
        drop_label = f"卡上的「{first['field']}」這一行一起拿掉，照你改的套用"
        revert_label = f"其實是改名：先把第八節那格改回去，再跟我說「{first['field']}」改叫什麼"
    else:
        line = f"{_drop_line(first)}；{HAND_NOT_APPLIED}"
        question = HAND_QUESTION.format(line=_drop_line(first))
        drop_label = f"卡上的{first['word']}一起拿掉，照你改的套用"
        revert_label = f"其實是要併到別的詞：先把第八節那格改回去，再跟我說{first['word']}併到哪個"
    results = []
    keyed = [pairs.get((_k(r["type"]), _k(r["field"]))) for r in left]
    if all(r["word"] is not None for r in left) and all(keyed):
        flags = []
        for r, (was, now) in zip(left, keyed):
            flags += ["--rename-tag", r["type"], r["field"], was, now]
        was, now = keyed[0]
        results.append(_result(1, f"「{was}」改叫「{now}」，卡上一起換", [cmd("profile", "apply", *flags)]))
    results.append(_result(len(results) + 1, drop_label, [cmd("profile", "apply", "--drop-tags")], True))
    results.append(dict(_result(len(results) + 1, revert_label), note=HAND_REVERT_NOTE))
    results.append(_result(len(results) + 1, "先不套用"))
    return Refused(line, {"question": question, "results": results})


def _write_with_tag_plan(vault, data, plan, turn_id, moves=(), renamed=()):
    """apply 寫設定檔；有 --rename-tag 的換詞、--drop-tags 的拿掉詞或整欄時，正本先改（by program）、卡跟著改，設定檔兩檔、
    實體清單與卡在同一個 _Undo 裡，中途失敗全部倒回（裁定 15）。型別夾要照編號改名、她改的夾名要跟上的（D-247）也在同一個 _Undo。
    回 (改到的卡, 型別夾那段的 changed, 動到的實體名)。"""
    if not plan["renames"] and not plan["drops"] and not moves and not renamed:
        profile.write_locked(vault, data, turn_id)
        return [], [], []

    def act(undo):
        undo.file(profile.PROFILE_POSIX)
        undo.file(profile.MD_POSIX)
        profile.write_locked(vault, data, turn_id)
        written = []
        for type_name, field, was, now, rels, ids in plan["renames"]:
            entities.retag_entities(vault, type_name, field, _word_fn(was, now), ids, turn_id, undo)
            written += _retag_cards(vault, rels, field, _word_fn(was, now), None, turn_id, undo)
        for type_name, field, word, rels, ids in plan["drops"]:
            fn = (lambda v: synthesis.DROP) if word is None else _word_fn(word, None)
            entities.retag_entities(vault, type_name, field, fn, ids, turn_id, undo, drop=synthesis.DROP)
            written += _retag_cards(vault, rels, field, fn, None, turn_id, undo)
        dir_changed, touched = _dir_work(vault, undo, turn_id, moves, renamed)
        return sorted(set(written)), dir_changed, touched
    return entities._guarded(vault, act)


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


PEOPLE_BAD = "幾個人用要寫正整數（例：kb profile set people 3）"


def _set_people_locked(vault, n, as_json, conversation):
    data = profile.load(vault)
    if data is None:
        raise Refused("還沒有設定檔，先建置")
    if data.get("people") == n:
        _out(as_json, [f"幾個人用已經是 {n}"], {"applied": False, "people": n})
        return 0
    turn_id = turn.current(vault)["turn"]
    profile.write_locked(vault, dict(data, people=n), turn_id)
    message = f"幾個人用記成 {n}（{'多人' if n >= 2 else '單人'}）"
    journal_cli.record_program_or_warn(vault, message, CHANGED, "profile set people", conversation)
    _out(as_json, [message], {"applied": True, "people": n, "message": message})
    return 0


def _with_whose(vault, args):
    """偏好的 --whose：給了照給（全隊→team）；多人沒給是這台的人（還沒設名字就全隊）；單人不帶。"""
    from kb._lib import people

    if args.section.strip() != "偏好" or any(f.partition("=")[0].strip() == "誰的" for f in args.fields):
        return
    given = getattr(args, "whose", None)
    if given is not None:
        value = people.parse_whose(given)
        value = value if value == people.TEAM else value[0]
    elif people.multi(vault):
        value = people.me(vault) or people.TEAM
    else:
        return
    if any(f.partition("=")[0].strip() == "序號" for f in args.fields) and given is None:
        return  # 改舊的一條：誰的照舊
    args.fields = list(args.fields) + [f"誰的={value}"]


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
    profile.render_if_missing(vault)  # md 還沒畫先畫，不然說「跑 kb init 補」（#42 問題 12）
    try:
        if command == "check":
            return _check(vault, as_json)
        if command == "show":
            return _show(vault, as_json)
        conversation = journal_cli.in_conversation(vault)
        if command == "set":
            if not (args.value.isdigit() and int(args.value) >= 1):
                print(PEOPLE_BAD)
                return 3
            return _locked(vault, _set_people_locked, int(args.value), as_json, conversation)
        if command in ("add", "remove"):
            if command == "add" and not args.check:
                _with_whose(vault, args)
            try:
                if command == "add" and args.check and args.section.strip() == "型別" and \
                        any(f.partition("=")[0].strip() == "序號" for f in args.fields):
                    return _locked(vault, lambda v: _type_order_check(v, args, as_json))
                if command == "add" and args.check:
                    return _preference_check(vault, args, as_json)
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
        if command == "tag":
            try:
                return _locked(vault, _tag_locked, args, as_json, conversation)
            except Refused as e:
                check = args.check and not e.payload
                return _refuse_payload(as_json, e, check=check,
                                       extra={"op": args.tag_command, "type": args.type, "field": args.field,
                                              "word": args.word} if check else None)
        if getattr(args, "from_md", None) and (getattr(args, "drop_tags", False) or getattr(args, "rename_tag", None)):
            print(FROM_WITH_TAGS)  # 裁定 20
            return 3
        return _apply(vault, as_json, conversation, getattr(args, "from_md", None), getattr(args, "drop_tags", False),
                      [tuple(x.strip() for x in r) for r in getattr(args, "rename_tag", None) or []])
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
