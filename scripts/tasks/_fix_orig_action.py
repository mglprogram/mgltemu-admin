# -*- coding: utf-8 -*-
"""修 `_原action` 里被重复转义的反斜杠。

现象（2026-10-28 发现）
----------------------
`TEMU-Goods-OnSale-Daily` 和 `TEMU-Goods-DailyList` 的 `_原action.arguments` 里
路径是 `D:\\\\AI-workplace\\\\TEMU\\\\...`（每个反斜杠都翻倍）。这是早先某次
JSON 往返（字符串被 encode 两次）留下的。

为什么危险
----------
`_原action` 只在 `migrate_registry_to_dsh.py --revert` 时用，平时看不出来。
真到要回滚那天，任务会被还原成一条**打不开的命令行** —— 而回滚通常发生在
「DSH 已经出问题」的时候，那时候再踩这个坑最要命。

判定
----
Windows 路径里不会出现 `\\\\`（两个连续反斜杠）。只在 `_原action` 这一个字段里
把 `\\\\` 收成 `\\`，其它字段不动 —— 避免误伤正文里正常的转义。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
BACKUP_DIR = Path(r"D:\AI-workplace\TEMU\backups\scheduled-tasks")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    dry = "--apply" not in sys.argv

    reg = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    fixed = []
    for t in reg.get("tasks") or []:
        act = t.get("_原action")
        if not isinstance(act, dict):
            continue
        args = act.get("arguments") or ""
        if "\\\\" not in args:
            continue
        act["arguments"] = args.replace("\\\\", "\\")
        fixed.append(t.get("name"))

    for n in fixed:
        print(f"  fix {n}")
    print(f"\n{'would fix' if dry else 'fixed'} {len(fixed)} task(s)")

    if dry:
        print("(dry run - add --apply)")
        return 0
    if not fixed:
        print("no change; not writing")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    bak = BACKUP_DIR / f"tasks.json.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.copy2(REGISTRY, bak)
    print(f"backup: {bak}")

    REGISTRY.write_text(
        json.dumps(reg, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8-sig",
        newline="\n",
    )
    print(f"written: {REGISTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
