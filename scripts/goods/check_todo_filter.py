# -*- coding: utf-8 -*-
"""验证「今日待办」的类别筛选是否真的生效（点筛选按钮 → 数表格行 + 看类别）。

用法：python scripts/goods/check_todo_filter.py
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
PORT = 9333
PROF = Path(r"D:\AI-workplace\TEMU\out\_chrome_filter")

JS_STATE = r"""
var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var grp = Array.prototype.slice
  .call(document.querySelectorAll('.goods-todo-filter .el-radio-button'))
  .map(function (b) { return txt(b); });
// Element Plus：表格行是 .el-table__row（表头在另一张 table 里）
// 只在「今日待办」卡片里数（页面上还有别的表格，别混进来）
var card = document.querySelector('#daily-todo');
var rows = card ? Array.prototype.slice.call(card.querySelectorAll('.el-table__row')) : [];
var kinds = rows
  .map(function (r) { var td = r.querySelector('td'); return td ? txt(td).slice(0, 6) : ''; })
  .filter(function (v, i, a) { return v && a.indexOf(v) === i; });
return JSON.stringify({
  filter: grp,
  rowCount: rows.length,
  first: rows.length ? txt(rows[0]).slice(0, 80) : '',
  kinds: kinds
});
"""

JS_CLICK = r"""
var want = %s;
var bs = Array.prototype.slice
  .call(document.querySelectorAll('.goods-todo-filter .el-radio-button'));
for (var i = 0; i < bs.length; i++) {
  if ((bs[i].innerText || '').indexOf(want) >= 0) {
    var inp = bs[i].querySelector('input');
    (inp || bs[i]).click();
    return 'clicked:' + want;
  }
}
return 'not-found:' + want;
"""


async def ev(cap, js: str):
    """在页面里跑一段含 return 的 JS，返回值。"""
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
            print("!! 拿不到页面 target")
            return 1
        tgt = next(t for t in cdp_net.http_json("http://127.0.0.1:%d/json/list" % PORT)
                   if t.get("id") == tid)
        async with cdp_net.NetCapture(tgt["webSocketDebuggerUrl"]) as cap:
            await asyncio.sleep(15)          # 等数据 + 渲染
            st = json.loads(await ev(cap, JS_STATE) or "{}")
            print("① 未筛选：筛选条 =", st.get("filter"))
            print("   行数 =", st.get("rowCount"), "| 类别 =", st.get("kinds"))

            for want in ("可开预售", "待到货确认", "需补货", "可加库存", "全部"):
                r = await ev(cap, JS_CLICK % json.dumps(want))
                await asyncio.sleep(2.5)
                st2 = json.loads(await ev(cap, JS_STATE) or "{}")
                print("② 点「%s」→ %-16s 行数 %-4s 类别 %s"
                      % (want, r, st2.get("rowCount"), st2.get("kinds")))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
