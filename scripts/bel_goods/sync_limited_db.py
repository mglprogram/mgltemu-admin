# -*- coding: utf-8 -*-
"""把监控抓到的限流快照写回 SQLite 的 traffic_limited[_<slug>] 表。

为什么需要这个模块：
  面板告警卡读的是 SQLite 表，而历史上只有一次性的 merge_traffic.py 会写它。
  监控每天更新的是 focus_watch_latest.json / focus_watch_history.jsonl，
  两套数据源脱节 -> 面板永远停在建表那天的旧数据（2026-10-10 发现的 bug）。

  本模块在每次监控后调用，用当日抓取结果**整体替换**该表，
  保证面板、趋势、飞书卡片三处口径一致。

⚠ 本模块是 traffic_limited* 表的**唯一写入者**（2026-10-12 起）。
   原先还有一个 merge_traffic.py 也写同一张表，但它用几天前的
   _traffic_raw.json 覆盖，会造成数据回退 —— 已删除。
  改动本模块前请确认没有第二个写入者被引入。

调用方：watch_focus.py main() 末尾（以及 scripts\tasks\run_temu_limited_watch.cmd 的幂等兜底）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")

# store -> (db路径, 表名)
DB_MAP = {
    "BEL": (ROOT / "data" / "bel" / "bel_products.db", "traffic_limited"),
    "BHY": (ROOT / "data" / "bhy" / "bhy_products.db", "traffic_limited_bhy"),
}

DDL = """CREATE TABLE IF NOT EXISTS {table} (
    skc_id TEXT PRIMARY KEY, spu_id TEXT, sku_code TEXT, title TEXT, site TEXT,
    sales7 REAL, flow_status TEXT, traffic_state TEXT,
    declare_price REAL, site_price REAL,
    daily_target_price REAL, activity_target_price REAL,
    price_skc_id TEXT, sku_count INTEGER, match_note TEXT, snapshot_at TEXT)"""

COLS = ["skc_id", "spu_id", "sku_code", "title", "site", "sales7", "flow_status",
        "traffic_state", "declare_price", "site_price", "daily_target_price",
        "activity_target_price", "price_skc_id", "sku_count", "match_note", "snapshot_at"]


def sync_store(store: str, snapshot: dict) -> dict:
    """用一次监控快照整体替换该店的限流表。

    注意：流量视角里 SKC 是**商品级**唯一键，但同一个 SKC 会有多个 SKU 行；
    这里按 sku_code 去重（监控已去重），skc_id 用作主键。
    """
    if store not in DB_MAP:
        return {"store": store, "skipped": "无对应数据库"}

    db, table = DB_MAP[store]
    if not db.exists():
        return {"store": store, "skipped": f"数据库不存在 {db}"}

    codes = snapshot.get("limited_codes") or []
    if not codes:
        return {"store": store, "skipped": "本次快照无数据（保留旧表，避免清空）"}

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute(DDL.format(table=table))

    # 保留上一次的 skc_id 映射（流量视角给的是 SKC 级信息，
    # 但历史表里可能已有 skc_id <-> sku_code 的对应关系）
    old = {}
    try:
        for r in cur.execute(f"SELECT sku_code, skc_id FROM {table}"):
            old[r[0]] = r[1]
    except sqlite3.Error:
        pass

    cur.execute(f"DELETE FROM {table}")
    rows = []
    for c in codes:
        code = c["sku_code"]
        skc = old.get(code, "")          # 优先沿用历史 skc_id
        rows.append((
            skc, str(c.get("spu_id", "")), code,
            c.get("title", ""), c.get("site", ""),
            c.get("sales7"), c.get("flow_status", ""), c.get("traffic_state", ""),
            c.get("declare_price"), c.get("declare_price"),   # site_price 同申报价
            c.get("target_price"), c.get("activity_price"),
            "", 1, "", ts,
        ))
    cur.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(COLS)}) "
        f"VALUES ({','.join('?' * len(COLS))})", rows)
    con.commit()
    con.close()
    return {"store": store, "table": table, "rows": len(rows), "snapshot_at": ts}


def sync_all(results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        if "error" in r or "store" not in r:
            continue
        try:
            out.append(sync_store(r["store"], r))
        except Exception as e:  # noqa: BLE001
            out.append({"store": r["store"], "error": str(e)[:200]})
    return out


if __name__ == "__main__":
    import json
    p = ROOT / "data" / "focus_watch_latest.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for r in sync_all(data):
        print(r)
