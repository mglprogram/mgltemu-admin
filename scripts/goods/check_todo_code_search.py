# -*- coding: utf-8 -*-
"""验证「今日待办」的**货号查询框**是否生效（真打字 → 数表格行 + 看货号）。

用法：python scripts/goods/check_todo_code_search.py
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
PORT = 9334
PROF = Path(r"D:\AI-workplace\TEMU\out\_chrome_codesearch")

JS_STATE = r"""
var txt = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var card = document.querySelector('#daily-todo');
var rows = card ? Array.prototype.slice.call(card.querySelectorAll('.el-table__row')) : [];
var codes = rows.map(function (r) {
  var tds = r.querySelectorAll('td');
  return tds.length > 2 ? txt(tds[2]) : '';
}).filter(function (v) { return v; });
var uniq = codes.filter(function (v, i, a) { return a.indexOf(v) === i; });
var tip = card ? txt(card.querySelector('.goods-todo-filter__tip') || document.createElement('span')) : '';
var inp = card ? card.querySelector('.goods-todo-filter__code input') : null;
return JSON.stringify({
  rowCount: rows.length,
  codes: uniq.slice(0, 6),
  inputExists: !!inp,
  inputValue: inp ? inp.value : '',
  tip: tip.slice(0, 120)
});
"""

#: 往输入框里「真打字」：走原生 setter + input 事件，Vue 的 v-model 才收得到
JS_TYPE = r"""
var card = document.querySelector('#daily-todo');
var inp = card ? card.querySelector('.goods-todo-filter__code input') : null;
if (!inp) return 'no-input';
var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
setter.call(inp, %s);
inp.dispatchEvent(new Event('input', { bubbles: true }));
return 'typed:' + inp.value;
"""



JS_CLICK = r"""
var want = %s;
var bs = Array.prototype.slice.call(
  document.querySelectorAll('.goods-todo-filter .el-radio-button'));
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
            tid = next((t.get("id") for t in tl if t.get("type") == "page"  # noqa: tab-safety 只读探测脚本：只 attach 读状态，不点击不导航、不关任何页
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
            print("① 初始：行数=%s | 输入框存在=%s | 货号样例=%s"
                  % (st.get("rowCount"), st.get("inputExists"), st.get("codes")))
            base = st.get("rowCount")
            sample = (st.get("codes") or [""])[0]

            # 用真实货号搜（取表里第一条的货号）
            for q in (sample, sample.lower() if sample else "", sample.replace("-", "") if sample else "",
                      "ZZZ-NOPE"):
                r = await ev(cap, JS_TYPE % json.dumps(q))
                await asyncio.sleep(2)
                st2 = json.loads(await ev(cap, JS_STATE) or "{}")
                print("② 输入「%s」→ %-22s 行数=%-4s 货号=%s"
                      % (q, r, st2.get("rowCount"), st2.get("codes")))
                if st2.get("tip"):
                    print("     提示:", st2["tip"][:100])
            # 跨类别提示：切到「可开预售」，再搜一个「可加库存」的货号
            await ev(cap, JS_CLICK % json.dumps("可开预售"))
            await asyncio.sleep(2)
            await ev(cap, JS_TYPE % json.dumps(sample))
            await asyncio.sleep(2)
            st4 = json.loads(await ev(cap, JS_STATE) or "{}")
            print("④ 类别=可开预售 + 搜「%s」→ 行数=%s" % (sample, st4.get("rowCount")))
            print("     提示:", (st4.get("tip") or "")[:150])
            await ev(cap, JS_CLICK % json.dumps("全部"))
            await asyncio.sleep(2)

            # 清空
            await ev(cap, JS_TYPE % json.dumps(""))
            await asyncio.sleep(2)
            st3 = json.loads(await ev(cap, JS_STATE) or "{}")
            print("③ 清空后行数 =", st3.get("rowCount"), "（初始是", base, "）")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
