# -*- coding: utf-8 -*-
"""peek_scan.py — 看 `JS_SCAN` 扫出来的每行字段（尤其 `manual` 标志怎么来的）。

代码里「可确认」= `track` 且 `manual`（行内有「去手动确认」）。
抽屉里却是「确认发货 0 / 暂无数据」⇒ 说明 `manual` 这个判据不对：
现在的 TEMU 版本里，**已经有运单号的待发货单**不显示「去手动确认」，
它们本来就能在抽屉里直接确认。

本脚本只读，打印前 N 行，并把「去手动确认」在整页里到底存不存在查清楚。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "lib"))
sys.path.insert(0, _HERE)

CLOSE = r"""() => {
  const txt = e => (e.innerText || '').trim();
  const b = [...document.querySelectorAll('button')]
    .filter(x => x.getBoundingClientRect().width > 0)
    .find(x => txt(x) === '取消');
  if (b) { b.click(); return 'closed'; }
  return 'no-cancel';
}"""

PROBE = r"""() => {
  const txt = e => (e.innerText || '').replace(/\s+/g, ' ').trim();
  const body = document.body.innerText || '';
  const rows = [...document.querySelectorAll('tr')]
    .filter(t => /PO-\d+-\d+/.test(txt(t)));
  return JSON.stringify({
    rowsTotal: rows.length,
    hasManualWord: body.indexOf('去手动确认') >= 0,
    manualCount: rows.filter(t => txt(t).indexOf('去手动确认') >= 0).length,
    sample: rows.slice(0, 3).map(t => txt(t).slice(0, 400)),
  }, null, 1);
}"""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    import temu_confirm_ship as T  # noqa: PLC0415

    pg = T._page("BHY", print)
    pg = T._goto_pending(pg, print)
    print("关抽屉：", pg.evaluate(CLOSE))
    import time
    time.sleep(2)

    st = json.loads(pg.evaluate(T.JS_SCAN) or "{}")
    rows = st.get("rows") or []
    print("\nJS_SCAN rows=%d 待发货=%s" % (len(rows), st.get("pending")))
    tr = [r for r in rows if r.get("track")]
    print("有运单号 %d；其中 manual=True %d；无运单号 %d"
          % (len(tr), len([r for r in tr if r.get("manual")]),
             len([r for r in rows if not r.get("track")])))
    if rows:
        print("\n首行字段：")
        print(json.dumps(rows[0], ensure_ascii=False, indent=1))

    print("\n页面里「去手动确认」实况：")
    print(pg.evaluate(PROBE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
