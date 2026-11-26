# -*- coding: utf-8 -*-
"""摸清 `/anniston-agent-seller/category/children/list` 的下钻参数名。

上一轮实测：
    · 不带 mallid → 403 400020037 No Permission
    · 带 mallid、body {} → 200，返回**全部一级类目**（25 个根）
    · body {"catId": 27011} → **还是 25 个根** ⇒ 参数名不是 catId

本脚本对候选参数名 × 候选值逐个试，用「返回里有没有 27011 这个类目本身」
判定该参数是否真的生效（生效 = 返回的是 27011 的**子节点**，不再含 27011）。
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

PAGE = "https://agentseller.temu.com/goods/create/category"
CHILDREN = "https://agentseller.temu.com/anniston-agent-seller/category/children/list"

JS = ('(function(U_,B_,H_){try{var x=new XMLHttpRequest();'
      'x.open("POST",U_,false);x.setRequestHeader("content-type","application/json");'
      'for(var k in H_){try{x.setRequestHeader(k,H_[k]);}catch(e){}}'
      'x.send(JSON.stringify(B_));return JSON.stringify({s:x.status,b:String(x.responseText||"").slice(0,200000)});}'
      'catch(e){return JSON.stringify({s:0,b:"XHRERR "+String(e)});}})'
      '(%s,%s,%s)')

# 目标父类目：27011 = 服装、鞋靴和珠宝饰品（一级，根里就有）
PARENT = 27011
KEYS = ("parentCatId", "parentId", "parentCategoryId", "catId", "categoryId",
        "parent_cat_id", "id", "catID", "parentCat", "pCatId")


async def post(cap, body, hdr):
    v, exc = await cap.eval(JS % (json.dumps(CHILDREN), json.dumps(body), json.dumps(hdr)),
                            timeout=40)
    if exc:
        return {"s": -1, "b": str(exc)[:300]}
    try:
        return json.loads(v or "{}")
    except Exception:
        return {"s": -2, "b": str(v)[:300]}


def nodes_of(r):
    try:
        return (json.loads(r["b"]).get("result") or {}).get("categoryNodeVOS") or []
    except Exception:
        return []


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

    res = []
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(a.settle)
            v, _ = await cap.eval(
                "(function(){var ps=document.cookie.split(';');for(var i=0;i<ps.length;i++){"
                "var p=ps[i].trim();if(p.indexOf('mallid=')===0)return p.substring(7);}return '';})()")
            hdr = {"mallid": str(v or "")}
            print("mallid =", hdr["mallid"], flush=True)

            base = await post(cap, {}, hdr)
            bn = nodes_of(base)
            print("基准 body={} → %d 个节点；含 27011? %s；首条=%s"
                  % (len(bn), any(n.get("catId") == PARENT for n in bn),
                     (bn[0] or {}).get("catName") if bn else "-"), flush=True)

            for k in KEYS:
                r = await post(cap, {k: PARENT}, hdr)
                n = nodes_of(r)
                contains_parent = any(x.get("catId") == PARENT for x in n)
                hit = r.get("s") == 200 and n and not contains_parent
                print("  %-18s → status=%s n=%-4d 父类目仍在?%-5s 首条=%s %s"
                      % (k, r.get("s"), len(n), contains_parent,
                         (n[0] or {}).get("catName") if n else "-",
                         "  ★ 生效" if hit else ""), flush=True)
                res.append({"key": k, "status": r.get("s"), "n": len(n),
                            "still_root": contains_parent, "hit": bool(hit),
                            "first": n[:2]})
                if hit:
                    # 顺便确认下钻出来的子节点结构（有没有 isLeaf / catLevel）
                    print("      样例:", json.dumps(n[:2], ensure_ascii=False)[:400], flush=True)
                    # 再用同一参数往下一层，看层级是否递增
                    child = n[0].get("catId")
                    d = await post(cap, {k: child}, hdr)
                    dn = nodes_of(d)
                    print("      下钻 %s → %d 个孙节点, status=%s" % (child, len(dn), d.get("s")),
                          flush=True)
                    res.append({"drill_from": child, "n": len(dn), "status": d.get("s")})
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("[x] 已关页", tid, flush=True)

    out = ROOT / "out" / "probe_create_category"
    out.mkdir(parents=True, exist_ok=True)
    p = out / ("argfuzz_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=9)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
