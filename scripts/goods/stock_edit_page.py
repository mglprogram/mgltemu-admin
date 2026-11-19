# -*- coding: utf-8 -*-
"""改库存的新通道：**按货号走 `goods/stock-edit` 单款编辑页**（用户 2026-11-19 给的路子）。

用户原话：
  · 「打开『修改库存』弹窗可以改成：点击站点与发货仓关系设置 → 直接新页面更容易改库存」
  · 「同一 SPU 多个 SKC ……可以改成用这个去搜索（货号 + `SKU(模糊查询)`），
     就不会出现多个 skc 的情况，只会有一个结果」

做法：
  1. 列表页把查询条件切到 `SKU(模糊查询)`，输入**完整货号**（`BHY-203804-4SetA`）→ 查询；
     实测这样列表只剩 1 行，不存在「同 SPU 多 SKC」的问题；
  2. 该行「修改库存」打开弹窗，点左下角 **`站点与发货仓关系设置`** → 弹出
     `agentseller.temu.com/goods/stock-edit?productId=<SPU>` 的**独立编辑页**；
  3. 编辑页里按「颜色/尺码/发货仓」逐行填「当前库存」，最后点「提交」。
     每行是**绝对库存**（不是增减），所以清零＝填 0，加库存＝填原值+N。

⚠ 本脚本默认只读（`--dry-run`）：打开编辑页、把每行的现有值读出来打印。
   真提交必须显式 `--apply`，而且**一次性提交整页**（提交后要重新读一遍核对）。

用法：
    python scripts/goods/stock_edit_page.py --store BHY --code BHY-203804-4SetA
    python scripts/goods/stock_edit_page.py --store BHY --code BHY-203804-4SetA --apply --set S=20,M=50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF", "KinglamanSport": "TM-KinglamanSport"}
URL = "https://agentseller.temu.com/goods/list"

#: 编辑页里每一行：颜色 / 尺码 / 发货仓 / 当前库存输入框
READ_PAGE = r"""
(function () {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var rows = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
  return JSON.stringify(rows.map(function (tr) {
    var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
    var ins = Array.prototype.slice.call(tr.querySelectorAll('input')).map(function (i) {
      return {v: i.value || '', ph: i.placeholder || ''};
    });
    return {cells: cells, inputs: ins};
  }));
})()
"""


async def ensure_list_page(port: int) -> dict:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if pages and pages[-1].get("webSocketDebuggerUrl"):
        return pages[-1]
    tid = await cdp_net.new_tab(port, URL, foreground=True)
    await asyncio.sleep(12)
    for _ in range(20):
        for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
            if x.get("id") == tid and x.get("webSocketDebuggerUrl"):
                return x
        await asyncio.sleep(1.5)
    raise RuntimeError("打不开 goods/list")


async def pick_sku_query(tab, mode: str = "模糊") -> bool:
    """把列表的查询条件切成 `SKU(模糊查询)` / `SKU(精准查询)`。

    用户 2026-11-19：用货号 + SKU(模糊查询) 搜，结果只有 1 行，天然避开「同 SPU 多 SKC」。
    """
    want = "SKU(模糊查询)" if mode == "模糊" else "SKU(精准查询)"
    # 条件下拉是只读输入框，值为「SKU(模糊查询)」之类；点它弹出 li 选项
    js = r"""
(function (want) {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
  var cur = ins.filter(function (i) { return /^(SPU|SKU|货号|商品ID)/.test(i.value || ''); })[0];
  if (!cur) return 'no-condition-input';
  if ((cur.value || '').trim() === want) return 'already';
  cur.click(); cur.focus();
  return 'opened:' + (cur.value || '');
})(%s)
"""
    r = await B.ev(tab, js % json.dumps(want))
    if r == "already":
        return True
    await asyncio.sleep(1.0)
    # 选项在 li 里（实测「SKU(模糊查询)」「SKC(精准查询)」等）
    pick = r"""
