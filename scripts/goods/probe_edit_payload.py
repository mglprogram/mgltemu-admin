# -*- coding: utf-8 -*-
"""单个 SKU 试不同 payload 切「卖家自发货」，只为摸清 6000074 的正确形状（每次都会真提交）。

    python scripts/goods/probe_edit_payload.py --port 59670 --spu 2946934746 --sku-id 96032800469 \
        --variant empty|jz|jz1

variant:
    empty  → warehouseStockList 传空数组（只改模式，不动库存）
    jz     → 只传加州3仓，库存 = --stock
    jz1    → 加州3 传 stock-1、kansas 传 1（kansas id 由 --wh2 给，没有就跳过）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import api_switch_mode as api  # noqa: E402

JZ = "WH-05667065528452987"


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        if a.variant == "empty":
            whl = []
        elif a.variant == "jz":
            whl = [(JZ, a.stock)]
        else:
            whl = [(JZ, max(1, a.stock - 1)), (a.wh2, 1)]
        res = await api.call_edit(cap, a.spu, a.sku_id, whl)
        print(json.dumps({"variant": a.variant, "sent": res.get("sent"),
                          "status": res.get("status"), "body": res.get("body"),
                          "err": res.get("err")}, ensure_ascii=False, indent=1))
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    ap.add_argument("--spu", required=True)
    ap.add_argument("--sku-id", required=True)
    ap.add_argument("--stock", type=int, default=1)
    ap.add_argument("--wh2", default="")
    ap.add_argument("--variant", default="empty", choices=("empty", "jz", "jz1"))
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
