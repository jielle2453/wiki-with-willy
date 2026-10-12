"""plugin 自身的路徑。從 __file__ 往上找，不寫死、不讀任何狀態檔（D-019）。"""
from pathlib import Path

from kb._lib.io import read_json

MANIFEST_REL = Path(".claude-plugin") / "plugin.json"


def plugin_root():
    here = Path(__file__).resolve()
    for d in here.parents:
        if (d / MANIFEST_REL).is_file():
            return d
    raise FileNotFoundError(f"從 {here} 往上找不到 {MANIFEST_REL}")


def read_manifest():
    return read_json(plugin_root() / MANIFEST_REL)
