# -*- coding: utf-8 -*-
"""temu_batch_confirm.py — TEMU 待发货：**按单号精确勾选可确认单** → 批量确认发货。

用户口径（2026-11-06）：
  「回到订单列表 → 待发货 → 勾选 → 批量确认发货」

真机坑（必须遵守）：
  · 「批量确认发货 N」的 N 是**全库可确认数**；勾选没有运单号的单会让它变成 0 并置灰。
  · 所以**不能全选**，只能勾「有运单号 + 行内有『去手动确认』」的行。
  · 全选框在表头第一列，是 beast-core `label[data-testid=beast-core-checkbox]`
    （checkbox 本体 0×0，必须点 label；点前要 scrollIntoView）。
  · 判断某行是否被勾：用 `data-checked` 属性（不是 `input.checked`）。

流程：读 confirmable json → 按单号逐行勾选 → 回读已选数 == 目标数 → 点「批量确认发货」
      → 处理确认弹窗 → 回读待发货数变化。
"""
import argparse
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

JS_STATE = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect();
    return e.getClientRects().length>0 && r.width>0 && r.height>0
        && getComputedStyle(e).visibility!=='hidden'; };
  const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
  const body = document.body.innerText||'';
  const m = body.match(/已选订单[：:]\s*(\d+)/);
  const tabs = [...document.querySelectorAll('[role=tab],[class*=tab]')].filter(vis).map(txt)
    .filter(t => /^待发货\(\d+\)$/.test(t));
  const btns = {};
  [...document.querySelectorAll('button')].filter(vis).forEach(b => {
    const t = txt(b);
    if (/批量确认发货|合并下单/.test(t)) {
      btns[t] = {disabled: b.disabled===true
                 || (b.className||'').toString().indexOf('disabled')>=0};
    }
  });
  return JSON.stringify({pending: tabs[0]||'', selected: m?m[1]:'', btns});
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()

    p = os.path.join(OUT, "temu_confirmable_%s.json" % a.store)
    if not os.path.exists(p):
        print("[FAIL] 先跑 temu_scan_confirmable.py %s" % a.store)
        return 42
    cfg = json.load(open(p, encoding="utf-8"))
    target = cfg.get("manual") or []
    print("目标可确认单 %d 个" % len(target))
    if not target:
        print("没有可确认的单")
        return 0

    port = zs.probe_port_by_window("TM-BEL-MGL" if a.store == "BEL" else "TM-BHY-YJF")
    tgs = [t for t in zs.list_page_targets(port) if "/mmsos/orders" in (t.get("url") or "")
           and "parentOrderSnList=P" not in (t.get("url") or "")]
    pg = CdpPage(port, tgs[0])
    print("① 初始:", pg.evaluate(JS_STATE))

    # ② 逐行勾选（点该行的勾选 label）
    miss = []
    for i, po in enumerate(target, 1):
        d = json.loads(pg.evaluate(r"""(ord) => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          const tr = [...document.querySelectorAll('tr')].filter(vis)
            .find(t => txt(t).indexOf(ord) >= 0);
          if (!tr) return '{}';
          tr.scrollIntoView({block: 'center'});
          const lab = tr.querySelector('label[data-testid=beast-core-checkbox]')
                    || tr.querySelector('label');
          if (!lab) return 'no-label';
          const box = lab.querySelector('[data-testid=beast-core-checkbox-checkIcon]') || lab;
          const r = box.getBoundingClientRect();
          return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                                 inView: r.top>=0 && r.bottom<=window.innerHeight,
                                 checked: lab.getAttribute('data-checked')});
        }""", po) or "{}")
        if not d.get("x") or not d.get("inView"):
            miss.append(po)
            continue
        # ★ 2026-11-06 修：**已勾的不要再点**（会切换回未勾）。
        #   上一轮 dry-run 已经把 32 单勾上了，这轮若再点一遍就变成 8→24→16→8→0 来回抖。
        if d.get("checked") == "true":
            continue
        pg.dispatch_mouse(d["x"], d["y"])
        time.sleep(0.35)
        if i % 8 == 0:
            print("   已勾 %d/%d …（%s）" % (i, len(target), pg.evaluate(JS_STATE)))

    st = json.loads(pg.evaluate(JS_STATE) or "{}")
    print("② 勾选后:", json.dumps(st, ensure_ascii=False))
    if miss:
        print("   ⚠ 这些行没勾上（可能滚出列表）：%s" % ", ".join(miss[:5]))
    sel = int(st.get("selected") or 0)
    if sel != len(target):
        print("   ⚠ 已选 %d ≠ 目标 %d（有行没勾上）" % (sel, len(target)))

    raw = pg.screenshot()
    with open(PEEK + "\\temu_conf_ready_%s.png" % a.store, "wb") as f:
        f.write(raw)
    print("   落图 temu_conf_ready_%s.png" % a.store)

    if not a.commit:
        print("\n[dry-run] 未提交")
        return 0

    # ③ 点「批量确认发货 N」
    b = json.loads(pg.evaluate(r"""() => {
      const vis = e => { const r = e.getBoundingClientRect();
        return e.getClientRects().length>0 && r.width>0 && r.height>0; };
      const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
      const el = [...document.querySelectorAll('button')].filter(vis)
        .find(x => /批量确认发货/.test(txt(x)));
      if (!el) return '{}';
      // ★ 2026-11-06 修：「批量确认发货」在页面**顶部工具条**，列表长时会滚到视口外
      //   （实测 y=-19787，CDP 点视口外坐标 = 静默无效）。必须先滚到中间再取坐标。
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({t: txt(el), disabled: el.disabled===true,
                             x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2),
                             inView: r.top>=0 && r.bottom<=window.innerHeight});
    }""") or "{}")
    print("③ 按钮:", b)
    if not b.get("x") or b.get("disabled") or not b.get("inView"):
        print("[FAIL] 按钮不可点（disabled 或不在视口内）")
        return 42
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(4)

    # ④ 弹窗
    for t in ("确认", "确认发货", "确定", "全部确认"):
        cd = json.loads(pg.evaluate(r"""(tt) => {
          const vis = e => { const r = e.getBoundingClientRect();
            return e.getClientRects().length>0 && r.width>0 && r.height>0; };
          const txt = e => (e.innerText||'').replace(/\s+/g,' ').trim();
          const el = [...document.querySelectorAll('button')].filter(vis).find(x => txt(x) === tt);
          if (!el) return '{}';
          const r = el.getBoundingClientRect();
          return JSON.stringify({t: txt(el), x: Math.round(r.x+r.width/2),
                                 y: Math.round(r.y+r.height/2)});
        }""", t) or "{}")
        if cd.get("x"):
            print("④ 弹窗「%s」" % t)
            pg.dispatch_mouse(cd["x"], cd["y"])
            time.sleep(8)
            break
    else:
        print("④ 无弹窗（可能直接执行）")

    print("\n⑤ 结果:", pg.evaluate(JS_STATE))
    raw = pg.screenshot()
    with open(PEEK + "\\temu_conf_done_%s.png" % a.store, "wb") as f:
        f.write(raw)
    print("   落图 temu_conf_done_%s.png" % a.store)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
