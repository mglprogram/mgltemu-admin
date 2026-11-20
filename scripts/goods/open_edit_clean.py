# -*- coding: utf-8 -*-
"""用**干净新页**打开某货号的单款编辑页（stock-edit），并打印每行现值。

为什么单独一个脚本：`apply_hx_half` 复用旧页时，旧页常留着上一次的筛选（货号/SPU），
导致搜索 0 行 → 操作列没有「修改库存」→ 一串 no-link。这里每轮都开**干净页**（跑完可关）。

用法：
    python scripts/goods/open_edit_clean.py BHY BHY-102105-3BGR
    python scripts/goods/open_edit_clean.py BHY BHY-102105-3BGR --keep   # 不关页
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import apply_hx_half as A  # noqa: E402
import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

CLEAN_SEARCH = r"""
(function (code) {
  var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
  // 先把两个查询区的关键词清空，只保留「货号区」写要查的货号
  ins.forEach(function (i, n) {
    if (!i.readOnly && n <= 1) window.__zr.write(i, '');
  });
  var target = -1;
  for (var i = 0; i < ins.length - 1; i++) {
    if (/^SKU\(/.test((ins[i].value || '').trim()) && !ins[i + 1].readOnly) { target = i + 1; break; }
  }
  if (target < 0) return 'no-box';
  window.__zr.write(ins[target], code);
  var btn = window.__zr.btn('\u67e5\u8be2');
  if (!btn) return 'no-btn';
  window.__zr.call(btn, 'onClick');
  return 'searched:' + code;
})(%s)
"""


async def main(store: str, code: str, keep: bool) -> int:
    port = probe_port(A.WINDOWS[store])
    tid = await cdp_net.new_tab(port, A.LIST_URL, foreground=True)
    print("新开干净页", tid[:8])
    await asyncio.sleep(13)
    page = None
    for _ in range(20):
        for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
            if x.get("id") == tid and x.get("webSocketDebuggerUrl"):
                page = x
                break
        if page:
            break
        await asyncio.sleep(1.5)
    if not page:
        print("✗ 新页没枚举出来")
        return 1
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    await tab.open()
    await tab.ensure()
    await zreact.inject(tab.session)
    if not await B.page_ready(tab, 60):
        print("✗ 列表没加载出来")
        return 1
    await B.close_overlays(tab)
    r = await tab.session.eval(CLEAN_SEARCH % json.dumps(code), timeout=25)
    r = r[0] if isinstance(r, tuple) else r
    print("搜索:", r)
    if r in (None, "no-box", "no-btn"):
        # 会话刚建好时 eval 偶发返回 None ⇒ 用 B.ev（自带 replMode 重试）再来一次
        r = await B.ev(tab, CLEAN_SEARCH % json.dumps(code))
        print("重试搜索:", r)
    await asyncio.sleep(5)
    c = await tab.session.eval(A.FIND_LINK % json.dumps(code), timeout=25)
    c = c[0] if isinstance(c, tuple) else c
    print("点修改库存:", c)
    if c in (None, "no-link"):
        await asyncio.sleep(4)
        c = await tab.session.eval(A.FIND_LINK % json.dumps(code), timeout=25)
        c = c[0] if isinstance(c, tuple) else c
        print("重试点修改库存:", c)
    await asyncio.sleep(3)
    d = await tab.session.eval(A.DIALOG_LINK, timeout=25)
    d = d[0] if isinstance(d, tuple) else d
    print("点站点与发货仓:", d)
    ed = None
    for _ in range(15):
        await asyncio.sleep(1.5)
        cands = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                 if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")]
        if cands:
            ed = cands[-1]
            break
    if not ed:
        print("✗ 没打开 stock-edit 页")
        return 1
    await asyncio.sleep(3)
    rows = await A.read_rows(ed)
    print("编辑页 %s 行 %d" % ((ed.get("url") or "")[-40:], len(rows)))
    for rr in rows:
        print("  %-4s %-14s %-6s own=%s" % (rr["size"], rr["wh"], rr["val"], rr["sizeOwn"]))
    if not keep:
        try:
            await tab.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("store")
    ap.add_argument("code")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.store, a.code, a.keep)))
