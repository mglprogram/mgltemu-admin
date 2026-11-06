# -*- coding: utf-8 -*-
"""pw_hwc_readwb.py — 从待打包列表页逐单读运单号（只读）。

真机教训：
    · 运单号在列表页的 `td[class^=waybill_<出库单id>]` 单元格里，**这里是权威来源**。
    · 详情页 `deliver.php?act=order_info&type=view` 的「运单号」字段常常是空的，
      而且页面上有大量 14~18 位数字（参考单号尾巴），用宽松正则去兜底会把它们
      误当运单号 —— 实测 124 单全被误判成"有号"。
      ⇒ 只认承运商前缀形态：1Z / GFUS / SPX / SWX / YWE / 2 位字母+8 位以上数字。

用法：
    python scripts/power/pw_hwc_readwb.py --list <单号txt> --tag final
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
import pw_hwc_track as T  # noqa: E402

OUT = os.path.join(HERE, "out", "2026-11-06")
PEEK = os.path.join(HERE, "out", "_peek")

#: 只认这些承运商形态（用户口径的四种 + 常见补充）。
#: 9234... 是 USPS 的 22 位 IMpb 运单号，也是合法号，别漏（实测踩到 2 单）。
WB_STRICT = re.compile(
    r"^(1Z[0-9A-Z]{16}|GFUS[0-9A-Z]{8,}|SPX[0-9A-Z]{6,}|SWX[0-9A-Z]{6,}"
    r"|YWE[0-9A-Z]{6,}|92\d{18,24}|[A-Z]{2}\d{8,15})$")

JS_WB = r"""() => {
  const out = [];
  document.querySelectorAll('tr[class^=tr_html_]').forEach(t => {
    const id = (t.className.match(/tr_html_(\d+)/) || [])[1];
    if (!id) return;
    const tds = [...t.children].filter(e => e.tagName === 'TD');
    const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
    const c = t.querySelector('[class^=waybill_]');
    out.push({id, po: txt(t.querySelector('[class^=deliver_no_]') || document.createElement('i')),
              wb: c ? txt(c) : '', status: tds[12] ? txt(tds[12]) : '',
              out_no: tds[1] ? txt(tds[1]) : '', fn: tds[4] ? txt(tds[4]) : '',
              wh: tds[9] ? txt(tds[9]) : ''});
  });
  return JSON.stringify(out);
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", nargs="*", default=[])
    ap.add_argument("--tag", default="final")
    a = ap.parse_args()
    os.makedirs(PEEK, exist_ok=True)

    pos = []
    for f in a.list:
        with open(f, encoding="utf-8") as fh:
            pos += [x.strip() for x in fh if x.strip()]
    print("清单 %d 单" % len(pos))

    pg = T.page()
    pg.goto("https://www.powerwarehouse.net/deliver.php?act=list&pb=2")
    T.arm(pg)
    for _ in range(30):
        time.sleep(1)
        T.arm(pg)
        if pg.evaluate("() => !!document.querySelector('textarea[name=keywords]')"):
            break

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
    for _ in range(50):
        time.sleep(1)
        T.arm(pg)
        rows = json.loads(pg.evaluate(JS_WB) or "[]")
        if rows and {x["po"] for x in rows} <= want:
            break

    from collections import Counter
    print("列表 %d 行；状态 %s" % (len(rows), dict(Counter(x["status"] for x in rows))))
    res = {}
    for r in rows:
        ok = bool(WB_STRICT.match(r["wb"] or ""))
        res[r["po"]] = {"id": r["id"], "out_no": r["out_no"], "wb": r["wb"],
                        "wb_ok": ok, "status": r["status"], "fn": r["fn"], "wh": r["wh"]}
    n_ok = sum(1 for v in res.values() if v["wb_ok"])
    print("\n有效运单号 %d / %d" % (n_ok, len(res)))
    p = os.path.join(OUT, "BHY_124_运单号.json")
    json.dump(res, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("落盘", p)
    for k, v in list(res.items())[:8]:
        print("   %-28s %-22s %-8s %s" % (k, v["wb"] or "-", v["status"], v["wh"]))
    miss = [k for k, v in res.items() if not v["wb_ok"]]
    if miss:
        print("\n[缺号/格式不符 %d 单]" % len(miss))
        for k in miss[:20]:
            print("   %-28s %r %s" % (k, res[k]["wb"], res[k]["status"]))
    open(os.path.join(PEEK, "readwb_%s.png" % a.tag), "wb").write(pg.screenshot())
    print("截图 readwb_%s.png" % a.tag)
    return 0 if not miss else 42


if __name__ == "__main__":
    raise SystemExit(main())
