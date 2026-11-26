# -*- coding: utf-8 -*-
"""从 chunk 源码里提取某个接口的调用上下文（方法、前后参数、是否 POST）。

用法：
    python scripts/goods/inspect_api_call.py --port 61407 --api product/draft/save
    python scripts/goods/inspect_api_call.py --port 61407 --api draft/save --chunk product-create-edit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
import websockets  # noqa: E402

LIST_JS = ("JSON.stringify(Array.from(performance.getEntriesByType('resource'))"
           ".filter(function(e){return /\\.js(\\?|$)/.test(e.name)})"
           ".map(function(e){return e.name}))")
FETCH_JS = """(function(){
  try{var x=new XMLHttpRequest();x.open('GET', %s, false);x.send();
    return x.responseText||'';}catch(e){return 'ERR '+String(e);}
})()"""


class Sess:
    def __init__(self, ws):
        self.ws, self.nid, self.pend = ws, 0, {}

    async def __aenter__(self):
        self.w = await websockets.connect(self.ws, max_size=256 * 1024 * 1024,
                                          ping_interval=None, ping_timeout=None)
        self.task = asyncio.create_task(self._rd())
        return self

    async def __aexit__(self, *a):
        self.task.cancel()
        try:
            await self.w.close()
        except Exception:
            pass

    async def _rd(self):
        try:
            async for raw in self.w:
                m = json.loads(raw)
                if "id" in m:
                    f = self.pend.pop(m["id"], None)
                    if f and not f.done():
                        f.set_result(m)
        except Exception:
            pass

    async def ev(self, expr, timeout=90):
        self.nid += 1
        i = self.nid
        fut = asyncio.get_running_loop().create_future()
        self.pend[i] = fut
        await self.w.send(json.dumps({"id": i, "method": "Runtime.evaluate",
                                      "params": {"expression": expr, "returnByValue": True}}))
        try:
            m = await asyncio.wait_for(fut, timeout)
        except Exception:
            return None
        return ((m.get("result") or {}).get("result") or {}).get("value")


async def main(a):
    pages = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if t.get("type") == "page" and "agentseller" in (t.get("url") or "")]
    if not pages:
        return 2
    tgt = pages[a.page_index]
    print("用页:", (tgt.get("url") or "")[:90])

    found_any = False
    async with Sess(tgt["webSocketDebuggerUrl"]) as s:
        urls = json.loads(await s.ev(LIST_JS) or "[]")
        for u in urls:
            name = u.rsplit("/", 1)[-1]
            if a.chunk and a.chunk not in u:
                continue
            txt = await s.ev(FETCH_JS % json.dumps(u))
            if not txt or txt.startswith("ERR "):
                continue
            idx = txt.find(a.api)
            if idx < 0:
                continue
            found_any = True
            print("\n=== 命中 chunk: %s ===" % name)
            # 打印命中点前后各若干字符，看调用方式
            for m in re.finditer(re.escape(a.api), txt):
                st = max(0, m.start() - 600)
                en = min(len(txt), m.end() + 400)
                frag = txt[st:en]
                print("\n--- 片段 @%d ---" % m.start())
                print(frag.replace("\\n", " "))
                if m.start() > 40000:
                    break

    if not found_any:
        print("\n没在已加载 js 里找到 %r" % a.api)
        print("试试 --chunk product-create-edit，或者确认草稿页是否已打开")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--api", required=True, help="接口路径片段，如 product/draft/save")
    ap.add_argument("--chunk", default="", help="只搜名字含该串的 chunk")
    ap.add_argument("--page-index", type=int, default=0)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
