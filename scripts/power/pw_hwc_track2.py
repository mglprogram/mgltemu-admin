# -*- coding: utf-8 -*-
"""pw_hwc_track2.py — 代发侧出库单：待确认 → 确认订单 → 待打包 → 批量获取单号 → 回读运单号。

用户口径（2026-11-06，重要）：
    **16:30–19:00 是禁下单时段，页面上不会出现「确认订单」等按钮** ——
    这段时间调用 order_queren 会被服务端以「请先获取参考运费」之类的理由拒掉。
    所以本脚本先检查 `select_types` 里到底有没有 `order_queren`；没有就**直接退出**，
    不要重试、不要绕过（那是时段闸门，不是权限问题）。

真机：
    pb=1 待确认订单页底部 `select[name=select_types]` → `order_queren=确认订单` + 「提交」
    pb=2 待打包订单页底部 `批量获取单号 = edit_order('huqumd')`（通过 `get_ups_dh()`）
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
from cdp_transport import CdpPage, http_targets  # noqa: E402

import pw_hwc_track as T  # noqa: E402

PORT = 9333
OUT = os.path.join(HERE, "out", "2026-11-06")
PEEK = os.path.join(HERE, "out", "_peek")
WB_RE = T.WB_RE


def open_list(pg: CdpPage, pb: str) -> None:
    pg.goto("https://www.powerwarehouse.net/deliver.php?act=list&pb=%s" % pb)
    T.arm(pg)
    # 批量操作栏是服务端随页面渲染的，刚导航完可能还没到位 → 轮询等它出现
    for _ in range(30):
        time.sleep(1)
        T.arm(pg)
        if pg.evaluate("() => !!document.querySelector('[name=select_types]')"):
            return
    print("[WARN] 等了 30s 仍没有批量操作栏")


def search_here(pg: CdpPage, pos: list, pb: str, wait: float = 30.0) -> list:
    """在**当前 pb 页**上按参考单号搜索。

    ⚠ 不能复用 `pw_hwc_track.do_search`：它会把 pb 强制写成 all，
    页面一重载 `select_types` 批量操作栏就没了（实测踩过）。
    """
    pg.evaluate(r"""(nos) => {
      const ta = document.querySelector('textarea[name=keywords]');
      const st = document.querySelector('select[name=search_types]');
      if (st) st.value = 'deliver_no';
      const f = ta.closest('form') || document.forms[0];
      const set = (n, v) => { const e = f.elements[n]; if (e) e.value = v; };
      set('pb', nos.pb);
      set('is_xuni', '0'); set('is_hui', '');
      set('shop_id', ''); set('ck_sku', ''); set('seller_sku', '');
      set('start_date', '2026-09-01'); set('end_date', '2026-12-31');
      set('is_cd', ''); set('ck', ''); set('chqd', '');
      ta.focus(); ta.select();
      return 1;
    }""", {"pb": pb, "nos": "\n".join(pos)})
    cdp = pg.cdp_session()
    cdp.send("Input.insertText", {"text": "\n".join(pos)})
    time.sleep(0.7)
    pg.evaluate("() => { searchOrder(1); return 1; }")
    want = set(pos)
    deadline = time.time() + wait
    best = []
    while time.time() < deadline:
        time.sleep(1)
        T.arm(pg)
        rows = [T.row_view(r) for r in T.read_rows(pg)]
        pos_now = {x["po"] for x in rows}
        if rows and (pos_now <= want) and len(pos_now) >= len(want):
            return rows
        if rows:
            best = rows
    if best:
        print("[WARN] 只匹配到 %d / %d 单" % (len(best), len(want)))
        return best
    raise SystemExit("[FATAL] 搜索没出结果")


def select_opts(pg: CdpPage) -> dict:
    return json.loads(pg.evaluate(r"""() => {
      const s = document.querySelector('[name=select_types]');
      if (!s) return 'null';
      return JSON.stringify(Object.fromEntries([...s.options].map(o => [o.value, o.text.trim()])));
    }""") or "null")


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

    # ---------- ① 待确认 → 确认订单 ----------
    open_list(pg, "1")
    opts = select_opts(pg)
    print("pb=1 下拉:", opts)
    if not opts or "order_queren" not in opts:
        print("[STOP] 当前没有「确认订单」选项。16:30–19:00 是禁下单时段，"
              "页面不放这个按钮 —— 换个时段再来，别绕过。")
        return 66

    rows = search_here(pg, pos, "1", wait=40)
    todo = [x for x in rows if "待确认" in (x["status"] or "")]
    print("待确认 %d / 查到 %d" % (len(todo), len(rows)))
    if not todo:
        print("[OK] 没有待确认的单了")
    else:
        n = T.set_checks(pg, [x["id"] for x in todo])
        print("勾选 %d" % n)
        r = json.loads(pg.evaluate(r"""() => {
          const s = document.querySelector('[name=select_types]');
          s.value = 'order_queren';
          s.dispatchEvent(new Event('change', {bubbles: true}));
          return JSON.stringify({now: s.value, text: (s.options[s.selectedIndex]||{}).text});
        }""") or "{}")
        print("下拉设为:", r)
        b = json.loads(pg.evaluate(r"""() => {
          const el = [...document.querySelectorAll('input[type=submit],button,input[type=button]')]
            .find(e => /^\s*提交\s*$/.test((e.value || e.innerText || '').trim()));
          if (!el) return '{}';
          el.scrollIntoView({block: 'center'});
          const rc = el.getBoundingClientRect();
          return JSON.stringify({x: Math.round(rc.x + rc.width/2),
                                 y: Math.round(rc.y + rc.height/2)});
        }""") or "{}")
        print("提交按钮:", b)
        open(os.path.join(PEEK, "track_confirm_ready.png"), "wb").write(pg.screenshot())
        if not a.commit:
            print("[dry-run] 未提交")
            return 0
        if not b.get("x"):
            raise SystemExit("[FATAL] 找不到提交按钮")
        T.arm(pg)
        pg.dispatch_mouse(b["x"], b["y"])
        time.sleep(18)
        T.arm(pg)
        print("确认后提示:", (pg.evaluate(
            "() => (document.body.innerText||'').replace(/\\s+/g,' ')") or "")[:220])
        open(os.path.join(PEEK, "track_confirm_done.png"), "wb").write(pg.screenshot())

    # ---------- ② 待打包 → 批量获取单号 ----------
    open_list(pg, "2")
    opts2 = select_opts(pg)
    print("pb=2 下拉:", opts2)
    rows2 = search_here(pg, pos, "all", wait=40)
    from collections import Counter
    print("出库单状态:", dict(Counter(x["status"] for x in rows2)))
    todo2 = [x for x in rows2 if "待打包" in (x["status"] or "")]
    print("待打包 %d" % len(todo2))
    if not todo2:
        print("[WARN] 没有待打包的单，跳过取号")
        json.dump(rows2, open(os.path.join(OUT, "track_after_confirm.json"), "w",
                              encoding="utf-8"), ensure_ascii=False, indent=1)
        return 0

    n2 = T.set_checks(pg, [x["id"] for x in todo2])
    print("勾选 %d" % n2)
    bt = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('input[type=button],button,a')]
        .find(e => /批量获取单号/.test((e.value || e.innerText || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const rc = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(rc.x + rc.width/2),
                             y: Math.round(rc.y + rc.height/2),
                             text: (el.value || el.innerText || '').trim(),
                             oc: el.getAttribute('onclick') || ''});
    }""") or "{}")
    print("批量获取单号按钮:", bt)
    open(os.path.join(PEEK, "track_ready.png"), "wb").write(pg.screenshot())
    if not a.commit:
        print("[dry-run] 未取号")
        return 0
    if not bt.get("x"):
        print("[WARN] 找不到「批量获取单号」（可能还在禁下单时段）")
        return 66
    T.arm(pg)
    pg.dispatch_mouse(bt["x"], bt["y"])
    # 等 waybill_ing 归 0
    t0 = time.time()
    while time.time() - t0 < 1800:
        time.sleep(4)
        T.arm(pg)
        st = json.loads(pg.evaluate(r"""() => {
          const g = n => { try { return eval(n); } catch(e) { return null; } };
          const ing = [...document.querySelectorAll('[class^=waybill_]')]
            .filter(e => /获取中/.test(e.innerText || '')).length;
          return JSON.stringify({ing: g('waybill_ing'), sm: g('waybill_sm'),
                                 cnt: g('waybill_count'), busy: ing});
        }""") or "{}")
        if st.get("ing") in (0, None) and st.get("busy") == 0:
            print("取号完成:", st)
            break
    else:
        print("[WARN] 取号超时")

    rows3 = search_here(pg, pos, "all", wait=40)
    print("取号后状态:", dict(Counter(x["status"] for x in rows3)))
    json.dump(rows3, open(os.path.join(OUT, "track_final.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    open(os.path.join(PEEK, "track_done.png"), "wb").write(pg.screenshot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
