# -*- coding: utf-8 -*-
"""在已打开的「发货模式」弹框里切到「卖家自发货」，把浮现出来的仓库行 id 读出来，然后 ESC 不提交。

目的：拿 kansas 仓的 warehouseId（接口 shippingMode/edit 必须把该 SKU 当前绑定的仓库一起提交，
只提交加州3 会回 6000074）。加州3 = WH-05667065528452987。

    python scripts/goods/read_switch_warehouses.py --port 59670
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import cdp_net  # noqa: E402
import finish_dialog_watch as FDW  # noqa: E402
import switch_mode_batch as SMB  # noqa: E402


async def main(port: int) -> int:
    pages = [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
             if x.get("type") == "page" and "goods/list" in (x.get("url") or "")]
    cap = await cdp_net.NetCapture(pages[-1]["webSocketDebuggerUrl"], match=()).__aenter__()
    try:
        if await SMB._ev(cap, SMB.JS_DLG) != "open":
            print("弹框没开，先跑 probe_pencil_zr.py 打开它")
            return 2
        switched = await SMB._switch_mode(cap)
        print("switch:", switched)
        await asyncio.sleep(2)
        await SMB._ev(cap, SMB.JS_DLG)
        raw = await SMB._ev(cap, FDW.JS_WH)
        wh = json.loads(raw or "[]")
        ids = sorted({v for _p, v in wh if str(v).startswith("WH-")})
        names = sorted({v for p, v in wh if "warehouseName" in p})
        print("ids:", ids)
        print("names:", names)
        out = Path(__file__).resolve().parents[2] / "_logs" / "switch_wh.json"
        out.write_text(json.dumps({"ids": ids, "names": names, "dump": wh},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print("saved", out)
        # 只读：ESC 关掉，不点确认
        for _ in range(2):
            for tp in ("keyDown", "keyUp"):
                await cap.send("Input.dispatchKeyEvent",
                               {"type": tp, "key": "Escape", "code": "Escape",
                                "windowsVirtualKeyCode": 27}, timeout=10)
            await asyncio.sleep(0.5)
        print("已 ESC（未提交）")
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 59670)))
