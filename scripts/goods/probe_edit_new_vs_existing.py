# -*- coding: utf-8 -*-
"""在两个「商品发布」候选入口里，找除「类目选择」之外的第二个接口。

背景（2026-11-26）：
    · `pageInfo` 给本店 `/goods/create/*` 的权限**只有** `/goods/create/category`
      —— `/goods/create/publish` 不在权限表里，是空壳路由
    · `/goods/edit`（菜单「商品发布」指向的入口）打开 **已有商品** 时会打 40 个接口
    · 用 `/goods/edit` **不带 productId** 打开，如果那是「新建」入口，就应该打
      属性模板 / 价格 / SKU / 图片 等接口 —— 那就是上链接的下一步

本脚本对比三个场景的接口集合：
    A. /goods/edit                （不带 productId）
    B. /goods/edit?from=productList&productId=…（已有商品，基线）
    C. /goods/create/category     （第一轮已探，只有类目接口）

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
SKIP = re.compile(r"\.(js|css|png|jpe?g|svg|gif|woff2?|ico|webp)(\?|$)|"
                  r"^data:|^blob:|^chrome-extension:", re.I)
#: 全局导航壳（跟商品无关）
SHELL = re.compile(r"/api/phantom/|/api/sec-csp/|/api/server/_stm|quick/merchant/|"
                   r"msgBox|ladyfish|marvel-mms|queryTotalExam|/drogon-api/|"
                   r"temu-sca-config|/lollipop/|gambit/api|feedback|"
                   r"web-performace|pftk\.temu\.com|/api/seller/auth/|"
                   r"queryFrontendReportUinResult|/goods-entry|"
                   r"queryMmsBtgProductStockBaseInfo|batchMatchBySupplierIds|"
                   r"min/amount|unreadNum|supplier/flag|mall/settle/time|"
                   r"config/common/(site|supplier|region)/query")

SCENES = (
    ("A_edit_new", "/goods/edit"),
    ("B_edit_existing", "/goods/edit?from=productList&productId=%s"),
    ("C_create_category", "/goods/create/category"),
)


async def snapshot(port, url, settle):
    """开页 → 等 settle → 返回 (api 路径集合, 页面状态, 带请求体的接口)。"""
    tid = await cdp_net.new_tab(port, url, foreground=True)
    if not tid:
        return None
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
        return None
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(settle)
            v, _ = await cap.eval(
                "JSON.stringify({url:location.href,t:document.title,"
                "txt:(document.body&&document.body.innerText||'').slice(0,700)})")
            urls, bodies = dict(cap._urls), dict(cap._bodies)
    finally:
        await cdp_net.close_tab(port, tid)
        print("     [x] 已关页 %s" % tid[:8], flush=True)

    apis, withbody = set(), {}
    for rid, u in urls.items():
        if not u or SKIP.search(u) or "agentseller.temu.com" not in u:
            continue
        p = u.replace(BASE, "").split("?")[0]
        if SHELL.search(p):
            continue
        apis.add(p)
        bd = bodies.get(rid) or ""
        if bd.strip():
            withbody[p] = bd[:1500]
    try:
        page = json.loads(v or "{}")
    except Exception:
        page = {"raw": v}
    return apis, page, withbody


async def main(a):
    outdir = ROOT / "out" / "probe_create_publish"
    outdir.mkdir(parents=True, exist_ok=True)
    report: dict = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "scenes": {}}

    for name, tmpl in SCENES:
        path = tmpl % a.product_id if "%s" in tmpl else tmpl
        url = BASE + path
        print("\n[%s] %s" % (name, url), flush=True)
        r = await snapshot(a.port, url, a.settle)
        if not r:
            print("   !! 开页失败", flush=True)
            continue
        apis, page, withbody = r
        print("   页面: %s | %s" % (page.get("t"), str(page.get("url"))[:80]), flush=True)
        print("   商品接口 %d 个" % len(apis), flush=True)
        for p in sorted(apis):
            print("     ", p, flush=True)
        if page.get("txt"):
            print("   正文片段:", str(page["txt"]).replace("\n", " | ")[:220], flush=True)
        report["scenes"][name] = {"url": url, "page": page, "apis": sorted(apis),
                                  "with_body": withbody}

    # 差集：A 有而 B/C 没有的 = 「新建」独有接口
    A = set((report["scenes"].get("A_edit_new") or {}).get("apis") or [])
    B = set((report["scenes"].get("B_edit_existing") or {}).get("apis") or [])
    C = set((report["scenes"].get("C_create_category") or {}).get("apis") or [])
    print("\n=== 差集 ===", flush=True)
    print("  A(新建) 独有 %d 个：" % len(A - B - C), flush=True)
    for p in sorted(A - B - C):
        print("     ", p, flush=True)
    print("  A 与 B 共有 %d 个（已有商品编辑也在用）" % len(A & B), flush=True)
    report["diff"] = {"A_only": sorted(A - B - C), "A_and_B": sorted(A & B),
                      "B_only": sorted(B - A - C)}

    p = outdir / ("edit_new_vs_existing_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--product-id", required=True)
    ap.add_argument("--settle", type=float, default=12)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
