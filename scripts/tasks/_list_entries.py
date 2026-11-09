# -*- coding: utf-8 -*-
"""列出注册表里每个任务的入口脚本（排障/批量改入口用）。"""
import io
import json
import re

P = r"C:\Users\saing\.agents\skills\scheduled-tasks\tasks.json"
PAT = re.compile(r"[A-Za-z]:\\\\[^\"\s]+?\.(?:cmd|bat|ps1|py)")


def main() -> int:
    with io.open(P, encoding="utf-8-sig") as fh:
        reg = json.load(fh)
    for t in reg["tasks"]:
        a = t.get("action") or {}
        s = str(a.get("execute", "")) + " " + str(a.get("arguments", ""))
        m = PAT.findall(s)
        print("%-32s => %s" % (t.get("name"), m[-1] if m else s[:110]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
