# -*- coding: utf-8 -*-
"""temu_confirm_final.py — 抽屉「确认发货 32」→ 二级弹窗「是否确认发货？」→ 点「确认」。

真机链路（2026-11-06 逐步拍到的）：
    列表顶部「批量确认发货 32」
      → 右侧滑出抽屉「批量确认发货」（列出 N 个 PK-… + 关联运单）
      → 抽屉底部「确认发货 32」
      → **居中二级弹窗「是否确认发货？」**（按钮：确认 / 取消）   ← 本脚本点这层
      → 成功后列表「待发货」数量下降

⚠ 关键：二级弹窗的「确认」按钮必须**精确文本匹配 `确认`**（不是「确认发货」），
  且它出现在抽屉**之上**，要取**最上层的那个**可见 `确认` 按钮。
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

JS_STATE = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const tabs = [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
    .filter(t => /^待发货\(\d+\)$/.test(t));
  const body = document.body.innerText||'';
  const m = body.match(/已选订单[：:]\s*(\d+)/);
  const asksConfirm = /是否确认发货/.test(body);
  const btns = [...document.querySelectorAll('button')].filter(vis).map(txt)
    .filter(t => /批量确认发货|确认发货|^确认$|^取消$/.test(t)).slice(0, 8);
  return JSON.stringify({pending: tabs[0]||'', selected: m?m[1]:'',
    asksConfirm, btns});
}"""

JS_YES = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  // 精确文本「确认」，取**最靠下/最上层**的那个（二级弹窗在抽屉之上，DOM 里通常更靠后）
  const all = [...document.querySelectorAll('button')].filter(vis).filter(x => txt(x) === '确认');
  if (!all.length) return '{}';
  const el = all[all.length - 1];
  const r = el.getBoundingClientRect();
  return JSON.stringify({t: txt(el), x: Math.round(r.x+r.width/2),
                         y: Math.round(r.y+r.height/2), n: all.length,
                         inView: r.top>=0 && r.bottom<=window.innerHeight});
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

    d = json.loads(pg.evaluate(JS_YES) or "{}")
    print("「确认」按钮:", d)
    if not d.get("x"):
        print("[FAIL] 没找到二级弹窗的「确认」")
        return 42
    if not a.commit:
        print("[dry-run] 未点")
        return 0

    pg.dispatch_mouse(d["x"], d["y"])
    time.sleep(10)
    print("后:", pg.evaluate(JS_STATE))
    raw = pg.screenshot()
    with open(PEEK + "\\temu_confirm_final_%s.png" % a.store, "wb") as f:
        f.write(raw)
    print("落图 temu_confirm_final_%s.png" % a.store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
