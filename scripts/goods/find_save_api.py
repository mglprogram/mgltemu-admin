# -*- coding: utf-8 -*-
"""找出「保存草稿」接口：从发布页的 JS chunk 里静态搜 API 路径。

背景：提交发布暂时不做，只需要「保存草稿」。但保存接口没在流量里出现过
（因为没点过保存）。这次反过来：直接读发布页加载的 JS，把里面出现的
`product/draft/*`、`product/*save*`、`product/*submit*` 之类的路径全捞出来。

用法：
    python scripts/goods/find_save_api.py --port 61407 --page-index 0
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

#: 在 chunk 源码里搜这些候选路径
PAT = re.compile(r"(?:product/(?:draft|submit|save|publish)[A-Za-z0-9_/\-]*"
                 r"|draft/[A-Za-z0-9_/\-]+"
                 r"|[A-Za-z0-9_/\-]*(?:saveDraft|saveProduct|submitProduct|"
                 r"createProduct|publishProduct)[A-Za-z0-9_/\-]*)")

FETCH_JS = """(function(){
  try{
    var x=new XMLHttpRequest();
    x.open('GET', %s, false);
    x.send();
    return x.responseText || '';
  }catch(e){ return 'ERR ' + String(e); }
})()"""


class Sess:
    def __init__(self, ws):
        self.ws = ws
        self.nid = 0
        self.pend: dict = {}
        self.evs: list = []

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
                else:
                    self.evs.append(m)
        except Exception:
            pass

    async def call(self, method, params=None, timeout=60):
        self.nid += 1
        i = self.nid
        fut = asyncio.get_running_loop().create_future()
        self.pend[i] = fut
        await self.w.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        try:
            m = await asyncio.wait_for(fut, timeout)
        except Exception:
            return {}
        return m.get("result") or {}

    async def ev(self, expr, timeout=60):
        r = await self.call("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True}, timeout)
        return (r.get("result") or {}).get("value")


async def main(a):
    pages = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if t.get("type") == "page" and "agentseller" in (t.get("url") or "")]
    if not pages:
        print("没有 agentseller 页面")
        return 2
    tgt = pages[a.page_index]
    print("用页:", (tgt.get("url") or "")[:90])

    async with Sess(tgt["webSocketDebuggerUrl"]) as s:
        raw = await s.ev(LIST_JS)
        try:
            js_urls = json.loads(raw or "[]")
        except Exception:
            js_urls = []
        print("页面已加载 %d 个 js" % len(js_urls))

        hits: dict = {}
        for u in js_urls:
            txt = await s.ev(FETCH_JS % json.dumps(u))
            if not txt or txt.startswith("ERR "):
                continue
            for m in PAT.finditer(txt):
                p = m.group(0)
                if len(p) < 8:
                    continue
                hits.setdefault(p, set()).add(u.rsplit("/", 1)[-1][:28])

    print("\n=== 命中 %d 个候选路径 ===" % len(hits))
    for p in sorted(hits):
        print("  %-52s  (%s)" % (p, ",".join(sorted(hits[p]))[:60]))
    if hits:
        out = ROOT / "out" / "save_api_candidates.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({k: sorted(v) for k, v in hits.items()},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n落盘", out)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--page-index", type=int, default=0)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
