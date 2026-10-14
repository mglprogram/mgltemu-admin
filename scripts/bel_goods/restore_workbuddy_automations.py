# -*- coding: utf-8 -*-
"""恢复 WorkBuddy 里被软删除的三个「每日必做任务提醒」。

背景：WorkBuddy 有独立调度器（workbuddy.db 的 automations 表），
与 DSH 的 schedule.json、Windows 计划任务 都是不同系统。

做法：把 deleted_at 置空、status 改回 ACTIVE、重算 next_run_at（未来最近的触发点）。
不新建记录，避免 id / owner 等字段缺失。

用法：
  python restore_workbuddy_automations.py --dry-run   # 只看会改什么
  python restore_workbuddy_automations.py             # 实际执行
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB = Path(r"C:\Users\saing\.workbuddy\workbuddy.db")
BACKUP_DIR = Path(r"D:\AI-workplace\TEMU\backups\workbuddy-db-20261010")

# 要恢复的三个（按名称精确匹配）
TARGETS = [
    "每日必做任务提醒 - 早9:00",
    "每日必做任务提醒 - 下午15:00",
    "每日必做任务提醒 - 晚19:00",
]


def backup() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB) + suffix)
        if src.exists():
            dst = BACKUP_DIR / f"workbuddy.db{suffix}.{stamp}.bak"
            shutil.copy2(src, dst)
            print(f"  备份 {src.name} -> {dst.name}")


def next_run_for(rrule: str, now: datetime | None = None) -> int:
    """解析 FREQ=DAILY;BYHOUR=9;BYMINUTE=0 -> 下一个触发点的毫秒时间戳。

    只处理本项目用到的 DAILY/WEEKLY 简单形态；解析不了就返回明天同刻。
    """
    now = now or datetime.now()
    parts = {}
    for seg in (rrule or "").split(";"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            parts[k.strip().upper()] = v.strip()

    freq = parts.get("FREQ", "DAILY").upper()
    hour = int(parts.get("BYHOUR", "9"))
    minute = int(parts.get("BYMINUTE", "0"))

    if freq == "WEEKLY":
        byday = parts.get("BYDAY", "MO").upper()
        wd_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
        target_wd = wd_map.get(byday, 0)
        cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        delta = (target_wd - now.weekday()) % 7
        cand = cand + timedelta(days=delta)
        if cand <= now:
            cand += timedelta(days=7)
    else:  # DAILY / 其它按每天
        cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand <= now:
            cand += timedelta(days=1)

    return int(cand.timestamp() * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not DB.exists():
        print(f"找不到 {DB}")
        return 1

    if not a.dry_run:
        print("备份数据库：")
        backup()

    con = sqlite3.connect(str(DB), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        now = datetime.now()
        now_ms = int(now.timestamp() * 1000)

        print(f"\n当前时间: {now:%Y-%m-%d %H:%M:%S}\n")
        done = 0
        for name in TARGETS:
            row = con.execute(
                "SELECT id, name, status, rrule, deleted_at, next_run_at FROM automations "
                "WHERE name = ? ORDER BY created_at DESC LIMIT 1",
                (name,),
            ).fetchone()
            if not row:
                print(f"[跳过] 找不到：{name}")
                continue

            nxt = next_run_for(row["rrule"], now)
            nxt_str = datetime.fromtimestamp(nxt / 1000).strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{'预览' if a.dry_run else '恢复'}] {row['name']}")
            print(f"    id={row['id']}")
            print(f"    rrule={row['rrule']}")
            print(f"    deleted_at: {row['deleted_at']} -> None")
            print(f"    status: {row['status']} -> ACTIVE")
            print(f"    next_run_at: {row['next_run_at']} -> {nxt}  ({nxt_str})")

            if not a.dry_run:
                con.execute(
                    "UPDATE automations SET deleted_at = NULL, status = 'ACTIVE', "
                    "next_run_at = ?, updated_at = ? WHERE id = ?",
                    (nxt, now_ms, row["id"]),
                )
            done += 1

        if not a.dry_run:
            con.commit()
            print(f"\n已提交，共恢复 {done} 条")
        else:
            print(f"\n预览完成，共 {done} 条（未写入）")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
