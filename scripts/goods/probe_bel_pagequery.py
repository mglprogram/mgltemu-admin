# -*- coding: utf-8 -*-
"""抓 BEL 商品列表页的 pageQuery 响应（只读），拿每个 SKU 的 shippingMode / 仓库库存。

    python scripts/goods/probe_bel_pagequery.py --port 59670 --spu 2946934746
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
import goto_spu as gs  # noqa: E402

LOGS = Path(__file__).resolve().parents[2] / "_logs"


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    match = ("pageQuery", "skc", "product/list", "sku")
    async with cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=match) as cap:
        await cap.wait_ready() if hasattr(cap, "wait_ready") else None
        await asyncio.sleep(1.0)
        if a.spu:
            ok, detail = await gs.goto_spu(cap, a.spu, expect_code=a.code)
            print("goto_spu:", ok, str(detail)[:120], flush=True)
        await asyncio.sleep(6)
        hits = cap.hits

    out = []
    for h in hits:
        out.append({"url": h["url"], "req": h.get("req", "")[:2000]})
    (LOGS / ("bel_pagequery_urls_%s.json" % (a.spu or "all"))).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("hits:", len(hits))
    for h in hits:
        print("  ", h["url"][:140], "len(body)=", len(h.get("body") or ""))
    if hits:
        big = max(hits, key=lambda h: len(h.get("body") or ""))
        p = LOGS / ("bel_pagequery_%s.json" % (a.spu or "all"))
        p.write_text(json.dumps(big.get("json") or big.get("body"), ensure_ascii=False),
                     encoding="utf-8")
        print("saved", p)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    ap.add_argument("--spu", default="")
    ap.add_argument("--code", default="")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
