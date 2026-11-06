# -*- coding: utf-8 -*-
"""pw_hwc_getsn.py — 待打包页(pb=2) 批量获取单号 → 回读运单号。

用户口径（2026-11-06）：
    16:30–19:00 是禁下单时段，页面上不会有「确认订单」/「批量获取单号」；
    过了 19 点刷新页面按钮才出现。

真机：
    · pb=2 页面**没有** `select_types`，批量按钮是顶部的
      `input[value=批量获取单号]`（= `edit_order('huoqumd')` → `get_ups_dh()`）
    · 按钮必须先勾选行才生效；勾选要 checked + attr 双写
    · 进度看页面上的 `waybill_ing` / `waybill_sm` / `waybill_count`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)
from cdp_transport import CdpPage  # noqa: E402

import pw_hwc_track as T  # noqa: E402

OUT = os.path.join(HERE, "out", "2026-11-06")
PEEK = os.path.join(HERE, "out", "_peek")


def open_pb2(pg: CdpPage) -> None:
    pg.goto("https://www.powerwarehouse.net/deliver.php?act=list&pb=2")
    T.arm(pg)
    for _ in range(30):
        time.sleep(1)
        T.arm(pg)
        if pg.evaluate("() => !!document.querySelector('input[value=\"批量获取单号\"]')"):
            return
    print("[WARN] 30s 内没等到「批量获取单号」按钮")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", nargs="*", default=[])
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    os.makedirs(PEEK, exist_ok=True)

    pos = []
    for f in a.list:
        with open(f, encoding="utf-8") as fh:
            pos += [x.strip() for x in fh if x.strip()]
    print("清单 %d 单" % len(pos))

    pg = T.page()
    open_pb2(pg)

    # 在 pb=2 页面上搜索（不能走 T.do_search：它会把 pb 改成 all 导致按钮消失）
    pg.evaluate(r"""(nos) => {
      const ta = document.querySelector('textarea[name=keywords]');
      const st = document.querySelector('select[name=search_types]');
      if (st) st.value = 'deliver_no';
      const f = ta.closest('form') || document.forms[0];
      const set = (n, v) => { const e = f.elements[n]; if (e) e.value = v; };
      set('pb', '2'); set('is_xuni', '0'); set('is_hui', '');
      set('shop_id', ''); set('ck_sku', ''); set('seller_sku', '');
      set('start_date', '2026-09-01'); set('end_date', '2026-12-31');
      set('is_cd', ''); set('ck', ''); set('chqd', '');
      ta.focus(); ta.select();
      return 1;
    }""", {"nos": "\n".join(pos)})
    cdp = pg.cdp_session()
    cdp.send("Input.insertText", {"text": "\n".join(pos)})
    time.sleep(0.8)
    pg.evaluate("() => { searchOrder(1); return 1; }")

    want = set(pos)
    rows = []
    for _ in range(45):
        time.sleep(1)
        T.arm(pg)
        rows = [T.row_view(r) for r in T.read_rows(pg)]
        if rows and ({x["po"] for x in rows} <= want):
            break
    from collections import Counter
    print("出库单 %d 行；状态 %s" % (len(rows), dict(Counter(x["status"] for x in rows))))
    todo = [x for x in rows if "待打包" in (x["status"] or "")]
    print("待打包 %d" % len(todo))
    if not todo:
        print("[OK] 没有待打包的单")
        return 0

    n = T.set_checks(pg, [x["id"] for x in todo])
    print("勾选 %d" % n)
    bt = json.loads(pg.evaluate(r"""() => {
      const el = document.querySelector('input[value="批量获取单号"]');
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const rc = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(rc.x + rc.width / 2),
                             y: Math.round(rc.y + rc.height / 2),
                             oc: el.getAttribute('onclick') || ''});
    }""") or "{}")
    print("按钮:", bt)
    open(os.path.join(PEEK, "getsn_ready.png"), "wb").write(pg.screenshot())
    if not a.commit:
        print("[dry-run] 未取号")
        return 0
    if not bt.get("x"):
        raise SystemExit("[FATAL] 找不到「批量获取单号」")

    T.arm(pg)
    pg.dispatch_mouse(bt["x"], bt["y"])
    t0 = time.time()
    last = None
    while time.time() - t0 < 2400:
        time.sleep(5)
        T.arm(pg)
        st = json.loads(pg.evaluate(r"""() => {
          const g = n => { try { return eval(n); } catch(e) { return null; } };
          const busy = [...document.querySelectorAll('[class^=waybill_]')]
            .filter(e => /获取中/.test(e.innerText || '')).length;
          const done = [...document.querySelectorAll('[class^=waybill_]')]
            .filter(e => /1Z|GFUS|SPX|SWX|YWE/.test(e.innerText || '')).length;
          return JSON.stringify({ing: g('waybill_ing'), sm: g('waybill_sm'),
                                 cnt: g('waybill_count'), busy, done});
        }""") or "{}")
        if st != last:
            print("   [取号] t=%.0fs ing=%s 已回=%s/%s 获取中=%s 已出号=%s"
                  % (time.time() - t0, st.get("ing"), st.get("sm"), st.get("cnt"),
                     st.get("busy"), st.get("done")))
            last = st
        if st.get("ing") in (0, None) and st.get("busy") == 0:
            print("取号结束:", st)
            break
    else:
        print("[WARN] 取号超时")

    open(os.path.join(PEEK, "getsn_done.png"), "wb").write(pg.screenshot())
    # 回读
    pg.evaluate("() => { searchOrder(1); return 1; }")
    for _ in range(45):
        time.sleep(1)
        T.arm(pg)
        rows = [T.row_view(r) for r in T.read_rows(pg)]
        if rows:
            break
    print("取号后状态:", dict(Counter(x["status"] for x in rows)))
    json.dump(rows, open(os.path.join(OUT, "getsn_final.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 运单号要逐单读 [详情]（列表没有运单号列）
    print("\n逐单读运单号（%d 单）…" % len(rows))
    got = {}
    for i, r in enumerate(rows, 1):
        try:
            h = pg.evaluate(r"""(oid) => {
              return new Promise(res => {
                jQuery.post('./deliver.php?act=order_info&type=view&id=' + oid,
                  {}, function(html) { res(String(html).slice(0, 20000)); }, 'text'
                ).fail(() => res(''));
              });
            }""", r["id"])
        except Exception:  # noqa: BLE001
            h = ""
        m = T.WB_RE.search(re.sub(r"<[^>]+>", " ", h or ""))
        got[r["po"]] = {"id": r["id"], "wb": (m.group(1) if m else ""), "status": r["status"]}
        if i % 20 == 0:
            print("   …%d/%d" % (i, len(rows)))
    n_ok = sum(1 for v in got.values() if v["wb"])
    print("有运单号 %d / %d" % (n_ok, len(got)))
    json.dump(got, open(os.path.join(OUT, "getsn_waybills.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    for k, v in list(got.items())[:5]:
        print("   %-28s %-20s %s" % (k, v["wb"] or "-", v["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
