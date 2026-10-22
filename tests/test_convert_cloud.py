"""kb convert 讀檔前看一次目前的屬性（manage 第二版 OneDrive 第 3 點，B-095）：只在雲端就照清冊 placeholder 的路回，
不靠清冊的 read（清冊過時時會把整份下載下來）、不讀內容。"""
import shutil
import unittest
from unittest import mock

from tests import _cards
from kb import convert
from kb._lib import extract
from kb._lib import sources as sources_lib  # noqa: E402


class CloudOnly(unittest.TestCase):
    def setUp(self):
        self.tmp, self.V, self.raw = _cards.make_card_vault()
        self.sources = sources_lib.load(self.V)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recall_attribute_refuses_as_placeholder_without_reading(self):
        entry, path, _uri = convert.resolve(self.V, self.sources, str(self.raw / _cards.MEETING), self.V)
        self.assertEqual(entry["read"], "ok")
        with mock.patch.object(convert, "_attrs", return_value=0x400000), \
                mock.patch.object(extract, "probe", side_effect=AssertionError("讀了內容")):
            with self.assertRaises(convert.Refused) as cm:
                convert.gate(self.V, self.sources, entry, path)
        self.assertEqual(cm.exception.args, (convert.PLACEHOLDER, convert.PLACEHOLDER))

    def test_local_file_passes(self):
        entry, path, _uri = convert.resolve(self.V, self.sources, str(self.raw / _cards.MEETING), self.V)
        with mock.patch.object(convert, "_attrs", return_value=0):
            convert.gate(self.V, self.sources, entry, path)


if __name__ == "__main__":
    unittest.main()
