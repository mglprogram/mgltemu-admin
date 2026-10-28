#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_nine_inv.py — 用看板**页面搜索框**核对 fetch_nine_inv.py 抓到的数字。

为什么要这一步
--------------
`fetch_nine_inv.py` 读的是页面内嵌的 `const INV` 快照；用户在看板上是**点搜索框**
（`#inv-q`，placeholder「搜索 SKU / 款号 / 套系 / 尺码」）筛选后看表的。
两条路径必须逐格一致，否则「面板说 A、看板说 B」—— 这种偏差最难查。

做法
----
headless Chrome → 打开 TTSA-9 看板 → 先点「全部 SKU」页签（默认是「补货清单」，
只有需补货的 170 行，滞销款不在里面）→ 往 `#inv-q` 输入款号（如 `204203`）
→ 读 `#inv-view` 里渲染出的表格 → 与 nine-inv.json 同款号数字逐格比对。

⚠ 两个踩过的坑
  · Chrome 必须带 `--no-proxy-server`：本机系统代理 127.0.0.1:10808 会让这页
    永远停在 `readyState=loading`（实测等 12 秒仍 0 张表）。
  · 默认页签是「补货清单」而不是「全部 SKU」；不切页签，滞销款一条都搜不到，
    会被误判成「看板里没有这个款」。

用法
----
    python scripts/goods/verify_nine_inv.py            # 核对 6 个款号
    python scripts/goods/verify_nine_inv.py 204203      # 只核对 1 个

退出码：0 全一致；1 有差异（逐格列出）；2 打不开页面/读不到表。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_JSON = ROOT / "temu-admin" / "public" / "data" / "nine-inv.json"
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

URL = "https://cbec756b1bad42778134c59af1ab67ce.app.workbuddy.host/"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
STYLES = ["204203", "204207", "204004", "103903", "103902", "103901"]

#: 切到「全部 SKU」页签（默认「补货清单」只含需补货的行）
JS_ALL_TAB = r"""
(function () {
  var b = document.querySelector('#inv-views button[data-v="all"]');
  if (!b) return 'no-all-tab';
  b.click();
  return 'clicked:' + b.textContent;
})()
"""

#: 搜索框打字（原生 setter + input 事件，页面自己监听的）。
#: 关键字先塞进 `window.__nineKw`，再跑这个 IIFE —— 不要写成 `(function(kw){...})`
#: 再用 Python 拼参数：`Runtime.evaluate` 会把裸函数表达式当**函数对象**返回
#: （实测 `{"type":"function","value":{}}`），永远拿不到值。
JS_TYPE = r"""
(function () {
  var kw = window.__nineKw || '';
  var b = document.getElementById('inv-q');
  if (!b) return 'no-search-box';
  var set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  set.call(b, kw);
  b.dispatchEvent(new Event('input', {bubbles: true}));
  return 'typed:' + b.value;
})()
"""

#: 读 `#inv-view` 里那张表（表头第一格是「SKU」）
JS_ROWS = r"""
(function () {
  var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
  var box = document.getElementById('inv-view');
  if (!box) return JSON.stringify({ok: false, why: 'no-inv-view'});
  var t = box.querySelector('table');
  if (!t) return JSON.stringify({ok: false, why: 'no-table',
                                 text: txt(box).slice(0, 160)});
  var heads = Array.prototype.slice.call(t.querySelectorAll('thead th')).map(txt);
  var rows = [];
  Array.prototype.slice.call(t.querySelectorAll('tbody tr')).forEach(function (tr) {
    var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(txt);
    if (cells.length) rows.push(cells);
  });
  return JSON.stringify({ok: true, heads: heads, rows: rows,
                         gen: (document.getElementById('inv-gen') || {}).textContent || '',
                         summary: txt(box).slice(0, 140)});
})()
"""


