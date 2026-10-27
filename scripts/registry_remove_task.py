#!/usr/bin/env python3
"""Remove one task entry from the scheduled-tasks registry (with backup).

Deleting a task has to touch several places, and missing one leaves a stale
reference that later looks like drift:
  1. Windows Task Scheduler   (Unregister-ScheduledTask)
  2. tasks.json               (this script)
  3. scripts/data/task_watch.json      (watchdog list)
  4. build_automation_data.py LABELS   (panel label table)

This script only does #2 — the others are edited directly so each change is
visible in review.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time

REGISTRY = os.path.join(
    os.path.expanduser("~"), ".agents", "skills", "scheduled-tasks", "tasks.json"
)
BACKUP_DIR = r"D:\AI-workplace\TEMU\backups\scheduled-tasks"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    with open(REGISTRY, encoding="utf-8-sig") as fh:
        reg = json.load(fh)

    tasks = reg.get("tasks", [])
    keep = [t for t in tasks if t.get("name") != a.name]
    removed = len(tasks) - len(keep)
    if removed == 0:
        print("not found in registry: %s" % a.name)
        return 0

    print("would remove %d entr(y/ies) named %r; %d -> %d tasks"
          % (removed, a.name, len(tasks), len(keep)))
    if not a.apply:
        print("(dry run - add --apply to write)")
        return 0

    os.makedirs(BACKUP_DIR, exist_ok=True)
    bak = os.path.join(BACKUP_DIR,
                       "tasks.json.%s.bak" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(REGISTRY, bak)
    print("backup:", bak)

    reg["tasks"] = keep
    with open(REGISTRY, "w", encoding="utf-8-sig", newline="\n") as fh:
        json.dump(reg, fh, ensure_ascii=False, indent=4)
        fh.write("\n")
    print("written:", REGISTRY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
