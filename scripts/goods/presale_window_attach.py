# -*- coding: utf-8 -*-
"""把「≤7 天预售窗口」判定接到 presale 数据里。

对每条 TEMU 行（一个 SKU）做三件事：
  ① 从看板聚合里取该尺码的 `近7天日均` / `代发库存`；
  ② 从 linxing-inbound 的 `map` 取该尺码在途的入库单号列表；
  ③ 用 shipment-track 的到架日算出「7 天内能不能接上」。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

API = Path(r"D:\AI-workplace\TEMU\temu-admin\public\data")


def load_track() -> dict:
    """{入库单号: shipment-track 行}（读不到就空，判定退回领星 expected）。"""
    try:
        d = json.loads((API / "shipment-track.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {str(r.get("inboundNo") or ""): r for r in (d.get("rows") or []) if r.get("inboundNo")}


def load_inbound_map() -> dict:
    """{`产品|变体|尺码`: [入库单]}（只含还没到齐的单）。"""
    try:
        d = json.loads((API / "linxing-inbound.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return d.get("map") or {}


def key_of(product: str, variant: str, size: str) -> str:
    return "%s|%s|%s" % (product, variant, size)


def attach(rows: list, agg: dict, inbound: dict, track: dict, today: date,
           days: int = 7, window_mod=None) -> dict:
    """给每条 row 补上 `presaleWindow` 字段。返回汇总（几条可开）。"""
    import presale_window as W  # noqa: PLC0415

    wm = window_mod or W
    opened = []
    for r in rows:
        key = r.get("probeSku") or ""            # `202504-5SetA-L` 形态
        parts = key.rsplit("-", 1)
        if len(parts) != 2:
            r["presaleWindow"] = {"verdict": "无数据", "note": "款式键不完整，算不了预售窗口"}
            continue
        model, size = parts
        prod, _, variant = model.partition("-")
        src = agg.get(key) or {}
        daily_raw = float(src.get("近7天日均") or 0)
        stock_raw = float(src.get("代发库存") or 0)
        f = float(r.get("factor") or 1) or 1.0
        # 看板是 5Set 件数、TEMU 是 10Set：日均折成 TEMU 单位再比
        daily = daily_raw / f
        stock = stock_raw / f

        orders = (inbound.get(key_of(prod, variant, size)) or [])
        batches = W.split_inbound(orders, track)
        res = W.judge_window(daily, stock, batches, today, days=days)
        res.update({
            "dailyRaw": round(daily_raw, 2), "daily": round(daily, 2),
            "stockRaw": round(stock_raw, 1), "stock": round(stock, 1),
            "factor": f, "days": days,
            "source": "看板 近7天日均 + 代发库存 × 货件跟踪到架日",
        })
        r["presaleWindow"] = res
        if res.get("verdict") == "可开":
            opened.append(r.get("skuCode") or key)
    return {"days": days, "opened": opened, "count": len(opened),
            "note": "≤%d 天预售窗口：日均与代发库存取看板，到架日取货件跟踪" % days}
