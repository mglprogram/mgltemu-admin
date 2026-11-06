# -*- coding: utf-8 -*-
"""probe_one_confirm.py — 只勾 **1 单**打开抽屉，看「确认发货 N」是几（只读，不提交）。

为什么要这个（2026-11-06 真机）：
    BHY 待发货 460 单，其中 67 单有运单号。全勾后点「批量确认发货」，
    抽屉里是 **「确认发货 0」且置灰、表体「暂无数据」**，只有一个置灰分组
    「7-12个工作日发货包裹」⇒ 这一批在平台侧**根本没有可确认的包裹**。

    但"全部不可确认"和"我勾错了行"在结果上看着一样，必须区分：
    只勾 1 单再看抽屉——若仍是 0，就是这批单本身不可确认；
    若变成 1，说明是我批量勾选时勾到了别的行。

用法：
    python scripts/power/probe_one_confirm.py --store BHY
    python scripts/power/probe_one_confirm.py --store BHY --po PO-211-xxx
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

PEEK = os.path.join(_HERE, "out", "_peek")

#: 抽屉里「确认发货 N」的真实数字 + 分组清单（比只看 found 更可信）
READ_DRAWER = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const dr = document.querySelector('[class*=Drawer_content_123]');
  if (!dr) return JSON.stringify({open:false});
  const btns = [...dr.querySelectorAll('button')].filter(vis).map(b => ({
    t: txt(b), disabled: b.disabled===true
        || (b.className||'').toString().indexOf('disabled')>=0}));
  const groups = [...dr.querySelectorAll('label[data-testid=beast-core-checkbox]')]
    .map(l => ({t: txt(l), checked: l.getAttribute('data-checked'),
                disabled: /groupDisabled/.test(l.innerHTML)}));
  const empty = /暂无数据/.test(txt(dr));
  return JSON.stringify({open:true, btns, groups, empty,
                         text: txt(dr).slice(0, 300)});
}"""


def main() -> int:
    ap = argparse.ArgumentParser(description="只勾 1 单看抽屉确认发货数（只读）")
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--po", default="", help="指定单号（缺省=扫到的第一个有运单号的单）")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    import temu_confirm_ship as T  # noqa: PLC0415

    pg = T._page(a.store, print)
    pg = T._goto_pending(pg, print)
    T._set_page_size_500(pg, print)

    st = T._read_state(pg)
    rows = st.get("rows") or []
    with_track = [r for r in rows if r.get("track")]
    print("待发货 %s ｜ 行 %d ｜ 有运单号 %d" % (st.get("pending"), len(rows), len(with_track)))
    po = a.po or (with_track[0]["po"] if with_track else "")
    if not po:
        print("[FAIL] 没有带运单号的单")
        return 42
    print("试点单：%s" % po)

    # 先清空已勾（取消全选），再只勾这一单
    pg.evaluate(r"""() => {
      const vis = e => { const r=e.getBoundingClientRect(); return r.width>0 && r.height>0; };
      const all = [...document.querySelectorAll('label[data-testid=beast-core-checkbox]')]
        .filter(vis).filter(l => l.getAttribute('data-checked') === 'true');
      // 只点「表头全选框」之外的行勾选：这里直接逐个点掉已勾的行
      all.forEach(l => { const b = l.querySelector('[data-testid=beast-core-checkbox-checkIcon]') || l;
                         const r = b.getBoundingClientRect();
                         if (r.width > 0) b.dispatchEvent(new MouseEvent('click', {bubbles:true})); });
      return all.length;
    }""")
    time.sleep(2)
    print("先清空已勾：%s" % (T._read_state(pg).get("selected")))

    sel = T._select_rows(pg, [po], print)
    picked = T._read_state(pg).get("selected")
    print("只勾 1 单后 已选=%s miss=%s" % (picked, sel.get("miss")))
    if picked != "1":
        print("[WARN] 已选不是 1（%s），结论要看抽屉数字" % picked)

    b = json.loads(pg.evaluate(T.JS_FIND_BATCH_BTN) or "{}")
    print("顶部「%s」 disabled=%s" % (b.get("t"), b.get("disabled")))
    if not b.get("x"):
        return 42
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(6)

    d = json.loads(pg.evaluate(READ_DRAWER) or "{}")
    print("\n抽屉：%s" % json.dumps(d, ensure_ascii=False, indent=1))
    os.makedirs(PEEK, exist_ok=True)
    with open(os.path.join(PEEK, "probe_one_confirm.png"), "wb") as f:
        f.write(pg.screenshot())
    print("图：probe_one_confirm.png")

    # 关掉抽屉，别留半开状态
    pg.evaluate(r"""() => {
      const txt = e => (e.innerText||'').trim();
      const b = [...document.querySelectorAll('button')]
        .filter(x => x.getBoundingClientRect().width > 0).find(x => txt(x) === '取消');
      if (b) b.click();
      return 1;
    }""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
