# -*- coding: utf-8 -*-
"""直接调 shippingMode/edit 接口切「卖家自发货」——不依赖铅笔弹框。

接口（2026-11-18 从真机抓包确认）：
    POST /visage-agent-seller/product/sku/shippingMode/edit
    {"productId": <SPU>, "productSkuId": <列表页数字列>, "shippingMode": 1,
     "warehouseStockList": [{"warehouseId": "WH-...", "warehouseStock": N}, ...]}
    → {"success": true, "errorCode": 1000000}

用法：
    # 冒烟：把一个已切好的 SKU 用同值重提（幂等），验证通道可用
    python scripts/goods/api_switch_mode.py --port 59670 --spu 2330660256 --sku-id 73142346438 --stock 68 --wh WH-05667065528452987
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

URL = "https://agentseller.temu.com/visage-agent-seller/product/sku/shippingMode/edit"

JS_TMPL = r"""
const URL_ = %s;
const BODY_ = %s;
const HDR_ = %s;
return fetch(URL_, {method: 'POST', credentials: 'include', headers: HDR_,
                    body: JSON.stringify(BODY_)})
  .then(function (r) { return r.text().then(function (t) { return JSON.stringify({status: r.status, body: t}); }); })
  .catch(function (e) { return JSON.stringify({err: String(e)}); });"""

HDR_FILE = Path(__file__).resolve().parents[2] / "_logs" / "edit_headers.json"
KEEP_HDR = ("Anti-Content", "mallid", "content-type")


def load_headers(mallid: str = "") -> dict:
    """读 Anti-Content 请求头。

    ⚠ 头是**按店**的（三店 mallid 不同）：传 mallid 就读 `edit_headers_<mallid>.json`；
    不传则回退到通用的 `edit_headers.json`（兼容旧调用）。
    """
    p = HDR_FILE
    if mallid:
        cand = HDR_FILE.parent / ("edit_headers_%s.json" % mallid)
        if cand.exists():
            p = cand
    if not p.exists():
        return {"content-type": "application/json"}
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"content-type": "application/json"}
    return {k: v for k, v in h.items() if k in KEEP_HDR}


async def ev_async(cap, js, timeout=60):
    """fetch 是异步的，用 awaitPromise 等结果。"""
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(async function(){\n%s\n})()" % js,
                        "returnByValue": True, "awaitPromise": True}, timeout=timeout)
    if r.get("exceptionDetails"):
        return {"err": "EXC:" + str(r["exceptionDetails"])[:200]}
    v = (r.get("result") or {}).get("value")
    try:
        return json.loads(v)
    except Exception:
        return {"raw": v}


async def attach(port):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        return None
    return await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()


async def call_edit(cap, product_id, sku_id, wh_list, headers=None):
    body = {"productId": int(product_id), "productSkuId": int(sku_id),
            "shippingMode": 1,
            "warehouseStockList": [{"warehouseId": w, "warehouseStock": int(n)} for w, n in wh_list]}
    hdr = headers if headers is not None else load_headers()
    res = await ev_async(cap, JS_TMPL % (json.dumps(URL), json.dumps(body), json.dumps(hdr)))
    res["sent"] = body
    res["hdr_keys"] = list(hdr)
    return res


async def main_async(a):
    cap = await attach(a.port)
    if cap is None:
        print("没有商品列表页")
        return 2
    try:
        wh = [(x.split("=")[0], int(x.split("=")[1])) for x in a.wh]
        res = await call_edit(cap, a.spu, a.sku_id, wh)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if str(res.get("body", "")).find('"success":true') >= 0 else 3
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--spu", required=True)
    ap.add_argument("--sku-id", required=True)
    ap.add_argument("--wh", action="append", required=True,
                    help="warehouseId=stock，可重复")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
