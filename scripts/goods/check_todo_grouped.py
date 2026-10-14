# -*- coding: utf-8 -*-
"""验证「今日待办」改成**款级一行 + 展开看尺码**后是否正确。

检查：
  ① 表格行数 = 款数（而不是尺码数）
  ② 每行有「N 个可加 / 共 M」与「建议加合计」
  ③ 点开第一行的展开箭头 → 出现该款尺码明细（尺码 + 建议加）

用法：python scripts/goods/check_todo_grouped.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp_net  # noqa: E402

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL = "https://mgltemu.us.ci/#/temu/on-sale?view=todo"
PORT = 9335
PROF = Path(r"D:\AI-workplace\TEMU\out\_chrome_grouped")

JS_STATE = r"""
var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var card = document.querySelector('#daily-todo');
if (!card) return JSON.stringify({error: 'no card'});
var outer = card.querySelector('.el-table');
var rows = outer ? Array.prototype.slice.call(outer.querySelectorAll(':scope > .el-table__inner-wrapper > .el-table__body-wrapper > .el-table__body > tbody > .el-table__row')) : [];
if (!rows.length) {
  // 兜底：退化成「排除展开行内部嵌套表」的计数
  rows = Array.prototype.slice.call(card.querySelectorAll('.el-table__row'))
    .filter(function (r) { return !r.closest('td .goods-sku-box'); });
}
var first = rows.length ? txt(rows[0]).slice(0, 150) : '';
var badges = Array.prototype.slice
  .call(card.querySelectorAll('.goods-todo-filter .el-radio-button'))
  .map(function (b) { return txt(b); });
var stats = Array.prototype.slice.call(card.querySelectorAll('.goods-low-stats__item'))
  .map(function (d) { return txt(d); });
var nested = card.querySelectorAll('.goods-sku-box').length;
var nestedRows = card.querySelectorAll('.goods-sku-box .el-table__row').length;
return JSON.stringify({
  rowCount: rows.length, first: first, badges: badges, stats: stats,
  nestedBoxes: nested, nestedRows: nestedRows
});
"""

JS_EXPAND = r"""
var card = document.querySelector('#daily-todo');
var rows = Array.prototype.slice.call(card.querySelectorAll('.el-table__row'))
  .filter(function (r) { return !r.closest('.goods-sku-box'); });
if (!rows.length) return 'no rows';
var icon = rows[0].querySelector('.el-table__expand-icon');
if (!icon) return 'no icon';
icon.click();
return 'clicked';
"""


async def ev(cap, js: str):
    expr = "(function(){ %s })()" % js
    r = await cap.send("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True, "awaitPromise": True},
                       timeout=60)
    return (r.get("result") or {}).get("value")


async def main() -> int:
    PROF.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
                             "--remote-debugging-port=%d" % PORT,
                             "--user-data-dir=" + str(PROF), URL],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tid = None
        for _ in range(40):
            await asyncio.sleep(1)
            try:
                tl = cdp_net.http_json("http://127.0.0.1:%d/json/list" % PORT)
            except Exception:  # noqa: BLE001
                continue
            tid = next((t.get("id") for t in tl if t.get("type") == "page"
                        and "on-sale" in (t.get("url") or "")), None)
            if tid:
                break
        if not tid:
            print("!! 拿不到 target")
            return 1
        tgt = next(t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % PORT)
                   if t.get("id") == tid)
        async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
            await asyncio.sleep(15)
            st = json.loads(await ev(cap, JS_STATE) or "{}")
            print("① 统计块:", st.get("stats"))
            print("   筛选条:", st.get("badges"))
            print("   表格行数(款) =", st.get("rowCount"))
            print("   第一行:", st.get("first"))
            r = await ev(cap, JS_EXPAND)
            await asyncio.sleep(2.5)
            st2 = json.loads(await ev(cap, JS_STATE) or "{}")
            print("② 展开第一行 →", r)
            print("   展开后 嵌套明细块 =", st2.get("nestedBoxes"),
                  "| 嵌套尺码行 =", st2.get("nestedRows"))
            # 再点一次收起
            await ev(cap, JS_EXPAND)
            await asyncio.sleep(2)
            st3 = json.loads(await ev(cap, JS_STATE) or "{}")
            print("③ 再点收起 → 嵌套尺码行 =", st3.get("nestedRows"),
                  "| 外层行数仍 =", st3.get("rowCount"))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
