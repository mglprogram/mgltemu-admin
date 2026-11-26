# -*- coding: utf-8 -*-
"""确认本店有没有「新建商品」入口权限，并把上架所需的后续接口分组列全。

结论性问题：`/goods/create/category` 能打开，但只打 2 个类目接口；
`/goods/create/publish` 是空壳。到底是没有权限，还是入口藏在别处？

做法：
    ① 完整拉 `/bert/api/page/info/agentSeller/pageInfo`（**不截断**），
       看 pagePath 里有没有 `/goods/create/*`、`/goods/edit`
    ② 拉 `/api/seller/auth/menu` 完整菜单树，找「商品管理 → 发布商品/新建商品」节点
    ③ 打开商品编辑页抓包，把 40 个接口按「类目/属性/价格/SKU/图片/物流/校验」分组落盘

只读。用完关页。
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

#: ⚠ 第一版截到 3000 字符，JSON 被切断没法 parse；这里给足上限
LIM = 30_000_000

JS_POST = ('(function(U_,B_,H_){try{var x=new XMLHttpRequest();x.open("POST",U_,false);'
           'x.setRequestHeader("content-type","application/json");'
           'for(var k in H_){try{x.setRequestHeader(k,H_[k]);}catch(e){}}'
           'x.send(JSON.stringify(B_));'
           'return JSON.stringify({s:x.status,b:String(x.responseText||"")});}'
           'catch(e){return JSON.stringify({s:0,b:"ERR "+String(e)});}})'
           '(%s,%s,%s)')

#: 上架流程的接口分组（按产品语义归类，方便后续按步骤实现）
GROUPS = (
    ("类目", ("categor", "category", "cat/")),
    ("类目模板/属性", ("template", "pg/template", "prop", "check/cat", "check/common")),
    ("品牌/白名单", ("brand", "WhiteList")),
    ("规格/SKU", ("sizespec", "spec/", "sku")),
    ("价格", ("price", "Price", "freight", "estimatedFreight")),
    ("图片/素材", ("image", "dimensionImage")),
    ("站点/语言/配置", ("site", "region", "supplier", "language", "queryMaterial")),
    ("校验/限制", ("queryProductSensitiveLimitInfo", "queryGuideFileRule", "queryMapping")),
    ("商品本体", ("/product/query", "accessory", "/goods/")),
)


async def post(cap, url, body, hdr, timeout=90):
    js = JS_POST % (json.dumps(url), json.dumps(body), json.dumps(hdr))
    v, exc = await cap.eval(js, timeout=timeout)
    if exc:
        return {"s": -1, "b": json.dumps(exc, ensure_ascii=False)[:300]}
    try:
        return json.loads(v or "{}")
    except Exception:
        return {"s": -2, "b": str(v)[:300]}


async def main(a):
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
    if not ws:
        return 3

    outdir = ROOT / "out" / "probe_create_publish"
    outdir.mkdir(parents=True, exist_ok=True)
    res: dict = {}
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(a.settle)
            v, _ = await cap.eval(
                "(function(){var ps=document.cookie.split(';');for(var i=0;i<ps.length;i++){"
                "var p=ps[i].trim();if(p.indexOf('mallid=')===0)return p.substring(7);}"
                "return '';})()")
            hdr = {"mallid": str(v or "")}
            print("mallid =", hdr["mallid"], flush=True)

            for name, path in (("pageInfo", "/bert/api/page/info/agentSeller/pageInfo"),
                               ("menu", "/api/seller/auth/menu")):
                r = await post(cap, BASE + path, {}, hdr)
                body = r.get("b") or ""
                print("\n== %s status=%s len=%d ==" % (name, r.get("s"), len(body)), flush=True)
                try:
                    j = json.loads(body)
                    res[name] = j
                    print("   parse OK, top keys:", list(j.keys()), flush=True)
                except Exception as e:
                    print("   !! 解析失败:", e, flush=True)
                    (outdir / ("%s_raw.txt" % name)).write_text(body, encoding="utf-8")
                    continue
                (outdir / ("%s.json" % name)).write_text(
                    json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")

                if name == "pageInfo":
                    lst = (j.get("result") or {}).get("pageInfoVOList") or []
                    paths = sorted({x.get("pagePath") for x in lst})
                    print("   页面权限 %d 条" % len(paths), flush=True)
                    hit = [p for p in paths if "goods" in p.lower()]
                    print("   含 goods 的权限路径（%d）：" % len(hit), flush=True)
                    for p in hit:
                        print("     ", p, flush=True)
                    res["goods_pages"] = hit

                if name == "menu":
                    ml = (j.get("result") or {}).get("menuList") or []

                    def walk(nodes, depth=0):
                        for n in nodes:
                            nm = n.get("menuName") or ""
                            rt = n.get("menuRoute") or ""
                            if any(k in nm for k in ("商品", "发布", "新建", "上架", "编辑")):
                                print("   %s%s  route=%s" % ("  " * depth, nm, rt), flush=True)
                            walk(n.get("children") or [], depth + 1)

                    print("   菜单 %d 个节点；商品相关：" % len(ml), flush=True)
                    walk(ml)
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("\n[x] 已关页", tid[:8], flush=True)

    # ---------- ③ 编辑页接口分组 ----------
    if a.product_id:
        from probe_create_publish import api_list, open_page, SHELL  # noqa
        print("\n[③] 编辑页接口分组（productId=%s）" % a.product_id, flush=True)
        eurl = BASE + "/goods/edit?from=productList&productId=%s" % a.product_id
        hits, urls, reqs, ctx = await open_page(a.port, eurl, a.settle)
        apis = [p for p in api_list(urls) if not SHELL.search(p)]
        grouped: dict = {}
        used = set()
        for gname, keys in GROUPS:
            got = [p for p in apis if any(k in p for k in keys) and p not in used]
            if got:
                grouped[gname] = got
                used.update(got)
        rest = [p for p in apis if p not in used]
        if rest:
            grouped["其他"] = rest
        for gname, got in grouped.items():
            print("  【%s】" % gname, flush=True)
            for p in got:
                print("     ", p, flush=True)
        # 把带请求体的命中留下（这些才是「上架要传什么」的关键）
        withreq = [{"url": h["url"].replace(BASE, ""), "req": (h.get("req") or "")[:800]}
                   for h in hits
                   if (h.get("req") or "").strip() and not SHELL.search(h["url"])]
        res["edit"] = {"url": eurl, "grouped": grouped,
                       "apis_with_body": withreq[:60],
                       "page": ctx.get("final")}
        print("\n  带请求体的接口 %d 个（落盘可查）" % len(withreq), flush=True)

    p = outdir / ("perm_and_apis_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=10)
    ap.add_argument("--product-id", default="")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
