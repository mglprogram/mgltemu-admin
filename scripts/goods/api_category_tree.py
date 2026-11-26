# -*- coding: utf-8 -*-
"""验证「选类目」完整链路：根 → … → 叶子，并把整棵树落盘。

已确认（2026-11-26 实测）：
    POST https://agentseller.temu.com/anniston-agent-seller/category/children/list
      headers: {"content-type":"application/json", "mallid":"<cookie mallid>"}
      body:    {}                      → 全部一级类目（25 个根）
      body:    {"parentCatId": 27011}  → 27011 的**直接子节点**
    不带 mallid → 403 {"error_code":400020037,"error_msg":"No Permission to Access"}
    Anti-Content 头**不需要**。

用法：
    python scripts/goods/api_category_tree.py --port 63854                 # 验证一条链
    python scripts/goods/api_category_tree.py --port 63854 --full          # 全树落盘
    python scripts/goods/api_category_tree.py --port 63854 --find 健腹轮    # 按名搜类目路径
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

PAGE = "https://agentseller.temu.com/goods/create/category"
CHILDREN = "https://agentseller.temu.com/anniston-agent-seller/category/children/list"
HINTS = "https://agentseller.temu.com/visage-agent-seller/product/queryCatHints"

JS = ('(function(U_,B_,H_){try{var x=new XMLHttpRequest();'
      'x.open("POST",U_,false);x.setRequestHeader("content-type","application/json");'
      'for(var k in H_){try{x.setRequestHeader(k,H_[k]);}catch(e){}}'
      'x.send(JSON.stringify(B_));return JSON.stringify({s:x.status,b:String(x.responseText||"").slice(0,500000)});}'
      'catch(e){return JSON.stringify({s:0,b:"XHRERR "+String(e)});}})'
      '(%s,%s,%s)')


async def post(cap, body, hdr):
    v, exc = await cap.eval(JS % (json.dumps(CHILDREN), json.dumps(body), json.dumps(hdr)),
                            timeout=60)
    if exc:
        raise RuntimeError(str(exc)[:300])
    r = json.loads(v or "{}")
    if r.get("s") != 200:
        raise RuntimeError("HTTP %s %s" % (r.get("s"), (r.get("b") or "")[:200]))
    j = json.loads(r["b"])
    if not j.get("success"):
        raise RuntimeError("接口失败 %s" % (j.get("errorMsg") or j))
    return (j.get("result") or {}).get("categoryNodeVOS") or []


async def post2(cap, url, body, hdr):
    """通用 POST + 解 JSON，返回 result（不假定字段名）。"""
    v, exc = await cap.eval(JS % (json.dumps(url), json.dumps(body), json.dumps(hdr)),
                            timeout=60)
    if exc:
        raise RuntimeError(str(exc)[:300])
    r = json.loads(v or "{}")
    if r.get("s") != 200:
        raise RuntimeError("HTTP %s %s" % (r.get("s"), (r.get("b") or "")[:200]))
    j = json.loads(r["b"])
    if not j.get("success"):
        raise RuntimeError("接口失败 %s" % (j.get("errorMsg") or j))
    return (j.get("result") or {})


async def main(a):
    tid = await cdp_net.new_tab(a.port, PAGE, foreground=True)
    if not tid:
        return 2
    ws = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port):
            if t.get("id") == tid:
                ws = t["webSocketDebuggerUrl"]
        if ws:
            break
    if not ws:
        return 3

    roots: list = []
    stats = {"calls": 0}
    tree: dict = {}
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(a.settle)
            v, _ = await cap.eval(
                "(function(){var ps=document.cookie.split(';');for(var i=0;i<ps.length;i++){"
                "var p=ps[i].trim();if(p.indexOf('mallid=')===0)return p.substring(7);}return '';})()")
            hdr = {"mallid": str(v or "")}
            if not hdr["mallid"]:
                print("!! cookie 里没有 mallid，先确认这店已登录")
                return 4
            print("mallid =", hdr["mallid"], flush=True)

            roots = await post(cap, {}, hdr)
            stats["calls"] += 1
            print("一级类目 %d 个" % len(roots), flush=True)

            async def kids(pid):
                stats["calls"] += 1
                return await post(cap, {"parentCatId": pid}, hdr)

            if a.hint:
                # 用 queryCatHints 给的完整路径反向验证 children/list 能否逐级走到同一叶子
                hb = (await post2(cap, HINTS, {}, hdr)).get("catHints") or []
                hs = hb
                stats["calls"] += 1
                print("catHints %d 条" % len(hs), flush=True)
                for x in hs[:a.hint]:
                    ids = [x.get("cat%d" % i) for i in range(1, 11)]
                    ids = [p for p in ids if p]
                    cs = roots
                    ok_chain, level = [], 0
                    for i, p in enumerate(ids):
                        got = next((c for c in cs if c.get("catId") == p.get("catId")), None)
                        level = i + 1
                        if got is None:
                            break
                        ok_chain.append((got.get("catId"), got.get("catName"), got.get("isLeaf")))
                        if not got.get("isLeaf"):
                            cs = await kids(got["catId"])
                    print("  " + " > ".join("%s(%s)" % (n, c) for c, n, _ in ok_chain)
                          + ("  ★叶子" if ok_chain and ok_chain[-1][2] else "  ✗断在L%d" % level),
                          flush=True)
                print("接口调用 %d 次" % stats["calls"])
                return 0

            if a.find:
                kw = a.find.lower()
                found = []

                async def walk_kw(node, path):
                    name = ("%s|%s" % (node.get("catName"), node.get("catEnName"))).lower()
                    if kw in name:
                        found.append(path + [node])
                    if node.get("isLeaf") or len(path) >= 9:
                        return
                    for c in await kids(node["catId"]):
                        await walk_kw(c, path + [node])

                for r in roots:
                    await walk_kw(r, [])
                    print("  …根 %s 扫完（累计命中 %d，调用 %d 次）"
                          % (r.get("catName"), len(found), stats["calls"]), flush=True)
                for p in found[:30]:
                    print("  " + " > ".join("%s(%s)" % (x.get("catId"), x.get("catName"))
                                            for x in p),
                          "| ★叶子" if p[-1].get("isLeaf") else "", flush=True)
                print("命中 %d 条，接口调用 %d 次" % (len(found), stats["calls"]))
                return 0

            # 走一条完整链路：第一个非叶子根一路到底
            path = []
            cur = roots[0]
            while True:
                path.append(cur)
                print("  L%s %-6s %s%s" % (cur.get("catLevel"), cur.get("catId"),
                                           cur.get("catName"),
                                           "  ★叶子" if cur.get("isLeaf") else ""), flush=True)
                if cur.get("isLeaf") or cur.get("catLevel", 0) >= 10:
                    break
                cs = await kids(cur["catId"])
                if not cs:
                    print("    (无子节点，链路在此结束)")
                    break
                cur = cs[0]
            print("链路深度 %d，接口调用 %d 次" % (len(path), stats["calls"]), flush=True)

            if a.full:
                # 限深广度遍历：默认只到 3 级（一级 25 × 二级若干），够看清结构。
                # 深到叶子用 --find / --hint，全树要几百次串行 XHR，没必要每次都抓。
                maxlv = a.depth
                frontier = list(roots)
                for n in roots:
                    tree[str(n["catId"])] = {"catId": n["catId"], "name": n.get("catName"),
                                             "en": n.get("catEnName"),
                                             "level": n.get("catLevel"),
                                             "parent": n.get("parentCatId"),
                                             "leaf": bool(n.get("isLeaf")), "children": []}
                while frontier:
                    nxt = []
                    for n in frontier:
                        if n.get("isLeaf") or int(n.get("catLevel") or 0) >= maxlv:
                            continue
                        for c in await kids(n["catId"]):
                            tree[str(c["catId"])] = {
                                "catId": c["catId"], "name": c.get("catName"),
                                "en": c.get("catEnName"), "level": c.get("catLevel"),
                                "parent": c.get("parentCatId"), "leaf": bool(c.get("isLeaf")),
                                "children": []}
                            tree[str(n["catId"])]["children"].append(c["catId"])
                            nxt.append(c)
                    frontier = nxt
                    print("  …已到第 %d 级，累计 %d 节点 / 调用 %d 次"
                          % (maxlv, len(tree), stats["calls"]), flush=True)
                print("抓到 %d 个节点（深度≤%d），接口调用 %d 次"
                      % (len(tree), maxlv, stats["calls"]), flush=True)
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("[x] 已关页", tid, flush=True)

    if a.full and tree:
        out = ROOT / "data" / "temu-category"
        out.mkdir(parents=True, exist_ok=True)
        p = out / ("category_tree_%s.json" % time.strftime("%Y%m%d"))
        p.write_text(json.dumps({"fetchedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "mallid": str(v or ""), "roots": [r["catId"] for r in roots],
                                 "nodes": tree}, ensure_ascii=False, indent=1),
                     encoding="utf-8")
        print("全树落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=9)
    ap.add_argument("--full", action="store_true", help="抓类目树并落盘")
    ap.add_argument("--depth", type=int, default=3, help="--full 的最大层级（默认 3）")
    ap.add_argument("--find", default="", help="按类目名（中/英）深搜路径（慢，整树逐层）")
    ap.add_argument("--hint", type=int, default=0, metavar="N",
                    help="用 queryCatHints 的前 N 条路径反向验证 children/list（快）")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
