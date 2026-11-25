# -*- coding: utf-8 -*-
"""探测帕沃尔（powerwarehouse / 易境通）发货全流程背后有没有 **JSON 接口**。

现状（`pw_hwc_flow.py`）：除了 `act=operate`（改仓库渠道）已经 HTTP 直提，
其余全是**页面 DOM 操作**：
    ensure_page → set_page_size → do_query（贴单号回车）
    → set_checks（勾选）→ click_submit（改仓渠道）→ click_xiadan（下单代发）
    → confirm_xiadan_dialog（弹窗确认）→ wait_xiadan

目标：抓一遍这些动作的真实请求（URL / 方法 / form data），看能不能全走 HTTP。

做法（只读）：浏览器级 Network 旁听 + 打开 `hwc_shop.php?act=shop_order&pb=1`，
记录所有非静态资源请求。
"""
import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import ziniao_store as zs  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\pw_api_probe.json"
URL = "https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1&uselastfilter=1"


def pick_window() -> int:
    """找一个已登录帕沃尔的窗口（帕沃尔页面通常在 BHY 窗口里）。"""
    for w in ("TM-BHY-YJF", "TM-BEL-MGL", "TM-KinglamanSport"):
        p = zs.probe_port_by_window(w)
        if p:
            return int(p)
    return 0


async def main():
    port = pick_window()
    if not port:
        print("没有可用窗口")
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port, timeout=10).read())
    print("窗口端口 %d，打开帕沃尔发货页…" % port)

    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=200 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params=None, sid=None):
            mid[0] += 1
            msg = {"id": mid[0], "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            return mid[0]

        await send("Target.setDiscoverTargets", {"discover": True})
        await send("Target.createTarget", {"url": URL, "background": True})

        recs, pending, seen = {}, {}, set()
        end = time.time() + 45
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError:
                continue
            except Exception as ex:  # noqa: BLE001
                print("ws:", str(ex)[:60])
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Target.attachedToTarget":
                if p.get("sessionId"):
                    await send("Network.enable", {"maxTotalBufferSize": 60000000},
                               p["sessionId"])
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and ti.get("targetId") not in seen:
                    seen.add(ti["targetId"])
                    await send("Target.attachToTarget",
                               {"targetId": ti["targetId"], "flatten": True})
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "powerwarehouse" not in u:
                    continue
                if any(u.endswith(x) for x in (".js", ".css", ".png", ".jpg", ".gif",
                                               ".woff", ".woff2", ".ico")):
                    continue
                rid = p.get("requestId")
                pending[rid] = {"url": u, "method": r.get("method"),
                                "postData": (r.get("postData") or "")[:600],
                                "headers": {k: str(v)[:80] for k, v in
                                            (r.get("headers") or {}).items()
                                            if k.lower() in
                                            ("content-type", "x-requested-with",
                                             "cookie", "referer")}}
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in pending:
                    rec = pending.pop(rid)
                    rec["status"] = (p.get("response") or {}).get("status")
                    rec["mime"] = (p.get("response") or {}).get("mimeType")
                    recs[rid] = rec

    print("\n帕沃尔域请求 %d 条：" % len(recs))
    for rec in recs.values():
        u = rec["url"].replace("https://www.powerwarehouse.net", "")
        is_api = bool(rec.get("postData")) or "act=" in u or "ajax" in u
        mark = "★" if is_api else " "
        print("%s %-4s %-6s %-58s %s"
              % (mark, rec.get("status"), rec.get("method"), u[:58],
                 str(rec.get("mime") or "")[:20]))
        if rec.get("postData"):
            print("      body:", rec["postData"][:200])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(list(recs.values()), open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已存 → %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
