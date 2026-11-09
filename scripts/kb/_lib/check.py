"""--check：先算後果、什麼都不寫（Obsidian 首頁，#22，D-256）。

kb entity progress／rename／merge 與 kb agenda cancel 帶 --check 時，Obsidian 的確認框照這裡算出來的後果句印給客戶看，按好才不帶 --check 真的做。
回 {"effects": [句子…], "refuse": None|句子}；會擋的條件跟真的做一樣（拿同樣的判斷、同樣的拒絕句），refuse 有字時呼叫端 exit 3（--json 照印）。
只讀：不開回合、不鎖檔、不備份。
"""
from kb._lib import agenda, entities

STATE_WORDS = {"closed": "結案", "paused": "擱置", "ongoing": "進行中"}


def emit(got, as_json):
    """印出 --check 的結果：--json 一律印 {"effects","refuse"}；人讀印後果句（有拒絕就印拒絕那句）。refuse 有字 exit 3。"""
    import json
    if as_json:
        print(json.dumps(got, ensure_ascii=False))
    else:
        print(got["refuse"] or "\n".join(got["effects"]))
    return 3 if got["refuse"] else 0


def _refused(e):
    return {"effects": [], "refuse": str(e)}


def progress(vault, ref, state):
    """entity progress <名> 結案|進行中|擱置 --check。state 是 ongoing／closed／paused。"""
    try:
        data = entities.load(vault)
        entry = entities._need_matter(vault, data, ref)
    except (entities.EntityRefused, ValueError) as e:
        return _refused(e)
    name = entry["name"]
    was = entry.get("progress") or "ongoing"
    items = [i for i in agenda.load(vault)["items"] if i.get("status") == "open"
             and any(entities.key(n) == entities.key(name) for n in i.get("entities") or [])]
    periods = [i for i in items if i.get("kind") == "period"]
    others = [i for i in items if i.get("kind") != "period"]
    if state == "closed":
        parts = []
        if periods:
            parts.append("、".join(f"『{i['text']}』" for i in periods) + "這段期間會在今天結束")
        if others:
            parts.append(f"還開著的 {len(others)} 件日程照舊")
        tail = "；".join(parts)
        if was == "closed":
            line = f"{name}本來就是結案"
        else:
            line = f"{name}記成結案" + (f"：{tail}" if tail else "")
    elif state == "paused":
        line = f"{name}記成擱置：日程照舊，時間軸上會畫一段擱置" if was != "paused" else f"{name}本來就是擱置"
    elif was == "closed":
        line = f"{name}改回進行中：已經結束的期間不會重開，要的話再改期"
    else:
        line = f"{name}記成進行中" if was != "ongoing" else f"{name}本來就是進行中"
    return {"effects": [line], "refuse": None}


def rename(vault, ref, new_name, new_type=None):
    """entity rename <名> --to <新名> [--type] --check。"""
    try:
        data = entities.load(vault)
        entry, _hit = entities._need(data, ref)
        entities._need_plain(entry, "改名")
        if new_type and entities.type_of(vault, new_type) is None:
            raise entities.EntityRefused(entities.type_refusal(new_type))
        why = entities.refusal(vault, data, new_name, ignore=entry)
        if why:
            raise entities.EntityRefused(why)
        mounted = len(entities.mounts(vault, data).get(entry["name"], []))
    except (entities.EntityRefused, ValueError) as e:
        return _refused(e)
    old = entry["name"]
    follow = []
    if entry.get("card"):
        follow.append("夾、綜合卡")
    if mounted:
        follow.append(f"{mounted} 張檔案卡")
    line = f"{old}改名叫{new_name}"
    if new_type and entities.key(new_type) != entities.key(entry.get("type")):
        line += f"、型別換成{new_type}"
    line += "：" + ("、".join(follow) + "跟著改，" if follow else "") + "舊名記成別名"
    return {"effects": [line], "refuse": None}


def merge(vault, ref, into):
    """entity merge <來源> --into <目標> --check。"""
    try:
        data = entities.load(vault)
        src, _h1 = entities._need(data, ref)
        dst, _h2 = entities._need(data, into)
        if src.get("id") == dst.get("id"):
            raise entities.EntityRefused(f"「{ref}」跟「{into}」是同一個實體")
        entities._need_plain(src, "合併")
        entities._need_plain(dst, "合併")
        loc = entities.locate(vault, src)
        mounted = len(entities.mounts(vault, data).get(src["name"], []))
    except (entities.EntityRefused, ValueError) as e:
        return _refused(e)
    follow = [f"{src['name']}記成{dst['name']}的別名"]
    if entities.key(src.get("type")) != entities.key(dst.get("type")):
        follow.append(f"型別先換成{dst.get('type')}")
    if mounted:
        follow.append(f"{mounted} 張檔案卡改掛{dst['name']}")
    if loc:
        follow.append(f"{src['name']}的綜合卡會刪掉")
    return {"effects": [f"{src['name']}併進{dst['name']}：" + "、".join(follow) + "；合併之後要分開只能退回"], "refuse": None}


def agenda_cancel(vault, aid):
    """agenda cancel <編號> --check。"""
    try:
        it = agenda.get(vault, aid)
    except (agenda.AgendaRefused, ValueError) as e:
        return _refused(e)
    text = it.get("text")
    if it.get("status") == "cancelled":
        return {"effects": [f"『{text}』本來就取消了"], "refuse": None}
    if it.get("kind") == "period":
        line = f"取消期間『{text}』：時間軸上整段拿掉"
        n = len([i for i in agenda.load(vault)["items"] if i.get("status") == "open" and i.get("id") != aid
                 and i.get("entities") and i.get("entities") == it.get("entities") and i.get("kind") != "period"])
        if n:
            line += f"，同一件事還開著的 {n} 件日程照舊"
    else:
        line = f"取消『{text}』（{agenda.md_of(it['date'])}）"
    return {"effects": [line + "；之後可以重開"], "refuse": None}
