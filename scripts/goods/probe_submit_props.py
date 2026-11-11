# -*- coding: utf-8 -*-
"""只读对比：BEL（能提交成功）与 BHY（点确认无反应）的「修改库存」弹窗提交链路。

用途：BHY 点「确认」后没有请求、没有报错、没有红字，怀疑提交挂在别的事件面上。
本脚本对**同一个按钮**往上逐层 dump React props（有哪些 on* 处理），
再对比 BEL 与 BHY 的差异（BEL 之前是能真正提交成功的）。

    python scripts/goods/probe_submit_props.py --spu-bel 1208899974 --spu-bhy 1208899974
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
from ziniao_port import probe_port  # noqa: E402

JS_PROPS = r"""
var t = function (e) { return (e.innerText || '').trim(); };
var btn = Array.prototype.slice.call(document.querySelectorAll('button')).filter(function (b) {
  var q = b.getBoundingClientRect();
  return t(b) === '\u786e\u8ba4' && q.width > 40 && q.y > 600;
}).pop();
if (!btn) return JSON.stringify({ok: false, why: 'no-confirm-btn'});
var out = [];
var e = btn;
for (var d = 0; d < 10 && e && e !== document.body; d++, e = e.parentElement) {
  var props = window.__zr.findProps(e);
  var handlers = props ? Object.keys(props).filter(function (k) { return k.indexOf('on') === 0; }) : [];
  out.push({
    depth: d,
    tag: e.tagName,
    cls: String(e.className || '').slice(0, 44),
    type: props && props.type ? String(props.type) : '',
    handlers: handlers,
    hasOnSubmit: !!(props && typeof props.onSubmit === 'function'),
    hasOnClick: !!(props && typeof props.onClick === 'function'),
    disabled: props ? !!props.disabled : null,
  });
}
return JSON.stringify({ok: true, btn: {text: t(btn), disabled: !!btn.disabled}, chain: out});"""


async def dump(store: str, spu: str) -> dict:
    win = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}[store]
    port = probe_port(win)
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        return {"store": store, "error": "窗口里没有商品列表页"}
    tid = pages[-1].get("id")
    tab = B.OwnTab(port, tid)
    tab.reuse_tid = tid
    tab.store = store
    if not await tab.open():
        return {"store": store, "error": "attach 失败"}
    try:
        await tab.ensure()
        await zreact.inject(tab.session)
        if not await B.dialog_open(tab):
            await B.pick_query_type(tab, "SPU")
            await B.search(tab, spu)
            await B.open_stock_dialog(tab, spu)
            await asyncio.sleep(3)
        if not await B.dialog_open(tab):
            return {"store": store, "error": "弹窗没打开"}
        raw = await B.ev(tab, JS_PROPS)
        try:
            d = json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            d = {"error": str(raw)[:200]}
        d["store"] = store
        return d
    finally:
        await tab.keep_tab()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY", help="主看的店铺（BHY）")
    ap.add_argument("--spu", required=True)
    ap.add_argument("--compare-bel", action="store_true", help="同时 dump BEL 的做对比")
    ap.add_argument("--spu-bel", default="")
    a = ap.parse_args()

    res = [await dump(a.store, a.spu)]
    if a.compare_bel and a.spu_bel:
        res.append(await dump("BEL", a.spu_bel))

    for r in res:
        print("\n===== %s =====" % r.get("store"))
        if r.get("error"):
            print("  ERR:", r["error"])
            continue
        print("  button:", r.get("btn"))
        for c in r.get("chain") or []:
            flag = []
            if c["hasOnSubmit"]:
                flag.append("ON_SUBMIT")
            if c["hasOnClick"]:
                flag.append("onClick")
            print("  %2d %-6s %-40s handlers=%s %s"
                  % (c["depth"], c["tag"], c["cls"], ",".join(c["handlers"]) or "-", " ".join(flag)))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
