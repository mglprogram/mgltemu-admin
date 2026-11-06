# -*- coding: utf-8 -*-
"""verify_confirmed.py — 回读「某批单号是否已经离开待发货」（只读，唯一判据）。

★ 2026-11-06 真机教训（我自己又错一次）：
    `temu_confirm_ship.py --commit` 四层都点了，判据读到「待发货 460 → 460」
    ⇒ 报 `no_effect`（失败）。但**实际是成功的**：平台计数有延迟，
    等一会儿再读是 **460 → 393**，正好少了 67 单（= 目标数）。
    之后 `probe_one_confirm.py` 再扫，带运单号的行已经是 0 行。

    ⇒ 判据没错（只认待发货下降），错在**等待窗口太短**（4×4s）。
    另外"重读同一个计数"不够，还要能**按单号确认它们真的不在待发货里**
    （计数被别的单补回来时，单看差值会误判）。

本脚本按单号回读：
    ① 待发货里逐页搜这批单号，命中 = 还没发出去（失败）
    ② 一个都搜不到 = 已经离开待发货（成功）

用法：
    python scripts/power/verify_confirmed.py --store BHY --file <单号清单.txt>
    python scripts/power/verify_confirmed.py --store BHY --orders "PO-x,PO-y"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "lib"))
sys.path.insert(0, _HERE)

#: 当前页签里所有订单行文本（用于按单号查在不在）
JS_ROWS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const body = document.body.innerText||'';
  const rows = [...document.querySelectorAll('tr')].filter(vis).map(txt)
    .filter(t => /PO-\d+-\d+/.test(t));
  const tabs = {};
  [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
    .forEach(t => { const m = t.match(/^(待发货|已发货|全部|平台处理中|已签收)\((\d+)\)$/);
                    if (m) tabs[m[1]] = Number(m[2]); });
  return JSON.stringify({rows, tabs,
    selected: (body.match(/已选订单[：:]\s*(\d+)/)||[])[1]||''});
}"""


def main() -> int:
    ap = argparse.ArgumentParser(description="按单号回读是否已离开待发货（只读）")
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--orders", default="", help="单号，逗号/空格分隔")
    ap.add_argument("--file", default="", help="单号清单文件（一行一个）")
    ap.add_argument("--wait", type=int, default=0, help="先等 N 秒再看（平台计数有延迟）")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    pos = [x.strip() for x in re.split(r"[,\s]+", a.orders) if x.strip()]
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            pos += [x.strip() for x in f if x.strip()]
    if not pos:
        print("[FAIL] 没给单号", file=sys.stderr)
        return 42

    if a.wait:
        print("先等 %ds（平台计数有延迟）…" % a.wait)
        time.sleep(a.wait)

    import temu_confirm_ship as T  # noqa: PLC0415

    pg = T._page(a.store, print)
    pg = T._goto_pending(pg, print)
    T._set_page_size_500(pg, print)
    # 强制刷新一次，避免读到缓存渲染
    pg.evaluate("() => { location.reload(); return 1; }")
    time.sleep(12)
    pg = T._goto_pending(pg, print)
    T._set_page_size_500(pg, print)

    st = json.loads(pg.evaluate(JS_ROWS) or "{}")
    rows = st.get("rows") or []
    blob = "\n".join(rows)
    still = [p for p in pos if p in blob]
    gone = [p for p in pos if p not in blob]
    print("\n页签计数：%s" % json.dumps(st.get("tabs") or {}, ensure_ascii=False))
    print("待发货当前渲染 %d 行" % len(rows))
    print("已离开待发货 %d / %d" % (len(gone), len(pos)))
    if still:
        print("仍在待发货 %d 单：%s" % (len(still), ", ".join(still[:10])))

    verdict = "all_gone" if not still else ("none_gone" if not gone else "partial")
    print("\n[RESULT] " + json.dumps({"store": a.store, "target": len(pos),
                                      "gone": len(gone), "still": len(still),
                                      "verdict": verdict}, ensure_ascii=False))
    return 0 if verdict == "all_gone" else 42


if __name__ == "__main__":
    raise SystemExit(main())
