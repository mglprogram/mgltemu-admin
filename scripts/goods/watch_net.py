"""监听商品编辑页发出的全部 XHR（Network 域），打印路径 + 请求体 + 响应摘要。

用法:
    python -u scripts/goods/watch_net.py --port 61407 --url-contains "/goods/edit" --seconds 300

设计要点（都是踩过的坑）:
  · 用 Network.* 而不是 Fetch.*：Fetch 拦截会暂停请求，处理不当会让页面卡死
  · loadingFinished 没有 request 对象，必须先在 requestWillBeSent 里记 requestId → url
  · 只打印业务接口（anniston/visage/phoenix 等），静态资源直接略过
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.request

import websockets

SKIP_SUFFIX = (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2",
               ".ico", ".gif", ".webp", ".map", ".ttf", ".otf")
BIZ = ("anniston-agent-seller", "visage-agent-seller", "phoenix-mms",
       "general_auth", "mms", "seller-api")


def _pages(port: int) -> list[dict]:
    raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json").read().decode()
    return [t for t in json.loads(raw) if t.get("type") == "page"]


async def run(port: int, url_contains: str, seconds: int, out_path: str) -> int:
    pages = [p for p in _pages(port) if url_contains in (p.get("url") or "")]
    if not pages:
        print("没有匹配页面，候选：")
        for p in _pages(port):
            print("  ", p.get("url"))
        return 2
    page = pages[0]
    print("attach:", page["url"], flush=True)

    records: list[dict] = []
    async with websockets.connect(page["webSocketDebuggerUrl"],
                                  max_size=128 * 1024 * 1024,
                                  ping_interval=None, ping_timeout=None) as w:
        await w.send(json.dumps({"id": 1, "method": "Network.enable"}))
        url_of: dict[str, str] = {}
        body_of: dict[str, str] = {}
        end = time.time() + seconds
        while time.time() < end:
            left = end - time.time()
            try:
                raw = await asyncio.wait_for(w.recv(), min(left, 5.0))
            except asyncio.TimeoutError:
                continue
            msg = json.loads(raw)
            m = msg.get("method")
            if m == "Network.requestWillBeSent":
                p = msg["params"]
                req = p["request"]
                url = req["url"]
                rid = p["requestId"]
                url_of[rid] = url
                if any(k in url for k in BIZ) and not url.split("?")[0].endswith(SKIP_SUFFIX):
                    body_of[rid] = req.get("postData") or ""
                    print("\n>>>", req.get("method"), url.split("?")[0], flush=True)
                    if body_of[rid]:
                        print("    body:", body_of[rid][:1500], flush=True)
                    records.append({"url": url, "method": req.get("method"),
                                    "postData": body_of[rid]})
            elif m == "Network.loadingFailed":
                pass
        try:
            await w.send(json.dumps({"id": 9999, "method": "Network.disable"}))
        except Exception:
            pass

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    print(f"\n共捕获 {len(records)} 条业务接口，落盘 {out_path}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="监听店铺窗口内的业务接口")
    ap.add_argument("--port", type=int, default=61407)
    ap.add_argument("--url-contains", default="/goods/edit")
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--out", default=r"D:\AI-workplace\TEMU\out\net_watch.json")
    a = ap.parse_args()
    return asyncio.run(run(a.port, a.url_contains, a.seconds, a.out))


if __name__ == "__main__":
    raise SystemExit(main())
