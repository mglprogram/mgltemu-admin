# -*- coding: utf-8 -*-
"""workbuddy_automations.py — 管理 WorkBuddy 自带调度器的自动化任务（workbuddy.db）。

为什么需要它
------------
本机有**四个互不相干**的调度器，WorkBuddy 是其中之一：

| 调度器 | 位置 | 谁管它 |
|---|---|---|
| Windows 计划任务 | 系统 | `scheduled-tasks` 技能的 `tasks.json` + `manage.py` |
| **WorkBuddy 调度器** | `~/.workbuddy/workbuddy.db` 的 `automations` 表 | **本脚本** |
| DSH 会话提醒 | `~/.dsh/storages/schedule.json` | DSH 的 `schedule_*` 工具 |
| 面板/看板 | 静态 JSON | `AutomationPanel-Refresh` |

所以「暂停/恢复/删除 WorkBuddy 任务」既不能用 `manage.py`，也不能改注册表 ——
必须直接动这个库。面板上显示「已暂停，不会自动触发（在 WorkBuddy 里恢复）」的就是它们。

口径（与库里既有做法一致）
--------------------------
* **删除＝软删除**：置 `deleted_at`，同时 `next_run_at=NULL`。可一条 UPDATE 复原，可回溯。
* **暂停/恢复＝`status`**：`PAUSED` / `ACTIVE`。**与 `deleted_at` 相互独立** ——
  实测库里存在 `status=ACTIVE` 但 `deleted_at` 已置的任务（已退休），也存在
  `status=PAUSED` 且 `deleted_at=NULL` 的任务（真暂停、可恢复）。两者别混。
* **恢复时必须重算 `next_run_at`**：暂停时它被置 NULL，只把 status 改回 ACTIVE
  会**永远不再触发**（调度器靠这个时间戳排下一次）。本脚本从 `rrule` 算。

用法
----
    python scripts/workbuddy_automations.py --list
    python scripts/workbuddy_automations.py --delete "每日发货 09:00（帕沃尔四店全流程）"
    python scripts/workbuddy_automations.py --resume "每周一清理：箱唛PDF + 发票集发票（保留7天）"
    # 加 --dry-run 先看会写什么；加 --id 用 id 匹配（名字含中文/特殊字符时更稳）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                        # noqa: BLE001
    pass

DB = Path(r"C:\Users\saing\.workbuddy\workbuddy.db")
BACKUP_DIR = Path(r"D:\AI-workplace\TEMU\backups\workbuddy-db")

#: rrule 里的星期缩写 → Python weekday()（周一=0）
_WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _fmt_ms(ms) -> str:
    if not ms:
        return "-"
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, TypeError):
        return str(ms)


def next_run_from_rrule(rrule: str, now: datetime = None) -> int:
    """按 rrule 算下一次触发的**毫秒时间戳**；算不出来返回 0（宁可不改，也不瞎写）。

    支持库里实际出现的三种：`FREQ=DAILY` / `FREQ=WEEKLY;BYDAY=..` / `FREQ=HOURLY;INTERVAL=n`。
    """
    now = now or datetime.now()
    if not rrule:
        return 0
    parts = {}
    for chunk in str(rrule).split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts[k.strip().upper()] = v.strip().upper()
    freq = parts.get("FREQ", "")
    try:
        hour = int(parts.get("BYHOUR", "0"))
        minute = int(parts.get("BYMINUTE", "0"))
    except ValueError:
        return 0

    def _at(d: datetime) -> datetime:
        return d.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if freq == "DAILY":
        cand = _at(now)
        if cand <= now:
            cand = _at(now + timedelta(days=1))
        return int(cand.timestamp() * 1000)

    if freq == "WEEKLY":
        days = [_WD[d] for d in (parts.get("BYDAY") or "").split(",") if d in _WD]
        if not days:
            return 0
        for delta in range(0, 15):                       # 两周内必有一天命中
            d = now + timedelta(days=delta)
            if d.weekday() in days:
                cand = _at(d)
                if cand > now:
                    return int(cand.timestamp() * 1000)
        return 0

    if freq == "HOURLY":
        try:
            interval = max(1, int(parts.get("INTERVAL", "1")))
        except ValueError:
            interval = 1
        cand = now.replace(minute=minute, second=0, microsecond=0) + timedelta(hours=interval)
        return int(cand.timestamp() * 1000)

    return 0


def _backup() -> None:
    """备份 db + wal + shm（wal 里可能有还没落盘的数据）。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB) + suffix)
        if src.exists():
            shutil.copy2(src, BACKUP_DIR / ("workbuddy.db%s.%s.bak" % (suffix, stamp)))
    print("  已备份 -> %s\\workbuddy.db*.%s.bak" % (BACKUP_DIR, stamp))


