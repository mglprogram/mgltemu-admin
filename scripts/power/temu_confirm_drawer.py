# -*- coding: utf-8 -*-
"""temu_confirm_drawer.py — 点右侧「批量确认发货」抽屉底部的「确认发货 N」。

用户口径（2026-11-06）：「把已经在恒信仓和帕沃尔仓下单成功的批量确认」

★ 真机：点列表顶部「批量确认发货 32」后，会从**右侧滑出一个抽屉**（不是居中弹窗），
  标题「批量确认发货」，列出 N 个包裹（PK-… + 关联运单），
  底部按钮是 **`确认发货 N`**（注意：不是「确认」，也不是「确认下单」）。
  这个抽屉的按钮是**页面级 fixed** ⇒ 查找时不能用 offsetParent 判可见。
"""
import argparse
import json
import sys
import time

LIB = r"D:\AI-workplace\TEMU\scripts\power\lib"
sys.path.insert(0, LIB)
import ziniao_store as zs  # noqa: E402
from cdp_transport import CdpPage  # noqa: E402

PEEK = r"D:\AI-workplace\TEMU\scripts\power\out\_peek"

JS_BTN = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const el = [...document.querySelectorAll('button')].filter(vis)
    .find(x => /^确认发货\s*\d*$/.test(txt(x)));
  if (!el) return JSON.stringify({found:false,
    cands: [...document.querySelectorAll('button')].filter(vis).map(txt)
             .filter(t=>t&&t.length<14).slice(0,12)});
  const r = el.getBoundingClientRect();
  return JSON.stringify({found:true, t: txt(el), disabled: el.disabled===true,
    x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
    inView: r.top>=0 && r.bottom<=window.innerHeight});
}"""

JS_STATE = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const body = document.body.innerText||'';
  const tabs = [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
    .filter(t => /^待发货\(\d+\)$/.test(t));
  const m = body.match(/已选订单[：:]\s*(\d+)/);
  const drawer = /批量确认发货/.test(body) && /确认发货/.test(body);
  return JSON.stringify({pending: tabs[0]||'', selected: m?m[1]:'', drawer,
    msg: (body.match(/(成功|失败|错误)[^，。]{0,30}/g)||[]).slice(0,3)});
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    port = zs.probe_port_by_window("TM-BEL-MGL" if a.store == "BEL" else "TM-BHY-YJF")
    tgs = [t for t in zs.list_page_targets(port) if "/mmsos/orders" in (t.get("url") or "")
           and "parentOrderSnList=P" not in (t.get("url") or "")]
    pg = CdpPage(port, tgs[0])
    print("前:", pg.evaluate(JS_STATE))

    d = json.loads(pg.evaluate(JS_BTN) or "{}")
    print("确认发货按钮:", json.dumps(d, ensure_ascii=False))
    if not d.get("found"):
        print("[FAIL] 找不到「确认发货 N」按钮 —— 抽屉可能没开")
        return 42

    raw = pg.screenshot()
    with open(PEEK + "\\temu_drawer_%s.png" % a.store, "wb") as f:
        f.write(raw)
    print("落图 temu_drawer_%s.png" % a.store)

    if not a.commit:
        print("[dry-run] 未点")
        return 0
    if d.get("disabled"):
        print("[FAIL] 按钮 disabled")
        return 42

    pg.dispatch_mouse(d["x"], d["y"])
    time.sleep(9)
    print("后:", pg.evaluate(JS_STATE))
    raw = pg.screenshot()
    with open(PEEK + "\\temu_drawer_done_%s.png" % a.store, "wb") as f:
        f.write(raw)
    print("落图 temu_drawer_done_%s.png" % a.store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
