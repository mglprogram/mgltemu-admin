# -*- coding: utf-8 -*-
"""探测 TEMU 上链接后续步骤：先确认「这店到底能不能建商品」。

背景（2026-11-26 第一轮）：`/goods/create/category` 只打 2 个类目接口；
`/goods/create/publish` 能打开，但只打了菜单/配置接口，**没打任何商品接口** ——
说明它很可能是一个空壳路由（权限不够 / 入口被藏），URL 存在不等于能用。

本脚本回答三件事：
    ① 这店能不能建商品：`pageInfo` 里有没有新建权限、`category/children/list` 能不能用
    ② 上架流程的真实后续接口：从「已存在/在编辑的商品」反查
       —— 打开商品编辑页 `goods/edit?productId=…` 抓包，属性/价格/SKU 接口一定在
    ③ 选中类目之后本该打哪些接口（用 queryCatHints 拿一个真叶子，看页面有没有反应）

只读，不改任何数据。用完关页（含进程退出兜底）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import cdp_net  # noqa: E402

BASE = "https://agentseller.temu.com"
CAT_PAGE = BASE + "/goods/create/category"
PUBLISH_PAGE = BASE + "/goods/create/publish"

JS_POST = ('(function(U_,B_,H_){try{var x=new XMLHttpRequest();x.open("POST",U_,false);'
           'x.setRequestHeader("content-type","application/json");'
           'for(var k in H_){try{x.setRequestHeader(k,H_[k]);}catch(e){}}'
           'x.send(JSON.stringify(B_));'
           'return JSON.stringify({s:x.status,b:String(x.responseText||"").slice(0,500000)});}'
           'catch(e){return JSON.stringify({s:0,b:"ERR "+String(e)});}})'
           '(%s,%s,%s)')

#: 静态资源/埋点过滤
SKIP = re.compile(r"\.(js|css|png|jpe?g|svg|gif|woff2?|ico|webp)(\?|$)|"
                  r"^data:|^blob:|^chrome-extension:", re.I)
#: 全局导航壳接口（跟商品无关），列出来只是为了让「商品接口」更醒目
SHELL = re.compile(r"/api/phantom/|/api/sec-csp/|/api/server/_stm|quick/merchant/|"
                   r"msgBox|ladyfish|marvel-mms|queryTotalExam|/drogon-api/|"
                   r"temu-sca-config|/lollipop/|gambit/api|feedback|"
                   r"web-performace|pftk\.temu\.com|sell\.temu\.com/site-cdn")


async def open_page(port: int, url: str, settle: float, evals=()):
    """新开前台页 → 抓包 → 等 settle → 跑 evals → 返回 (hits, urls, ctx)。"""
    tid = await cdp_net.new_tab(port, url, foreground=True)
    if not tid:
        raise RuntimeError("建页失败 port=%s" % port)
    ws = None
    for _ in range(24):
        await asyncio.sleep(0.5)
        for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
            if t.get("id") == tid:
                ws = t.get("webSocketDebuggerUrl")
                break
        if ws:
            break
    if not ws:
        raise RuntimeError("拿不到 ws")

    ctx = {"url": url, "evals": []}
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(settle)
            for name, js in evals:
                v, exc = await cap.eval(js, timeout=60)
                rec = {"name": name, "value": v, "exc": str(exc)[:200] if exc else None}
                ctx["evals"].append(rec)
                print("   [%s] %s" % (name, str(v)[:300]), flush=True)
                await asyncio.sleep(a_settle_after)
            urls = dict(cap._urls)
            reqs = dict(cap._bodies)
            hits = list(cap.hits)
            ctx["final"] = None
            v, _ = await cap.eval(
                "JSON.stringify({url:location.href,t:document.title,"
                "txt:(document.body&&document.body.innerText||'').slice(0,400)})")
            ctx["final"] = v
    finally:
        await cdp_net.close_tab(port, tid)
        print("   [x] 已关页 %s" % tid[:8], flush=True)
    return hits, urls, reqs, ctx


a_settle_after = 4.0


def api_list(urls: dict) -> list:
    out = []
    for rid, u in urls.items():
        if not u or SKIP.search(u):
            continue
        if "agentseller.temu.com" not in u:
            continue
        p = u.replace(BASE, "")
        if p.startswith("/goods/create/category") or p == "/goods/create/publish":
            continue
        out.append(p)
    return sorted(set(out))


async def main(a):
    global a_settle_after
    a_settle_after = a.step_settle
    outdir = ROOT / "out" / "probe_create_publish"
    outdir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    report: dict = {"ts": ts, "port": a.port, "stages": {}}

    # ---------------- ① 类目页 + 权限 ----------------
    print("[①] 类目页 + 权限", flush=True)
    perm_js = []
    for name, path in (("pageInfo", "/bert/api/page/info/agentSeller/pageInfo"),
                       ("menu", "/api/seller/auth/menu"),
                       ("userInfo", "/api/seller/auth/userInfo")):
        perm_js.append((name, JS_POST % (json.dumps(BASE + path), json.dumps({}),
                                         json.dumps({"mallid": "__MALL__"}))))
    hits, urls, reqs, ctx = await _stage_cat(a, perm_js)
    report["stages"]["category"] = {"apis": api_list(urls), "evals": ctx["evals"],
                                    "final": ctx["final"]}
    print("   类目页接口：", api_list(urls), flush=True)

    # ---------------- ② 商品编辑页（真实上架后续接口在这里） ----------------
    if a.product_id:
        print("\n[②] 商品编辑页 productId=%s" % a.product_id, flush=True)
        eurl = BASE + "/goods/edit?from=productList&productId=%s" % a.product_id
        hits2, urls2, reqs2, ctx2 = await open_page(a.port, eurl, a.settle, evals=())
        apis2 = [p for p in api_list(urls2) if not SHELL.search(p)]
        report["stages"]["edit"] = {"url": eurl, "apis": apis2, "final": ctx2["final"]}
        for h in hits2:
            p = h["url"].replace(BASE, "")
            if any(k in p for k in ("categor", "cat/", "attr", "prop", "price", "sku",
                                    "skuInfo", "product", "template", "sale")):
                report["stages"]["edit"].setdefault("hits", []).append(
                    {"url": p, "req": (h.get("req") or "")[:500],
                     "body": (h.get("body") or "")[:1200]})
        print("   商品接口（%d 个）：" % len(apis2), flush=True)
        for p in apis2:
            print("     ", p, flush=True)

    # ---------------- ③ 发布页 ----------------
    print("\n[③] 发布页 %s" % PUBLISH_PAGE, flush=True)
    hits3, urls3, reqs3, ctx3 = await open_page(a.port, PUBLISH_PAGE, a.settle,
                                                evals=[("publish_page", "JSON.stringify({url:location.href,t:document.title,txt:(document.body&&document.body.innerText||'').slice(0,300)})")])
    apis3 = [p for p in api_list(urls3) if not SHELL.search(p)]
    report["stages"]["publish"] = {"apis": apis3, "evals": ctx3["evals"]}
    print("   商品接口：", apis3 or "（无 —— 空壳路由）", flush=True)

    p = outdir / ("report_%s.json" % ts)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n落盘 %s" % p)
    return 0


async def _stage_cat(a, perm_js):
    """类目页：抓首屏接口，并逐档试权限接口（先拿 mallid，再带 mallid 调）。"""
    tid = await cdp_net.new_tab(a.port, CAT_PAGE, foreground=True)
    ws = None
    for _ in range(24):
        await asyncio.sleep(0.5)
        for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port):
            if t.get("id") == tid:
                ws = t.get("webSocketDebuggerUrl")
                break
        if ws:
            break
    ctx = {"evals": [], "final": None}
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(a.settle)
            v, _ = await cap.eval(
                "(function(){var ps=document.cookie.split(';');for(var i=0;i<ps.length;i++){"
                "var p=ps[i].trim();if(p.indexOf('mallid=')===0)return p.substring(7);}"
                "return '';})()")
            mallid = str(v or "")
            print("   mallid=%s" % mallid, flush=True)
            for name, js in perm_js:
                r, exc = await cap.eval(js.replace("__MALL__", mallid), timeout=60)
                try:
                    d = json.loads(r)
                    body = d.get("b") or ""
                    ctx["evals"].append({"name": name, "status": d.get("s"),
                                         "body": body[:3000]})
                except Exception:
                    ctx["evals"].append({"name": name, "exc": str(exc)[:200],
                                         "value": str(r)[:300]})
            urls, reqs, hits = dict(cap._urls), dict(cap._bodies), list(cap.hits)
            v2, _ = await cap.eval(
                "JSON.stringify({url:location.href,t:document.title})")
            ctx["final"] = v2
            # 类目接口命中也带上
            for h in hits:
                pth = h["url"].replace(BASE, "")
                if any(k in pth for k in ("categor", "cat/", "queryCat")):
                    ctx.setdefault("cat_hits", []).append({"url": pth,
                                                           "req": (h.get("req") or "")[:400]})
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("   [x] 已关页 %s" % tid[:8], flush=True)
    return hits, urls, reqs, ctx


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=9)
    ap.add_argument("--step-settle", type=float, default=4)
    ap.add_argument("--product-id", default="", help="拿一个已有商品的 productId，反查编辑页接口")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
