# -*- coding: utf-8 -*-
"""关掉 BHY/BEL 窗口里残留的 `stock-edit`（设置库存）页 —— 用户 2026-11-21：「设置完一个关一个」。

只关 `goods/stock-edit` 这类**脚本自己开的编辑页**；用户自己开的商品列表/订单/销量分析一律不碰。

用法：
    python scripts/goods/close_stock_edit_tabs.py            # 列出（不关）
    python scripts/goods/close_stock_edit_tabs.py --apply     # 真关
    python scripts/goods/close_stock_edit_tabs.py --store BHY --apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="ALL")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--keep", type=int, default=0, help="保留最新的 N 个（默认 0＝全关）")
    a = ap.parse_args()
    stores = list(WINDOWS) if a.store.upper() == "ALL" else [a.store.upper()]
    total = 0
    for store in stores:
        try:
            port = probe_port(WINDOWS[store])
        except Exception as e:  # noqa: BLE001
            print("[%s] 探不到窗口：%s" % (store, e))
            continue
        if not port:
            continue
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                 if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")]
        pages = pages[a.keep:]
        print("[%s] stock-edit 页 %d 个%s" % (store, len(pages), "" if a.apply else "（只列不关）"))
        for p in pages:
            print("   ", p["id"][:8], (p.get("url") or "")[:70])
            if a.apply:
                ws = cdp_net.http_json("http://127.0.0.1:%d/json/version" % port)["webSocketDebuggerUrl"]
                await cdp_net._one_shot(ws, "Target.closeTarget", {"targetId": p["id"]})
                total += 1
    if a.apply:
        print("已关闭 %d 个设置库存页" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