def _connect() -> sqlite3.Connection:
    if not DB.exists():
        raise SystemExit("找不到 %s" % DB)
    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 30000")
    return con


def _find(con, key: str, by_id: bool) -> sqlite3.Row:
    col = "id" if by_id else "name"
    rows = list(con.execute("SELECT * FROM automations WHERE %s = ?" % col, (key,)))
    if not rows:
        raise SystemExit("没找到%s＝%s 的自动化任务" % ("id" if by_id else "名字", key))
    if len(rows) > 1:
        ids = ", ".join(r["id"] for r in rows)
        raise SystemExit("名字不唯一（%d 条）：%s —— 请改用 --id" % (len(rows), ids))
    return rows[0]


def _show(r: sqlite3.Row) -> None:
    print("  名字   : %s" % r["name"])
    print("  id     : %s" % r["id"])
    print("  状态   : %s%s" % (r["status"],
                               "（已软删除）" if r["deleted_at"] else ""))
    print("  rrule  : %s" % (r["rrule"] or "(空)"))
    print("  下次   : %s" % _fmt_ms(r["next_run_at"]))
    print("  上次   : %s" % _fmt_ms(r["last_run_at"]))


def cmd_list(con) -> int:
    rows = list(con.execute("SELECT * FROM automations ORDER BY created_at"))
    live = [r for r in rows if not r["deleted_at"]]
    print("共 %d 条；未删除 %d 条\n" % (len(rows), len(live)))
    for r in rows:
        flag = "已删除" if r["deleted_at"] else r["status"]
        print("  [%-7s] %-46s %s  next=%s" % (flag, r["name"], r["id"][:12], _fmt_ms(r["next_run_at"])))
    return 0


def cmd_delete(con, key: str, by_id: bool, dry: bool) -> int:
    r = _find(con, key, by_id)
    print("[%s] 删除" % ("预览" if dry else "执行"))
    _show(r)
    if r["deleted_at"]:
        print("  → 已经是软删除状态，无需再动")
        return 0
    if not dry:
        now = _now_ms()
        con.execute("UPDATE automations SET deleted_at=?, updated_at=?, next_run_at=NULL WHERE id=?",
                    (now, now, r["id"]))
        con.commit()
    print("  → %s deleted_at + next_run_at=NULL（软删除，可复原）" % ("会置" if dry else "已置"))
    return 0


def cmd_resume(con, key: str, by_id: bool, dry: bool) -> int:
    r = _find(con, key, by_id)
    print("[%s] 恢复" % ("预览" if dry else "执行"))
    _show(r)
    if r["deleted_at"]:
        raise SystemExit("  这条是**软删除**状态（不是暂停）→ 拒绝恢复。"
                         "先确认要复活，再手工清 deleted_at。")
    nxt = next_run_from_rrule(r["rrule"])
    if not nxt:
        raise SystemExit("  无法从 rrule 算出下一次触发（rrule=%r）→ 拒绝写入，避免恢复后不触发"
                         % (r["rrule"],))
    print("  → status=ACTIVE, next_run_at=%s" % _fmt_ms(nxt))
    if not dry:
        now = _now_ms()
        con.execute("UPDATE automations SET status='ACTIVE', next_run_at=?, updated_at=? WHERE id=?",
                    (nxt, now, r["id"]))
        con.commit()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="管理 WorkBuddy 调度器的自动化任务")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--delete", metavar="NAME_OR_ID")
    ap.add_argument("--resume", metavar="NAME_OR_ID")
    ap.add_argument("--id", action="store_true", help="按 id 而不是名字匹配")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    a = ap.parse_args()

    if not (a.list or a.delete or a.resume):
        ap.print_help()
        return 2

    con = _connect()
    try:
        if a.list:
            return cmd_list(con)
        if not a.dry_run and not a.no_backup:
            _backup()
            print()
        if a.delete:
            rc = cmd_delete(con, a.delete, a.id, a.dry_run)
        else:
            rc = cmd_resume(con, a.resume, a.id, a.dry_run)
        print("\n=== 未删除任务现状 ===")
        for r in con.execute("SELECT * FROM automations WHERE deleted_at IS NULL ORDER BY created_at"):
            print("  [%-7s] %-46s next=%s" % (r["status"], r["name"], _fmt_ms(r["next_run_at"])))
        return rc
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
