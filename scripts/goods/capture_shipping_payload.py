# -*- coding: utf-8 -*-
"""抓 `POST .../sku/shippingMode/edit` 的请求体 + 响应，用于评估能否直接调接口批量改。

默认空跑：只要页面发生一次「发货模式」提交（人工确认），就把 payload 落到
`_logs/shipping_edit_payload.jsonl`。

用法：
    python scripts/goods/capture_shipping_payload.py --port 59670 --timeout 3600
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

LOGS = Path(__file__).resolve().parents[2] / "_logs"
OUT = LOGS / "shipping_edit_payload.jsonl"
KEYS = ["shippingMode"]


class Cap(cdp_net.NetCapture):
    def __init__(self, *a, keys=None, **kw):
        super().__init__(*a, **kw)
        self.pd = []
        self.keys = keys or KEYS

    def _hit(self, url: str) -> bool:
        return any(k in url for k in self.keys)

    def _on_event(self, m):
        super()._on_event(m)
        if m.get("method") == "Network.requestWillBeSent":
            p = m.get("params") or {}
            req = p.get("request") or {}
            url = req.get("url") or ""
            if self._hit(url):
                rid = p.get("requestId")
                self.pd.append({"url": url, "method": req.get("method"),
                                "postData_inline": req.get("postData") or "", "rid": rid})
                asyncio.ensure_future(self._pd(rid, url))
        elif m.get("method") == "Network.responseReceived":
            p = m.get("params") or {}
            url = (p.get("response") or {}).get("url") or ""
            if self._hit(url):
                self.pd.append({"url": url,
                                "status": (p.get("response") or {}).get("status")})

    async def _pd(self, rid, url):
        try:
            r = await self.send("Network.getRequestPostData", {"requestId": rid}, timeout=15)
            self.pd.append({"url": url, "postData_get": r.get("postData") or ""})
        except Exception as e:  # noqa: BLE001
            self.pd.append({"url": url, "postData_err": str(e)[:120]})


async def main_async(a) -> int:
    end = time.time() + a.timeout
    seen = 0
    while time.time() < end:
        pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
                 if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
        if not pages:
            await asyncio.sleep(10)
            continue
        try:
            cap = await Cap(pages[-1]["webSocketDebuggerUrl"], match=tuple(ap.match.split(","))).__aenter__()
        except Exception as e:  # noqa: BLE001
            print("attach fail: %s" % str(e)[:120], flush=True)
            await asyncio.sleep(5)
            continue
        print("已挂上抓包 port=%d" % a.port, flush=True)
        try:
            while time.time() < end:
                await asyncio.sleep(2.0)
                while len(cap.pd) > seen:
                    rec = cap.pd[seen]
                    seen += 1
                    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    with open(OUT, "a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    print(json.dumps(rec, ensure_ascii=False)[:600], flush=True)
                for h in cap.hits[-3:]:
                    if h.get("url", "").find(KEY) >= 0:
                        with open(OUT, "a", encoding="utf-8") as f:
                            f.write(json.dumps({"resp": h, "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                                               ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            print("异常重连: %s" % str(e)[:120], flush=True)
            await asyncio.sleep(5)
        finally:
            try:
                await cap.__aexit__(None, None, None)
            except Exception:
                pass
    print("抓包结束", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--match", default="shippingMode,warehouse")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
