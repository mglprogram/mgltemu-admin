# -*- coding: utf-8 -*-
"""pw_hwc_track.py — 代发侧出库单：按参考单号批量「确认订单」→ 批量「获取跟踪号」→ 回读运单号。

用户口径（2026-11-06）：
    「下单代发 → 获取跟踪号」是两步。第一步在 hwc_shop 全部订单页（见 pw_hwc_flow.py），
    第二步在 deliver.php 出库单页：底部「请选择操作 + 提交」下拉里的
    `确认订单=order_queren`，确认后才会出运单号。

真机：
    · 搜索框 textarea[name=keywords]，多行单号；search_types 要先设 deliver_no
    · 底部 select[name=select_types] 含 order_queren
    · 确认后状态变「待打包」，运单号列（或 [详情] 里）才有值
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

PORT = int(os.environ.get("PW_PORT") or 9333)
OUT = os.path.join(HERE, "out", "2026-11-06")
PEEK = os.path.join(HERE, "out", "_peek")
WB_RE = re.compile(r"\b(1Z[0-9A-Z]{10,}|GFUS[0-9A-Z]{6,}|SPX[0-9A-Z]{6,}|SWX[0-9A-Z]{6,}"
                   r"|YWE[0-9A-Z]{6,}|[A-Z]{2}\d{8,}|[A-Z0-9]{14,})\b")


def page() -> CdpPage:
    t = [x for x in http_targets(PORT) if x.get("type") == "page"][0]
    return CdpPage(PORT, t)


def arm(pg: CdpPage) -> None:
    pg.evaluate("() => { window.alert=()=>{}; window.confirm=()=>true;"
                " try{ if(window.layer && layer.closeAll) layer.closeAll(); }catch(e){}"
                " document.querySelectorAll('.layui-layer').forEach(e=>e.remove());"
                " try { if (typeof is_dianji !== 'undefined') is_dianji = false; } catch(e) {}"
                " return 1; }")


JS_ROWS = r"""() => {
  const out = [];
  document.querySelectorAll('tr[class^=tr_html_], tr[id^=tr_html_]').forEach(t => {
    const id = (t.className.match(/tr_html_(\d+)/) || [])[1]
            || (t.id || '').replace('tr_html_', '');
    const tds = [...t.children].filter(e => e.tagName === 'TD');
    const txt = (e) => (e.innerText || '').replace(/\s+/g, ' ').trim();
    const cb = t.querySelector('input[name="checkboxes[]"]');
    out.push({id, cb: cb ? cb.value : '',
              tds: tds.map(txt),
              cbChecked: cb ? cb.checked : null});
  });
  return JSON.stringify(out);
}"""


def read_rows(pg: CdpPage) -> list:
    return json.loads(pg.evaluate(JS_ROWS) or "[]")


def row_view(r: dict) -> dict:
    """deliver 列表列序：0 序号 1 出库单号 2 参考单号 3 重量 4 Fnsku 5 sellersku
    6 数量 7 图片 8 运输方式 9 发货仓库 10 店铺 11 来源 12 状态 13 添加时间 14 操作"""
    t = r["tds"]
    g = lambda i: (t[i] if i < len(t) else "")
    return {"id": r["id"], "cb": r["cb"], "out_no": g(1), "po": g(2), "fn": g(4),
            "sku": g(5), "wh": g(9), "status": g(12), "addtime": g(13),
            "wb": ""}


def do_search(pg: CdpPage, pos: list, pb: str = "all", wait: float = 25.0) -> list:
    pg.goto("https://www.powerwarehouse.net/deliver.php?act=list&pb=%s&uselastfilter=1" % pb)
    time.sleep(6)
    arm(pg)
    pg.evaluate(r"""(nos) => {
      const ta = document.querySelector('textarea[name=keywords]');
      const st = document.querySelector('select[name=search_types]');
      if (st) st.value = 'deliver_no';
      const f = ta.closest('form') || document.forms[0];
      const set = (n, v) => { const e = f.elements[n]; if (e) e.value = v; };
      // ★ pb 一定要显式写 all：uselastfilter 会把上一次的 pb=2 记进表单，
      //   带着 pb=2 按参考单号搜「已生成但还没进待打包」的单会直接 0 条（实测踩过）。
      set('pb', 'all'); set('is_xuni', '0'); set('is_hui', '');
      set('shop_id', ''); set('ck_sku', ''); set('seller_sku', '');
      set('start_date', '2026-09-01'); set('end_date', '2026-12-31');
      set('is_cd', ''); set('ck', ''); set('chqd', '');
      set('keywords', '');
      ta.focus(); ta.select();
      return 1;
    }""", pos)
    cdp = pg.cdp_session()
    cdp.send("Input.insertText", {"text": "\n".join(pos)})
    time.sleep(0.7)
    got = pg.evaluate("() => document.querySelector('textarea[name=keywords]').value") or ""
    if len([x for x in got.split("\n") if x.strip()]) != len(pos):
        raise SystemExit("[FATAL] keywords 回读行数不符")
    b = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('button,input[type=button]')]
        .find(e => /^\s*查询\s*$/.test((e.innerText || e.value || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not b.get("x"):
        raise SystemExit("[FATAL] 找不到查询按钮")
    arm(pg)
    pg.dispatch_mouse(b["x"], b["y"])
    want = set(pos)
    deadline = time.time() + wait
    best = []
    while time.time() < deadline:
        time.sleep(1)
        arm(pg)
        rows = [row_view(r) for r in read_rows(pg)]
        pos_now = {x["po"] for x in rows}
        if rows and (pos_now <= want) and len(pos_now) >= len(want):
            return rows
        if rows:
            best = rows
    if best:
        print("[WARN] 只匹配到 %d / %d 单" % (len(best), len(want)))
        return best
    raise SystemExit("[FATAL] 查询没出结果")


def set_checks(pg: CdpPage, ids: list) -> int:
    pg.evaluate(r"""() => {
      const hdr = document.querySelector('th input[type=checkbox], input[name=checkboxes]');
      if (hdr && !hdr.checked) { hdr.click(); }
      return 1;
    }""")
    time.sleep(0.3)
    return int(pg.evaluate(r"""(ids) => {
      const want = new Set(ids.map(String));
      let n = 0;
      document.querySelectorAll('input[name="checkboxes[]"]').forEach(cb => {
        const hit = want.has(String(cb.value));
        cb.checked = hit;
        if (hit) { cb.setAttribute('checked', 'checked'); n++; }
        else { cb.removeAttribute('checked'); }
      });
      return n;
    }""", [str(i) for i in ids]) or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "queren", "track"])
    ap.add_argument("--list", nargs="*", default=[])
    ap.add_argument("--pb", default="all")
    ap.add_argument("--tag", default="deliver")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    os.makedirs(PEEK, exist_ok=True)

    pos = []
    for f in a.list:
        with open(f, encoding="utf-8") as fh:
            pos += [x.strip() for x in fh if x.strip()]
    print("清单 %d 单" % len(pos))

    pg = page()
    rows = do_search(pg, pos, a.pb)
    from collections import Counter
    print("出库单 %d 行；状态 %s" % (len(rows), dict(Counter(x["status"] for x in rows))))
    json.dump(rows, open(os.path.join(OUT, "deliver_%s.json" % a.tag), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)

    if a.cmd == "list":
        for x in rows[:12]:
            print("  %-28s %-10s %-16s %-14s" % (x["po"], x["status"], x["fn"], x["wh"]))
        open(os.path.join(PEEK, "deliver_%s.png" % a.tag), "wb").write(pg.screenshot())
        return 0

    # queren / track 都先要用「请选择操作 + 提交」
    n = set_checks(pg, [x["id"] for x in rows if x["id"]])
    print("勾选 %d / %d" % (n, len(rows)))
    sel = a.cmd == "queren" and "order_queren" or "order_piliang_qu_track"
    info = json.loads(pg.evaluate(r"""(want) => {
      const s = document.querySelector('[name=select_types]');
      if (!s) return JSON.stringify({err: 'no select'});
      const opts = [...s.options].map(o => ({v: o.value, t: o.text.trim()}));
      s.value = want;
      s.dispatchEvent(new Event('change', {bubbles: true}));
      return JSON.stringify({want, opts, now: s.value,
                             text: (s.options[s.selectedIndex] || {}).text});
    }""", sel) or "{}")
    print("下拉:", info)
    if info.get("now") != sel:
        raise SystemExit("[FATAL] 下拉里没有 %s，选项=%s"
                         % (sel, [o["v"] + "=" + o["t"] for o in info.get("opts") or []]))
    open(os.path.join(PEEK, "deliver_ready_%s.png" % a.cmd), "wb").write(pg.screenshot())
    if not a.commit:
        print("[dry-run] 未给 --commit")
        return 0

    b = json.loads(pg.evaluate(r"""() => {
      const el = [...document.querySelectorAll('input[type=submit],button,input[type=button]')]
        .find(e => /^\s*提交\s*$/.test((e.value || e.innerText || '').trim()));
      if (!el) return '{}';
      el.scrollIntoView({block: 'center'});
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    }""") or "{}")
    if not b.get("x"):
        raise SystemExit("[FATAL] 找不到提交按钮")
    arm(pg)
    pg.dispatch_mouse(b["x"], b["y"])
    time.sleep(15)
    arm(pg)
    print("提交后 url:", pg.url[:100])
    print("页面提示:", (pg.evaluate("() => (document.body.innerText||'').replace(/\\s+/g,' ')")
                        or "")[:260])
    open(os.path.join(PEEK, "deliver_after_%s.png" % a.cmd), "wb").write(pg.screenshot())

    rows2 = do_search(pg, pos, a.pb)
    print("回读出库单 %d 行；状态 %s"
          % (len(rows2), dict(Counter(x["status"] for x in rows2))))
    json.dump(rows2, open(os.path.join(OUT, "deliver_after_%s.json" % a.cmd), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
