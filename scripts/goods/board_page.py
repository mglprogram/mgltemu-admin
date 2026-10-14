# -*- coding: utf-8 -*-
"""board_page.py — 直接读**海外仓库存看板页面渲染后的表格**（而非它内嵌的快照 JSON）。

为什么（2026-10-12 用户：「数量都对应不上」）
--------------------------------------------
看板页是静态页 + 内嵌 `SNAP`，但它按自己的节奏重新生成；解析 HTML 里的 `SNAP` 得到的是
**下载到的那一版**，用户浏览器里看的是**当前那一版** ⇒ 在"换版窗口"里必然不同。
实测同一时刻两边逐格一致 ⇒ 解析口径没问题，差异只来自版本。
要让面板永远跟用户看到的一致，就得读**页面渲染值**。

实现要点（踩过的坑）
--------------------
· 看板页**分页 50 行/页**，DOM 里根本没有全部 3000 行 ⇒ 不能"一次性 dump 整表"。
· 它的搜索框（placeholder `搜索小款号 / 店铺 / 仓库（输入产品号会自动列出变体）…`）
  **支持一次查多个**（空格/逗号分隔）⇒ 按**款号**批量查（一个款号能带出它全部尺码）。
· 表格首个 `th` 是「小款号」，第 2 列是「覆盖货源·仓库」（页面把多个仓挤在一格，
  如 `kansas加州3`），第 5 列是「代发库存」。

对外：`fetch_warehouse_stock(products)` → `{小款号: {仓库关键字: 代发库存}}`
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

URL = "https://warehouse-probe.app.workbuddy.host/"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOT = Path(r"D:\AI-workplace\TEMU")

#: 把当前页的表格行 dump 出来（只 dump 主表：第一个表头为「小款号」的那张）
JS_ROWS = r"""
(function () {
  var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var tables = Array.prototype.slice.call(document.querySelectorAll('table'));
  var t = null;
  for (var i = 0; i < tables.length; i++) {
    var first = tables[i].querySelector('th');
    if (first && txt(first).indexOf('小款号') >= 0) { t = tables[i]; break; }
  }
  if (!t) return JSON.stringify({ok: false, why: 'no-main-table',
                                 tables: tables.length});
  var heads = Array.prototype.slice.call(t.querySelectorAll('th')).map(txt);
  var rows = [];
  Array.prototype.slice.call(t.querySelectorAll('tbody tr')).forEach(function (tr) {
    var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(txt);
    if (cells.length) rows.push(cells);
  });
  var body = (document.body.innerText || '').replace(/\s+/g, ' ');
  var i = body.indexOf('筛选结果');
  return JSON.stringify({ok: true, heads: heads, rows: rows,
                         summary: i >= 0 ? body.slice(i, i + 60) : null});
})()
"""

JS_SEARCH = r"""
(function (kw) {
  var ins = Array.prototype.slice.call(document.querySelectorAll('input[type=search]'));
  var box = ins.filter(function (i) { return i.getClientRects().length > 0; })[0];
  if (!box) return 'no-search-box';
  var set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  set.call(box, kw);
  box.dispatchEvent(new Event('input', {bubbles: true}));
  box.dispatchEvent(new Event('change', {bubbles: true}));
  return 'typed:' + box.value;
})
"""

JS_TOTAL_INPUT = r"""
(function () {
  var ins = Array.prototype.slice.call(document.querySelectorAll('input'));
  return JSON.stringify(ins.filter(function (i) { return i.getClientRects().length; })
    .map(function (i) { return {v: i.value, ph: (i.placeholder || '').slice(0, 24),
                                t: i.type, cls: String(i.className).slice(0, 30)}; })
    .slice(0, 14));
})()
"""


def _split_warehouses(cell: str) -> list:
    """页面把多个仓库挤在一格（`kansas加州3`）⇒ 按已知关键字拆开。"""
    known = ["加州3", "加州2", "加州1", "kansas", "新泽西2", "新泽西", "美西"]
    found, rest = [], cell or ""
    for k in known:
        if k in rest:
            found.append(k)
            rest = rest.replace(k, "", 1)
    return found


class BoardPage:
    """一个常驻的无头 Chrome 会话，反复搜索 + 读表。"""

    def __init__(self, port: int = 9362):
        self.port = port
        self.proc = None
        self.cap = None
        self.loop = None

    def __enter__(self):
        import asyncio
        sys.path.insert(0, str(ROOT / "scripts" / "goods"))
        import cdp_net
        self.cdp_net = cdp_net
        prof = ROOT / "out" / "_chrome_board_page"
        prof.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={self.port}",
             f"--user-data-dir={prof}", "--no-first-run", "--disable-gpu",
             "--window-size=1600,1000", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.5)
            try:
                cdp_net.http_json(f"http://127.0.0.1:{self.port}/json/version", timeout=2)
                break
            except Exception:  # noqa: BLE001
                continue
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self._open())
        return self

    async def _open(self):
        port = self.port
        self.tid = await self.cdp_net.new_tab(port, URL, foreground=True)
        tgt = None
        for _ in range(30):
            await asyncio.sleep(1)
            tgt = next((x for x in self.cdp_net.http_json(
                f"http://127.0.0.1:{port}/json/list")
                if (x.get("id") or x.get("targetId")) == self.tid), None)
            if tgt:
                break
        self.cap = self.cdp_net.NetCapture(tgt["webSocketDebuggerUrl"])
        await self.cap.__aenter__()
        await asyncio.sleep(8)
        for _ in range(20):
            d = await self._rows()
            if d.get("ok") and d.get("rows"):
                break
            await asyncio.sleep(3)

    async def _rows(self) -> dict:
        """读主表。⚠ 页面表是 JS 渲染的，刚打开时可能还没有表（`why=no-main-table`）
        ⇒ 这里最多等 ~18s 让「小款号」那张表出现再读。"""
        data = {}
        for _ in range(12):
            r = await self.cap.send("Runtime.evaluate",
                                    {"expression": JS_ROWS, "returnByValue": True},
                                    timeout=120)
            data = json.loads((r.get("result") or {}).get("value") or "{}")
            if data.get("ok") and data.get("rows") is not None:
                return data
            await asyncio.sleep(1.5)
        return data

    async def _search(self, kw: str) -> dict:
        """填搜索框 → 轮询等结果（看板页是 JS 渲染的，读太早会拿到 0 行）。

        ⚠ 实测偶发：刚填完就读表，DOM 还是旧的一页（0 行或全部行）。
          所以这里打满 5 次、每次间隔 1.5s，直到行数稳定非空才返回。
        """
        await self.cap.send("Runtime.evaluate",
                            {"expression": JS_SEARCH.strip() + "(" + json.dumps(kw) + ")",
                             "returnByValue": True, "userGesture": True}, timeout=60)
        data = {}
        for _ in range(6):
            await asyncio.sleep(1.5)
            data = await self._rows()
            if data.get("ok") and data.get("rows"):
                # 再等一拍确认不是渲染中的中间态
                await asyncio.sleep(1.0)
                again = await self._rows()
                if len(again.get("rows") or []) == len(data.get("rows") or []):
                    return again
                data = again
        return data

    def search(self, kw: str) -> dict:
        return self.loop.run_until_complete(self._search(kw))

    def close(self):
        try:
            if self.cap:
                self.loop.run_until_complete(self.cap.__aexit__(None, None, None))
        except Exception:  # noqa: BLE001
            pass
        if self.proc:
            self.proc.terminate()

    def __exit__(self, *exc):
        self.close()
        return False


def parse_rows(data: dict) -> dict:
    """表格行 → {小款号: {仓库关键字: 代发库存}}。

    第 1 列小款号；第 2 列「覆盖货源·仓库」（可能一格多仓）；第 5 列代发库存。
    一格多仓时无法精确归属 ⇒ 记到 `_合计`，由调用方决定是否用。
    """
    out: dict = {}
    for cells in data.get("rows") or []:
        if len(cells) < 5:
            continue
        key = cells[0]
        if not key or key.startswith("合计"):
            continue
        try:
            stock = float(cells[4])
        except ValueError:
            continue
        whs = _split_warehouses(cells[1])
        per = out.setdefault(key, {})
        if len(whs) == 1:
            per[whs[0]] = per.get(whs[0], 0.0) + stock
        else:
            per["_合计"] = per.get("_合计", 0.0) + stock
            per.setdefault("_多仓", []).append(cells[1])
    return out


def fetch_warehouse_stock(products: list, batch: int = 6, verbose: bool = True,
                          port: int = 9362) -> dict:
    """按**款号**批量查，返回 {小款号: {仓库: 代发库存}}。

    products: 款号列表（如 ['102204-6SetC', '102303-6SetA']），会自动带出全部尺码。
    """
    stock: dict = {}
    with BoardPage(port=port) as bp:
        for i in range(0, len(products), batch):
            chunk = products[i:i + batch]
            kw = " ".join(chunk)
            got, data = {}, {}
            # ⚠ 看板页偶发「第一次搜索读空」（JS 还在渲染）⇒ 同一批最多重试 3 次
            for attempt in range(3):
                try:
                    data = bp.search(kw)
                except Exception as e:  # noqa: BLE001
                    if verbose:
                        print("   [看板页] 批次失败 %s: %s" % (chunk[:2], str(e)[:100]))
                    continue
                got = parse_rows(data)
                if got:
                    break
                if attempt < 2:
                    time.sleep(2.5)
            if not got and verbose:
                print("   [看板页] 批次无结果 %s（重试过）: rows=%s why=%s"
                      % (chunk[:2], len(data.get("rows") or []), data.get("why")))
            stock.update(got)
            if verbose:
                print("   [看板页] %2d/%2d 批 %s → %d 键 %s"
                      % (i + len(chunk), len(products), chunk[:2], len(got),
                         data.get("summary")))
    return stock


if __name__ == "__main__":
    prods = sys.argv[1:] or ["102204-6SetC", "102303-6SetA"]
    m = fetch_warehouse_stock(prods, batch=2)
    for k in sorted(m):
        print("  %-22s %s" % (k, m[k]))
