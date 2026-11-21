# -*- coding: utf-8 -*-
"""把当前**已打开**的「发货模式」弹框里每行的 warehouseId / 仓库名读出来（只读）。"""
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
        print("dlg:", await SMB._ev(cap, SMB.JS_DLG))
        raw = await SMB._ev(cap, FDW.JS_WH)
        wh = json.loads(raw or "[]")
        ids = sorted({v for _p, v in wh if str(v).startswith("WH-")})
        names = [(p, v) for p, v in wh if "warehouseName" in p]
        print("ids:", ids)
        print("names:", json.dumps(names, ensure_ascii=False)[:600])
        out = Path(__file__).resolve().parents[2] / "_logs" / "dialog_warehouses.json"
        out.write_text(json.dumps({"ids": ids, "dump": wh}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print("saved", out)
        return 0
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 59670)))
