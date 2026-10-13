"""門檻與預設數字：config/defaults.json 是底，客戶端 .system/config.json 同鍵覆寫。
鍵一定要在 defaults.json 裡（每個鍵旁一個 _why_<鍵>），不在就 KeyError，客戶端檔加不了新鍵。
"""
from pathlib import Path

from kb._lib.io import read_json
from kb._lib.paths import plugin_root

DEFAULTS_REL = Path("config") / "defaults.json"
CLIENT_REL = Path(".system") / "config.json"


def defaults():
    return read_json(plugin_root() / DEFAULTS_REL, default={}) or {}


def get(vault, key):
    base = defaults()
    if key not in base or key.startswith("_why_"):
        raise KeyError(key)
    client = read_json(Path(vault) / CLIENT_REL, default={}) or {}
    return client.get(key, base[key])
