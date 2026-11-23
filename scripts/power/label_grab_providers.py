# -*- coding: utf-8 -*-
"""抓 `batch_query_shipping_provider_optional` 的**完整 body + 响应**（A 方案）。

用法：先起监听，然后在 BHY 页面上把某单的仓库切到 kansas、让物流下拉刷出来（别提交）。
产物：`_logs/temu-buylabel-api/batch_providers.json`
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import ziniao_store as zs  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api"


async def main():
    port = int(zs.probe_port_by_window("TM-BHY-YJF") or 0)
    tabs = zs.list_page_targets(port)
    tab = [t for t in tabs if "online-ship" in str(t.get("url") or "")]
    if not tab:
        print("⚠ BHY 没有「在线下单」页 → 请先点开一单的在线下单")
        return 2
    ws_url = tab[0].get("webSocketDebuggerUrl")
    print("监听:", str(tab[0].get("url"))[:100])
    print("👉 请把某单的「发货仓库」切成 kansas（或点「物流服务」下拉），让渠道刷出来…",
          flush=True)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "batch_providers.json")
    recs, pending = [], {}

    async with websockets.connect(ws_url, max_size=128 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.enable",
                                 "params": {"maxTotalBufferSize": 60000000}}))
        loop = asyncio.get_event_loop()
        end = loop.time() + 240
        n = 100000
        while loop.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, end - loop.time()))
            except asyncio.TimeoutError:
                break
            except Exception as ex:  # noqa: BLE001
                print("ws 断开:", str(ex)[:80])
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "shipping_provider_optional" not in u:
                    continue
                rid = p.get("requestId")
                pending[rid] = {"url": u, "method": r.get("method"),
                                "headers": {k: str(v)[:300]
                                            for k, v in (r.get("headers") or {}).items()},
                                "postData": r.get("postData") or ""}
                tag = " ★批量" if "batch_" in u else ""
                print("\n→ %s %s%s（body %d 字节）"
                      % (r.get("method"), u[-60:], tag,
                         len(pending[rid]["postData"])), flush=True)
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in pending:
                    rec = pending.pop(rid)
                    rec["status"] = (p.get("response") or {}).get("status")
                    recs.append(rec)
                    n += 1
                    await ws.send(json.dumps({"id": n,
                                              "method": "Network.getResponseBody",
                                              "params": {"requestId": rid}}))
                    json.dump(recs, open(path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
            elif m.get("id", 0) >= 100000 and "result" in m:
                body = (m["result"] or {}).get("body") or ""
                if recs:
                    recs[-1]["responseBody"] = body[:200000]
                    json.dump(recs, open(path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)

    print("\n落盘 %d 条 → %s" % (len(recs), path))
    for r in recs:
        tag = "批量" if "batch_" in r["url"] else "单渠道"
        print("  [%s] %-4s body=%d 响应=%d"
              % (tag, r.get("status"), len(r.get("postData") or ""),
                 len(r.get("responseBody") or "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
