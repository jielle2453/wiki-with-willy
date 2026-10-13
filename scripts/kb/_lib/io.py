"""檔案讀寫：一律 UTF-8、LF；寫入先寫 .tmp 再 os.replace，寫一半不會留下壞檔。
os.replace 被暫時拒絕（防毒或索引正在讀，I-018）重試最多 20 次、每次 0.1 秒。
FileLock：建鎖檔實作的簡單檔案鎖，給同時寫同一份狀態檔的程序互斥（I-080）。
"""
import json
import os
import time
from pathlib import Path

REPLACE_RETRIES = 20
REPLACE_WAIT = 0.1
LOCK_TIMEOUT = 3  # hook timeout 10 秒，鎖等不到就要讓 before_write 回 backup_failed（第一輪 M1）
STALE_LOCK_SECONDS = 30


def read_text(path, encoding="utf-8"):
    with open(path, encoding=encoding, newline="") as f:
        return f.read()


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_text_atomic(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for i in range(REPLACE_RETRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if i == REPLACE_RETRIES - 1:
                    raise
                time.sleep(REPLACE_WAIT)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, data):
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


class FileLock:
    """with FileLock(path): …。鎖檔是 <path>.lock，O_CREAT|O_EXCL 保證只有一個程序建得成。
    超過 stale 秒的鎖視為殘留（程式中途被中斷）清掉；等超過 timeout 秒丟 TimeoutError（是 OSError 的子類）。
    Windows 上 O_EXCL 碰到待刪除的鎖檔會丟 PermissionError，當成有人持鎖繼續等（L6）。
    """

    def __init__(self, path, timeout=LOCK_TIMEOUT, stale=STALE_LOCK_SECONDS):
        self.path = str(path) + ".lock"
        self.timeout = timeout
        self.stale = stale
        self.fd = None

    def __enter__(self):
        start = time.time()
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - os.path.getmtime(self.path) > self.stale:
                        try:
                            os.remove(self.path)
                        except FileNotFoundError:
                            pass  # 別的程序先清掉了
                        continue
                except FileNotFoundError:
                    continue
                except OSError:
                    pass
                if time.time() - start > self.timeout:
                    raise TimeoutError(f"等待檔案鎖逾時：{self.path}")
                time.sleep(0.1)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
