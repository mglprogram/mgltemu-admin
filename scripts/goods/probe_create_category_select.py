# -*- coding: utf-8 -*-
"""在「新建商品」页上真的选中一个类目，抓它之后打的属性/价格/SKU 接口。

前提：上一轮已确认 `/goods/create/category` 权限在，页面有「1 选择商品分类 →
2 基本信息」两步；类目接口是
    POST /anniston-agent-seller/category/children/list  {"parentCatId": N}
    POST /visage-agent-seller/product/queryCatHints     {}

本脚本：
    ① 打开新建页
    ② 用 React 受控输入 + 点击，在「常用推荐」里点一个默认类目
       （点不到就用键盘/滚动兜底，并打印页面真实结构方便下次定位）
    ③ 抓点击之后新出现的接口，并按「属性/价格/SKU/图片」分组打印
    ④ 同时试接口直调：给 children/list 一个叶子 catId，看有没有「按类目取模板」的接口

只读（不提交、不保存草稿）。用完关页。
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

#: 页面结构自检：把「常用推荐」区块的 DOM 摸清楚（下次不靠猜）
DUMP_JS = r"""(function(){
  function pick(sel){var e=document.querySelector(sel);return e?{tag:e.tagName,cls:String(e.className).slice(0,120),txt:String(e.innerText||'').slice(0,300)}:null;}
  var cands=[];
  var all=document.querySelectorAll('div,span,a,li');
  for(var i=0;i<all.length;i++){
    var t=(all[i].innerText||'').trim();
    if(t==='常用推荐'){cands.push({k:'常用推荐',tag:all[i].tagName,cls:String(all[i].className).slice(0,100)});}
  }
  var clickables=[];
  for(var i=0;i<all.length;i++){
    var e=all[i];
    var t=(e.innerText||'').trim();
    if(t.indexOf('>')>0 && t.length<120 && e.children.length<=3){
      clickables.push({tag:e.tagName,cls:String(e.className).slice(0,80),txt:t.slice(0,100)});
      if(clickables.length>=8) break;
    }
  }
  return JSON.stringify({body:pick('body'),recTitles:cands.slice(0,3),samplePath:clickables});
})()"""

#: ⚠ 坑（2026-11-26 实测）：
#:  ① 按「innerText 含 > 且子元素少」挑，会挑中**外层容器**（innerText 是整块拼接），
#:     `el.click()` 打在 div 上无事发生 → 返回 null。
#:  ② 即使挑对叶子，`el.click()` 也**不触发 React 的 onClick**（React 走合成事件 +
#:     根节点委托，需要真实的 pointerdown/mousedown/mouseup/click 序列）。
#:  ⇒ 两段式：先 RETURN 坐标，再用 CDP `Input.dispatchMouseEvent` 真点。
LOCATE_JS = r"""(function(){
  var out={steps:[]};
  // 1) 商品分类下拉框（点开才能看到常用推荐 / 全部分类）
  var trig=null;
  var ins=document.querySelectorAll('input');
  for(var i=0;i<ins.length;i++){ trig=ins[i]; break; }
  if(!trig){
    var ss=document.querySelectorAll('[class*=select],[class*=Select]');
    if(ss.length) trig=ss[0];
  }
  out.trigger = trig? {tag:trig.tagName,cls:String(trig.className).slice(0,90)} : null;
  // 1b) 经营站点下拉（没选站点时也能先选类目，但先点开更接近真人）
  var site=null;
  var ss2=document.querySelectorAll('[class*=select],[class*=Select]');
  for(var i=0;i<ss2.length;i++){
    var t2=String(ss2[i].innerText||'').trim();
    if(t2.indexOf('经营站点')>=0||t2.indexOf('请选择经营站点')>=0){ site=ss2[i]; break; }
  }
  out.site = site?String(site.className).slice(0,90):null;

  // 2) 找「常用推荐」里的第一条建议（叶子级：innerText 含 > 且不含换行）
  var block=null, all=document.querySelectorAll('[class*=suggestWrapper],[class*=suggest]');
  for(var i=0;i<all.length;i++){ if((all[i].innerText||'').indexOf('常用推荐')>=0){block=all[i];break;} }
  out.block = block?String(block.className).slice(0,90):null;
  var items=[];
  if(block){
    var ds=block.querySelectorAll('*');
    for(var i=0;i<ds.length;i++){
      var t=String(ds[i].innerText||'').trim();
      if(t.indexOf('>')<0||t.indexOf('\n')>=0||t.length>120) continue;
      items.push(ds[i]);
    }
  }
  out.n_items=items.length;
  var target = items[0] || trig;
  if(!target) return JSON.stringify(Object.assign(out,{ok:false,why:'nothing to click'}));
  target.scrollIntoView({block:'center'});
  var r=target.getBoundingClientRect();
  out.ok=true;
  out.picked=String(target.innerText||'').trim().slice(0,120);
  out.picked_cls=String(target.className).slice(0,90);
  out.x=Math.round(r.x+r.width/2); out.y=Math.round(r.y+r.height/2);
  out.w=Math.round(r.width); out.h=Math.round(r.height);
  return JSON.stringify(out);
})()"""


async def real_click(cap, x, y):
    """真实鼠标点击序列（React 只认这个）。"""
    for et in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent", {
            "type": et, "x": x, "y": y, "button": "left",
            "clickCount": 1 if et != "mouseMoved" else 0,
        })


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

    report: dict = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "url": CREATE}
    try:
        async with cdp_net.NetCapture(ws, match=()) as cap:
            await asyncio.sleep(a.settle)
            d, _ = await cap.eval(DUMP_JS, timeout=40)
            report["dom"] = str(d)[:800]

            before = {u.replace(BASE, "").split("?")[0] for u in cap._urls.values()
                      if u and "agentseller.temu.com" in u and not SKIP.search(u)}
            print("[前] 已有接口 %d 个" % len(before), flush=True)

            c, exc = await cap.eval(LOCATE_JS, timeout=40)
            print("[定位] %s" % str(c)[:500], flush=True)
            report["locate"] = str(c)[:700]
            try:
                loc = json.loads(c or "{}")
            except Exception:
                loc = {}
            if loc.get("ok"):
                await real_click(cap, loc["x"], loc["y"])
                print("[真点] (%s,%s) → %s" % (loc["x"], loc["y"], loc.get("picked")), flush=True)
                await asyncio.sleep(a.after)

            # 点完看是否真的选中了（「请完整选择商品分类」应该消失）
            v2, _ = await cap.eval(
                "JSON.stringify({url:location.href,"
                "stillHint:(document.body.innerText||'').includes('请完整选择商品分类'),"
                "needSite:(document.body.innerText||'').includes('请选择经营站点'),"
                "crumbs:(function(){var e=document.querySelectorAll('[class*=crumb],[class*=Crumb],[class*=tag]');"
                "var o=[];for(var i=0;i<e.length&&i<12;i++){var t=String(e[i].innerText||'').trim();"
                "if(t&&t.length<150)o.push(t);}return o;})()})")
            print("[选中状态] %s" % str(v2)[:500], flush=True)
            report["after_click"] = str(v2)[:700]

            # 再点「下一步」（同样走真实点击）
            n, nex = await cap.eval(
                "(function(){var bs=document.querySelectorAll('button,a,span,div');"
                "for(var i=0;i<bs.length;i++){var t=String(bs[i].innerText||'').trim();"
                "if(t==='下一步'&&bs[i].children.length===0){var r=bs[i].getBoundingClientRect();"
                "return JSON.stringify({ok:true,x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}}"
                "return JSON.stringify({ok:false});})()", timeout=40)
            print("[下一步定位] %s" % str(n)[:200], flush=True)
            report["next_click"] = str(n)[:200]
            try:
                nb = json.loads(n or "{}")
            except Exception:
                nb = {}
            if nb.get("ok"):
                await real_click(cap, nb["x"], nb["y"])
            await asyncio.sleep(a.after)

            v3, _ = await cap.eval(
                "JSON.stringify({url:location.href,t:document.title,"
                "txt:(document.body&&document.body.innerText||'').slice(0,1800)})")
            report["page_after"] = json.loads(v3 or "{}")
            print("\n[页面后] url=%s\ntitle=%s" % (report["page_after"].get("url"),
                                                   report["page_after"].get("t")), flush=True)
            txt = str(report["page_after"].get("txt") or "").replace("\n", " | ")
            print("[页面后正文] %s" % txt[:900], flush=True)

            urls, bodies, hits = dict(cap._urls), dict(cap._bodies), list(cap.hits)
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
        after[p] = (bodies.get(rid) or "")[:2000]

    new = sorted(set(after) - before)
    print("\n=== 选中类目 + 下一步后【新出现】的接口 %d 个 ===" % len(new), flush=True)
    for p in new:
        bd = after.get(p) or ""
        print("  ", p, flush=True)
        if bd.strip():
            print("       req:", bd[:260].replace("\n", " "), flush=True)
    print("\n=== 页面全部商品接口 %d 个 ===" % len(after), flush=True)
    for p in sorted(after):
        print("  ", p, flush=True)
    report["new_apis"] = new
    report["all_apis"] = sorted(after)
    report["bodies"] = {k: v for k, v in after.items() if v.strip()}

    p = outdir / ("create_category_clicked_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    p.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n落盘", p)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--settle", type=float, default=10)
    ap.add_argument("--after", type=float, default=10, help="点击后等待秒数")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
