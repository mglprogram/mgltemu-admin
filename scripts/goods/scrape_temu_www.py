# -*- coding: utf-8 -*-
"""从 www.temu.com 搜索结果抓商品标题（按 Top sales 排序）。

实测结构（2026-11-26）：
    · 点 Sort by → Top sales 后 URL 带 `&filter_items=1%3A1`
    · 商品标题在 `img[alt]` 里，形如 "item picture   <标题>"，需剥前缀
    · 卡片链接是 `a[href*="-g-"]`，商品 ID 形如 `-g-<gid>.html`
    · 页面懒加载：要滚动到位置才会渲染，body 里有无意义零宽字符

用法：
    python scripts/goods/scrape_temu_www.py --port 61407 --q "9pcs hoodies"
    python scripts/goods/scrape_temu_www.py --port 61407 --q "hooded fishing shirt" --scroll 8
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
import websockets  # noqa: E402

#: 标题在 img[alt]，剥掉 "item picture" 之类前缀
EXTRACT_JS = r"""(function(){
  var out=[], seen={};
  var imgs=document.querySelectorAll('img[alt]');
  for(var i=0;i<imgs.length;i++){
    var alt=imgs[i].getAttribute('alt')||'';
    var t=alt.replace(/^\s*(item\s+picture|image|img)\s*/i,'').trim();
    if(t.length<12){continue;}
    var a=imgs[i].closest('a');
    var href=a?(a.getAttribute('href')||''):'';
    var m=href.match(/-g-(\d+)/);
    var gid=m?m[1]:('n'+i);
    if(seen[gid]){continue;}
    seen[gid]=1;
    var card=imgs[i].closest('[data-testid],div');
    var box=card?card.parentElement||card:null;
    var txt=box?((box.innerText||'').replace(/\s+/g,' ')):'';
    out.push({
      gid:gid,
      title:t,
      href:href,
      sold:(txt.match(/([\d.,]+)\s*sold/i)||[])[0]||'',
      price:(txt.match(/\$\s?[\d.,]+/)||[])[0]||''
    });
  }
  return JSON.stringify(out);
})()"""

SORT_BTN = r"""(function(){
  var bs=document.querySelectorAll('button,div,span,a');
  for(var i=0;i<bs.length;i++){
    var t=(bs[i].innerText||'').trim();
    if(/^Sort by/i.test(t)&&bs[i].children.length<=3){
      var r=bs[i].getBoundingClientRect();
      if(r.width>10){return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}
    }
  }
  return JSON.stringify({ok:false});
})()"""

TOP_SALES = r"""(function(){
  var bs=document.querySelectorAll('div,span,li,a');
  for(var i=0;i<bs.length;i++){
    var t=(bs[i].innerText||'').trim();
    if(/^Top sales$/i.test(t)){
      var r=bs[i].getBoundingClientRect();
      if(r.width>10&&r.height>5){return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}
    }
  }
  return JSON.stringify({ok:false});
})()"""


class Sess:
    def __init__(self, ws):
        self.ws, self.nid, self.pend = ws, 0, {}

    async def __aenter__(self):
        self.w = await websockets.connect(self.ws, max_size=128 * 1024 * 1024,
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

    async def call(self, method, params=None, timeout=45):
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

    async def ev(self, expr, timeout=45):
        r = await self.call("Runtime.evaluate",
                            {"expression": expr, "returnByValue": True, "userGesture": True},
                            timeout)
        if r.get("exceptionDetails"):
            return None
        return (r.get("result") or {}).get("value")

    async def click(self, x, y):
        for et, cc in (("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 1)):
            await self.call("Input.dispatchMouseEvent",
                            {"type": et, "x": x, "y": y, "button": "left", "clickCount": cc}, 15)
            await asyncio.sleep(0.08)

    async def wheel(self, dy):
        await self.call("Input.dispatchMouseEvent",
                        {"type": "mouseWheel", "x": 700, "y": 500,
                         "deltaX": 0, "deltaY": dy}, 15)


async def scrape_async(port: int, query: str, scroll: int = 6,
                       do_sort: bool = True, log=print,
                       auto_open: bool = True) -> dict:
    """抓一个关键词的 Top sales 商品标题。返回 {query, url, items}（可被 pipeline 复用）。

    auto_open=True 时，若窗口里没有 www.temu.com 页面会自动新开一个，用完关掉。
    """
    pages = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if t.get("type") == "page" and "www.temu.com" in (t.get("url") or "")]
    owned_tid = None
    if not pages:
        if not auto_open:
            raise RuntimeError("没有 www.temu.com 页面；先在店铺窗口打开 temu.com")
        log("  没有 temu.com 页，新开一个")
        owned_tid = await cdp_net.new_tab(port, "https://www.temu.com/", foreground=True)
        if not owned_tid:
            raise RuntimeError("新建 temu.com 标签页失败（端口 %d 不可用？）" % port)
        for _ in range(24):
            await asyncio.sleep(0.5)
            for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
                if t.get("id") == owned_tid:
                    pages = [t]
                    break
            if pages:
                break
        if not pages:
            raise RuntimeError("新开的 temu.com 标签页拿不到 ws 地址")

    ws = pages[0]["webSocketDebuggerUrl"]
    url = ("https://www.temu.com/search_result.html?search_key=%s&search_method=user"
           % quote(query))

    try:
        async with Sess(ws) as s:
            log("  导航 → %s" % query)
            await s.call("Page.navigate", {"url": url}, 30)
            await asyncio.sleep(7)

            if do_sort:
                btn = json.loads(await s.ev(SORT_BTN) or "{}")
                if btn.get("ok"):
                    await s.click(btn["x"], btn["y"])
                    await asyncio.sleep(1.5)
                    ts = json.loads(await s.ev(TOP_SALES) or "{}")
                    if ts.get("ok"):
                        await s.click(ts["x"], ts["y"])
                        await asyncio.sleep(6)
                        log("  已切 Top sales")
                    else:
                        log("  !! 未找到 Top sales")
                else:
                    log("  !! 未找到 Sort by")

            for _ in range(scroll):
                await s.wheel(2400)
                await asyncio.sleep(1.8)

            raw = await s.ev(EXTRACT_JS)
            try:
                items = json.loads(raw or "[]")
            except Exception:
                items = []
            final_url = await s.ev("location.href")
    finally:
        if owned_tid:
            await cdp_net.close_tab(port, owned_tid)
            log("  已关闭临时 temu.com 标签页")

    # 过滤非商品图
    clean = []
    for it in items:
        t = str(it.get("title") or "").strip()
        if len(t) < 20 or ".png" in t.lower() or "card" in t.lower():
            continue
        it["title"] = t
        clean.append(it)
    return {"query": query, "url": final_url, "items": clean}


def scrape(port: int, query: str, scroll: int = 6, do_sort: bool = True,
           log=print) -> dict:
    """同步包装。"""
    return asyncio.run(scrape_async(port, query, scroll, do_sort, log))


async def main(a):
    pages = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if t.get("type") == "page" and "www.temu.com" in (t.get("url") or "")]
    if not pages:
        print("没有 www.temu.com 页面；先在店铺窗口打开 temu.com")
        return 2
    ws = pages[0]["webSocketDebuggerUrl"]
    url = "https://www.temu.com/search_result.html?search_key=%s&search_method=user" % quote(a.q)

    async with Sess(ws) as s:
        print("导航 →", url)
        await s.call("Page.navigate", {"url": url}, 30)
        await asyncio.sleep(7)

        if a.sort:
            btn = json.loads(await s.ev(SORT_BTN) or "{}")
            if btn.get("ok"):
                await s.click(btn["x"], btn["y"])
                await asyncio.sleep(1.5)
                ts = json.loads(await s.ev(TOP_SALES) or "{}")
                if ts.get("ok"):
                    await s.click(ts["x"], ts["y"])
                    await asyncio.sleep(6)
                    print("已切换到 Top sales")
                else:
                    print("!! 未找到 Top sales 选项")
            else:
                print("!! 未找到 Sort by 按钮")

        for i in range(a.scroll):
            await s.wheel(2400)
            await asyncio.sleep(1.8)

        raw = await s.ev(EXTRACT_JS)
        try:
            items = json.loads(raw or "[]")
        except Exception:
            items = []
        final_url = await s.ev("location.href")

    print("\n== query=%r  排序=%s ==" % (a.q, "Top sales" if "1%3A1" in str(final_url) else str(final_url)[-40:]))
    print("抓到 %d 个商品\n" % len(items))
    for i, r in enumerate(items, 1):
        print("%2d. %s" % (i, r["title"][:115]))
        extra = " ".join(x for x in (r.get("price"), r.get("sold")) if x)
        if extra:
            print("    %s" % extra)

    outdir = ROOT / "out" / "temu-www-scrape"
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / ("search_%s_%s.json" % (re.sub(r"[^a-z0-9]+", "_", a.q.lower()),
                                         time.strftime("%Y%m%d-%H%M%S")))
    p.write_text(json.dumps({"query": a.q, "url": final_url, "items": items},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--q", required=True)
    ap.add_argument("--scroll", type=int, default=6)
    ap.add_argument("--sort", dest="sort", action="store_true", default=True)
    ap.add_argument("--no-sort", dest="sort", action="store_false")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
