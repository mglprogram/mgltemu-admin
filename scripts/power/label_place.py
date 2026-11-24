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
import label_diag_shot as DIAG  # noqa: E402
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


def query_channels(store: str, warehouse_id: str, goods: list, dims: dict,
                   warehouse_name: str = "", product_skus: list | None = None) -> list:
    """用**批量接口** + 六家 `shipping_company_id_list` 查渠道（2026-11-23 真机 body 对齐）。

    ★ 为什么不用单渠道版：页面 kansas 有 `UPS, Ground saver`，单渠道接口不返回 UPS。
    ★ 之前 20016 系统异常 = query_list 里字段没给全，现在按真机结构原样补齐。
    返回 `rec_result_list[].rec_channel` 扁平列表；失败返回 []。
    """
    import asyncio as _aio  # noqa: PLC0415
    q = {
        "ship_company_name": "",
        "user_address_snapshot_id": "",
        "package_sn": "",
        "shipping_label_action": 0,
        "dimension_unit": dims.get("dimension_unit", "in"),
        "height": dims.get("height", "3.00"),
        "length": dims.get("length", "12.30"),
        "weight": dims.get("weight", "1.50"),
        "weight_unit": dims.get("weight_unit", "lb"),
        "width": dims.get("width", "9.30"),
        "order_send_info_list": goods,
        "order_sn_list": [g["order_sn"] for g in goods],
        "order_info_list": [{"order_sn": g["order_sn"],
                             "parent_order_sn": g["parent_order_sn"],
                             "quantity": g.get("quantity", 1)} for g in goods],
        "product_sku_quantity_info_request_list": product_skus or [],
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse_name or "",
        "timezone": "America/Los_Angeles",
        "is_cod_parent_order": False,
        "need_signature_service": False,
        "region_id1": 211,
        "online_estimated_vo": {},
        "certified_warehouse_info": {},
        "order_snfo_list": [{"order_sn": g["order_sn"],
                             "parent_order_sn": g["parent_order_sn"]} for g in goods],
    }
    body = {
        "is_work_day_limited": True, "is_nature_day_limited": True,
        "nature_day_limit_type": "no-limit", "work_day_limit_type": "no-limit",
        "max_nature_day_delivery": "", "max_work_day_delivery": "",
        "rec_type": 1,
        "shipping_company_id_list": list(CARRIER_IDS.values()),
        "query_list": [q],
    }
    out = json.loads(_aio.run(A._ev(*_ws_and_js(store, BATCH_PROVIDERS, body))) or "{}")
    # ★ 2026-11-23 修：`_ev` 返回 {"status":..,"body":"<json 字符串>"}，
    #   没有 `json` 键（那是 `A.call()` 才加的）—— 原来读 `out["json"]` 永远为空 ⇒ 0 条渠道。
    try:
        j = json.loads(out.get("body") or "{}")
    except Exception:  # noqa: BLE001
        j = {}
    res = j.get("result") or {}
    rows = []
    for it in res.get("rec_result_list") or []:
        ch = it.get("rec_channel") or {}
        if ch:
            ch["_rec_success"] = bool(it.get("success", True))
            ch.setdefault("fail_reason", it.get("fail_reason"))
            rows.append(ch)
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
    # ★ 2026-11-23 真机抓包更正：kansas 是 WH-02922306109573163
    #   （原写 WH-04940925214853163，那是别的仓，导致 kansas 查不到 UPS）
    "kansas": {"warehouse_id": "WH-02922306109573163", "warehouse_name": "kansas"},
    "恒信加州3": {"warehouse_id": "WH-04940925214853163", "warehouse_name": "恒信加州3"},
}


def _mandatory_flag(store: str, channel: dict, goods: list, dims: dict,
                    info: dict) -> bool | None:
    """`is_mandatory_on_call`；None = 字段缺失（无法判定 ⇒ 按不强制处理）。"""
    try:
        rs = A.reserve(store, A.build_send_item(channel, goods, dims, info, (0, 0)))
        lst = (((rs.get("json") or {}).get("result") or {})
               .get("available_reserved_period_list")) or []
        if not lst:
            return None
        return lst[0].get("is_mandatory_on_call")
    except Exception:  # noqa: BLE001
        return None