class Page:
    def __init__(self, port: int = 9371):
        self.port = port
        self.proc = None
        self.cap = None
        self.loop = None

    def __enter__(self):
        import cdp_net
        self.cdp = cdp_net
        prof = ROOT / "out" / "_chrome_nine_verify"
        prof.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={self.port}",
             f"--user-data-dir={prof}", "--no-first-run", "--disable-gpu",
             "--no-proxy-server", "--proxy-bypass-list=*",
             "--window-size=1600,1400", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.5)
            try:
                self.cdp.http_json(f"http://127.0.0.1:{self.port}/json/version", timeout=2)
                break
            except Exception:  # noqa: BLE001
                continue
        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(self._open())
        return self

    async def _open(self):
        self.tid = await self.cdp.new_tab(self.port, URL, foreground=True)
        tgt = None
        for _ in range(30):
            await asyncio.sleep(1)
            tgt = next((x for x in self.cdp.http_json(f"http://127.0.0.1:{self.port}/json/list")
                        if (x.get("id") or x.get("targetId")) == self.tid), None)
            if tgt:
                break
        if not tgt:
            raise RuntimeError("打不开看板页面（没拿到 target）")
        self.cap = self.cdp.NetCapture(tgt["webSocketDebuggerUrl"])
        await self.cap.__aenter__()
        for _ in range(20):
            await asyncio.sleep(1.5)
            d = await self._rows()
            if d.get("ok") and d.get("rows"):
                # 切「全部 SKU」，否则滞销款搜不到
                print("[verify] %s" % (await self._eval(JS_ALL_TAB)))
                await asyncio.sleep(2.5)
                return
        raise RuntimeError("看板主表一直没渲染出来（%s）" % (d or {}))

    async def _eval(self, js: str):
        r = await self.cap.send("Runtime.evaluate",
                                {"expression": js, "returnByValue": True,
                                 "userGesture": True}, timeout=120)
        return (r.get("result") or {}).get("value")

    async def _rows(self) -> dict:
        v = await self._eval(JS_ROWS)
        try:
            return json.loads(v or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def search(self, kw: str) -> dict:
        """同步入口（`_search` 是 async，这里在自建 loop 上跑完再返回）。"""
        return self.loop.run_until_complete(self._search(kw))

    async def _search(self, kw: str) -> dict:
        await self._eval("window.__nineKw = %s; 1" % json.dumps(kw))
        await self._eval(JS_TYPE)
        data = {}
        for _ in range(8):
            await asyncio.sleep(1.2)
            data = await self._rows()
            rows = data.get("rows") or []
            if rows and all(kw in (r[0] or "") for r in rows if r):
                return data
        return data

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


def to_num(v):
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    want = argv or STYLES
    if not PUBLIC_JSON.exists():
        print("先跑 fetch_nine_inv.py 生成 %s" % PUBLIC_JSON)
        return 2
    payload = json.loads(PUBLIC_JSON.read_text(encoding="utf-8"))
    mine = {}
    for st in payload["styles"]:
        for v in st["variants"]:
            for r in v["rows"]:
                mine[r["sku"]] = r

    diffs, checked, skipped = [], 0, []
    with Page() as pg:
        print("[verify] 本地数据 %s ｜ 看板生成 %s"
              % (payload["generatedAt"], payload["board"].get("gen")))
        for code in want:
            data = pg.search(code)
            rows = data.get("rows") or []
            heads = data.get("heads") or []
            if not rows:
                skipped.append(code)
                print("  %-8s 搜索后无行（%s）" % (code, data.get("summary", "")[:80]))
                continue

            def col(*names):
                for n in names:
                    for i, h in enumerate(heads):
                        if n in h:
                            return i
                return None

            c_sku = col("SKU")
            c_stock = col("代发库存")
            c_transit = col("代发途中")
            c_over = col("海外仓库存")
            c_days = col("可售天数")
            c_open = col("未交货订单")
            if c_sku is None:
                print("  %-8s 表头里找不到 SKU 列：%s" % (code, heads))
                continue
            hit = 0
            for r in rows:
                sku = r[c_sku]
                if not sku or sku not in mine:
                    continue
                hit += 1
                checked += 1
                m = mine[sku]
                for label, ci, exp, tol in [
                    ("代发库存", c_stock, m["stock"], 0.51),
                    ("代发途中", c_transit, m["transit"], 0.51),
                    ("海外仓库存", c_over, m["overseas"], 0.51),
                    ("可售天数", c_days, m["sellDays"], 0.51),
                    ("未交货订单", c_open, m["openOrders"], 0.51),
                ]:
                    if ci is None or ci >= len(r):
                        continue
                    got = to_num(r[ci])
                    if got is None or exp is None:
                        continue
                    if abs(got - float(exp)) > tol:
                        diffs.append("%s %s 页面=%s 本地=%s" % (sku, label, got, exp))
            expect = len([k for k in mine if "".join(c for c in k.split("-")[0] if c.isdigit()) == code])
            print("  %-8s 页面 %3d 行 / 匹配 %3d 行 / 本地 %3d 行"
                  % (code, len(rows), hit, expect))

    if diffs:
        print("\n[verify] ❌ 有 %d 处不一致：" % len(diffs))
        for d in diffs[:40]:
            print("   ", d)
        return 1
    if not checked:
        print("\n[verify] ⚠ 一行都没比对到（搜索没生效？）")
        return 2
    print("\n[verify] ✅ %d 个 SKU 逐格一致（代发库存/代发途中/海外仓库存/可售天数/未交货订单）"
          % checked)
    if skipped:
        print("[verify] 跳过了这几个款号：%s" % "、".join(skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
