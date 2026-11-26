# -*- coding: utf-8 -*-
"""在「新建商品」页真正走完第 1 步（选站点 → 选类目 → 下一步），抓第 2 步的接口。

为什么必须先选站点：实测（2026-11-26）不选「经营站点」时
「下一步」点了只出红字「请选择经营站点」，**不发任何请求**，
一条属性/价格/SKU 接口都抓不到。

DOM 实况（2026-11-26）：
    [class*=suggestList] > [class*=catName]   ← 「常用推荐」5 条，如
        「服装、鞋靴和珠宝饰品>...>男士保暖内衣上衣」
    [class*=selectValueSingle]               ← 经营站点下拉（ST_ 组件）
    [class*=footerContainer] > 下一步

⚠ React 不认 `el.click()`（合成事件 + 根节点委托），必须走 CDP
   `Input.dispatchMouseEvent` 发真实 pointerdown/mousedown/mouseup/click。

只读（只走流程，不提交、不保存草稿）。用完关页。
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
CREATE = BASE + "/goods/create/category"
SKIP = re.compile(r"\.(js|css|png|jpe?g|svg|gif|woff2?|ico|webp)(\?|$)|"
                  r"^data:|^blob:|^chrome-extension:", re.I)
SHELL = re.compile(r"/api/phantom/|/api/sec-csp/|/api/server/_stm|quick/merchant/|"
                   r"msgBox|ladyfish|marvel-mms|queryTotalExam|/drogon-api/|"
                   r"temu-sca-config|/lollipop/|gambit/api|feedback|web-performace|"
                   r"pftk\.temu\.com|/api/seller/auth/|queryFrontendReportUinResult|"
                   r"/goods-entry|queryMmsBtgProductStockBaseInfo|"
                   r"batchMatchBySupplierIds|min/amount|unreadNum|supplier/flag|"
                   r"mall/settle/time|mallQuery|course/exam|pop/query")

#: 站点下拉定位（保持极简，避免复杂正则/模板块踩坑）。
#: 判定：input 的父元素 class 以 `ST_` 开头（实测 ST_inputBlockCell_5-125-0）。
SITE_JS = r"""(function(){try{
  var ins=document.querySelectorAll('input');
  var inp=null;
  for(var i=0;i<ins.length;i++){
    var p=ins[i].parentElement;
    var c=p?String(p.className||''):'';
    if(c.indexOf('ST_')===0||c.indexOf('ST_inputBlockCell')>=0){inp=ins[i];break;}
  }
  if(!inp){
    for(var i=0;i<ins.length;i++){
      if(String(ins[i].placeholder||'')==='请选择'){inp=ins[i];break;}
    }
  }
  if(!inp){return JSON.stringify({ok:false,why:'no site input'});}
  inp.scrollIntoView({block:'center'});
  var r=inp.getBoundingClientRect();
  return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),
    w:Math.round(r.width),h:Math.round(r.height),
    ph:String(inp.placeholder||''),
    cls:String(inp.className||'').slice(0,70),
    parentCls:String((inp.parentElement&&inp.parentElement.className)||'').slice(0,70)});
}catch(err){return JSON.stringify({ok:false,why:'ERR '+String(err.message)});}})()"""

#: 下拉展开后，列出弹层里的选项。
#: 极简版：只取 `[role=option]` 和 ST_ 的 option 类；排除左侧「全部分类」栏（x < 300）。
OPTIONS_JS = r"""(function(){try{
  var out=[];
  var ds=document.querySelectorAll('[role=option],[class*=option],[class*=Option]');
  for(var i=0;i<ds.length;i++){
    if(out.length>=40){break;}
    var e=ds[i];
    var t=String(e.innerText||'').trim();
    if(!t){continue;}
    if(t.length>40){continue;}
    if(t.indexOf('\n')>=0){continue;}
    var r=e.getBoundingClientRect();
    if(r.width<10){continue;}
    if(r.height<6){continue;}
    if(r.x<300){continue;}
    out.push({txt:t,cls:String(e.className).slice(0,70),
      x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});
  }
  return JSON.stringify({n:out.length,opts:out});
}catch(err){return JSON.stringify({ok:false,n:0,why:'ERR '+String(err.message)});}})()"""

#: 「常用推荐」第一条类目的坐标
SUGGEST_JS = r"""(function(){try{
  var a=document.querySelectorAll('[class*=suggestList]');
  if(!a.length) return JSON.stringify({ok:false,why:'no suggestList'});
  var ch=a[0].children;
  if(!ch.length) return JSON.stringify({ok:false,why:'no children'});
  var c=ch[0];
  c.scrollIntoView({block:'center'});
  var r=c.getBoundingClientRect();
  return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),
    txt:String(c.innerText||'').trim().slice(0,90)});
}catch(err){return JSON.stringify({ok:false,why:'ERR '+err.message});}})()"""

#: 页面状态：站点选了没、类目选了没、有没有红字提示
STATE_JS = r"""(function(){try{
  var txt=String(document.body.innerText||'');
  var sel=document.querySelectorAll('[class*=selectValueSingle]');
  var vals=[];
  for(var i=0;i<sel.length;i++) vals.push(String(sel[i].innerText||'').trim().slice(0,40));
  var foot=document.querySelector('[class*=footerContainer]');
  var tip=document.querySelector('[class*=selectCatTips]');
  return JSON.stringify({url:location.href,
    siteVals:vals,
    footTxt:foot?String(foot.innerText||'').replace(/\n/g,' | ').slice(0,160):null,
    tip:tip?String(tip.innerText||'').slice(0,120):null,
    hasSiteRed:txt.indexOf('请选择经营站点')>=0,
    hasCatRed:txt.indexOf('请完整选择商品分类')>=0});
}catch(err){return JSON.stringify({err:String(err.message)});}})()"""

NEXT_JS = r"""(function(){try{
  var bs=document.querySelectorAll('button,span,div,a');
  for(var i=0;i<bs.length;i++){
    var t=String(bs[i].innerText||'').trim();
    if(t!=='下一步'){continue;}
    if(bs[i].children.length!==0){continue;}
    var r=bs[i].getBoundingClientRect();
    if(r.width<5){continue;}
    return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});
  }
  return JSON.stringify({ok:false,why:'no next'});
}catch(err){return JSON.stringify({ok:false,why:'ERR '+String(err.message)});}})()"""

#: 除站点外的其它「请选择」选择器（站点与发货仓关系 → 必须选发货仓），
#: 逐个数出来并给出坐标，用来点开第二个下拉。
OTHER_SELECTS_JS = r"""(function(){try{
  var out=[];
  var ins=document.querySelectorAll('input');
  for(var i=0;i<ins.length;i++){
    if(String(ins[i].placeholder||'')!=='请选择'){continue;}
    var e=ins[i], r=e.getBoundingClientRect();
    var p=e.parentElement, c=p?String(p.className||''):'';
    out.push({i:i,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),
      y0:Math.round(r.y),w:Math.round(r.width),
      parentCls:c.slice(0,80),
      isSite:(c.indexOf('ST_inputBlockCell')>=0)});
  }
  return JSON.stringify({n:out.length,items:out});
}catch(err){return JSON.stringify({ok:false,why:'ERR '+String(err.message)});}})()"""

#: 页面上所有红字校验提示（定位「还差什么」）
RED_HINTS_JS = r"""(function(){try{
  var out=[];
  var ds=document.querySelectorAll('[class*=error],[class*=Error],[class*=invalid],[class*=Invalid],[class*=danger]');
  for(var i=0;i<ds.length;i++){
    if(out.length>=15){break;}
    var t=String(ds[i].innerText||'').trim();
    if(!t){continue;}
    if(t.length>60){continue;}
    out.push({cls:String(ds[i].className).slice(0,60),txt:t});
  }
  return JSON.stringify({n:out.length,items:out});
}catch(err){return JSON.stringify({ok:false,why:'ERR '+String(err.message)});}})()"""


async def real_click(cap, x, y):
    for et, cc in (("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 1)):
        try:
            await cap.send("Input.dispatchMouseEvent",
                           {"type": et, "x": x, "y": y, "button": "left", "clickCount": cc})
        except Exception as e:  # noqa: BLE001
            print("      !! dispatch %s 失败: %s" % (et, str(e)[:80]), flush=True)


async def j(cap, js, label, timeout=40):
    v, e = await cap.eval(js, timeout=timeout)
    if e:
        print("   [%s] EXC %s" % (label, str(e)[:200]), flush=True)
        return {}
    try:
        return json.loads(v or "{}")
    except Exception:
        print("   [%s] raw %s" % (label, str(v)[:200]), flush=True)
        return {"raw": str(v)[:300]}


async def main(a):
    outdir = ROOT / "out" / "probe_create_publish"
    outdir.mkdir(parents=True, exist_ok=True)

    tid = await cdp_net.new_tab(a.port, CREATE, foreground=True)
    if not tid:
        return 2
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

    rep: dict = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "steps": []}
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(a.settle)
            before = {u.replace(BASE, "").split("?")[0] for u in cap._urls.values()
                      if u and "agentseller.temu.com" in u and not SKIP.search(u)}
            print("[0] 初始接口 %d 个" % len(before), flush=True)

            # ---- 1) 选经营站点 ----
            st = await j(cap, STATE_JS, "state0")
            print("[1] 站点值=%s" % st.get("siteVals"), flush=True)
            if "美国" not in str(st.get("siteVals")):
                site = await j(cap, SITE_JS, "site")
                print("    站点下拉坐标 %s" % site, flush=True)
                if site.get("ok"):
                    await real_click(cap, site["x"], site["y"])
                    await asyncio.sleep(1.5)
                    opts = await j(cap, OPTIONS_JS, "options")
                    print("    选项 %d 个: %s" % (opts.get("n", 0),
                                                [o.get("txt") for o in (opts.get("opts") or [])][:12]),
                          flush=True)
                    rep["site_options"] = opts
                    want = next((o for o in (opts.get("opts") or [])
                                 if "美国" in str(o.get("txt"))), None)
                    if want:
                        await real_click(cap, want["x"], want["y"])
                        print("    已点站点: %s" % want["txt"], flush=True)
                        await asyncio.sleep(2)
                    else:
                        print("    !! 没找到「美国」选项", flush=True)
            st = await j(cap, STATE_JS, "state1")
            print("    选站点后: %s" % json.dumps(st, ensure_ascii=False)[:240], flush=True)
            rep["steps"].append({"name": "pick_site", "state": st})

            # ---- 1b) 选「站点与发货仓关系」（第二个「请选择」，必填，不选会红字「不能为空」） ----
            sels = await j(cap, OTHER_SELECTS_JS, "selects")
            print("[1b] 下拉框 %s" % json.dumps(sels, ensure_ascii=False)[:400], flush=True)
            rep["selects"] = sels
            wh = next((x for x in (sels.get("items") or []) if not x.get("isSite")), None)
            if wh:
                await real_click(cap, wh["x"], wh["y"])
                await asyncio.sleep(1.5)
                opts2 = await j(cap, OPTIONS_JS, "warehouse_options")
                print("    发货仓选项 %d: %s" % (opts2.get("n", 0),
                                              [o.get("txt") for o in (opts2.get("opts") or [])][:12]),
                      flush=True)
                rep["warehouse_options"] = opts2
                pick = (opts2.get("opts") or [None])[0]
                if pick:
                    await real_click(cap, pick["x"], pick["y"])
                    print("    已选发货仓: %s" % pick["txt"], flush=True)
                    await asyncio.sleep(2)
            st = await j(cap, STATE_JS, "state1b")
            print("    选仓后: %s" % json.dumps(st, ensure_ascii=False)[:240], flush=True)
            rep["steps"].append({"name": "pick_warehouse", "state": st})

            # ---- 2) 选类目（常用推荐第一条） ----
            sug = await j(cap, SUGGEST_JS, "suggest")
            print("[2] 推荐坐标 %s" % sug, flush=True)
            if sug.get("ok"):
                await real_click(cap, sug["x"], sug["y"])
                await asyncio.sleep(a.after)
            st = await j(cap, STATE_JS, "state2")
            print("    选类目后: %s" % json.dumps(st, ensure_ascii=False)[:300], flush=True)
            rep["steps"].append({"name": "pick_category", "state": st,
                                 "picked": sug.get("txt")})

            mid = {u.replace(BASE, "").split("?")[0] for u in cap._urls.values()
                   if u and "agentseller.temu.com" in u and not SKIP.search(u)}
            print("    选类目后新增接口: %s" % sorted(mid - before), flush=True)

            # ---- 3) 下一步 ----
            nb = await j(cap, NEXT_JS, "next")
            print("[3] 下一步 %s" % nb, flush=True)
            if nb.get("ok") and not st.get("hasSiteRed") and not st.get("hasCatRed"):
                await real_click(cap, nb["x"], nb["y"])
                await asyncio.sleep(a.after)
            else:
                print("    !! 还有必填没满足，不点下一步", flush=True)
            st = await j(cap, STATE_JS, "state3")
            print("    下一步后: %s" % json.dumps(st, ensure_ascii=False)[:400], flush=True)
            rep["steps"].append({"name": "next", "state": st})

            # 抓第 2 步的页面正文（标题、表单字段名）
            v, _ = await cap.eval(
                "JSON.stringify({url:location.href,t:document.title,"
                "txt:(document.body&&document.body.innerText||'').slice(0,2500),"
                "inputs:(function(){var o=[],n=document.querySelectorAll('input,textarea');"
                "for(var i=0;i<n.length&&i<40;i++){o.push((n[i].placeholder||n[i].name||n[i].type||''));}return o;})()})",
                timeout=40)
            rep["page2"] = str(v)[:3000]
            try:
                p2 = json.loads(v or "{}")
            except Exception:
                p2 = {}
            print("\n[页面] url=%s title=%s" % (p2.get("url"), p2.get("t")), flush=True)
            print("[正文] %s" % str(p2.get("txt") or "").replace("\n", " | ")[:1200], flush=True)
            print("[输入框] %s" % (p2.get("inputs") or []), flush=True)

            urls, bodies = dict(cap._urls), dict(cap._bodies)
    finally:
        await cdp_net.close_tab(a.port, tid)
        print("\n[x] 已关页", tid[:8], flush=True)

    after = {}
    for rid, u in urls.items():
        if not u or SKIP.search(u) or "agentseller.temu.com" not in u:
            continue
        p = u.replace(BASE, "").split("?")[0]
        if SHELL.search(p):
            continue
        after[p] = (bodies.get(rid) or "")[:3000]
    new = sorted(set(after) - before)
    print("\n=== 全程【新出现】的接口 %d 个 ===" % len(new), flush=True)
    for p in new:
        print("  ", p, flush=True)
        if (after.get(p) or "").strip():
            print("       req:", (after[p] or "")[:240].replace("\n", " "), flush=True)
    rep["new_apis"] = new
    rep["all_apis"] = sorted(after)
    rep["bodies"] = {k: v for k, v in after.items() if v.strip()}

    p = outdir / ("create_step2_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=10)
    ap.add_argument("--after", type=float, default=8)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
