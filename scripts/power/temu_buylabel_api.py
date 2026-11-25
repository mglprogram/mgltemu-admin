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

#: 各店 mallid（**必须与窗口 cookie 里的 `mallid` 完全一致**）。
#: ★ 2026-11-25 真机：BEL 原来写成 63441822**8**477419，比真值多一个 8 ⇒ 所有
#:   `/kirogi`、`/mms` 请求 403 400020037。真值见括号内（cookie 实读）。
MALLID = {
    "BHY": "634418228526171",          # cookie 实读一致
    "BEL": "634418227477419",          # ★ 2026-11-25 修正（原 634418228477419）
    "KINGLAMANSPORT": "27364108847582",
}
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
def mallid_of(store: str, ws_url: str = "") -> str:
    """取该店 **cookie 里的** mallid（运行时真值），常量只作兜底。

    为什么：常量写错一个字符就 403，而 cookie 永远是页面真实的店铺 ID。
    2026-11-25 BEL 就是常量多写了一个 8 造成的 403。
    """
    fallback = MALLID.get(store.upper(), "")
    if not ws_url:
        try:
            ws_url, _u = _page_ws(store)
        except Exception:  # noqa: BLE001
            return fallback
    try:
        import cdp_transport  # noqa: PLC0415
        r = cdp_transport.Cdp(ws_url).send("Network.getCookies", {}, timeout=10)
        for c in ((r.get("result") or {}).get("cookies") or []):
            if c.get("name") == "mallid" and c.get("value"):
                v = str(c["value"])
                if fallback and v != fallback:
                    print("   [mallid] 常量 %s ≠ cookie %s → 用 cookie"
                          % (fallback, v))
                return v
    except Exception:  # noqa: BLE001
        pass
    return fallback


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
    # ★ 2026-11-25：没有订单页就**自己开一张**（授权后窗口经常只剩授权入口页）
    try:
        tid = _open_orders_tab(int(port))
        import time as _t  # noqa: PLC0415
        for _ in range(12):
            _t.sleep(2)
            for t in zs.list_page_targets(int(port)):
                if "orders.html" in str(t.get("url") or ""):
                    return t.get("webSocketDebuggerUrl"), str(t.get("url"))
        raise RuntimeError("开了订单页但没等到（target=%s）" % str(tid)[:12])
    except RuntimeError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise RuntimeError("窗口 %s 里没有订单页，且自动开页失败：%s"
                           % (win, str(ex)[:100])) from ex


def _open_orders_tab(port: int):
    """在窗口里后台新开一张 orders.html，返回 targetId。"""
    import json as _json  # noqa: PLC0415
    import urllib.request as _ur  # noqa: PLC0415

    import websockets as _ws  # noqa: PLC0415

    ver = _json.loads(_ur.urlopen(
        "http://127.0.0.1:%d/json/version" % int(port), timeout=10).read())

    async def _go():
        async with _ws.connect(ver["webSocketDebuggerUrl"]) as sock:
            await sock.send(_json.dumps({
                "id": 1, "method": "Target.createTarget",
                "params": {"url": "https://agentseller-us.temu.com/mmsos/orders.html",
                           "background": True}}))
            while True:
                m = _json.loads(await sock.recv())
                if m.get("id") == 1:
                    return (m.get("result") or {}).get("targetId", "")

    return asyncio.run(_go())


def _eval_sync(ws_url: str, expression: str):
    """同步版 `_ev`（给不在事件循环里的调用方用）。"""
    return asyncio.run(_ev(ws_url, expression))


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
    // ★ 2026-11-23：上限放到 4MB —— 订单列表一页（595 单）远超原来 3 万字符，
    //   截断后 JSON 解析失败会被误报成「0 单」。
    return JSON.stringify({status: r.status, body: t.slice(0, 4000000)});
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
                    prefer_hour: int = -1, duration_hours: int = 0,
                    period_index: int = 0) -> tuple | None:
    """从 `query_avail_reserve_period` 的响应里算出**合法的**揽收时段 `(begin_ts, end_ts)`。

    ★★ 2026-11-24 真机（`batch_send` 报 `90009 预约时间范围有误`，逐字对照页面后定稿）：

      页面弹窗显示「立即预约 / **8:00-14:00**」（仓库当地时区），
      接口 `period_time_list` 是**分段**的，每段首尾两个点就是这一段的起止：
        {period_index:0, available_points:[8:00(28800), 14:00(50400)]}   ← 页面默认这段
        {period_index:1, available_points:[12:00(43200), 18:00(64800)]}
      日基准 `reserved_time_period_list[].timestamp` = 仓库当地 0 点（实测 1795507200
      = UTC 11-24 08:00 = 洛杉矶 11-24 00:00）。

      ⇒ `begin = 日基准 + 段内首点 offset`，`end = 日基准 + 段内末点 offset`。
      ✗ 不要再「取单点 + 自己加 N 小时」——那个区间不在任何段里，平台必拒（90009）。
    """
    lst = ((res or {}).get("available_reserved_period_list")) or []
    for it in lst:
        if warehouse_id and it.get("warehouse_id") != warehouse_id:
            continue
        if channel_id and it.get("channel_id") not in (None, "", channel_id):
            continue
        days = [x for x in (it.get("reserved_time_period_list") or []) if x.get("is_available")]
        if not days:
            continue
        base = int(days[0].get("timestamp") or 0)
        periods = it.get("period_time_list") or []
        # 优先按 period_index 取；取不到就用第一个有点的段
        seg = None
        for pt in periods:
            if int(pt.get("period_index", -1)) == int(period_index):
                seg = pt
                break
        if seg is None:
            seg = next((pt for pt in periods if pt.get("available_points")), None)
        if not seg:
            continue
        pts = seg.get("available_points") or []
        if not pts:
            continue
        offs = [int(p.get("timestamp_offset") or 0) for p in pts]
        begin = base + min(offs)
        end = base + max(offs)
        if end <= begin:                     # 只有一个点：退化为最小 1 小时
            end = begin + 3600
        return (begin, end)
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
