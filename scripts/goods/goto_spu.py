# -*- coding: utf-8 -*-
"""在商品列表页把 SPU 查询框设成指定 SPU 并点查询；也提供 list_progress 读取每个尺码的发货模式。

用法：
    python scripts/goods/goto_spu.py --port 59670 --spu 1967628638
    python scripts/goods/goto_spu.py --port 59670 --status        # 只读：看当前 SPU 完成度
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

JS_SPU_XY = r"""
var ins = Array.prototype.slice.call(document.querySelectorAll('input')).filter(function (i) {
  return (i.getAttribute('placeholder') || '').indexOf('多个查询请空格或逗号依次输入') >= 0; });
if (!ins.length) return 'none';
var r = ins[0].getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""

JS_QUERY_XY = r"""
var b = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (x) {
  return (x.innerText || '').replace(/[ \t\r\n]+/g, '') === '查询'; })[0];
if (!b) return 'none';
b.scrollIntoView({block: 'center'});
var r = b.getBoundingClientRect();
return JSON.stringify([Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)]);"""

JS_STATUS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = {code: '', spu: '', rows: []};
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
trs.forEach(function (tr) {
  var s = t(tr);
  var m = s.match(/货号[:：]\s*([A-Za-z0-9\-]+)/);
  if (m && !out.code) out.code = m[1];
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var sz = s.match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  var mode = '';
  cells.forEach(function (c) { if (c === '合作对接仓发货' || c === '卖家自发货') mode = c; });
  var sku = '';
  cells.forEach(function (c) { if (/^[A-Z]{2,3}-[A-Za-z0-9\-]+$/.test(c) && !sku) sku = c; });
  if (mode) out.rows.push({size: sz ? sz[1] : '', mode: mode, sku: sku});
});
var ins = Array.prototype.slice.call(document.querySelectorAll('input')).filter(function (i) {
  return (i.getAttribute('placeholder') || '').indexOf('多个查询请空格或逗号依次输入') >= 0; });
if (ins.length) out.spu = (ins[0].value || '').trim();
return JSON.stringify(out);"""


async def ev(cap, js, timeout=40):
    r = await cap.send("Runtime.evaluate",
                       {"expression": "(function(){\n%s\n})()" % js, "returnByValue": True},
                       timeout=timeout)
    if r.get("exceptionDetails"):
        return "EXC:" + str(r["exceptionDetails"])[:160]
    return (r.get("result") or {}).get("value")


async def click(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1}, timeout=20)
        await asyncio.sleep(0.2)


async def focus_list_page(port):
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        return None
    return await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()


async def get_status(cap):
    v = await ev(cap, JS_STATUS)
    try:
        return json.loads(v)
    except Exception:
        return {"rows": [], "code": "", "spu": "", "err": v}


async def goto_spu(cap, spu, wait_close=25, expect_code="", tries=3):
    before = (await get_status(cap)).get("code") or ""
    for t in range(tries):
        xy = await ev(cap, JS_SPU_XY)
        if not str(xy).startswith("["):
            return False, "找不到 SPU 输入框"
        await click(cap, json.loads(xy))
        await asyncio.sleep(0.4)
        for tp in ("keyDown", "keyUp"):
            await cap.send("Input.dispatchKeyEvent", {"type": tp, "key": "a", "code": "KeyA",
                                                      "windowsVirtualKeyCode": 65, "modifiers": 2},
                           timeout=10)
        await asyncio.sleep(0.15)
        await cap.send("Input.insertText", {"text": str(spu)}, timeout=20)
        await asyncio.sleep(0.5)
        q = await ev(cap, JS_QUERY_XY)
        if str(q).startswith("["):
            await click(cap, json.loads(q))
        for _ in range(wait_close):
            await asyncio.sleep(1.0)
            st = await get_status(cap)
            code = st.get("code") or ""
            if expect_code:
                if code == expect_code:
                    return True, st
            elif code and code != before:
                return True, st
        print("查询未生效，重试 %d/%d" % (t + 1, tries), flush=True)
    return False, "查询后列表未刷新"


async def main_async(a):
    cap = await focus_list_page(a.port)
    if cap is None:
        print("没有商品列表页")
        return 2
    try:
        if a.status:
            print(json.dumps(await get_status(cap), ensure_ascii=False, indent=1))
            return 0
        ok, st = await goto_spu(cap, a.spu)
        print(json.dumps({"ok": ok, "detail": st if not ok else st.get("code")},
                         ensure_ascii=False, indent=1))
        return 0 if ok else 3
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--spu", default="")
    ap.add_argument("--status", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
