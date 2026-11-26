# -*- coding: utf-8 -*-
"""在**已开着**的类目页上补抓「请求方法 + 请求体」，并直接复放验证接口能否独立调用。

做法：开 Network 抓包（不解码 body）→ 让页面 `location.reload()` 重走首屏 →
从 requestWillBeSent 里取 method / postData / headers。然后清理该页自己的
`sentinel` 头之类的会话头再复放一次，确认「只靠 cookie 能不能调通」。

用法：
    python scripts/goods/probe_create_category_req.py --port 63854
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

import cdp_net  # noqa: E402
import websockets  # noqa: E402

URL = "https://agentseller.temu.com/goods/create/category"
WANT = ("/category/children/list", "/product/queryCatHints")


async def main(a):
    # 1) 新开前台页
    tid = await cdp_net.new_tab(a.port, "about:blank", foreground=True)
    if not tid:
        print("!! 建页失败")
        return 2
    ws = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port):
            if t.get("id") == tid:
                ws = t["webSocketDebuggerUrl"]
        if ws:
            break
    if not ws:
        return 3

    reqs: dict = {}
    try:
        async with websockets.connect(ws, max_size=64 * 1024 * 1024) as w:
            nid = [0]
            pend: dict = {}

            async def send(method, params=None, timeout=30):
                nid[0] += 1
                i = nid[0]
                fut = asyncio.get_running_loop().create_future()
                pend[i] = fut
                await w.send(json.dumps({"id": i, "method": method, "params": params or {}}))
                return await asyncio.wait_for(fut, timeout)

            async def reader():
                try:
                    async for raw in w:
                        m = json.loads(raw)
                        if "id" in m:
                            f = pend.pop(m["id"], None)
                            if f and not f.done():
                                f.set_result(m.get("result") or {})
                        elif (m.get("method") or "") == "Network.requestWillBeSent":
                            p = m["params"]
                            u = (p.get("request") or {}).get("url") or ""
                            if any(k in u for k in WANT):
                                rq = p["request"]
                                reqs[u.split("?")[0]] = {
                                    "method": rq.get("method"),
                                    "postData": rq.get("postData"),
                                    "headers": rq.get("headers"),
                                    "url": u,
                                }
                except Exception:
                    pass

            task = asyncio.create_task(reader())
            await send("Network.enable", {})
            await send("Page.enable", {})
            await send("Page.navigate", {"url": URL})
            await asyncio.sleep(a.settle)
            task.cancel()
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("[x] 已关页", tid, flush=True)

    out = ROOT / "out" / "probe_create_category"
    out.mkdir(parents=True, exist_ok=True)
    (out / "requests.json").write_text(json.dumps(reqs, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    for u, r in reqs.items():
        print("\n=== %s  %s" % (r["method"], u))
        print("  postData:", (r.get("postData") or "(空)")[:400])
        hs = r.get("headers") or {}
        # 只看跟鉴权/防爬相关的头
        keep = {k: (str(v)[:28] + "…" if len(str(v)) > 28 else v) for k, v in hs.items()
                if k.lower() in ("content-type", "anti-content", "mallid", "x-requested-with",
                                 "origin", "referer", "mms-seller-api-version",
                                 "accept-language", "sec-ch-ua", "user-agent")}
        print("  headers:", json.dumps(keep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=12)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