def _submit_no_reserve(store: str, channel: dict, goods: list, dims: dict, info: dict,
                       wh: str, tried: list, apply: bool, log=print) -> dict:
    """不传 `reserve_time_period` 直接提交（平台稍后自行约 / 本就无需预约）。

    含「重量差异过大（N 磅）」自动回填重试。
    """
    import re as _re  # noqa: PLC0415

    item = A.build_send_item(channel, goods, dims, info, (0, 0))
    item.pop("reserve_time_period", None)
    item["auto_reserve"] = True
    item["agree_collection_confirm"] = True
    if not apply:
        return {"ok": True, "dry": True, "why": "", "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"),
                "weight": dims.get("weight"), "body": A.build_send_body([item])}
    r = A.send(store, [item])
    j = r.get("json") or {}
    if j.get("success"):
        res = j.get("result") or {}
        return {"ok": True, "why": "", "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"),
                "package_sn": (res.get("package_sn_list") or [""])[0],
                "task_id": res.get("batch_task_id", ""),
                "weight": dims.get("weight")}
    msg = str(j.get("error_msg") or j.get("error_code") or "")
    # ★ 2026-11-24：预售单 / 重复单是**终态**，不再重试也不再换仓
    _kind = K.classify_error(msg)
    if _kind:
        log("   [终态] %s → 不再重试" % _kind)
        try:
            DIAG.capture_failure(store, goods[0].get("parent_order_sn", ""), _kind,
                                 {"wh": wh, "msg": msg[:200], "raw_result": j}, log=log)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "why": _kind, "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"),
                "package_sn": "", "task_id": ""}
    m = _re.search(r"（\s*([0-9]+(?:\.[0-9]+)?)\s*磅\s*）", msg)
    if m:
        want = "%.2f" % float(m.group(1))
        if want != str(dims.get("weight")):
            log("   [重量] 平台要 %s 磅 → 重填后重试" % want)
            return _submit_no_reserve(store, channel, goods, dict(dims, weight=want),
                                      info, wh, tried, apply=apply, log=log)
    return {"ok": False, "why": msg[:160], "wh": wh, "tried": tried,
            "channel": channel.get("shipping_company_name"), "raw": j,
            "package_sn": "", "task_id": ""}


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
        chans = query_channels(store, info["warehouse_id"], goods, dims,
                               warehouse_name=info.get("warehouse_name", ""))
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
        # ★ 2026-11-24 修（用户实拍三单 UPS）：**只要渠道有报价就按免时段提交**
        #   页面明确写「UPS 之外的物流商可不预约」；UPS 页面也显示「不需要预约时段」。
        #   原来死等时段 ⇒ 报「无可用揽收时段」，与页面不一致。
        if _mandatory_flag(store, channel, goods, dims, info) is not True:
            log("   [%s] 渠道 %s 无时段且不强制预约 → 直接提交"
                % (wh, channel.get("shipping_company_name")))
            return _submit_no_reserve(store, channel, goods, dims, info, wh, tried,
                                      apply=apply, log=log)
    if not ts:
        # ★ 2026-11-24：先看这个渠道**是否强制预约**（`is_mandatory_on_call`）。
        #   不强制 ⇒ 直接不带时段提交（页面规则：「其余物流商可以不预约，稍后到面单管理约」）。
        _probe = A.build_send_item(channel, goods, dims, info, (0, 0))
        _rs = A.reserve(store, _probe)
        _entry = (((_rs.get("json") or {}).get("result") or {})
                  .get("available_reserved_period_list") or [{}])
        _mand = bool((_entry[0] or {}).get("is_mandatory_on_call"))
        if not _mand:
            log("   [%s] 渠道 %s 不强制预约 → 不带时段直接提交"
                % (wh, channel.get("shipping_company_name")))
            item = A.build_send_item(channel, goods, dims, info, (0, 0))
            item.pop("reserve_time_period", None)
            item["auto_reserve"] = True
            item["agree_collection_confirm"] = True
            if not apply:
                return {"ok": True, "dry": True, "why": "", "wh": wh, "tried": tried,
                        "channel": channel.get("shipping_company_name"),
                        "weight": dims.get("weight"),
                        "body": A.build_send_body([item])}
            r0 = A.send(store, [item])
            j0 = r0.get("json") or {}
            if j0.get("success"):
                res0 = j0.get("result") or {}
                return {"ok": True, "why": "", "wh": wh, "tried": tried,
                        "channel": channel.get("shipping_company_name"),
                        "package_sn": (res0.get("package_sn_list") or [""])[0],
                        "task_id": res0.get("batch_task_id", ""),
                        "weight": dims.get("weight")}
            _msg = str(j0.get("error_msg") or j0.get("error_code") or "")
            log("   [%s] 免时段提交失败：%s" % (wh, _msg[:80]))
            # ★ 2026-11-24：重复下单是**终态**，不能再换仓找时段（否则真实原因被盖掉）
            if any(k in _msg for k in ("重复", "已下单", "duplicate", "Duplicate")):
                try:
                    DIAG.capture_failure(store, goods[0].get("parent_order_sn", ""),
                                         "已买过面单：" + _msg[:80],
                                         {"wh": wh, "channel":
                                          channel.get("shipping_company_name"),
                                          "raw_result": j0}, log=log)
                except Exception:  # noqa: BLE001
                    pass
                return {"ok": False, "why": "已买过面单（重复提交）", "wh": wh,
                        "tried": tried, "channel":
                        channel.get("shipping_company_name"),
                        "package_sn": "", "task_id": ""}
            log("   [%s] → 继续找时段" % wh)
    if not ts:
        # ★ 2026-11-24 真机教训：**不许硬造时段**。
        #   实测 UPS 无时段时硬填「次日 12:00 UTC」→ batch_send 报「系统异常，请稍后重试」。
        #   时段必须来自 reserve 的真实返回。
        # ★ 2026-11-24 追加：没时段 ⇒ **先换仓再试**（用户口径「没时段就换仓」），
        #   换仓也没有才判失败并留证。
        nxt = S.next_warehouse(tried, DROPDOWN_WH.get(store.upper(), set()))
        if nxt:
            log("   [%s] 无可用时段 → 换仓 %s 重试" % (wh, nxt))
            tried.append(nxt)
            wh = nxt
            info = _wh_info(wh)
            chans = query_channels(store, info["warehouse_id"], goods, dims,
                                   warehouse_name=info.get("warehouse_name", ""))
            allowed = [c for c in chans
                       if A.carrier_ok(c.get("shipping_company_name")
                                       or c.get("ship_company_name") or "")]
            for cand in S.candidates(allowed):
                cn = cand.get("shipping_company_name") or ""
                rs = A.reserve(store, A.build_send_item(cand, goods, dims, info, (0, 0)))
                ts2 = A.pick_reserve_ts((rs.get("json") or {}).get("result") or {},
                                        warehouse_id=info["warehouse_id"],
                                        channel_id=cand.get("channel_id"))
                if ts2:
                    channel, ts = cand, ts2
                    log("   [%s] 时段 OK：%s（begin=%s）" % (wh, cn, ts[0]))
                    break
                log("   [%s] 渠道 %s 没拿到揽收时段 → 换下一个" % (wh, cn))
    if not ts:
        # ★ 2026-11-24：失败自动留证（截图 + 页面快照），不再靠用户贴图
        try:
            DIAG.capture_failure(store, goods[0].get("parent_order_sn", ""),
                                 "无可用揽收时段", {"tried": tried, "wh": wh}, log=log)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "why": "无可用揽收时段（所有候选渠道均未返回可取日期）",
                "wh": wh, "tried": tried, "channel": "", "package_sn": "", "task_id": ""}
    def _send_with_weight_retry(chan, wh_info, ts_use, dims_use) -> dict:
        """提交 + 「重量差异过大（N 磅）」自动回填重试（最多 2 轮）。"""
        import re as _re  # noqa: PLC0415
        cur_dims = dict(dims_use)
        last = {}
        for attempt in range(3):
            it = A.build_send_item(chan, goods, cur_dims, wh_info, ts_use)
            r = A.send(store, [it])
            j2 = r.get("json") or {}
            if j2.get("success"):
                res2 = j2.get("result") or {}
                return {"ok": True, "why": "", "wh": wh, "tried": tried,
                        "channel": chan.get("shipping_company_name"),
                        "package_sn": (res2.get("package_sn_list") or [""])[0],
                        "task_id": res2.get("batch_task_id", ""),
                        "weight": cur_dims.get("weight")}
            msg = str(j2.get("error_msg") or j2.get("error_code") or "")
            last = {"raw": j2, "msg": msg}
            m = _re.search(r"（\s*([0-9]+(?:\.[0-9]+)?)\s*磅\s*）", msg)
            if not m:
                break
            want = "%.2f" % float(m.group(1))
            if want == str(cur_dims.get("weight")):
                break
            log("   [重量] 平台要 %s 磅（原 %s）→ 重填后重试（第 %d 次）"
                % (want, cur_dims.get("weight"), attempt + 1))
            cur_dims = dict(cur_dims, weight=want)
        return {"ok": False, "why": (last.get("msg") or "")[:160], "wh": wh,
                "tried": tried, "channel": chan.get("shipping_company_name"),
                "raw": last.get("raw"), "dims_used": cur_dims,
                "package_sn": "", "task_id": ""}

    item = A.build_send_item(channel, goods, dims, info, ts)
    if not apply:
        return {"ok": True, "dry": True, "why": "", "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"),
                "weight": dims.get("weight"), "body": A.build_send_body([item])}

    _ret = _send_with_weight_retry(channel, info, ts, dims)
    if _ret.get("ok"):
        return _ret
    j = _ret.get("raw") or {}
    if True:
        msg = str(j.get("error_msg") or j.get("error_code") or "")
        hint = None
        if msg and "磅" in msg:
            import re  # noqa: PLC0415
            m = re.search(r"（\s*([0-9.]+)\s*磅\s*）", msg)
            hint = float(m.group(1)) if m else None
        # ★ 2026-11-24：提交失败自动留证（含完整错误体 + 当时拼的 body）
        try:
            DIAG.capture_failure(store, goods[0].get("parent_order_sn", ""), msg[:200],
                                 {"wh": wh, "channel": channel.get("shipping_company_name"),
                                  "tried": tried, "reserve_ts": ts,
                                  "reserve_time_period": item.get("reserve_time_period"),
                                  "raw_result": j, "body_sent": item}, log=log)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "why": msg[:160], "wh": wh, "tried": tried,
                "channel": channel.get("shipping_company_name"), "weight_hint": hint,
                "package_sn": "", "task_id": ""}
    res = j.get("result") or {}
    return {"ok": True, "why": "", "wh": wh, "tried": tried,
            "channel": channel.get("shipping_company_name"),
            "package_sn": (res.get("package_sn_list") or [""])[0],
            "task_id": res.get("batch_task_id", ""), "weight": dims.get("weight")}
