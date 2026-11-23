# -*- coding: utf-8 -*-
"""接口版单条下单主流程：check → providers → (无渠道则换 Kansas) → reserve → send。

把 `label_switch_wh`（换仓+选第一个）与 `label_fail_kind`（失败分类）串起来，
供整链与 `temu_label_dryrun.py` 共用。

口径（用户 2026-11-23）：
  · 原仓无可用渠道 ⇒ 换 **Kansas** 仓重查，取**第一个**可用渠道
  · Kansas 也没有 ⇒ 失败「没有可用物流商及服务」，交人工
  · 渠道只看六家白名单（USPS/SpeedX/GOFO/UPS/SwiftX/FedEx）
  · 重量：先基准，平台报「（N 磅）」再改成 N
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import label_fail_kind as K  # noqa: E402
import label_switch_wh as S  # noqa: E402
import temu_buylabel_api as A  # noqa: E402

#: 店铺买面单下拉里真实存在的仓名（BHY/BEL 两套写法，见 data/temu_buylabel.json）
DROPDOWN_WH = {
    "BHY": {"加州三仓", "kansas", "恒信加州3"},
    "BEL": {"加州3仓", "kansas", "恒信加州3仓"},
    "KINGLAMANSPORT": {"加州三仓", "kansas", "恒信加州3"},
}

#: 仓名 → 接口里的 warehouse_id / warehouse_name（真机抓包值）
WH_IDS = {
    "加州三仓": {"warehouse_id": "WH-09107063234693163", "warehouse_name": "加州三仓"},
    "加州3仓": {"warehouse_id": "WH-09107063234693163", "warehouse_name": "加州三仓"},
    "kansas": {"warehouse_id": "WH-04940925214853163", "warehouse_name": "kansas仓"},
    "恒信加州3": {"warehouse_id": "WH-04940925214853163", "warehouse_name": "恒信加州3"},
}


def _wh_info(name: str) -> dict:
    return WH_IDS.get(name) or {"warehouse_id": "", "warehouse_name": name}


def place_one(store: str, goods: list, dims: dict, warehouse: str,
              apply: bool = False, log=print) -> dict:
    """单条买面单。返回 `{ok, why, wh, channel, package_sn, task_id, weight}`。"""
    tried = []
    wh = warehouse
    rows_for_reserve = None
    channel = None
    while True:
        tried.append(wh)
        info = _wh_info(wh)
        pv = A.providers(store, info["warehouse_id"], goods, dims)
        chans = ((pv.get("json") or {}).get("result") or {}).get("online_channel_vo_list") or []
        # 六家白名单过滤
        allowed = [c for c in chans if A.carrier_ok(c.get("shipping_company_name") or "")]
        after_switch = len(tried) > 1
        channel = S.pick_channel(allowed, after_switch=after_switch)
        if channel:
            log("   [%s] 渠道 %s / %s" % (wh, channel.get("shipping_company_name"),
                                          channel.get("service_code")))
            rows_for_reserve = (info, channel)
            break
        # 无可用渠道 → 换仓
        reason = K.classify(chans, False)
        nxt = S.next_warehouse(tried, DROPDOWN_WH.get(store.upper(), set()))
        if not nxt:
            return {"ok": False, "why": reason or K.NO_CHANNEL, "wh": wh,
                    "tried": tried, "channel": "", "package_sn": "", "task_id": ""}
        log("   [%s] %s → 换仓 %s 重查" % (wh, reason or "无可用渠道", nxt))
        wh = nxt

    info, channel = rows_for_reserve
    probe = A.build_send_item(channel, goods, dims, info, (0, 0))
    rs = A.reserve(store, probe)
    ts = A.pick_reserve_ts((rs.get("json") or {}).get("result") or {},
                           warehouse_id=info["warehouse_id"],
                           channel_id=channel.get("channel_id"))
    if not ts:
        return {"ok": False, "why": "没拿到揽收时段", "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"), "package_sn": "",
                "task_id": ""}
    item = A.build_send_item(channel, goods, dims, info, ts)
    if not apply:
        return {"ok": True, "dry": True, "why": "", "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"),
                "weight": dims.get("weight"), "body": A.build_send_body([item])}

    r = A.send(store, [item])
    j = r.get("json") or {}
    if not j.get("success"):
        msg = str(j.get("error_msg") or j.get("error_code") or "")
        hint = None
        if msg and "磅" in msg:
            import re  # noqa: PLC0415
            m = re.search(r"（\s*([0-9.]+)\s*磅\s*）", msg)
            hint = float(m.group(1)) if m else None
        return {"ok": False, "why": msg[:160], "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"), "weight_hint": hint,
                "package_sn": "", "task_id": ""}
    res = j.get("result") or {}
    return {"ok": True, "why": "", "wh": wh, "tried": tried,
            "channel": channel.get("shipping_company_name"),
            "package_sn": (res.get("package_sn_list") or [""])[0],
            "task_id": res.get("batch_task_id", ""), "weight": dims.get("weight")}
