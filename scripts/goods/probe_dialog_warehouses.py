# -*- coding: utf-8 -*-
"""只读：打开某个已切好的 SKU 的「发货模式」弹框，把里面每行的 warehouseId / 仓库名挖出来。

用途：接口 `shippingMode/edit` 必须把该 SKU **当前绑定的仓库** 一起提交（只提交加州3 会回
`6000074 当前站点和发货仓关系已变更`）。加州3 = WH-05667065528452987，kansas 的 id 一直没抓，
这个脚本就是去拿它。**不点确认、不改任何东西**，读完按 ESC 关掉弹框。

    python scripts/goods/probe_dialog_warehouses.py --port 59670 --sku BEL-H103903-2B-S
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
import switch_mode_batch as SMB  # noqa: E402
import finish_dialog_watch as FDW  # noqa: E402


async def _esc(cap) -> None:
    for _ in range(2):
        for tp in ("keyDown", "keyUp"):
            await cap.send("Input.dispatchKeyEvent",
                           {"type": tp, "key": "Escape", "code": "Escape",
                            "windowsVirtualKeyCode": 27}, timeout=10)
        await asyncio.sleep(0.5)


async def main_async(a) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % a.port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    if not pages:
        print("没有商品列表页")
        return 2
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        await cap.send("Page.bringToFront", {}, timeout=10)
        st = await SMB._search(cap, a.sku)
        print("search:", json.dumps(st, ensure_ascii=False), flush=True)
        if st.get("n") != 1:
            return 3
        if not await SMB._open_dialog(cap, a.sku):
            print("弹框没打开（0 库存的款会这样）")
            return 4
        await SMB._ev(cap, SMB.JS_DLG)          # 把 window.__dlg 指到那个弹框
        raw = await SMB._ev(cap, FDW.JS_WH)
        print("warehouses:", raw)
        try:
            wh = json.loads(raw or "[]")
        except Exception:
            wh = []
        ids = sorted({v for _p, v in wh if str(v).startswith("WH-")})
        names = [p for p in wh if "warehouseName" in p[0]]
        print("ids:", ids)
        print("names:", json.dumps(names, ensure_ascii=False)[:400])
        Path(a.out).write_text(json.dumps({"sku": a.sku, "ids": ids, "dump": wh},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        await _esc(cap)
        print("已按 ESC 关掉弹框（没有提交）")
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    ap.add_argument("--sku", default="BEL-H103903-2B-S")
    ap.add_argument("--out", default="_logs/dialog_warehouses.json")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
