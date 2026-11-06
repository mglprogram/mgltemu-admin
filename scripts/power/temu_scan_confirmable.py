# -*- coding: utf-8 -*-
"""temu_scan_confirmable.py — 在「待发货」里找出**真正可确认发货**的单（有运单号 + 有「去手动确认」）。

用户口径（2026-11-06）：
  「如果没有记录就说明下单成功，然后你回到订单列表 → 待发货 → 勾选 → 批量确认发货」

真机坑（刚踩到）：
  「批量确认发货 32」这个数字是**全库可确认数**；一旦勾选当前页那 20 单（都没运单号），
  按钮立刻变成「批量确认发货 0」并置灰 ⇒ 说明它们**不可确认**。
  故必须先筛出「有运单号 + 行内有『去手动确认』」的行，再勾选。

本脚本：只读枚举，按条件分组输出，并把这些单号存 json 供下一步精确确认。
"""
import json
import os
import sys
import time

LIB = r"D:\AI-workplace\TEMU\scripts\power\lib"
sys.path.insert(0, LIB)
import ziniao_store as zs  # noqa: E402
from cdp_transport import CdpPage  # noqa: E402

PEEK = r"D:\AI-workplace\TEMU\scripts\power\out\_peek"
OUT = r"D:\AI-workplace\TEMU\scripts\power\out\2026-11-06"

_TRACK = r"1Z[0-9A-Z]{16}|GFUS\d{8,}|SPX[A-Z0-9]{6,}|SWX\d{6,}|YWE\d{8,}|[A-Z]{2,6}\d{8,}"

JS_SCAN = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const rows = [...document.querySelectorAll('tr')].filter(vis).map(txt)
    .filter(t => /PO-\d+-\d+/.test(t));
  const trackRe = new RegExp(%s);
  const out = rows.map(t => ({
    po: (t.match(/PO-\d+-\d+/)||[''])[0],
    manual: /去手动确认/.test(t),
    track: (t.match(trackRe)||[''])[0],
    buy: /在线下单/.test(t),
  }));
  const uniq = [];
  out.forEach(o => { if (!uniq.some(u => u.po === o.po)) uniq.push(o); });
  // 页头数字
  const b = document.body.innerText||'';
  const cnt = (b.match(/共有\s*(\d+)\s*条/)||[])[1]||'';
  const tabs = [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
    .filter(t => /^待发货\(\d+\)$/.test(t));
  return JSON.stringify({rows: uniq, total: cnt, pending: tabs[0]||''});
}""" % json.dumps(_TRACK)


def main():
    store = sys.argv[1] if len(sys.argv) > 1 else "BEL"
    page_size = "500"
    port = zs.probe_port_by_window("TM-BEL-MGL" if store == "BEL" else "TM-BHY-YJF")
    tgs = [t for t in zs.list_page_targets(port) if "/mmsos/orders" in (t.get("url") or "")
           and "parentOrderSnList=P" not in (t.get("url") or "")]
    if not tgs:
        print("[FAIL] 没有订单列表页")
        return 42
    pg = CdpPage(port, tgs[0])

    # 每页拉满 500，保证一页看完
    st = json.loads(pg.evaluate(r"""() => {
      const b = (document.body.innerText||'');
      const m = b.match(/每页\s*(\d+)\s*条/);
      return JSON.stringify({size: m?m[1]:''});
    }""") or "{}")
    print("当前每页:", st.get("size"))
    if st.get("size") != page_size:
        # 点「每页」下拉 → 选 500
        d = json.loads(pg.evaluate(r"""() => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const el = [...document.querySelectorAll('*')].filter(vis)
            .filter(e => /^每页/.test((e.innerText||'').trim()) && e.children.length <= 3)[0];
          if (!el) return '{}';
          const r = el.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
        }""") or "{}")
        if d.get("x"):
            pg.dispatch_mouse(d["x"], d["y"]); time.sleep(2)
            o = json.loads(pg.evaluate(r"""() => {
              const vis = e => { const r = e.getBoundingClientRect();
                return e.getClientRects().length>0 && r.width>0 && r.height>0; };
              const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
              const it = [...document.querySelectorAll('li,[role=option]')].filter(vis)
                .filter(e => txt(e) === '500' || txt(e) === '500条')[0];
              if (!it) return '{}';
              const r = it.getBoundingClientRect();
              return JSON.stringify({t: txt(it), x: Math.round(r.x+r.width/2),
                                     y: Math.round(r.y+r.height/2)});
            }""") or "{}")
            if o.get("x"):
                pg.dispatch_mouse(o["x"], o["y"]); time.sleep(6)
    print("每页:", pg.evaluate(r"""() => {
      const m = (document.body.innerText||'').match(/每页\s*(\d+)\s*条/);
      return m ? m[1] : '';
    }"""))

    d = json.loads(pg.evaluate(JS_SCAN) or "{}")
    rows = d.get("rows") or []
    ok = [r for r in rows if r["track"]]
    manual = [r for r in ok if r["manual"]]
    no_track = [r for r in rows if not r["track"]]
    print("\n%s 待发货 %s 条（本页 %d 行）" % (store, d.get("pending"), len(rows)))
    print("  有运单号 %d / 其中带「去手动确认」%d / 无运单号 %d"
          % (len(ok), len(manual), len(no_track)))
    print("\n可确认（有运单号+去手动确认）前 10:")
    for r in manual[:10]:
        print("   %-26s %s" % (r["po"], r["track"]))
    if len(manual) > 10:
        print("   …共 %d 单" % len(manual))
    print("\n有运单号但没「去手动确认」前 5:")
    for r in ok[:5]:
        if r["manual"]:
            continue
        print("   %-26s %s" % (r["po"], r["track"]))

    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "temu_confirmable_%s.json" % store)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"store": store, "pending": d.get("pending"),
                   "manual": [r["po"] for r in manual],
                   "with_track": [r["po"] for r in ok],
                   "no_track": [r["po"] for r in no_track]}, f, ensure_ascii=False, indent=1)
    print("\n已存", p)
    raw = pg.screenshot()
    os.makedirs(PEEK, exist_ok=True)
    with open(os.path.join(PEEK, "temu_confirmable_%s.png" % store), "wb") as fh:
        fh.write(raw)
    print("落图 temu_confirmable_%s.png" % store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
