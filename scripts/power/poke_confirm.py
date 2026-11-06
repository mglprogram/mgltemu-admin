# -*- coding: utf-8 -*-
"""poke_confirm.py — 手工逐步探查「批量确认发货」每一层的真实 DOM（只读，不提交）。

用途：`temu_confirm_ship.py --commit` 四层都点到了，但「待发货」计数不变。
      需要看清到底是哪一层没有真的生效：
        ① 顶部按钮点完 → 抽屉长什么样？
        ② 抽屉里那个「确认发货 N」点完 → 有没有二级弹窗？
        ③ 二级弹窗的「确认」到底是哪个元素？
      本脚本每层都 dump 所有可见的 dialog/button + 截图，**不点最后一下**。

用法：
    python scripts/power/poke_confirm.py --store BHY --step top     # 点顶部按钮
    python scripts/power/poke_confirm.py --store BHY --step drawer  # 再点抽屉按钮
    python scripts/power/poke_confirm.py --store BHY --step dump    # 只看当前
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "lib"))

PEEK = os.path.join(_HERE, "out", "_peek")

DUMP = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0
      && getComputedStyle(e).visibility !== 'hidden'; };
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const body = document.body.innerText || '';
  const buttons = [...document.querySelectorAll('button')].filter(vis).map(b => {
    const r = b.getBoundingClientRect();
    return {t: txt(b).slice(0, 30), disabled: b.disabled === true,
            cls: (b.className || '').toString().slice(0, 60),
            x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
            inView: r.top >= 0 && r.bottom <= window.innerHeight};
  }).filter(b => b.t);
  const overlays = [...document.querySelectorAll(
      '.modal,[class*=Modal],[class*=dialog],[class*=Dialog],[class*=drawer],'
      + '[class*=Drawer],[class*=popover],[class*=Popover]')]
    .filter(vis).map(e => ({cls: (e.className || '').toString().slice(0, 70),
                            t: txt(e).slice(0, 200)}))
    .filter(o => o.t);
  return JSON.stringify({
    sel: (body.match(/已选订单[：:]\s*(\d+)/) || [])[1] || '',
    pending: (body.match(/待发货\((\d+)\)/) || [])[1] || '',
    hasConfirmWord: /是否确认发货/.test(body),
    buttons: buttons.slice(0, 40), overlays: overlays.slice(0, 8)}, null, 1);
}"""


def main() -> int:
    ap = argparse.ArgumentParser(description="逐层探查批明确认发货的 DOM（只读）")
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--step", default="dump", choices=("dump", "top", "drawer"))
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    import temu_confirm_ship as T  # noqa: PLC0415

    pg = T._page(a.store, print)
    pg = T._goto_pending(pg, print)
    os.makedirs(PEEK, exist_ok=True)

    def shot(name: str) -> None:
        with open(os.path.join(PEEK, name), "wb") as f:
            f.write(pg.screenshot())
        print("  图：%s" % name)

    def dump(tag: str) -> dict:
        d = json.loads(pg.evaluate(DUMP) or "{}")
        print("\n===== %s =====" % tag)
        print("已选=%s 待发货=%s 有「是否确认发货」字样=%s"
              % (d.get("sel"), d.get("pending"), d.get("hasConfirmWord")))
        for o in d.get("overlays") or []:
            print("  [overlay] %-40s %s" % (o["cls"], o["t"][:110]))
        for b in d.get("buttons") or []:
            print("  [btn] %-24s disabled=%-5s inView=%-5s (%s,%s) %s"
                  % (b["t"], b["disabled"], b["inView"], b["x"], b["y"],
                     b["cls"][:34]))
        return d

    dump("初始状态")

    if a.step in ("top", "drawer"):
        b = json.loads(pg.evaluate(T.JS_FIND_BATCH_BTN) or "{}")
        print("\n点顶部「%s」 disabled=%s" % (b.get("t"), b.get("disabled")))
        if b.get("x"):
            pg.dispatch_mouse(b["x"], b["y"])
            time.sleep(6)
        shot("poke_1_after_top.png")
        dump("点完顶部按钮")

    if a.step == "drawer":
        db = json.loads(pg.evaluate(T.JS_FIND_DRAWER_BTN) or "{}")
        print("\n点抽屉「%s」 found=%s" % (db.get("t"), db.get("found")))
        if db.get("found"):
            pg.dispatch_mouse(db["x"], db["y"])
            time.sleep(6)
        shot("poke_2_after_drawer.png")
        dump("点完抽屉按钮")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
