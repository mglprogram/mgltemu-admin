# -*- coding: utf-8 -*-
"""peek_pagesize.py — 看某店订单列表的「每页」控件与可选值（只读）。

★ 2026-11-06 真机：BEL 待发货 99 单，`_set_page_size_500` 报「下拉里没有 500 选项」，
  于是只扫了 20 行就下结论「可确认 0」——**不可信**。
  两个店的每页选项可能不是一套（BHY 有 500，BEL 可能只到 100）。

本脚本把控件结构、当前值、展开后的全部选项都打出来，别猜。

用法：
    python scripts/power/peek_pagesize.py --store BEL
    python scripts/power/peek_pagesize.py --store BHY
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "lib"))
sys.path.insert(0, _HERE)

#: 找「每页」控件：不只看 li.PGT_sizeChanger_123，任何含「x条/页」或「每页」的可见元素都收
FIND = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
      && getComputedStyle(e).visibility !== 'hidden'; };
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const out = [];
  [...document.querySelectorAll('li,div,span,button')].filter(vis).forEach(e => {
    const t = txt(e);
    if (!t || t.length > 24) return;
    if (!/(每页|\d+\s*条\s*\/\s*页)/.test(t)) return;
    const r = e.getBoundingClientRect();
    out.push({t, cls: (e.className||'').toString().slice(0,60),
              x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2),
              kids: e.children.length});
  });
  // 去重（父子会重复出现）
  const seen = new Set(), uniq = [];
  out.forEach(o => { const k = o.cls + '|' + o.t; if (!seen.has(k)) { seen.add(k); uniq.push(o); } });
  return JSON.stringify(uniq.slice(0, 10), null, 1);
}"""

#: 展开后所有可见选项（不限定 class，任何像「N条/页」「N」的项都收）
OPTIONS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
      && getComputedStyle(e).visibility !== 'hidden'; };
  const txt = e => (e.innerText || '').replace(/\s+/g, '').trim();
  const out = [];
  [...document.querySelectorAll('li,div[class*=item],div[class*=Item]')].filter(vis)
    .forEach(e => {
      const t = txt(e);
      if (!/^\d+$|^\d+条\/页$/.test(t)) return;
      const r = e.getBoundingClientRect();
      out.push({t, cls: (e.className||'').toString().slice(0,50),
                x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    });
  const seen = new Set(), uniq = [];
  out.forEach(o => { const k = o.t + '|' + o.y; if (!seen.has(k)) { seen.add(k); uniq.push(o); } });
  return JSON.stringify(uniq.slice(0, 20), null, 1);
}"""


def main() -> int:
    ap = argparse.ArgumentParser(description="看每页控件与选项（只读）")
    ap.add_argument("--store", default="BEL")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    import temu_confirm_ship as T  # noqa: PLC0415

    pg = T._page(a.store, print)
    pg = T._goto_pending(pg, print)

    print("\n== 每页控件 ==")
    ctl = json.loads(pg.evaluate(FIND) or "[]")
    for c in ctl:
        print("  %-14s cls=%-34s (%s,%s) kids=%s"
              % (c["t"], c["cls"][:34], c["x"], c["y"], c["kids"]))
    if not ctl:
        print("  （没找到）")
        return 0

    # 点最像「每页」的那个（class 里带 sizeChanger 优先，否则第一个）
    pick = next((c for c in ctl if "sizeChanger" in c["cls"]), ctl[0])
    print("\n点它：%s (%s,%s)" % (pick["t"], pick["x"], pick["y"]))
    pg.dispatch_mouse(int(pick["x"]), int(pick["y"]))
    time.sleep(2)

    print("\n== 展开后的选项 ==")
    print(pg.evaluate(OPTIONS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
