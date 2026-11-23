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

#: ★ 2026-11-23 真机：六家承运商的 `shipping_company_id`（批量查渠道要指定）
CARRIER_IDS = {
    "USPS": 960246690,
    "UPS": 468275019,
    "GOFO": 999304734,
    "SpeedX": 314439762,
    "FedEx": 699272611,
    "SwiftX": 999289591,
}

BATCH_PROVIDERS = "/mms/eagle/package/online/batch_query_shipping_provider_optional"


def query_channels(store: str, warehouse_id: str, goods: list, dims: dict) -> list:
    """用**批量接口** + 六家 `shipping_company_id_list` 查渠道。

    为什么不用单渠道版：单渠道 `query_shipping_provider_optional` 返回的候选比页面少
    （实测页面 kansas 有 UPS Ground saver，单渠道接口里根本没有 UPS）。
    返回 `rec_result_list` 里的 `rec_channel` 扁平列表；失败返回 []。
    """
    import asyncio as _aio  # noqa: PLC0415
    body = {
        "is_work_day_limited": True, "is_nature_day_limited": True,
        "nature_day_limit_type": "no-limit", "work_day_limit_type": "no-limit",
        "max_nature_day_delivery": "", "max_work_day_delivery": "",
        "rec_type": 1,
        "shipping_company_id_list": list(CARRIER_IDS.values()),
        "query_list": [dict(dims,
                            warehouse_id=warehouse_id,
                            order_info_list=[{"order_sn": g["order_sn"],
                                              "parent_order_sn": g["parent_order_sn"],
                                              "quantity": g.get("quantity", 1)}
                                             for g in goods],
                            need_signature_service=False)],
    }
    out = json.loads(_aio.run(A._ev(*_ws_and_js(store, BATCH_PROVIDERS, body))) or "{}")
    j = out.get("json") or {}
    res = j.get("result") or {}
    rows = []
    for it in res.get("rec_result_list") or []:
        ch = it.get("rec_channel") or {}
        if ch:
            ch["_rec_success"] = bool(it.get("success", True))
            ch.setdefault("fail_reason", it.get("fail_reason"))
            rows.append(ch)
        # 有的返回直接在 query_list 里给 online_channel_vo_list
        for c in it.get("online_channel_vo_list") or []:
            rows.append(c)
    return rows


def _ws_and_js(store: str, path: str, body: dict):
    ws_url, _u = A._page_ws(store)
    hdr = A.load_headers(store) or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep.setdefault("mallid", A.MALLID.get(store.upper(), ""))
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(path),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    return ws_url, js


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
        # ★ 2026-11-23：改走**批量**渠道接口（单渠道版候选比页面少，页面有 UPS 它没有）
        chans = query_channels(store, info["warehouse_id"], goods, dims)
        if not chans:
            # ★ 2026-11-23 兜底：批量接口的完整 body 还没抓准（返回 20016 系统异常），
            #   先回落到单渠道版；它虽然候选少（没有 UPS），但至少能跑通主流程。
            pv0 = A.providers(store, info["warehouse_id"], goods, dims)
            chans = ((pv0.get("json") or {}).get("result") or {}).get("online_channel_vo_list") or []
        # 六家白名单过滤（批量接口的字段名可能是 shipping_company_name）
        allowed = [c for c in chans
                   if A.carrier_ok(c.get("shipping_company_name")
                                   or c.get("ship_company_name") or "")]
        after_switch = len(tried) > 1
        channel = S.pick_channel(allowed, after_switch=after_switch)
        if channel:
            log("   [%s] 渠道 %s / %s" % (wh, channel.get("shipping_company_name"),
                                          channel.get("service_code")))
            rows_for_reserve = (info, channel, S.candidates(allowed))
            break
        # 无可用渠道 → 换仓
        reason = K.classify(chans, False)
        nxt = S.next_warehouse(tried, DROPDOWN_WH.get(store.upper(), set()))
        if not nxt:
            return {"ok": False, "why": reason or K.NO_CHANNEL, "wh": wh,
                    "tried": tried, "channel": "", "package_sn": "", "task_id": ""}
        log("   [%s] %s → 换仓 %s 重查" % (wh, reason or "无可用渠道", nxt))
        wh = nxt

    info, channel = rows_for_reserve[0], rows_for_reserve[1]
    # ★ 2026-11-23：**逐个渠道试揽收时段** —— 一个渠道没时段不代表这单下不了，
    #   换下一个候选再问（原来直接失败，UPS 没时段就把整单判死）。
    all_rows = list(rows_for_reserve[2]) if len(rows_for_reserve) > 2 else [channel]
    ordered = [channel] + [c for c in all_rows if c is not channel]
    ts = None
    for cand in ordered:
        cn = cand.get("shipping_company_name") or cand.get("ship_company_name") or ""
        probe = A.build_send_item(cand, goods, dims, info, (0, 0))
        rs = A.reserve(store, probe)
        ts = A.pick_reserve_ts((rs.get("json") or {}).get("result") or {},
                               warehouse_id=info["warehouse_id"],
                               channel_id=cand.get("channel_id"))
        if ts:
            channel = cand
            log("   [%s] 时段 OK：%s（begin=%s）" % (wh, cn, ts[0]))
            break
        log("   [%s] 渠道 %s 没拿到揽收时段 → 换下一个" % (wh, cn))
    if not ts:
        return {"ok": False, "why": "所有渠道都没有可用揽收时段", "wh": wh, "tried": tried,
                "channel": "", "package_sn": "", "task_id": ""}
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
