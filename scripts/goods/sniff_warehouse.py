# -*- coding: utf-8 -*-
"""刷新商品列表页，抓所有含 warehouse / shippingMode 的请求与响应体，用来找 kansas仓 的 warehouseId。"""
from __future__ import annotations
import asyncio, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa

KEYS = ("warehouse", "Warehouse", "shippingMode", "shipping-mode", "deliveryMode")


async def main(port, wait=22):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page"); return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=KEYS).__aenter__()
    try:
        await cap.send("Network.enable", {"maxPostDataSize": 8 * 1024 * 1024}, timeout=20)
        await cap.send("Page.enable", {}, timeout=20)
        await cap.send("Page.reload", {"ignoreCache": False}, timeout=20)
        print("已刷新，等 %ds…" % wait, flush=True)
        await asyncio.sleep(wait)
        hits = cap.hits
        print("命中 %d 条" % len(hits), flush=True)
        for h in hits:
            print("URL:", h.get("url", "")[:160], flush=True)
            b = (h.get("body") or "")[:1200]
            print("  BODY:", b.replace("\n", " ")[:1200], flush=True)
        # 也从抓到的所有 URL 里挑
        urls = {}
        for rd in cap._urls.values():
            if any(k in rd for k in KEYS):
                urls[rd] = 1
        print("--- 相关 URL 列表 ---", flush=True)
        for u in urls:
            print(u[:200], flush=True)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 59670)))
