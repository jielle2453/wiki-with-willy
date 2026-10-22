"""B-068：cards sync 之後來源多一份新檔、清冊掃描套用，cards todo 不該列 moved；
B-069：原檔不見、待處理還開著時卡照算掛載，synthesis_stale 不把它列 removed，客戶答「刪了」（cards drop）之後才列（manage 第二版第 6 點）。"""
import shutil
import unittest
from pathlib import Path

from tests import _cards, _entities
from kb._lib import cards, entities, inventory, pending, turn  # noqa: E402
from kb._lib import sources as sources_lib  # noqa: E402


def card_all(V):
    """把 todo 列的 new 全寫成卡（標題用檔名），回寫了幾張。"""
    data = cards.todo(V, sources_lib.load(V))
    for item in data["new"]:
        title = Path(item["uri"]).stem.replace("[", "").replace("]", "")
        _cards.write_card(V, item, title)
    return len(data["new"])


class B068(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def todo_moved(self):
        return cards.todo(self.V, sources_lib.load(self.V))["moved"]

    def test_new_file_after_sync_does_not_list_moved(self):
        self.assertGreater(card_all(self.V), 3)
        self.assertEqual(self.todo_moved(), [])
        # 原檔搬一份 → sync
        src = self.raw / "客戶案件" / "內湖李" / "估價單.pdf"
        dst = self.raw / "客戶案件" / "大安陳" / "估價單.pdf"
        src.rename(dst)
        _cards.scan_apply(self.V)
        self.assertEqual(len(self.todo_moved()), 1)
        cards.sync(self.V, sources_lib.load(self.V), turn.start(self.V, None, "")["turn"])
        self.assertEqual(self.todo_moved(), [])
        # 來源多一份新檔 → 掃描套用 → 不該再列 moved
        (self.raw / "客戶案件" / "大安陳" / "新的.txt").write_text("新的一份", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.assertEqual(self.todo_moved(), [])

    def test_new_file_without_prior_sync(self):
        card_all(self.V)
        (self.raw / "報價與合約" / "新的.txt").write_text("又一份", encoding="utf-8")
        _cards.scan_apply(self.V)
        self.assertEqual(self.todo_moved(), [])


class B069(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _entities.make_entity_vault()
        _entities.put_entities(self.V, [_entities.entity(_entities.XINYI, "信義王")])
        self.rel = _entities.write_card(self.V, _entities.ESTIMATE, "內湖李估價", ["信義王"])
        self.fid = _cards.entry_of(self.V, _entities.ESTIMATE)["id"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lose_file(self):
        (self.raw / "客戶案件" / "內湖李" / "估價單.pdf").unlink()
        _cards.scan_apply(self.V)

    def test_missing_with_open_pending_still_mounted(self):
        self.lose_file()
        entry = next(e for e in inventory.load(self.V)["entries"] if e["id"] == self.fid)
        self.assertEqual(entry["status"], "missing")
        self.assertIsNotNone(pending.open_for(self.V, self.fid, "missing_file"))
        ms = entities.mounts(self.V)
        self.assertEqual([m["id"] for m in ms.get("信義王", [])], [self.fid])

    def write_synthesis(self):
        from kb._lib import synthesis
        data = entities.load(self.V)
        e = entities.find(data, "信義王")[0]
        ms = entities.mounts(self.V, data)
        p = Path(self.V) / synthesis.card_rel(e)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_entities.synthesis_text(synthesis.header_for(self.V, e, ms.get("信義王", [])), "信義王"),
                     encoding="utf-8", newline="\n")

    def stale_removed(self):
        data = cards.todo(self.V, sources_lib.load(self.V))
        return [r["id"] for s in data["synthesis_stale"] for r in s["removed"]]

    def test_synthesis_keeps_card_until_dropped(self):
        self.write_synthesis()
        self.assertEqual(cards.todo(self.V, sources_lib.load(self.V))["synthesis_stale"], [])
        self.lose_file()
        self.assertEqual(self.stale_removed(), [])
        from kb import cards as cards_cli
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cards_cli._drop(self.V, self.fid, False, False)
        self.assertEqual(self.stale_removed(), [self.fid])

    def test_after_drop_not_mounted(self):
        self.lose_file()
        from kb import cards as cards_cli
        import argparse
        import io
        import contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            cards_cli._drop(self.V, self.fid, False, False)
        self.assertEqual(entities.mounts(self.V).get("信義王", []), [])

    def test_answered_keep_not_mounted_after_resolve(self):
        """客戶答「先留著」（resolve）之後待處理不再 open：照舊不算掛載（卡不在 active 的條目上）。"""
        self.lose_file()
        item = pending.open_for(self.V, self.fid, "missing_file")
        pending.resolve(self.V, item["id"], "客戶說先留著", turn.start(self.V, None, "")["turn"])
        self.assertEqual(entities.mounts(self.V).get("信義王", []), [])


if __name__ == "__main__":
    unittest.main()
