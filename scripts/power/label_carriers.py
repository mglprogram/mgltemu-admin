# -*- coding: utf-8 -*-
"""自动生成 `shein_label_<店>.json`（领星发货要的承运商来源）。

★ 2026-11-25 真机（用户原话 + 实测）：
    用户：「你首先要在 temu 店铺买面单，才能拉到物流面单，然后才去领星下单才能拉到面单」
    实测：`confirmShippingList` 里这单有
        packageSn=PK-2562745313525893163
        trackingNumber=1Z1969JFYW00225794
        interlineInfoForAggregationList=[{shippingCompanyName:"UPS", trackingNumber:"1Z..."}]
    ⇒ **承运商就在 TEMU 面单数据里**，不用手填。
    而缺这个文件时领星报「please fill in the logistics carrier!」。

本模块：给一批 PO，从 `confirmShippingList` 抽出 `{order_no, carrier, tracking}`，
        写成 `shein_label_<店>.json`（`gw_carriers` 只读 `order_no` / `carrier`）。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import temu_confirm_api as TC  # noqa: E402


def _carrier_of(item: dict) -> str:
    """从 confirmShippingList 条目里取承运商名（优先 interline 的 shippingCompanyName）。"""
    for x in (item.get("interlineInfoForAggregationList") or []):
        n = str((x or {}).get("shippingCompanyName") or "").strip()
        if n:
            return n
    for k in ("shippingCompanyName", "carrier", "logisticsCarrier"):
        n = str(item.get(k) or "").strip()
        if n:
            return n
    return ""


def build(store: str, pos: list, out_path: str, log=print) -> dict:
    """按 PO 列表从 TEMU 面单数据生成 `shein_label_<店>.json`。

    返回 `{written, missing, rows}`；`missing` = 没有运单号/承运商的单（面单没买到）。
    """
    want = {str(x).strip() for x in pos if str(x).strip()}
    d = TC.list_pending(store, page_size=100)
    rows, got = [], set()
    for it in (d.get("items") or []):
        pos_in = [p.get("parentOrderSn") for p in TC._po_list(it)]
        hit = [p for p in pos_in if p in want]
        if not hit:
            continue
        carrier = _carrier_of(it)
        tn = str(it.get("trackingNumber") or "").strip()
        for po in hit:
            got.add(po)
            rows.append({"order_no": po, "carrier": carrier,
                         "tracking_number": tn,
                         "package_sn": it.get("packageSn")})
    missing = sorted(want - got)
    doc = {"store": store, "rows": rows, "missing": missing}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    log("   [承运商] %d 单有面单（承运商 %d 种）｜%d 单没买到面单"
        % (len(rows), len({r["carrier"] for r in rows if r["carrier"]}), len(missing)))
    if missing:
        log("   [承运商] 缺面单：%s" % ", ".join(missing[:5]))
    return {"written": len(rows), "missing": missing, "rows": rows}


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "BHY"
    fs = sys.argv[2] if len(sys.argv) > 2 else ""
    pos = [x.strip() for x in open(fs, encoding="utf-8").read().splitlines() if x.strip()] if fs else []
    p = os.path.join(_HERE, "out", "shein_label_%s.json" % st)
    print(build(st, pos, p))
