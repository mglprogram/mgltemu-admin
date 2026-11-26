# -*- coding: utf-8 -*-
"""在页面已加载的 JS 里搜任意关键词，列出命中路径 + 所在 chunk。

用途：补探「图片上传」「提交发布」这类还没在流量里出现过的接口。
比 inspect_api_call.py 更轻：只报路径和 chunk，不打印大段源码。

用法：
    python scripts/goods/scan_js_for.py --port 61407 --pattern "image/upload|upload/image|oss"
    python scripts/goods/scan_js_for.py --port 61407 --pattern "submit|publish" --show 3
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
        print("没有 agentseller 页面")
        return 2
    tgt = pages[0]
    print("用页:", (tgt.get("url") or "")[:90])

    pat = re.compile(a.pattern, re.I)
    hits: dict = {}
    async with Sess(tgt["webSocketDebuggerUrl"]) as s:
        urls = json.loads(await s.ev(LIST_JS) or "[]")
        print("js 文件 %d 个，开始扫 …" % len(urls))
        for n, u in enumerate(urls, 1):
            name = u.rsplit("/", 1)[-1]
            txt = await s.ev(FETCH_JS % json.dumps(u))
            if not txt or txt.startswith("ERR "):
                continue
            # 只认「像接口路径」的命中：含 / 且长度适中
            for m in pat.finditer(txt):
                st = max(0, m.start() - 60)
                en = min(len(txt), m.end() + 60)
                frag = txt[st:en]
                # 抓出紧邻的完整路径
                pm = re.search(r"[A-Za-z0-9_\-/]{6,90}", frag)
                key = pm.group(0) if pm else m.group(0)
                if key not in hits:
                    hits[key] = {"chunk": name, "snippet": frag.replace("\n", " ")[:180]}
            if n % 15 == 0:
                print("   … 已扫 %d/%d，命中 %d" % (n, len(urls), len(hits)), flush=True)

    print("\n=== 命中 %d 条 ===" % len(hits))
    for k, v in sorted(hits.items()):
        print("\n  %s" % k)
        print("     chunk: %s" % v["chunk"])
        if a.show:
            print("     …%s…" % v["snippet"])

    if hits:
        out = ROOT / "out" / "js_scan.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n落盘", out)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--pattern", required=True)
    ap.add_argument("--show", type=int, default=0, help="是否显示片段（1/0）")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
