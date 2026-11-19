# -*- coding: utf-8 -*-
"""验证「在售款明细」的货号搜索框：只匹配货号/SKU/SPU，不再匹配标题。

用法：python scripts/goods/check_code_search.py
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
URL = "https://mgltemu.us.ci/#/temu/on-sale"
PORT = 9336
PROF = Path(r"D:\AI-workplace\TEMU\out\_chrome_codesearch2")

JS_STATE = r"""
var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var rows = Array.prototype.slice.call(document.querySelectorAll('.el-table__row'))
  .filter(function (r) { return !r.closest('.goods-sku-box'); });
var codes = rows.map(function (r) {
  var td = r.querySelectorAll('td');
  // 列序：店铺 / 货号 / SPU ...
  return td.length > 1 ? txt(td[1]) : '';
}).filter(function (v) { return v; });
var total = (document.querySelector('.goods-card-head') || {}).innerText || '';
return JSON.stringify({ count: rows.length, codes: codes.slice(0, 8), head: total.slice(0, 60) });
"""

JS_TYPE = r"""
var inp = null;
var boxes = Array.prototype.slice.call(document.querySelectorAll('input'));
for (var i = 0; i < boxes.length; i++) {
  var ph = boxes[i].placeholder || '';
  if (ph.indexOf('货号') >= 0) { inp = boxes[i]; break; }
}
if (!inp) return 'no-input';
var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
setter.call(inp, %s);
inp.dispatchEvent(new Event('input', { bubbles: true }));
return 'typed';
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
            print("① 未搜索: 行数 =", st.get("count"), "|", st.get("head"))
            for q in ("YOGA", "yoga", "BEL-YOGA", "102104-5SetA", "yoga leggings"):
                await ev(cap, JS_TYPE % json.dumps(q))
                await asyncio.sleep(2)
                st2 = json.loads(await ev(cap, JS_STATE) or "{}")
                print("② 搜「%s」→ 行数 %-3s 货号 %s" % (q, st2.get("count"), st2.get("codes")))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