(function (want) {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var li = Array.prototype.slice.call(document.querySelectorAll('li'))
    .filter(function (e) { return t(e) === want && e.getClientRects().length; })[0];
  if (!li) return 'no-option';
  if (window.__zr) {
    var p = window.__zr.findProps(li);
    if (p && typeof p.onClick === 'function') { window.__zr.call(li, 'onClick'); return 'zr'; }
  }
  li.click();
  return 'dom';
})(%s)
"""
    return (await B.ev(tab, pick % json.dumps(want))) in ("zr", "dom")


async def search_code(tab, code: str) -> dict:
    """用「货号」查（SKU 模糊查询 + 完整货号），返回列表行数。"""
    await B.ev(tab, "return window.__zr.write(window.__zr.inputs()[1], %s);" % json.dumps(code))
    await asyncio.sleep(0.5)
    await B.ev(tab, "return window.__zr.call(window.__zr.btn('查询'), 'onClick');")
    st = {}
    for _ in range(8):
        await asyncio.sleep(2)
        st = await B.ev_json(tab, r"""
        var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
        var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
        var hit = trs.filter(function (x) { return t(x).indexOf(%s) >= 0; });
        return JSON.stringify({rows: trs.length, hit: hit.length});""" % json.dumps(code))
        if st.get("rows"):
            break
    return st


async def open_stock_edit_page(tab, code: str) -> str:
    """在该行的「修改库存」弹窗里点 **站点与发货仓关系设置**，返回新页面 URL。"""
    if not await B.dialog_open(tab):
        await B.open_stock_dialog(tab, code)
        await B.wait_dialog(tab, 14)
    r = await B.ev(tab, B.JS_DIALOG + r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var a = Array.prototype.slice.call(b[0].querySelectorAll('a,button,span,div'))
  .filter(function (e) { return t(e) === '\u7ad9\u70b9\u4e0e\u53d1\u8d27\u4ed3\u5173\u7cfb\u8bbe\u7f6e'; })[0];
if (!a) return 'no-link';
if (window.__zr) { var p = window.__zr.findProps(a);
  if (p && typeof p.onClick === 'function') { window.__zr.call(a, 'onClick'); return 'zr'; } }
a.click();
return 'dom';""")
    return r


async def read_edit_page(page: dict) -> list:
    async with cdp_net.NetCapture(page["webSocketDebuggerUrl"], match=()) as cap:
        await asyncio.sleep(2)
        v, _ = await cap.eval(READ_PAGE, timeout=25)
    try:
        return json.loads(v or "[]")
    except Exception:  # noqa: BLE001
        return []


async def run(store: str, code: str, apply: bool, sets: dict) -> int:
    port = probe_port(WINDOWS[store])
    if not port:
        print("!! 探不到窗口 %s" % store)
        return 3
    page = await ensure_list_page(port)
    tid = page["id"]
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.any_page_ok = True
    await tab.open()
    await tab.ensure()
    await zreact.inject(tab.session)
    await B.page_ready(tab, 60)
    await B.close_overlays(tab)
    print("① 查询条件 → SKU(模糊查询)：", await pick_sku_query(tab, "模糊"))
    st = await search_code(tab, code)
    print("② 按货号搜索 %s：%s" % (code, st))
    if st.get("rows") != 1:
        print("   ⚠ 行数不是 1（%s），仍继续尝试用这一行开弹窗" % st.get("rows"))
    before = {x.get("id") for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
              if x.get("type") == "page"}
    print("③ 打开弹窗：", await B.open_stock_dialog(tab, code))
    print("④ 点「站点与发货仓关系设置」：", await open_stock_edit_page(tab, code))
    edit = None
    for _ in range(15):
        await asyncio.sleep(1.5)
        cands = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                 if x.get("type") == "page" and "stock-edit" in (x.get("url") or "")]
        if cands:
            edit = cands[-1]
            break
    if not edit:
        # 也可能是前端路由（同页换了 URL）而不是新标签
        for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port):
            if x.get("type") == "page" and x.get("id") == tid:
                print("   当前列表页 URL：", (x.get("url") or "")[:120])
    if not edit:
        print("!! 没看到 stock-edit 编辑页（可能同一页路由跳转，或入口点不到）")
        return 4
    print("   编辑页：", (edit.get("url") or "")[:120])
    rows = await read_edit_page(edit)
    print("⑤ 编辑页行数 %d：" % len(rows))
    for r in rows:
        cells = r.get("cells") or []
        vals = [i.get("v") for i in (r.get("inputs") or [])]
        print("   %-28s 库存输入 %s" % (" | ".join(c for c in cells if c)[:60], vals))
    if apply:
        print("!! --apply 还没实装提交（先看清结构再动）：本次只读")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--set", default="", help="S=20,M=50 这样的绝对库存（仅 --apply 用）")
    a = ap.parse_args()
    sets = {}
    for part in [p for p in a.set.split(",") if p]:
        k, _, v = part.partition("=")
        sets[k.strip().upper()] = int(v)
    return asyncio.run(run(a.store, a.code, a.apply, sets))


if __name__ == "__main__":
    raise SystemExit(main())
