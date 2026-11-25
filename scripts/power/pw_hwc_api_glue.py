# -*- coding: utf-8 -*-
"""`pw_hwc_api_glue` — 把 `pw_ship_api.ship` 包成整链要的**批量结果**结构。

`run_temu_fulfill.step_hwc_order`（接口版分支）调：
    ship_many(pos, wh_map, platform="temu") -> {
        "ok": [[po, track], ...], "fail": [[po, why], ...],
        "skip": [[po, why], ...], "unfinished": [po, ...], "total": N }

判据（`pw_hwc_flow.order_pos` 定稿，不变）：
  **只认「状态」列离开「未下单」+ 运单号**；接口 msg / 弹层都不算数。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib"),
           os.path.join(os.path.dirname(_HERE), "pw_orders", "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pw_hwc_api as H  # noqa: E402
import pw_ship_api as S  # noqa: E402

#: 这些错误说明是**真实业务失败**（换仓/换 Fnsku 才可能成），不是接口问题
BIZ_FAIL = ("仓库没有库存", "没有库存", "无库存", "库存不足")


def ship_many(pos: list, wh_map: dict, platform: str = "temu", log=print) -> dict:
    """逐单跑「设仓渠道 → 下单代发 → 回读」。"""
    res = {"total": len(pos), "ok": [], "fail": [], "skip": [], "unfinished": []}
    pw = None
    for po in pos:
        po = str(po).strip()
        wh = str((wh_map or {}).get(po) or "加州3仓").strip()
        try:
            if pw is None:
                pw = H._pw()
            r = S.ship(pw, po, wh, platform, apply=True, log=log)
        except Exception as ex:  # noqa: BLE001
            res["fail"].append([po, "接口异常: %s" % str(ex)[:100]])
            continue
        msg = str(r.get("api_msg") or "")
        after = str(r.get("after") or "")
        if r.get("skip"):
            res["skip"].append([po, r["skip"]])
        elif after and after not in ("未下单", "?"):
            if any(k in msg for k in BIZ_FAIL):
                res["fail"].append([po, msg[:120]])
            else:
                res["ok"].append([po, r.get("track") or after])
        elif r.get("why"):
            res["fail"].append([po, str(r["why"])[:120]])
        else:
            res["unfinished"].append(po)
    return res
