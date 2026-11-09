"""清單 6（原 2、3，D-054）：版本與骨架。她改過、新版這個檔也改了還沒處理完的骨架檔（CLAUDE.md 這類）→ 待處理一件
（要留她的還是換回程式的；新版沒改這個檔她自己改的、或已經 --keep 的不問，D-528）；沒有骨架紀錄的舊知識庫不判。存的 schema 比現在舊的狀態檔幾個、
舊範本的卡幾張是數字（遷移由升級做）。"""
import hashlib
from pathlib import Path

from kb._lib import cards, migrate, skeleton
from kb.lint import runner

NAME = "版本與骨架"


def run(ctx):
    out = []
    now = skeleton.hashes()
    rec = skeleton.recorded(ctx.vault)
    if rec is None and (Path(ctx.vault) / skeleton.SKELETON_JSON).exists():  # 壞了：判不出她改了沒，算沒查成（T-28）
        raise ValueError(f"{skeleton.SKELETON_JSON} 讀不到")
    # 沒有骨架紀錄是 0.21.0 之前建的庫：跳過比對，數字照出（第三輪 T-35）
    for rel in skeleton.files() if rec is not None else []:
        p = Path(ctx.vault) / rel
        want = rec["files"].get(rel)
        if not want or not p.is_file():
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if sha == want or want == now.get(rel) or skeleton.known(rel, sha):  # want == now：新版沒改這個檔，留她的不問（同 reconcile，D-528）
            continue
        out.append(runner.finding(
            "versions", "decide", f"skeleton:{rel}:{sha[:12]}",
            f"你改過「{rel}」，跟程式的版本不同；要留你的，還是換回程式的？",
            data={"file": rel, "results": [{"label": "換回程式的", "do": [f"kb upgrade skeleton {rel} --replace"],
                                            "note": "她那份會備進回合，說「退回」就回來"},
                                           {"label": "留我的", "do": [f"kb upgrade skeleton {rel} --keep"],
                                            "note": "她的留著，之後升級不再問"}]},
            once=True))
    old_template = sum(1 for c in ctx.cards().values() if str(c["front"].get("範本版本")) != cards.TEMPLATE_VERSION)
    out.append(runner.finding("versions", "number", "versions", "",
                              data={"old_schema": len(migrate.pending(ctx.vault)), "old_template_cards": old_template}))
    return out
