"""檔案讀寫：一律 UTF-8、LF；寫入先寫 .tmp 再 os.replace，寫一半不會留下壞檔。"""
import json
import os
from pathlib import Path


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
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, data):
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
