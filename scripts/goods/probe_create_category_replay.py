# -*- coding: utf-8 -*-
"""复放「选类目」接口：验证能否只靠页面 cookie 调通，并摸清 children/list 的参数。

三个实验：
    ① 裸调（只带 content-type，不带 Anti-Content/mallid）→ 看是否 403
    ② 带页面现有 Anti-Content（若有缓存头文件）+ mallid
    ③ 逐层下钻：根 → cat 27011 → …，直到 isLeaf=true，验证链路
    ④ queryCatHints 有无入参差异

只读，不改数据。用完关页。
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

# ⚠ 两个坑：
#  ① `return fetch(...)` 在页面 fetch 下会报 Illegal invocation —— 且 NetCapture.eval 是
#    `awaitPromise:False`，返回 Promise 拿不到结果，所以**不用 fetch**。
#  ② 用**同步 XHR**（open 第三参 false）：表达式直接返回字符串，不需要 await，
#    也不受页面里被 instrument 过的 Promise 影响。
#  ③ 变量必须包在 IIFE 里 —— CDP 的 Runtime.evaluate 在**同一个执行上下文**里重复求值时，
#    `const U_` 会报 "Identifier 'U_' has already been declared"（同页第二次调用必炸）。
JS = ('(function(U_,B_,H_){try{var x=new XMLHttpRequest();'
      'x.open("POST",U_,false);x.setRequestHeader("content-type","application/json");'
      'for(var k in H_){try{x.setRequestHeader(k,H_[k]);}catch(e){}}'
      'x.send(JSON.stringify(B_));return JSON.stringify({s:x.status,b:String(x.responseText||"").slice(0,200000)});}'
      'catch(e){return JSON.stringify({s:0,b:"XHRERR "+String(e)});}})'
      '(%s,%s,%s)')

# 试参数名：children/list 很可能收 parentCatId / catId / parentId
ARGKEYS = ("catId", "parentCatId", "parentId", "parentCategoryId", "catLevel", "level")


async def post(cap, url, body, hdr):
    v, exc = await cap.eval(JS % (json.dumps(url), json.dumps(body), json.dumps(hdr)), timeout=40)
    if exc:
        return {"s": -1, "b": str(exc)[:300]}
    try:
        return json.loads(v or "{}")
    except Exception:
        return {"s": -2, "b": str(v)[:300]}


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

    report: dict = {"probes": [], "drill": []}
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(a.settle)

            # 页面 cookie 里的 mallid（不信任硬编码）
            v, _ = await cap.eval(
                "(function(){var ps=document.cookie.split(';');for(var i=0;i<ps.length;i++){"
                "var p=ps[i].trim();if(p.indexOf('mallid=')===0)return p.substring(7);}"
                "return '';})()")
            mallid = str(v or "")
            print("mallid =", mallid, flush=True)

            mallid_hdr = {"mallid": mallid} if mallid else {}

            # ① 裸调
            r = await post(cap, CHILDREN, {"catId": 0}, {})
            print("① 裸调 children/list:", r.get("s"), (r.get("b") or "")[:160], flush=True)
            report["probes"].append({"case": "naked", "status": r.get("s"),
                                     "body": (r.get("b") or "")[:400]})

            # ② 只加 mallid
            r = await post(cap, CHILDREN, {"catId": 0}, mallid_hdr)
            print("② +mallid:", r.get("s"), (r.get("b") or "")[:160], flush=True)
            report["probes"].append({"case": "+mallid", "status": r.get("s"),
                                     "body": (r.get("b") or "")[:400]})
            ok = r.get("s") == 200 and '"success":true' in (r.get("b") or "").replace(" ", "")

            if ok:
                # ③ 拿根节点，逐层下钻
                j = json.loads(r["b"])
                roots = (j.get("result") or {}).get("categoryNodeVOS") or []
                print("   根节点 %d 个（一级类目）" % len(roots), flush=True)
                # 挑「服装、鞋靴和珠宝饰品」= 27011 这条链走一遍
                path, cur = [], 27011
                for lvl in range(1, 11):
                    body = {"catId": cur}
                    rr = await post(cap, CHILDREN, body, mallid_hdr)
                    kids = []
                    if rr.get("s") == 200:
                        try:
                            kj = json.loads(rr["b"])
                            kids = (kj.get("result") or {}).get("categoryNodeVOS") or []
                        except Exception:
                            pass
                    print("   L%d catId=%s → %d 个子节点 (status=%s)"
                          % (lvl, cur, len(kids), rr.get("s")), flush=True)
                    report["drill"].append({"level": lvl, "catId": cur,
                                            "status": rr.get("s"),
                                            "n": len(kids),
                                            "sample": kids[:3],
                                            "body_sent": body})
                    if not kids:
                        break
                    nxt = next((k for k in kids if not k.get("isLeaf")), kids[0])
                    path.append({"catId": cur, "n": len(kids)})
                    cur = nxt.get("catId")
                    if nxt.get("isLeaf"):
                        print("   → 到叶子：%s %s (catLevel=%s)"
                              % (nxt.get("catId"), nxt.get("catName"), nxt.get("catLevel")),
                              flush=True)
                        report["leaf"] = nxt
                        break

            # ④ queryCatHints
            for label, hdr in (("naked", {}), ("+mallid", mallid_hdr)):
                r = await post(cap, HINTS, {}, hdr)
                okk = r.get("s") == 200 and '"success":true' in (r.get("b") or "").replace(" ", "")
                n = 0
                if okk:
                    n = len((json.loads(r["b"]).get("result") or {}).get("catHints") or [])
                print("④ queryCatHints %s: status=%s ok=%s hints=%d"
                      % (label, r.get("s"), okk, n), flush=True)
                report["probes"].append({"case": "hints " + label, "status": r.get("s"),
                                         "ok": okk, "n": n})
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("[x] 已关页", tid, flush=True)

    out = ROOT / "out" / "probe_create_category"
    out.mkdir(parents=True, exist_ok=True)
    p = out / ("replay_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=9)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
