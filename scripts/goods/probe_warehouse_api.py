# -*- coding: utf-8 -*-
"""试探「发货模式/仓库」相关只读接口，找能返回每仓 warehouseId 的那个。只读 GET/POST，不改数据。"""
from __future__ import annotations
import argparse, asyncio, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))
import cdp_net  # noqa
import api_switch_mode as api  # noqa

CANDS = [
    ("POST", "/visage-agent-seller/product/sku/shippingMode/detail"),
    ("POST", "/visage-agent-seller/product/sku/shippingMode/query"),
    ("POST", "/visage-agent-seller/product/sku/shippingMode/get"),
    ("POST", "/visage-agent-seller/product/sku/shippingMode/list"),
    ("POST", "/visage-agent-seller/product/sku/warehouse/list"),
    ("GET", "/visage-agent-seller/product/warehouse/list"),
    ("GET", "/visage-agent-seller/warehouse/list"),
    ("POST", "/visage-agent-seller/product/sku/detail"),
]

JS = r"""
const M_=%s, U_=%s, B_=%s, H_=%s;
const opt = {method: M_, credentials: 'include', headers: H_};
if (M_ === 'POST') { opt.body = JSON.stringify(B_); }
return fetch('https://agentseller.temu.com' + U_, opt)
  .then(function (r) { return r.text().then(function (t) { return JSON.stringify({status: r.status, body: t.slice(0, 400)}); }); })
  .catch(function (e) { return JSON.stringify({err: String(e)}); });"""


async def main(a):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("no page"); return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    hdr = api.load_headers()
    body = {"productId": int(a.spu), "productSkuId": int(a.sku_id)}
    try:
        for m, u in CANDS:
            js = JS % (json.dumps(m), json.dumps(u), json.dumps(body), json.dumps(hdr))
            r = await api.ev_async(cap, js)
            print("%-5s %-58s %s" % (m, u.split("/")[-1], json.dumps(r, ensure_ascii=False)[:260]), flush=True)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--spu", required=True)
    ap.add_argument("--sku-id", required=True)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
