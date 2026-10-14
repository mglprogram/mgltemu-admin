# -*- coding: utf-8 -*-
"""聚合 focus_watch_history.jsonl 的历史快照，产出限流趋势。

产出 data/trend_data.json：
  snapshots   —— 按时间排列的每次快照（限流总数、命中数、流量状态分布）
  recurring   —— 反复被限流的货号排行（出现次数、跨越天数、首次/最近出现）
  focus_track —— 重点 SPU 逐次状态（正常/被限流）
  resolved    —— 曾被限流、现已解除的货号

被安排的定时任务每天追加一条 history，所以这里做的是多日累积分析。
只有 1 天数据时，趋势块会自动降级为「当前快照」，不硬画折线。
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
HIST = ROOT / "data" / "focus_watch_history.jsonl"
OUT = ROOT / "data" / "trend_data.json"

# SPU -> 货号(款号) 的来源：本地商品库（最全），历史快照的 limited_codes 作补充
STORE_DB = {
    "BEL": ROOT / "data" / "bel" / "bel_products.db",
    "BHY": ROOT / "data" / "bhy" / "bhy_products.db",
}


def spu_code_map(snaps) -> dict:
    """(store, spu) -> 货号(款号)。

    优先本地商品库的 skc_code（一个 SPU 可能有多个款号，取第一个并按名称排序保证稳定）；
    库里查不到的，用历史快照 limited_codes 里出现过的货号补。
    """
    m: dict[tuple[str, str], str] = {}

    for store, db in STORE_DB.items():
        if not db.exists():
            continue
        try:
            con = sqlite3.connect(db)
            for spu, code in con.execute(
                    "SELECT DISTINCT spu_id, skc_code FROM skus "
                    "WHERE skc_code IS NOT NULL AND skc_code <> '' "
                    "ORDER BY skc_code"):
                m.setdefault((store, str(spu)), str(code))
            con.close()
        except Exception:  # noqa: BLE001
            pass

    for s in snaps:
        store = s.get("store")
        for c in s.get("limited_codes", []):
            spu = str(c.get("spu_id") or "")
            code = (c.get("sku_code") or "").strip()
            if spu and code:
                m.setdefault((store, spu), code)
    return m


def load():
    if not HIST.exists():
        return []
    rows = []
    for line in HIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def main():
    raw = load()
    snaps = []          # 每次运行一个快照（可能多店）
    for rec in raw:
        for store in rec:
            if "error" in store or "checked_at" not in store:
                continue
            snaps.append(store)

    snaps.sort(key=lambda s: s["checked_at"])

    # ---- 时间轴 ----
    timeline = []
    for s in snaps:
        day = s["checked_at"][:10]
        flow = {k: v for k, v in Counter(
            c.get("flow_status", "") for c in s.get("limited_codes", [])).items() if k}
        timeline.append({
            "checked_at": s["checked_at"],
            "day": day,
            "store": s["store"],
            "limited_total": s["limited_total"],
            "hit_count": s["hit_count"],
            "flow": flow,
        })

    # ---- 反复被限流排行 ----
    appear = defaultdict(list)   # code -> [snapshot...]
    for s in snaps:
        for c in s.get("limited_codes", []):
            appear[c["sku_code"]].append((s["checked_at"], s["store"], c))

    recurring = []
    for code, hits in appear.items():
        days = {h[0][:10] for h in hits}
        last = hits[-1][2]
        first = hits[0][2]
        recurring.append({
            "sku_code": code,
            "spu_id": last.get("spu_id", ""),
            "appear": len(hits),
            "days": len(days),
            "first_seen": hits[0][0],
            "last_seen": hits[-1][0],
            "flow_status": last.get("flow_status", ""),
            "sales7": last.get("sales7"),
            "declare_price": last.get("declare_price"),
            "target_price": last.get("target_price"),
        })
    recurring.sort(key=lambda x: (-x["days"], -x["appear"], x["sku_code"]))

    # ---- 曾限流现已解除（只在有 >=2 次快照时才判断）----
    resolved = []
    if len(snaps) >= 2:
        by_store = defaultdict(list)
        for s in snaps:
            by_store[s["store"]].append(s)
        for store, seq in by_store.items():
            if len(seq) < 2:
                continue
            prev = {c["sku_code"] for c in seq[-2].get("limited_codes", [])}
            cur = {c["sku_code"] for c in seq[-1].get("limited_codes", [])}
            for code in sorted(prev - cur):
                info = next((c for c in seq[-2]["limited_codes"] if c["sku_code"] == code), {})
                resolved.append({
                    "store": store, "sku_code": code,
                    "spu_id": info.get("spu_id", ""),
                    "flow_status": info.get("flow_status", ""),
                    "cleared_at": seq[-1]["checked_at"],
                })

    # ---- 重点 SPU 逐次状态（带店铺前缀，避免两店 SPU 号撞车）----
    code_of = spu_code_map(snaps)
    focus_track = []
    spu_seq = defaultdict(list)
    for s in snaps:
        hit_sp = {h["spu_id"] for h in s.get("hits", [])}
        safe_sp = set(s.get("safe", []))
        for spu in hit_sp | safe_sp:
            spu_seq[(s["store"], spu)].append(
                (s["checked_at"], spu in hit_sp))
    for (store, spu), seq in spu_seq.items():
        seq.sort()
        focus_track.append({
            "store": store,
            "spu": spu,
            "sku_code": code_of.get((store, spu), ""),
            "checks": len(seq),
            "hit_times": sum(1 for _, h in seq if h),
            "first": seq[0][0], "last": seq[-1][0],
            "current": seq[-1][1],
            "timeline": [{"at": t, "limited": h} for t, h in seq],
        })
    focus_track.sort(key=lambda x: (x["store"], not x["current"], -x["hit_times"]))

    days = sorted({t["day"] for t in timeline})
    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "snapshot_count": len(snaps),
        "distinct_days": len(days),
        "days": days,
        "first_check": snaps[0]["checked_at"] if snaps else None,
        "last_check": snaps[-1]["checked_at"] if snaps else None,
        "timeline": timeline,
        "recurring": recurring,
        "focus_track": focus_track,
        "resolved": resolved,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"快照数    : {len(snaps)}")
    print(f"覆盖天数  : {len(days)}  {days}")
    print(f"出现过的货号: {len(recurring)}")
    print(f"重点SPU追踪: {len(focus_track)}")
    print(f"已解除    : {len(resolved)}")
    print("->", OUT)


if __name__ == "__main__":
    main()