"""抓 createSizeSpec / draft/save 的真实请求体，并对页面做兜底选择。

坑：
  1. 必须用 `Network.getRequestPostData`，`requestWillBeSent.request.postData` 常为空
  2. 页面随时可能被关掉/跳走，不能硬编码 `/goods/edit`，否则 IndexError
"""
import asyncio
import json
import sys
import time
import urllib.request

import websockets

PORT = 61407
WANT = ("createSizeSpec", "draft/save", "sizespec/save", "sizespec/create")


def pick_page() -> dict | None:
    targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json").read().decode())
    pages = [t for t in targets if t.get("type") == "page"
             and "agentseller" in (t.get("url") or "")]
    # 优先编辑页，其次任意卖家中心页
    for p in pages:
        if "/goods/edit" in p.get("url", ""):
            return p
    return pages[0] if pages else None


async def main(seconds: int) -> int:
    page = pick_page()
    if not page:
        print("没有可监听的卖家中心页面，先打开一个再跑", flush=True)
        return 2
    print("attach:", page.get("url"), flush=True)
    hits = []
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=128 * 1024 * 1024,
                                  ping_interval=None, ping_timeout=None) as w:
        await w.send(json.dumps({"id": 1, "method": "Network.enable"}))
        nid = 100
        end = time.time() + seconds
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(w.recv(), 5.0)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            if msg.get("method") != "Network.requestWillBeSent":
                continue
            p = msg["params"]
            url = p["request"]["url"].split("?")[0]
            if not any(k in url for k in WANT):
                continue
            rid = p["requestId"]
            body = p["request"].get("postData") or ""
            if not body:
                my = nid
                nid += 1
                await w.send(json.dumps({"id": my, "method": "Network.getRequestPostData",
                                         "params": {"requestId": rid}}))
                while True:
                    try:
                        m2 = json.loads(await asyncio.wait_for(w.recv(), 15.0))
                    except asyncio.TimeoutError:
                        break
                    if m2.get("id") == my:
                        body = (m2.get("result") or {}).get("postData") or ""
                        break
            print("\n=====", url, "| body", len(body), flush=True)
            print(body[:8000], flush=True)
            hits.append({"url": url, "body": body})
            with open(r"D:\AI-workplace\TEMU\out\size_capture.json", "w",
                      encoding="utf-8") as f:
                json.dump(hits, f, ensure_ascii=False, indent=2)
    print("\n共抓到", len(hits), "条 → out/size_capture.json", flush=True)
    return 0


raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 420)))
