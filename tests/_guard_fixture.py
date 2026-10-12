"""守門測試共用：把 tests/fixtures 的 vault 與 raw 整包複製到 tmp，驗 `../raw/...` spec 真的相對 vault 解析。"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SCRIPTS = ROOT / "scripts"
HOOK = ROOT / "hooks" / "guard.py"
KB = SCRIPTS / "kb.py"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def make_vault():
    """回 (tmp, vault, raw)。tmp 由呼叫端 shutil.rmtree。"""
    tmp = Path(tempfile.mkdtemp(prefix="kbguard-"))
    shutil.copytree(FIXTURES / "vault", tmp / "vault")
    shutil.copytree(FIXTURES / "raw", tmp / "raw")
    print(f"[guard fixture] V = {tmp / 'vault'}")
    return tmp, tmp / "vault", tmp / "raw"


TOOL_KEY = {
    "Read": "file_path", "Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path", "Glob": "path", "Grep": "path",
    "Bash": "command", "PowerShell": "command",
}


def payload(tool, value, cwd, permission_mode="default"):
    tool_input = {} if value is None else {TOOL_KEY[tool]: str(value)}
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd), "permission_mode": permission_mode}
