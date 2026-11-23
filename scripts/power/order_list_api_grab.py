# -*- coding: utf-8 -*-
"""找订单列表接口 v2：先真正点一次「查询」（用 Input.dispatchMouseEvent），
再旁听——上一版只取坐标没点，所以什么都没抓到。

要点：
  · 只 attach 订单列表页，Network.enable 后**主动点查询**
  · 把每个 /mms/ 响应体都存，重点看哪个含 goodsId/skuId/orderSn
"""
import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import ziniao_store as zs  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api"
SKIP = ("pftka", "/pmm/", "monitor")


async def main():
    win = sys.argv[1] if len(sys.argv) > 1 else "TM-BHY-YJF"
    port = int(zs.probe_port_by_window(win) or 0)
    tab = [t for t in zs.list_page_targets(port)
           if "orders.html" in str(t.get("url") or "")]
    if not tab:
        print("没有订单列表页")
        return 2
    tid = tab[0].get("id") or tab[0].get("targetId")
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port).read())
    recs, bodies = {}, {}
    path = os.path.join(OUT, "order_list_api.json")

    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=200 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params=None, sid=None, wait_id=None):
            mid[0] += 1
            msg = {"id": mid[0] if wait_id is None else wait_id,
                   "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            return msg["id"]

        await send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = None
        t0 = asyncio.get_event_loop().time() + 8
        while asyncio.get_event_loop().time() < t0 and not sid:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            except Exception:  # noqa: BLE001
                break
            if m.get("method") == "Target.attachedToTarget":
                sid = (m.get("params") or {}).get("sessionId")
        print("sessionId:", sid)
        await send("Network.enable", {"maxTotalBufferSize": 100000000}, sid)

        # 取「查询」按钮坐标
        await send("Runtime.evaluate", {
            "expression": r"""(() => {
              const vis = e => { const r = e.getBoundingClientRect();
                return e.getClientRects().length>0 && r.width>0 && r.height>0; };
              const b = [...document.querySelectorAll('button')].filter(vis)
                .find(x => (x.innerText||'').replace(/\s+/g,'') === '查询');
              if (!b) return 'no-btn';
              const r = b.getBoundingClientRect();
              return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
            })()""", "returnByValue": True}, sid)

        # 等坐标回执
        box = {}
        t1 = asyncio.get_event_loop().time() + 6
        while asyncio.get_event_loop().time() < t1:
            m = json.loads(await ws.recv())
            if m.get("id") == mid[0]:
                v = (m.get("result") or {}).get("result", {}).get("value")
                print("查询按钮:", v)
                try:
                    box = json.loads(v or "{}")
                except Exception:  # noqa: BLE001
                    box = {}
                break

        # ★ 真正点下去
        if box.get("x"):
            for t in ("mousePressed", "mouseReleased"):
                await ws.send(json.dumps({"id": 90000 + mid[0], "sessionId": sid,
                                          "method": "Input.dispatchMouseEvent",
                                          "params": {"type": t, "x": box["x"], "y": box["y"],
                                                     "button": "left", "clickCount": 1}}))
            print("已点「查询」")
        else:
            print("⚠ 没找到「查询」按钮")

        print("旁听 25s…", flush=True)
        loop = asyncio.get_event_loop()
        end = loop.time() + 25
        while loop.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, end - loop.time()))
            except asyncio.TimeoutError:
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "/mms/" not in u or any(s in u for s in SKIP):
                    continue
                recs[p.get("requestId")] = {"url": u, "method": r.get("method"),
                                            "postData": (r.get("postData") or "")[:1500]}
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in recs:
                    recs[rid]["status"] = (p.get("response") or {}).get("status")
                    await send("Network.getResponseBody", {"requestId": rid}, sid)
            elif me == "Network.getResponseBody" or ("id" in m and "result" in m
                                                     and (m["result"] or {}).get("body")):
                pass
            elif "id" in m and "result" in m:
                # 按请求顺序把响应体配回去（简化：取最后一个还没配的）
                for rid in recs:
                    if "responseBody" not in recs[rid]:
                        recs[rid]["responseBody"] = str((m["result"] or {}).get("body") or "")[:4000]
                        break

    json.dump(list(recs.values()), open(path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n抓到 %d 个接口 → %s\n" % (len(recs), path))
    for rec in recs.values():
        rb = str(rec.get("responseBody") or "")
        hit = [k for k in ("goodsId", "skuId", "orderSn", "parentOrderSn") if k in rb]
        print("  %-4s %-60s %s" % (rec.get("status"), rec["url"][:60],
                                   ("★ " + ",".join(hit)) if hit else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
