# -*- coding: utf-8 -*-
"""直接读「上架草稿」的完整字段结构（不抓包，最可靠）。

为什么走这条路：
    2026-11-26 两轮抓包都失败了（CDP 互锁 + 导航断连），拿不到请求体/响应体。
    但草稿是**后端已经存好的**，`product/draft/query` 一次就能返回
    类目、属性、SKU、价格、图片、站点、语言等全部已填字段 —— 比抓填表过程靠谱。

前提：紫鸟里 BHY 窗口开着（任意 agentseller.temu.com 页面在就行，脚本借用其 cookie）。

用法：
    # 1) 先列出手上所有草稿
    python scripts/goods/read_draft.py --port 63854 --list

    # 2) 按 ID 读（不要 --list 时给的 productDraftId）
    python scripts/goods/read_draft.py --port 63854 --id 57682030962

    # 3) 读最近一个草稿并把响应落盘
    python scripts/goods/read_draft.py --port 63854 --latest --out out/draft

只读，不写任何数据。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402
import websockets  # noqa: E402

BASE = "https://agentseller.temu.com"
LIST_URL = BASE + "/visage-agent-seller/product/draft/pageQuery"
QUERY_URL = BASE + "/visage-agent-seller/product/draft/query"

#: 同步 XHR：表达式直接返回字符串，不需要 awaitPromise，也不会被页面 fetch 包装干扰
JS = ('(function(U_,B_,H_){try{var x=new XMLHttpRequest();x.open("POST",U_,false);'
      'x.setRequestHeader("content-type","application/json");'
      'for(var k in H_){try{x.setRequestHeader(k,H_[k]);}catch(e){}}'
      'x.send(JSON.stringify(B_));'
      'return JSON.stringify({s:x.status,b:String(x.responseText||"")});}'
      'catch(e){return JSON.stringify({s:0,b:"ERR "+String(e)});}})'
      '(%s,%s,%s)')


async def call(ws, url, body, hdr, timeout=60):
    async with websockets.connect(ws, max_size=128 * 1024 * 1024,
                                  ping_interval=None, ping_timeout=None) as w:
        nid = [0]
        pend: dict = {}
        evs: list = []

        async def send(m, p=None):
            nid[0] += 1
            i = nid[0]
            f = asyncio.get_running_loop().create_future()
            pend[i] = f
            await w.send(json.dumps({"id": i, "method": m, "params": p or {}}))
            end = asyncio.get_running_loop().time() + timeout
            while True:
                left = end - asyncio.get_running_loop().time()
                if left <= 0:
                    return {}
                try:
                    raw = await asyncio.wait_for(w.recv(), min(left, 10))
                except asyncio.TimeoutError:
                    continue
                mm = json.loads(raw)
                if "id" in mm and mm["id"] == i:
                    return mm.get("result") or {}
                evs.append(mm)

        js = JS % (json.dumps(url), json.dumps(body), json.dumps(hdr))
        r = await send("Runtime.evaluate", {"expression": js, "returnByValue": True,
                                            "userGesture": True})
        res = r.get("result") or {}
        if r.get("exceptionDetails"):
            raise RuntimeError("页面执行异常: %s"
                               % str(r["exceptionDetails"])[:300])
        val = res.get("value")
        d = json.loads(val or "{}")
        status = d.get("s")
        text = d.get("b") or ""
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        return status, text, parsed


async def main(a):
    # 借一个已登录页面的 cookie 上下文
    cands = [t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if t.get("type") == "page" and BASE in (t.get("url") or "")]
    if not cands:
        print("!! 没有 agentseller.temu.com 的页面。先把 BHY 窗口打开（任意该域名页面）。")
        print("   当前页面：")
        for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port):
            if t.get("type") == "page":
                print("     ", (t.get("url") or "")[:90])
        return 2
    cands.sort(key=lambda x: 0 if "/goods/" in (x.get("url") or "") else 1)
    ws = cands[0]["webSocketDebuggerUrl"]
    print("借用页面:", (cands[0].get("url") or "")[:80])

    # mallid 从 cookie 取
    st, txt, d = await call(ws, LIST_URL, {"page": 1, "pageSize": 1}, {})
    # 上面这一步顺带验证鉴权；mallid 单独取
    async with websockets.connect(ws, max_size=16 * 1024 * 1024,
                                  ping_interval=None, ping_timeout=None) as w:
        nid = [0]
        await w.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": "(function(){var ps=document.cookie.split(';');"
                          "for(var i=0;i<ps.length;i++){var p=ps[i].trim();"
                          "if(p.indexOf('mallid=')===0)return p.substring(7);}"
                          "return '';})()",
            "returnByValue": True}}))
        mallid = ""
        while True:
            raw = await asyncio.wait_for(w.recv(), 15)
            mm = json.loads(raw)
            if mm.get("id") == 1:
                mallid = str((mm.get("result") or {}).get("result", {}).get("value") or "")
                break
    print("mallid =", mallid)
    hdr = {"mallid": mallid} if mallid else {}

    outdir = Path(a.out) if Path(a.out).is_absolute() else ROOT / a.out
    outdir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")

    # ---- 列表 ----
    if a.list or a.latest:
        for probe in ({"page": 1, "pageSize": 50}, {"pageNum": 1, "pageSize": 50}):
            st, txt, d = await call(ws, LIST_URL, probe, hdr)
            print("\n[列表] body=%s → HTTP %s" % (probe, st))
            if st != 200:
                print("   ", txt[:300])
                continue
            j = d or {}
            res = j.get("result") or {}
            items = (res.get("pageItems") or res.get("list") or res.get("records") or [])
            print("   success=%s total=%s items=%d"
                  % (j.get("success"), res.get("total"), len(items)))
            if items:
                print("   字段:", list(items[0].keys())[:20])
                for it in items[:30]:
                    print("     id=%s  status=%s  %s"
                          % (it.get("productDraftId") or it.get("draftId"),
                             it.get("draftStatus") or it.get("status"),
                             str(it.get("productName") or it.get("title") or "")[:40]))
                (outdir / ("draft_list_%s.json" % ts)).write_text(
                    json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
            break

    # ---- 单条草稿 ----
    did = a.id
    if a.latest and not did:
        # 从列表里挑最新的
        st, txt, d = await call(ws, LIST_URL, {"page": 1, "pageSize": 50}, hdr)
        res = ((d or {}).get("result") or {})
        items = (res.get("pageItems") or res.get("list") or res.get("records") or [])
        if items:
            did = str(items[0].get("productDraftId") or items[0].get("draftId") or "")
    if not did:
        print("\n没有指定 --id，也没有可用的 --latest 结果。用 --list 看有哪些草稿。")
        return 0

    print("\n[草稿 %s]" % did)
    for body in ({"productDraftId": int(did) if did.isdigit() else did},
                 {"productDraftId": did},
                 {"draftId": did}):
        try:
            st, txt, d = await call(ws, QUERY_URL, body, hdr)
        except Exception as e:  # noqa: BLE001
            print("   body=%s → 异常 %s" % (body, str(e)[:150]))
            continue
        print("   body=%s → HTTP %s，响应 %d 字节" % (body, st, len(txt)))
        if st != 200:
            print("     ", txt[:300])
            continue
        j = d or {}
        if not j.get("success"):
            print("     ", json.dumps(j, ensure_ascii=False)[:300])
            continue
        res = j.get("result") or {}
        # 只打印结构，不打印全部内容（可能很大）
        print("     result 顶层字段:", list(res.keys())[:40])
        p = outdir / ("draft_%s_%s.json" % (did, ts))
        p.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
        print("     落盘:", p)
        # 关键子树概览
        for k in ("catList", "categories", "categoryPath", "properties", "propList",
                  "skuList", "skcList", "skcs", "priceInfo", "images", "imageList",
                  "sites", "languages", "productName", "title"):
            if k in res:
                v = res[k]
                if isinstance(v, list):
                    print("       %-16s list[%d]  首条keys=%s"
                          % (k, len(v), list(v[0].keys())[:12] if v and isinstance(v[0], dict) else "-"))
                elif isinstance(v, dict):
                    print("       %-16s dict keys=%s" % (k, list(v.keys())[:12]))
                else:
                    print("       %-16s %s" % (k, str(v)[:60]))
        break
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--list", action="store_true", help="列出草稿")
    ap.add_argument("--id", default="", help="productDraftId")
    ap.add_argument("--latest", action="store_true", help="读最新一个草稿")
    ap.add_argument("--out", default="out/draft")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
