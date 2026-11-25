# -*- coding: utf-8 -*-
"""接口直读本店「在售中」货号（不点页面、不翻页）。

`skc/pageQuery` 一次给 191 条，每条带 `skcStatus` / `skcSiteStatus`。
实测（2026-11-23）：
    skcStatus=11 且 skcSiteStatus=1  → 在售中（页签口径）
其余（如 7/0 = 未发布到站点、已下架）都不算。

用法：
    python scripts/goods/read_onsale_api.py --port 50748 --store BHY
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402

OUTDIR = ROOT / "data" / "store-db"
URL = "https://agentseller.temu.com/visage-agent-seller/product/skc/pageQuery"


async def main(a):
    cap = await HX.attach(a.port, timeout=25)
    if cap is None:
        print("没有可用页面")
        return 3
    # 头按店取（三店 mallid 不同，用通用的 edit_headers.json 会 403）
    mallid = ""
    try:
        r = await cap.send("Runtime.evaluate", {
            "expression": ("(function(){var parts=document.cookie.split(';');"
                           "for(var i=0;i<parts.length;i++){var p=parts[i].trim();"
                           "if(p.indexOf('mallid=')===0){return p.substring(7);}}return '';})()"),
            "returnByValue": True}, timeout=15)
        mallid = str((r.get("result") or {}).get("value") or "")
    except Exception:
        pass
    hdr = api.load_headers(mallid)
    print("  mallid=%s anti=%d chars" % (mallid or "?", len(hdr.get("Anti-Content") or "")), flush=True)
    if not hdr.get("Anti-Content"):
        print("!! 该店没有 Anti-Content 头（先手动提交一次库存修改）")
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
        return 3
    onsale, allitems = [], []
    try:
        page = 1
        while page <= 30:
            r = await HX.call(cap, URL, {"page": page, "pageSize": 100}, hdr)
            if r.get("status") != 200:
                print("请求失败：", r.get("status"))
                break
            res = (r.get("json") or {}).get("result") or {}
            items = res.get("pageItems") or []
            if not items:
                break
            allitems += items
            if len(allitems) >= int(res.get("total") or 0):
                break
            page += 1
        for it in allitems:
            code = str(it.get("extCode") or "")
            if not code:
                continue
            if int(it.get("skcStatus") or 0) == 11 and int(it.get("skcSiteStatus") or 0) == 1:
                onsale.append(code)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / ("%s.onsale.txt" % a.store)
    # ⚠ 2026-11-23：抓到 0 条时**绝不能覆盖**已有清单（否则 store_db 会把整店算成 0 货号）
    if not onsale:
        print("%s：抓到 0 条在售中 → **保留原文件不动** %s" % (a.store, out))
        return 4
    out.write_text("\n".join(sorted(set(onsale))) + "\n", encoding="utf-8")
    print("%s：全部 %d 条 / 在售中 %d 个货号 → %s"
          % (a.store, len(allitems), len(set(onsale)), out))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", required=True)
    x = ap.parse_args()
    raise SystemExit(asyncio.run(main(x)))
