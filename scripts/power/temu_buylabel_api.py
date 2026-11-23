# -*- coding: utf-8 -*-
"""temu_buylabel_api.py — TEMU **买面单（在线下单）接口层**（2026-11-23 真机抓包）。

链路（全部实测过，除最后一步 batch_send 待验证）：
  ① 抄页面 header（mallid + x-phan-data + web-version + x-document-referer）
  ② POST /mms/eagle/package/batch_check_request                    校验订单
  ③ POST /mms/eagle/package/online/query_shipping_provider_optional 查可选物流商
  ④ POST /mms/eagle/package/calculate_channel_total_estimated_amount 算价
  ⑤ POST /mms/eagle/package/reservation/query_avail_reserve_period  查揽收日
  ⑥ POST /mms/eagle/package/online/batch_send                       **真下单（花钱）**
  ⑦ POST /mms/eagle/package/online/query_label_task_progress        轮询任务

鉴权：`/mms/*` 网关要 `mallid` + **`x-phan-data`**（页面 JS 现算，不在 cookie 里）。
取法＝`grab_headers()`：在页面里临时包一层 fetch/XHR，等页面自己发一条 /mms/ 请求抄下来。

用法：
    python scripts/power/temu_buylabel_api.py --store BHY --grab
    python scripts/power/temu_buylabel_api.py --store BHY --dry-run PO-211-xxx
    python scripts/power/temu_buylabel_api.py --store BHY --apply   PO-211-xxx   # 真花钱

⚠ 默认只读：不加 `--apply` 绝不调 batch_send。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")
for p in (_HERE, _LIB):
    if p not in sys.path:
        sys.path.insert(0, p)

MALLID = {"BHY": "634418228526171", "BEL": "634418228477419",
          "KINGLAMANSPORT": "27364108847582"}
WINDOWS = {"BHY": "TM-BHY-YJF", "BEL": "TM-BEL-MGL", "KinglamanSport": "TM-KinglamanSport"}
HDR_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))),
                         "_logs", "temu-buylabel-api", "phan_header.json")

API = {
    "check": "/mms/eagle/package/batch_check_request",
    "providers": "/mms/eagle/package/online/query_shipping_provider_optional",
    "amount": "/mms/eagle/package/calculate_channel_total_estimated_amount",
    "reserve": "/mms/eagle/package/reservation/query_avail_reserve_period",
    "send": "/mms/eagle/package/online/batch_send",
    "progress": "/mms/eagle/package/online/query_label_task_progress",
}


# ---------------------------------------------------------------- 页面连接
def _page_ws(store: str):
    import ziniao_store as zs  # noqa: PLC0415
    win = WINDOWS.get(store, store)
    port = zs.probe_port_by_window(win)
    if not port:
        raise RuntimeError("窗口 %s 未开" % win)
    tabs = zs.list_page_targets(int(port))
    for key in ("online-ship", "orders.html"):
        for t in tabs:
            if key in str(t.get("url") or ""):
                return t.get("webSocketDebuggerUrl"), str(t.get("url"))
    raise RuntimeError("窗口 %s 里没有订单页/在线下单页" % win)


async def _ev(ws_url: str, expr: str):
    import websockets  # noqa: PLC0415
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expr, "awaitPromise": True, "returnByValue": True,
            "userGesture": True}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                r = m.get("result") or {}
                if r.get("exceptionDetails"):
                    raise RuntimeError(str(r["exceptionDetails"])[:300])
                return (r.get("result") or {}).get("value")


JS_CALL = r"""(async (path, body, hdr) => {
  try {
    const h = Object.assign({'Content-Type': 'application/json;charset=UTF-8'}, hdr || {});
    const r = await fetch(path, {method: 'POST', headers: h, credentials: 'include',
                                 body: JSON.stringify(body)});
    const t = await r.text();
    return JSON.stringify({status: r.status, body: t.slice(0, 30000)});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})"""

JS_GRAB = r"""(() => {
  if (window.__phanHooked) return 'already';
  window.__phan = null;
  const keep = (h) => {
    const o = {};
    try {
      if (h && typeof h.forEach === 'function') h.forEach((v, k) => { o[k] = String(v); });
      else if (h) Object.keys(h).forEach((k) => { o[k] = String(h[k]); });
    } catch (e) {}
    if (o['x-phan-data']) window.__phan = o;
  };
  const of = window.fetch;
  window.fetch = function (input, init) {
    try {
      const u = (typeof input === 'string') ? input : (input && input.url) || '';
      if (u.indexOf('/mms/') >= 0) keep((init && init.headers) || (input && input.headers) || {});
    } catch (e) {}
    return of.apply(this, arguments);
  };
  const osh = XMLHttpRequest.prototype.setRequestHeader, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    try { if (String(k).toLowerCase() === 'x-phan-data') window.__phan = window.__phan || {k: v}; } catch (e) {}
    return osh.apply(this, arguments);
  };
  window.__phanHooked = true;
  return 'installed';
})()"""


def grab_headers(store: str, wait_s: int = 90, log=print) -> dict:
    """在页面里抄一次 `/mms/` 请求的 header（含 x-phan-data）。抄不到→{}。"""
    ws_url, _u = _page_ws(store)
    log("   装 header 钩子…")
    asyncio.run(_ev(ws_url, JS_GRAB))
    log("   👉 请点一下页面上的「发货仓库」下拉（只读），让它发一条 /mms/ 请求…")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(3)
        v = asyncio.run(_ev(ws_url, "(() => JSON.stringify(window.__phan || null))()"))
        if v and v != "null":
            h = json.loads(v)
            if h.get("x-phan-data"):
                os.makedirs(os.path.dirname(HDR_CACHE), exist_ok=True)
                with open(HDR_CACHE, "w", encoding="utf-8") as f:
                    json.dump(h, f, ensure_ascii=False, indent=1)
                log("   ✅ 已抄到 header（含 x-phan-data）→ %s" % HDR_CACHE)
                return h
    log("   ⚠ 没抄到 x-phan-data")
    return {}


def load_headers(store: str) -> dict:
    if os.path.exists(HDR_CACHE):
        try:
            h = json.load(open(HDR_CACHE, encoding="utf-8"))
            if h.get("x-phan-data"):
                return h
        except Exception:  # noqa: BLE001
            pass
    return {}


def call(store: str, key: str, body: dict, headers: dict | None = None) -> dict:
    """在页面上下文里调一个已知 `/mms/` 接口。返回 {status, json|body|error}。"""
    ws_url, _u = _page_ws(store)
    hdr = headers if headers is not None else load_headers(store)
    hdr = dict(hdr)
    hdr.setdefault("mallid", MALLID.get(store.upper(), ""))
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    js = "(%s)(%s, %s, %s)" % (JS_CALL, json.dumps(API[key]),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    raw = asyncio.run(_ev(ws_url, js))
    out = json.loads(raw or "{}")
    txt = out.get("body") or ""
    try:
        out["json"] = json.loads(txt)
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------- 拼 batch_send body
#: ★ 2026-11-23 用户令（贴「批量填充」弹窗截图）：「**只能选这六个渠道**」——
#:   USPS / SpeedX / GOFO / UPS / SwiftX / FedEx（截图里还列了 Pilot / YANWEN Express /
#:   BFE express，但都没勾）。比价和下单**只在这六个里选**，其余渠道一律不用
#:   （哪怕更便宜），因为运营侧的「配送商范围」就是这么限的。
#: 可用 `TEMU_LABEL_CARRIERS` 覆盖（逗号分隔，按需临时放开）。
ALLOWED_CARRIERS = ("USPS", "SpeedX", "GOFO", "UPS", "SwiftX", "FedEx")


def allowed_carriers() -> tuple:
    import os as _os  # noqa: PLC0415
    env = str(_os.environ.get("TEMU_LABEL_CARRIERS") or "").strip()
    if env:
        return tuple(x.strip() for x in env.split(",") if x.strip())
    return ALLOWED_CARRIERS


def carrier_ok(name: str) -> bool:
    """承运商是否在允许名单里（大小写不敏感，允许前缀匹配如 `SwiftX` / `SwifX-D2D`）。"""
    n = str(name or "").strip().lower()
    if not n:
        return False
    return any(n.startswith(a.lower()) or a.lower() in n for a in allowed_carriers())


#: 2026-11-23 真机抓到的完整 body 字段；这里按同一结构拼（缺一不可，网关会静默拒绝）
def warehouse_zone(store: str, warehouse_name: str) -> dict:
    """取该仓的 `{zone_id, region_id1}`（★ 2026-11-23：三个只读接口都不返回 zone_id，
    只能按仓配置在 `data/temu_buylabel.json → warehouse_zone`）。"""
    try:
        import json as _json  # noqa: PLC0415
        cfg = _json.load(open(os.path.join(_HERE, "data", "temu_buylabel.json"),
                              encoding="utf-8"))
        zone = (cfg.get("warehouse_zone") or {}).get(warehouse_name)
        if zone:
            return {"zone_id": int(zone.get("zone_id", 7)),
                    "region_id1": int(zone.get("region_id1", 211))}
    except Exception:  # noqa: BLE001
        pass
    return {"zone_id": 7, "region_id1": 211}


def pick_reserve_ts(res: dict, warehouse_id: str = "", channel_id: str = "",
                    prefer_hour: int = 12, duration_hours: int = 4) -> tuple | None:
    """从 `query_avail_reserve_period` 的响应里算出**合法的**揽收时段 `(begin_ts, end_ts)`。

    ★★ 2026-11-23 真机（`batch_send` 报 `90009 预约时间范围有误`）：
      `reserved_time_period_list[].timestamp` 是**当天 0 点（UTC）**，
      直接拿它当 `begin_time_ts` 会被判非法。真正的时刻＝
        `begin = timestamp + available_points[].timestamp_offset`
      （`timestamp_offset` 是「当天第几秒」，如 12:00 → 43200）；
        `end   = begin + minimum_period_duration_hours * 3600`
      实测对齐：1795507200 + 43200 = **1795550400**（真机 begin）、+14400 = **1795564800**（真机 end）。
    """
    lst = ((res or {}).get("available_reserved_period_list")) or []
    for it in lst:
        if warehouse_id and it.get("warehouse_id") != warehouse_id:
            continue
        if channel_id and it.get("channel_id") not in (None, "", channel_id):
            continue
        # ① 可用的日子
        days = [x for x in (it.get("reserved_time_period_list") or []) if x.get("is_available")]
        if not days:
            continue
        day = days[0]
        base = int(day.get("timestamp") or 0)
        # ② 那天可选的小时点
        points = []
        for pt in (it.get("period_time_list") or []):
            points.extend(pt.get("available_points") or [])
        if not points:
            continue
        # ③ 优先取 prefer_hour（默认 12 点），没有就取第一个
        pick = None
        for p in points:
            if int(p.get("hour_time") or -1) == int(prefer_hour):
                pick = p
                break
        pick = pick or points[0]
        begin = base + int(pick.get("timestamp_offset") or 0)
        # ★ 2026-11-23 真机：`minimum_period_duration_hours` 实测是 1，但平台接受的窗口是 **4 小时**
        #   （真机 begin=1795550400 / end=1795564800，差 14400）。所以固定 duration_hours（默认 4），
        #   不再跟着 `minimum_period_duration_hours` 走 —— 上次跟它算出 1 小时窗口，被 90009 拒。
        hours = int(duration_hours or 4)
        return (begin, begin + hours * 3600)
    return None


def build_send_item(chosen: dict, goods: list, dims: dict, warehouse: dict,
                    reserve_ts: tuple, margin_ratio: float = 0.01) -> dict:
    """把「选中的渠道 + 货 + 尺寸 + 仓」拼成 batch_send 的一条 send_request。

    ★ 2026-11-23 修（真机）：`query_shipping_provider_optional` 返回里
      **承运商名在 `shipping_company_name`**（不是 `ship_company_name`），
      报价在 `online_estimated_vo.charge_amount_si`。拼 body 时：
        ship_company_name  ← shipping_company_name（**必须兼容两种 key**）
        expect_shipping_fee ← online_estimated_vo.charge_amount_si
    `chosen` = 该渠道的整条 `online_channel_vo_list[i]`；
    `reserve_ts` = (begin_time_ts, end_time_ts) 揽收时段。
    """
    v = chosen.get("online_estimated_vo") or {}
    carrier = (chosen.get("ship_company_name") or chosen.get("shipping_company_name") or "")
    fee = int(chosen.get("expect_shipping_fee") or v.get("charge_amount_si") or 0)
    mask = int(chosen.get("mask_charge_amount_si")
               or v.get("mask_charge_amount_si")
               or v.get("semi_total_charge_amount_si")
               or int(round(fee * (1 + margin_ratio))))
    ship_id = chosen.get("ship_company_id")
    _zn = warehouse_zone("", warehouse.get("warehouse_name") or chosen.get("warehouse_name") or "")
    return {
        "ship_company_name": carrier,
        "shipping_label_ship_type": 2,
        "user_address_snapshot_id": "",
        "package_sn": "",
        "shipping_label_action": 0,
        "zone_id": int(chosen.get("zone_id") or _zn["zone_id"]),
        "expect_delivery_upper_limit": int(v.get("delivery_day_max") or 5),
        "faraway_type": chosen.get("faraway_type", 1),
        "channel_id_version": chosen.get("channel_id_version", 0),
        "service_code": chosen.get("service_code", ""),
        "dimension_unit": dims.get("dimension_unit", "in"),
        "height": dims.get("height", "3.00"),
        "length": dims.get("length", "12.30"),
        "weight": dims.get("weight", "1.50"),
        "expect_shipping_fee": fee,
        "expect_currency": "USD",
        "expect_delivery_day_unit": int(v.get("day_unit") or 2),
        "expect_delivery_lower_limit": int(v.get("delivery_day_min") or 1),
        "weight_unit": dims.get("weight_unit", "lb"),
        "ship_company_id": ship_id if ship_id is not None else 0,
        "width": dims.get("width", "9.30"),
        "order_send_info_list": goods,
        "order_sn_list": [g["order_sn"] for g in goods],
        "order_info_list": [{"order_sn": g["order_sn"],
                             "parent_order_sn": g["parent_order_sn"],
                             "quantity": g.get("quantity", 1)} for g in goods],
        "product_sku_quantity_info_request_list":
            chosen.get("product_sku_quantity_info_request_list") or [],
        "ship_logistics_type": chosen.get("ship_logistics_type") or "Standard",
        "channel_id": chosen.get("channel_id", ""),
        "warehouse_id": warehouse.get("warehouse_id") or chosen.get("warehouse_id", ""),
        "ship_product_name": chosen.get("ship_product_name") or "Standard",
        "warehouse_name": warehouse.get("warehouse_name") or chosen.get("warehouse_name", ""),
        "timezone": chosen.get("timezone", "America/Los_Angeles"),
        "recommend_req_sn": chosen.get("recommend_req_sn", "-"),
        "recommend_res_sn": chosen.get("recommend_res_sn", "-"),
        "is_cod_parent_order": False,
        "pay_way_code": chosen.get("pay_way_code", 1),
        "electric_channel": bool(chosen.get("electric_channel", False)),
        "need_mailing_date": bool(chosen.get("need_mailing_date", False)),
        "interline_channel_info_list": [],
        "need_signature_service": False,
        "region_id1": int(chosen.get("region_id1") or _zn["region_id1"]),
        "online_estimated_vo": {
            "charge_amount_si": fee,
            "currency_type": v.get("currency_type") or "USD",
            "mask_charge_amount_si": mask,
        },
        "need_standardized_address": bool(chosen.get("need_standardized_address", False)),
        "certified_warehouse_info": {},
        "route_id": chosen.get("route_id", ""),
        "agree_collection_confirm": True,
        "relative_delay_send_time_second": 86400,
        "auto_reserve": True,
        "reserve_time_period": {"begin_time_ts": reserve_ts[0], "end_time_ts": reserve_ts[1]},
    }


def build_send_body(items: list) -> dict:
    return {"send_type": 0, "send_request_list": items,
            "replaced_package_sn_list": [], "push_to_cw": False,
            "delay_send_time_dto_list": []}


# ---------------------------------------------------------------- 业务步骤
def check(store: str, po: str, goods: list, dims: dict | None = None) -> dict:
    d = dims or {"weight": "1.50", "length": "12.30", "width": "9.30", "height": "3.00",
                 "dimension_unit": "in", "weight_unit": "lb"}
    body = {"check_info_type_list": [8, 11],
            "send_request_list": [dict(d, row_index=0, order_send_info_list=goods)]}
    return call(store, "check", body)


def providers(store: str, warehouse_id: str, goods: list, dims: dict | None = None) -> dict:
    d = dims or {"weight": "1.50", "length": "12.30", "width": "9.30", "height": "3.00",
                 "dimension_unit": "in", "weight_unit": "lb"}
    body = dict(d, warehouse_id=warehouse_id,
                order_sn_list=[g["order_sn"] for g in goods],
                order_info_list=[{"order_sn": g["order_sn"],
                                  "parent_order_sn": g["parent_order_sn"],
                                  "quantity": g.get("quantity", 1)} for g in goods],
                need_signature_service=False,
                product_sku_quantity_info_request_list=[])
    return call(store, "providers", body)


def reserve(store: str, ship: dict) -> dict:
    return call(store, "reserve", {"reserve_info_list": [ship]})


def send(store: str, ships: list) -> dict:
    """★ 真下单（花钱）。ships = batch_send 的 send_request_list。"""
    return call(store, "send", {"send_type": 0, "send_request_list": ships})


def progress(store: str) -> dict:
    return call(store, "progress", {})


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 买面单（接口版）")
    ap.add_argument("--store", required=True, choices=sorted(MALLID))
    ap.add_argument("--grab", action="store_true", help="只抄 header")
    ap.add_argument("--dry-run", default="", metavar="PO", help="校验+查价（不下单）")
    ap.add_argument("--apply", default="", metavar="PO", help="真下单（花钱！）")
    a = ap.parse_args()

    if a.grab:
        h = grab_headers(a.store)
        print("header:", sorted(h))
        return 0 if h else 2

    po = a.apply or a.dry_run
    if not po:
        ap.print_help()
        return 0
    hdr = load_headers(a.store)
    print("header 就绪:", bool(hdr.get("x-phan-data")))
    print("⚠ 本命令需要 goods 明细（order_sn/goods_id/sku_id）；先用 --grab 验证 header，"
          "业务参数由上层从订单页提供。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
