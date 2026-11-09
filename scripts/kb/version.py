"""kb version：印 plugin 名稱與版號，來源是 plugin.json。"""
import json

from kb._lib.paths import read_manifest


def run(args):
    manifest = read_manifest()
    name, version = manifest["name"], manifest["version"]
    if args.json:
        print(json.dumps({"name": name, "version": version}, ensure_ascii=False))
    else:
        print(f"{name} {version}")
    return 0
