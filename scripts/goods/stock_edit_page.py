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

    ⚠ 2026-11-19 真机实测（BEL）：
      · 页面上**同时有两个查询区**（「商品ID查询」和「货号」），`inputs[0]` 是商品ID区的条件（值 "SKC"），
        `inputs[5]` 才是货号区的条件（值已是「SKU(模糊查询)」）；
      · 点只读的条件框**不会弹出选项**（下拉是点右边的箭头展开的），所以「点开再选 li」这条路走不通；
      · 判成功的口径改成：**货号区的输入框（inputs[5] 后面那个）里写入完整货号 → 查询 → 列表行数 = 1**。
    """
    want = "SKU(模糊查询)" if mode == "模糊" else "SKU(精准查询)"
    js = r"""
(function (want) {
  var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
  var idx = -1;
  for (var i = 0; i < ins.length - 1; i++) {
    var v = (ins[i].value || '').trim();
    if (/^SKU\(/.test(v)) { idx = i; break; }
  }
  if (idx < 0) {
    for (var j = 0; j < ins.length - 1; j++) {
      if (/^SKC\(/.test((ins[j].value || '').trim())) { idx = j; break; }
    }
  }
  if (idx < 0) return JSON.stringify({ok: false, why: 'no-sku-condition'});
  return JSON.stringify({ok: (ins[idx].value || '').trim() === want, cur: (ins[idx].value || ''),
                         valueIdx: idx, inputIdx: idx + 1});
})(%s)
"""
    r = await B.ev(tab, js % json.dumps(want))
    try:
        info = json.loads(r or "{}")
    except Exception:  # noqa: BLE001
        info = {}
    # 查询条件切换：真实站点上「SKU(模糊查询)」往往已经是默认值（readonly 输入框里就是它），
    # 点它不会弹出下拉 ⇒ 这里只判断「当前条件是不是已经是目标」，是就直接往下走。
    cond = await B.ev(tab, r"""
(function (want) {
  var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
  var hit = ins.filter(function (i) { return (i.value || '').trim() === want; })[0];
  return hit ? 'ok' : 'not-now';
})(%s)
""" % json.dumps("SKU(模糊查询)" if mode == "模糊" else "SKU(精准查询)"))
    print("   查询条件：", cond)
    return cond == "ok"


async def search_code(tab, code: str, cap=None) -> dict:
    """用「货号」查：①货号区（SKU 模糊查询）②商品ID区（SKC 模糊）两处都写，再点《查询》。

    ⚠ 2026-11-19 真机：两区**各自**带上一次的关键词，只写一个会被另一个残留条件夹住
      （实测只写货号区 → 5 行；两个都写同一个货号 → 1 行）。
    `cap` 传页面会话时走 `cap.eval`（`B.ev` 在同一目标上经常返回 None）。
    """
    js = r"""
(function (code) {
  var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
  var wrote = [];
  for (var i = 0; i + 1 < ins.length; i++) {
    var v = (ins[i].value || '').trim();
    if (/^SKU\(/.test(v)) {
      var box = ins[i + 1];
      if (!box) continue;
      if (window.__zr) window.__zr.write(box, code);
      else { box.value = code; box.dispatchEvent(new Event('input', {bubbles: true})); }
      wrote.push(v);
    }
  }
  return JSON.stringify(wrote);
})(%s)
"""
    body = js % json.dumps(code)
    if cap is not None:
        v, _ = await cap.eval(body, timeout=25)
        print("   写入的查询区：", v)
        _, _ = await cap.eval(
            "(function(){var b=window.__zr&&window.__zr.btn('\u67e5\u8be2');"
            "if(!b) return 'no-btn'; window.__zr.call(b,'onClick'); return 'clicked';})()", timeout=25)
        return {}
    print("   写入：", await B.ev(tab, body))
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
        if st.get("rows") == 1:
            break
    return st


async def count_rows(cap, code: str, tries: int = 8) -> dict:
    """数当前列表行数（用 cap.eval，避开 B.ev 在同目标上返回 None 的问题）。"""
    js = r"""
(function (code) {
  var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
  var hit = trs.filter(function (x) { return t(x).indexOf(code) >= 0; });
  return JSON.stringify({rows: trs.length, hit: hit.length});
})(%s)
""" % json.dumps(code)
    st = {}
    for _ in range(tries):
        await asyncio.sleep(2)
        v, _ = await cap.eval(js, timeout=25)
        try:
            st = json.loads(v or "{}")
        except Exception:  # noqa: BLE001
            st = {}
        if st.get("rows") == 1:
            break
    return st


async def open_stock_edit_page(tab, code: str) -> str:
    """在该行的「修改库存」弹窗里点 **站点与发货仓关系设置**，返回新页面 URL。"""
    if not await B.dialog_open(tab):
        await B.open_stock_dialog(tab, code)
        await B.wait_dialog(tab, 14)
    r = await B.ev(tab, B.JS_DIALOG + r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var all = Array.prototype.slice.call(b[0].querySelectorAll('a'));
var a = all.filter(function (e) { return t(e) === '\u7ad9\u70b9\u4e0e\u53d1\u8d27\u4ed3\u5173\u7cfb\u8bbe\u7f6e'; })[0]
     || all.filter(function (e) { return /BTN_textPrimary/.test(String(e.className || '')) && /站点与发货仓/.test(t(e)); })[0];
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
    # ⚠ 2026-11-19 真机：`B.ev()` 在「刚连上的目标」上经常返回 None（要复用同一个 capture 会话才行），
    #   所以这里**复用 tab 自己那条会话**做全部页面操作。
    cap = tab.session
    print("① 按货号搜索（货号区 + 商品ID 区都写 %s）：" % code)
    await search_code(tab, code, cap)
    st = await count_rows(cap, code)
    print("② 搜索后行数：", st)
    # ★ 2026-11-19 真机（BEL）：货号搜索命中 1 个货号，但列表里那个货号仍有 **5 行**（每个尺码一行）。
    #   多 SKC 的歧义已经消掉（只剩一个货号），但「按行点修改库存」仍要挑对行 ⇒ 用 `open_dialog_by_code`。
    if st.get("hit") != 1:
        print("   ⚠ 命中的货号数不是 1（%s），仍继续尝试" % st.get("hit"))
    before = {x.get("id") for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
              if x.get("type") == "page"}
    print("③ 打开弹窗：", await B.open_stock_dialog(tab, code))
    if not await B.dialog_open(tab):
        # 按货号搜出来的行有时没有「修改库存」链接（列表行是商品级）⇒ 退回按 SPU 打开
        spu = ""
        try:
            import fetch_presale15 as _F  # noqa: PLC0415
            spu = _F._goods_spu().get((store, code)) or ""
        except Exception:  # noqa: BLE001
            spu = ""
        if spu:
            print("   按 SPU 重试：", spu, await B.open_stock_dialog(tab, spu))
            await B.wait_dialog(tab, 14)
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
    await asyncio.sleep(4)
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
