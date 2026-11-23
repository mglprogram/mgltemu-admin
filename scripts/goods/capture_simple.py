# -*- coding: utf-8 -*-
"""极简抓包：直接把商品列表页所有 POST 请求（含 body）实时打到 stdout。

不带任何正则/环境依赖，用来拿「修改库存」提交的真实接口。跑一次就够。

用法：
    python scripts/goods/capture_simple.py --port 50748
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402


async def run(port: int, seconds: int):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    ws = None
    for p in pages:
        try:
            await asyncio.wait_for(cdp_net._one_shot(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                                                     {"expression": "1", "returnByValue": True}),
                                   timeout=8)
            ws = p["webSocketDebuggerUrl"]
            print("挂在", p["id"][:8], p.get("url", "")[:60], flush=True)
            break
        except Exception:
            continue
    if not ws:
        print("没有可用页")
        return 2

    seen = []

    class Cap(cdp_net.NetCapture):
        def _on_event(self, m):
            super()._on_event(m)
            method = m.get("method") or ""
            if method != "Network.requestWillBeSent":
                return
            p = m.get("params") or {}
            req = p.get("request") or {}
            url = req.get("url") or ""
            if req.get("method") != "POST":
                return
            if any(k in url for k in ("/api/", "agent-seller", "stock", "inventory")):
                rid = p.get("requestId")
                body = req.get("postData") or ""
                rec = {"ts": time.strftime("%H:%M:%S"), "url": url, "post": body[:1200],
                       "headers": dict(req.get("headers") or {})}
                seen.append(rec)
                print("POST", url[:110], flush=True)
                print("  BODY", body[:800], flush=True)
                if not body:
                    asyncio.ensure_future(self._grab_body(rid, url))
                # 顺手把 Anti-Content 存一份，供接口复用
                # ⚠ 2026-11-23：头是**按店**的（三店 mallid 不同），必须分店落盘，
                #   否则后抓到的会覆盖前一个店的，导致 BEL/KLS 拿到 BHY 的头（403）。
                try:
                    h = {k.lower(): v for k, v in (req.get("headers") or {}).items()}
                    anti = h.get("anti-content") or h.get("anti_content") or ""
                    if anti:
                        base = Path(__file__).resolve().parents[2] / "_logs"
                        mallid = h.get("mallid") or ""
                        payload = json.dumps({"Anti-Content": anti, "mallid": mallid,
                                              "content-type": "application/json"},
                                             ensure_ascii=False)
                        (base / "edit_headers.json").write_text(payload, encoding="utf-8")
                        if mallid:
                            (base / ("edit_headers_%s.json" % mallid)).write_text(
                                payload, encoding="utf-8")
                        print("  ANTI saved (%d chars, mallid=%s)" % (len(anti), mallid),
                              flush=True)
                except Exception:
                    pass

        async def _grab_body(self, rid, url):
            try:
                r = await self.send("Network.getRequestPostData", {"requestId": rid}, timeout=10)
                print("  BODY(retro)", (r.get("postData") or "")[:800], flush=True)
            except Exception:
                pass

    cap = await Cap(ws, match=()).__aenter__()
    try:
        await cap.send("Network.enable", {"maxPostDataSize": 8 * 1024 * 1024}, timeout=20)
        print("抓包中…（%ds）请去页面提交一次库存修改" % seconds, flush=True)
        await asyncio.sleep(seconds)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    out = Path(__file__).resolve().parents[2] / "_logs" / "stock_posts.jsonl"
    with open(out, "a", encoding="utf-8") as f:
        for r in seen:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("共 %d 条 POST → %s" % (len(seen), out), flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--seconds", type=int, default=300)
    a = ap.parse_args()
    return asyncio.run(run(a.port, a.seconds))


if __name__ == "__main__":
    raise SystemExit(main())
