# -*- coding: utf-8 -*-
"""只读排查：点「确认」后，弹窗里有没有红字/错误态/toast，或表单校验没通过。

    python scripts/goods/probe_submit_errors.py --spu 1208899974

流程：打开弹窗 → 只清 L 行（减少=当前）→ 点底部确认 → 每 0.5s 采样：
   · 红色元素（color 含 245/255 系红、class 含 error/err/red/danger）
   · toast / message / notification 容器
   · form 里每个 input 的 required/aria-invalid/校验 class
不点风险框，避免继续卡在那里。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import zreact  # noqa: E402
import bel_stock_set as B  # noqa: E402
import topup_by_sell_days as T  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

JS_SCAN = r"""
var t = function (e) { return (e.innerText || '').replace(/\s+/g, ' ').trim(); };
var out = {red: [], toast: [], form: []};
Array.prototype.slice.call(document.querySelectorAll('*')).forEach(function (e) {
  var q = e.getBoundingClientRect();
  if (!q.width || q.width > 900 || q.y < 60) return;
  var s = t(e);
  if (!s || s.length > 80 || e.children.length) return;
  var st = null; try { st = getComputedStyle(e); } catch (x) { return; }
  var cls = String(e.className || '');
  var red = /rgb\(\s*(2[0-5]\d|19[0-9])\s*,\s*([0-9]|[1-9]\d)\s*,\s*([0-9]|[1-9]\d)\s*\)/.test(st.color) ||
            /error|err|danger|red|warn/i.test(cls);
  if (red) out.red.push(s);
});
Array.prototype.slice.call(document.querySelectorAll('[class*=toast],[class*=message],[class*=Message],[class*=notification],[class*=Notice]')).forEach(function (e) {
  var q = e.getBoundingClientRect();
  if (!q.width) return;
  var s = t(e);
  if (s) out.toast.push(s.slice(0, 120));
});
Array.prototype.slice.call(document.querySelectorAll('form input')).forEach(function (i) {
  var q = i.getBoundingClientRect();
  out.form.push({ph: i.placeholder || '', val: (i.value || '').slice(0, 20),
                 ro: !!i.readOnly, dis: !!i.disabled, invisible: !q.width,
                 cls: String(i.className || '').slice(0, 40)});
});
return JSON.stringify(out);"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spu", required=True)
    ap.add_argument("--code", default="BHY-203805-3BHL")
    ap.add_argument("--size", default="L")
    ap.add_argument("--clear", action="store_true", help="连清零也做（默认不填，只看点确认的反应）")
    a = ap.parse_args()

    port = probe_port("TM-BHY-YJF")
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    tid = pages[-1].get("id")
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.store = "BHY"
    assert await tab.open(), "工作页打不开"
    await tab.ensure()
    await zreact.inject(tab.session)
    if not await B.dialog_open(tab):
        print("打开弹窗:", await B.open_stock_dialog(tab, a.spu))
        await asyncio.sleep(3)
    await T.scroll_right(tab)
    await asyncio.sleep(0.8)

    row = await T.row_of_code(tab, a.code, a.size)
    print("行:", row)
    if a.clear and row.get("ok"):
        print("清零:", await B.set_row(tab, row["attr"], "减少", "21"))
        await asyncio.sleep(1)

    print("\n=== 点确认前的状态 ===")
    before = json.loads(await B.ev(tab, JS_SCAN) or "{}")
    print("红字:", before.get("red"))
    print("toast:", before.get("toast"))
    print("form 输入框数:", len(before.get("form") or []))

    xy = json.loads(await B.ev(tab, r"""
var btns = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  var q = b.getBoundingClientRect();
  return (b.innerText || '').trim() === '确认' && q.width > 40 && q.y > 600; });
if (!btns.length) return 'null';
var q = btns[btns.length-1].getBoundingClientRect();
return JSON.stringify([Math.round(q.x + q.width/2), Math.round(q.y + q.height/2)]);""") or "null")
    print("\n=== 点确认:", xy, "===")
    if xy:
        await B.click_xy(tab, xy)

    for i in range(6):
        await asyncio.sleep(0.5)
        st = json.loads(await B.ev(tab, JS_SCAN) or "{}")
        print("+%.1fs 红字=%s toast=%s" % ((i + 1) * 0.5, st.get("red")[:6], st.get("toast")[:3]))
    print("\n弹窗还开:", await B.dialog_open(tab))
    print("form 明细:", json.dumps((json.loads(await B.ev(tab, JS_SCAN) or "{}").get("form") or [])[:12],
                                   ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
