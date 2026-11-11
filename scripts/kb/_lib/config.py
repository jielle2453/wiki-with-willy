"""門檻與預設數字：config/defaults.json 是底，客戶端 .system/config.json 同鍵覆寫。
鍵一定要在 defaults.json 裡（每個鍵旁一個 _why_<鍵>），不在就 KeyError，客戶端檔加不了新鍵。
客戶端的值型別跟預設不同（例如 "十"）就用預設並寫一行 hook-errors.log，不讓守門因此 deny（第一輪 L5）。
"""
from pathlib import Path

from kb._lib import ledger
from kb._lib.io import read_json
from kb._lib.paths import plugin_root

DEFAULTS_REL = Path("config") / "defaults.json"
# 使用者說一句就改的門檻與頻率（kb config show／set／reset，放寬甲 D-322 第 2 件）；其他鍵是保護用的，不給改。
USER_KEYS = (
    "ingest_auto_max", "answer_verify_max", "agenda_window_days", "agenda_past_days", "lint_background_days",
    "pending_set_aside_days", "set_aside_wake_days", "lint_inferred_days", "preference_lint_days", "versions_keep_days",
    "versions_keep_turns", "backup_keep", "snapshots_keep", "home_timeline_days", "home_recent_max", "find_recent_days",
    "find_results_max", "card_parallel", "preference_max", "preference_max_chars", "ask_expire_days",
    "card_section_map_min_chars", "card_section_map_min_pages", "index_icons", "index_order", "background_scan_minutes",
)
CLIENT_REL = Path(".system") / "config.json"


def defaults():
    return read_json(plugin_root() / DEFAULTS_REL, default={}) or {}


def get(vault, key):
    base = defaults()
    if key not in base or key.startswith("_why_"):
        raise KeyError(key)
    default = base[key]
    if key == "agenda_export_dir":  # 每台的路徑不同，存這台的 env.json（多人共用 1）；舊的 config.json 值遷移前照舊讀
        from kb._lib import env
        here = env.agenda_export_dir(vault)
        if here is not None:
            return here
    try:
        client = read_json(Path(vault) / CLIENT_REL, default={})
    except ValueError as e:
        ledger.log_error(vault, f"config.json 讀不了，{key} 用預設 {default!r}：{type(e).__name__}: {e}")
        return default
    if client is None:
        client = {}
    if not isinstance(client, dict):  # 整份不是物件（第二輪 L5 補）
        ledger.log_error(vault, f"config.json 不是物件（{type(client).__name__}），{key} 用預設 {default!r}")
        return default
    if key not in client:
        return default
    value = client[key]
    if type(default) is float and type(value) is int:  # 小數鍵寫整數照收（裁定 30）；bool 不是 int 型別，照擋
        return float(value)
    if type(value) is not type(default):  # bool 不當 int，"十" 不當 10
        ledger.log_error(vault, f"config.json 的 {key} 型別不對（{type(value).__name__}），用預設 {default!r}")
        return default
    return value


def move_export_dir_to_env(vault):
    """遷移：config.json 的 agenda_export_dir 搬到這台的 env.json 並從 config.json 拿掉。回有沒有搬。"""
    from kb._lib import env
    from kb._lib.io import write_json_atomic
    p = Path(vault) / CLIENT_REL
    cur = read_json(p, default={})
    if not isinstance(cur, dict) or not isinstance(cur.get("agenda_export_dir"), str):
        return False
    value = cur.pop("agenda_export_dir")
    if value and env.agenda_export_dir(vault) is None:
        env.set_agenda_export_dir(vault, value)
    write_json_atomic(p, cur)
    return True
