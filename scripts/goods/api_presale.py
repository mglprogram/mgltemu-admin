# -*- coding: utf-8 -*-
"""TEMU 开在途预售 / 清零 —— 接口直调（2026-11-24 真实抓包固化）。

抓包样本（BHY-102503-5SetB XL，用户在页面上手开一次，我这边捕获）：
    POST .../stock/updateMmsBtgProductSalesStock
    {
      "productId": 5856725503,
      "skuStockChangeList": [{"productSkuId":87367614111,"stockDiff":-69,
                              "currentStockAvailable":69,"currentShippingMode":1,
                              "warehouseId":"WH-04940925214853163"}],
      "skuTypeChangeList": [],
      "skuPreSaleStockChangeList": [{
         "productSkuId": 87367614111,
         "preSaleDTO": {"enablePreSale": true, "preSaleDeliveryDay": 1796140799999},
         "preSaleStockList": [
            {"productSkuId":87367614111,"warehouseId":"WH-04940925214853163",
             "targetPreSaleStockAvailable":74,"currentPreSaleStockAvailable":null,"currentShippingMode":1},
            {"productSkuId":87367614111,"warehouseId":"WH-09107063234693163",
             "targetPreSaleStockAvailable":0,"currentPreSaleStockAvailable":null,"currentShippingMode":1}
         ]}],
      "isCheckVersion": true, "source": 0
    }

要点（都是这次抓包确认的，别再猜）：
  · **清零和开预售是同一个请求**：skuStockChangeList 里 stockDiff=-当前 就是减到 0
  · `preSaleDeliveryDay` 是「补货日」当天 23:59:59.999 的毫秒时间戳（UTC+8）；
    回读 `deliveryDayFromNowMaxDay=7` ⇒ 平台只放行今天 ~ 今天+7，默认取今天+7
  · **预售量填哪个仓 = 这票头程的归属仓**（用户 2026-11-24 口径）：
      account=14450（帕沃尔/乐航）→ 填「加州三仓」行，恒信行填 0
      account=LM739（恒信领星）  → 填「恒信加州3」行，加州三仓行填 0
  · **两个仓都要清零**才能开预售
  · `currentPreSaleStockAvailable` 首次开填 null

⚠ 两个真机坑（2026-11-24 实测）：
  1. **`success:true` 不代表成功**。预售被拒时顶层仍是 success=true，
     真正的拒绝在 `result.failEnablePreSaleSkuList[]`：
     `errorCode 2000079 = 该SKU在近7天内关闭过「在途库存预售」功能，不支持预售`（7 天锁）。
  2. **被拒时库存清零也一起不生效**（整个请求回滚）。⇒ 撞到 7 天锁时，
     要单独用 `--zero-only` 再补一次清零（该请求不含预售段，不会被拒）。

⚠ 预售量**已经开着**之后想换仓（把量从 A 仓挪到 B 仓）—— 做不到：
  · 不带库存段 → `2000070 库存已发生变更，请刷新后重试`
  · 带 stockDiff=0 占位 → `2000000 库存变更数量不能为0`
  · 刷新页面后重试 → 依旧 2000070
  ⇒ 平台只在「库存真的发生变更」的那一刻接受预售量写入。
    已经清零的尺码要把预售量换仓，只能等**下次有库存变动时**一起改，
    或者关掉预售重开（会触发 7 天锁，得不偿失）。
    首次开预售时务必按归属仓填对（`--wh hx|ca3`）。

仓 ↔ 货源对应（用户 2026-11-24）：
  加州三仓   = 帕沃尔库存（头程 account=14450 / 乐航）
  恒信加州3  = 恒信领星库存（头程 account=LM739）
  ⇒ 这票在途属于哪个货源，预售量就填对应仓。

用法：
    # 只看（计划）
    python scripts/goods/api_presale.py --port 50748 --store BHY --code BHY-102503-5SetB --size XL --qty 74
    # 真写
    python scripts/goods/api_presale.py --port 50748 --store BHY --code BHY-102503-5SetB --size XL --qty 74 --apply
    # 只清零不开预售
    python scripts/goods/api_presale.py --port 50748 --store BHY --code BHY-103806-6SetA --size XXL --zero-only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402

LOGS = ROOT / "_logs"
LEDGER = LOGS / "api_presale.jsonl"
TZ = timezone(timedelta(hours=8))


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:500], flush=True)


def delivery_ts(day: str | None = None) -> int:
    """补货日 → 当天 23:59:59.999 (UTC+8) 的毫秒时间戳。

    抓包 + 回读双重确认：用户在日历选「最后可点格子」，落到后端是 1796140799999
    = 2026-12-01 23:59:59.999 +08:00；而回读该 SKU 得 `deliveryDayFromNowMaxDay = 7`，
    即**平台只放行今天 ~ 今天+7**。⇒ 不指定 day 时取「今天+7」当天末毫秒。
    """
    if day:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=TZ)
    else:
        d = (datetime.now(TZ) + timedelta(days=7))
    end = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(end.timestamp() * 1000)


def find_hx_wh(rows: list) -> tuple:
    """从 queryBtgProductStockInfo 拉平结果里挑 恒信仓 / 帕沃尔加州三仓 / 其余仓。

    真实仓名与 id（2026-11-24 实测，BHY-102503-5SetB）：
        恒信加州3  → WH-04940925214853163
        加州三仓   → WH-09107063234693163
    ⚠ 匹配必须用**完整仓名**：`恒信加州3` 里既含「恒信」也含「加州」，
      用 `"加州" in wh` 之类的松匹配会把恒信仓认成加州三仓（踩过）。
    """
    hx, ca3, others = None, None, []
    for x in rows:
        wh = x.get("wh") or ""
        if "恒信" in wh:
            if hx is None or int(x["stock"]) > int(hx["stock"]):
                hx = x
        elif wh.strip() in ("加州三仓", "加州3仓") or ("加州三" in wh):
            if ca3 is None or int(x["stock"]) > int(ca3["stock"]):
                ca3 = x
        elif x.get("whid"):
            others.append(x)
    return hx, ca3, others


def wh_for(x: dict) -> str:
    """这票头程归属哪个仓（用户 2026-11-24：按头程跟踪归属决定预售量填哪行）。

    account 来自 plan_v4：14450 = 帕沃尔/乐航 → 加州三仓；LM739 = 恒信领星 → 恒信加州3。
    """
    a = str(x.get("account") or "").strip().upper()
    if a in ("14450", "PAWOER", "LH"):
        return "ca3"
    if a in ("LM739", "HX", "恒信"):
        return "hx"
    return ""


def pick(rows: list, size: str) -> dict:
    got = {}
    for x in rows:
        got.setdefault(x["wh"], int(x["stock"]))
    return got


async def do_code(cap, hdr, code: str, size: str, qty: int, zero_only: bool,
                  day: str | None, apply: bool, wh_side: str = "") -> str:
    size = size.upper()
    q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
    if q.get("status") != 200:
        log({"event": "skc-fail", "code": code, "status": q.get("status")})
        return "no-header" if q.get("status") == 403 else "fail"
    items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
    if not items:
        log({"event": "skc-empty", "code": code})
        return "fail"
    it = items[0]
    spu = int(it.get("productId"))
    sku_size = {}
    for s in it.get("productSkuSummaries") or []:
        ext = str(s.get("extCode") or "")
        sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
        if s.get("productSkuId") and sz:
            sku_size[int(s["productSkuId"])] = sz
    want = [k for k, v in sku_size.items() if v == size]
    if not want:
        log({"event": "size-missing", "code": code, "size": size, "have": sorted(set(sku_size.values()))})
        return "fail"
    sku_id = want[0]

    r = await HX.call(cap, HX.URL_QUERY, {"productId": spu, "productSkuIdList": sorted(sku_size)}, hdr)
    rows = []
    HX.walk_stock(r.get("json") or {}, rows)
    mine = [x for x in rows if int(x.get("skuId") or 0) == sku_id]
    if not mine:
        log({"event": "stock-empty", "code": code, "size": size})
        return "fail"
    hx, ca3, others = find_hx_wh(mine)
    if not hx:
        log({"event": "no-hx-wh", "code": code, "size": size,
             "whs": [x.get("wh") for x in mine]})
        return "fail"
    if not zero_only and not ca3:
        log({"event": "no-ca3-wh", "code": code, "size": size,
             "whs": [x.get("wh") for x in mine],
             "note": "预售量要填帕沃尔加州三仓行，但这个 SKU 没有该仓"})
        return "fail"

    before = {x["wh"]: int(x["stock"]) for x in mine}
    # ★ 两个仓都清零（用户 2026-11-24：只清一个开不了预售）
    change = []
    for x in [hx, ca3]:
        if not x:
            continue
        c = int(x["stock"])
        if c != 0:
            change.append({"productSkuId": sku_id, "stockDiff": -c, "currentStockAvailable": c,
                           "currentShippingMode": 1, "warehouseId": x["whid"]})

    pre_list = []
    if not zero_only:
        ts = delivery_ts(day)
        # ★ 预售量填到**这票头程的归属仓**，另一仓填 0（用户 2026-11-24）
        target_side = wh_side or "hx"
        target_wh = hx if target_side == "hx" else ca3
        other_wh = ca3 if target_side == "hx" else hx
        if not target_wh:
            log({"event": "no-target-wh", "code": code, "size": size, "side": target_side})
            return "fail"
        pre_list.append({"productSkuId": sku_id, "warehouseId": target_wh["whid"],
                         "targetPreSaleStockAvailable": int(qty),
                         "currentPreSaleStockAvailable": None, "currentShippingMode": 1})
        if other_wh:
            pre_list.append({"productSkuId": sku_id, "warehouseId": other_wh["whid"],
                             "targetPreSaleStockAvailable": 0,
                             "currentPreSaleStockAvailable": None, "currentShippingMode": 1})
        for x in others:
            wh = x.get("wh") or ""
            if ("kansas" in wh.lower()) or ("堪萨斯" in wh):
                pre_list.append({"productSkuId": sku_id, "warehouseId": x["whid"],
                                 "targetPreSaleStockAvailable": 0,
                                 "currentPreSaleStockAvailable": None, "currentShippingMode": 1})

    print("  %s-%s spu=%d sku=%d 两仓清零=%s，预售 %s 填【%s】"
          % (code, size, spu, sku_id,
             [(c["warehouseId"][-6:], c["stockDiff"]) for c in change],
             ("不开" if zero_only else qty),
             ("-" if zero_only else (hx if (wh_side or "hx") == "hx" else ca3)["wh"])), flush=True)
    print("    补货日 %s / 分仓现况 %s"
          % (("-" if zero_only else datetime.fromtimestamp(delivery_ts(day) / 1000, TZ)
              .strftime("%Y-%m-%d %H:%M:%S")), json.dumps(before, ensure_ascii=False)), flush=True)
    if not change and zero_only:
        log({"event": "nothing", "code": code, "size": size, "wh": before})
        return "nothing"
    if not apply:
        return "dry"

    pl = [{"productSkuId": sku_id, "warehouseId": c["warehouseId"]} for c in change]
    if pl:
        pre = await HX.call(cap, HX.URL_CHECK,
                            {"productId": spu, "preCheckSkuWarehouseList": pl}, hdr)
        print("    预检 success=%s" % bool((pre.get("json") or {}).get("success")), flush=True)

    # ⚠ 2026-11-24 实测：`skuStockChangeList` 为空时平台回 2000070
    #   「库存已发生变更，请刷新后重试」。只改预售量（库存已为 0）也必须带一条
    #   库存变更记录 ⇒ 补一条 stockDiff=0 的占位。
    stock_list = change
    if not stock_list and pre_list:
        stock_list = [{"productSkuId": sku_id, "stockDiff": 0,
                       "currentStockAvailable": 0, "currentShippingMode": 1,
                       "warehouseId": (hx if (wh_side or "hx") == "hx" else ca3)["whid"]}]

    body = {"productId": spu, "skuStockChangeList": stock_list, "skuTypeChangeList": []}
    if pre_list:
        body["skuPreSaleStockChangeList"] = [{
            "productSkuId": sku_id,
            "preSaleDTO": {"enablePreSale": True, "preSaleDeliveryDay": delivery_ts(day)},
            "preSaleStockList": pre_list,
        }]
    body["isCheckVersion"] = True
    body["source"] = 0
    up = await HX.call(cap, HX.URL_UPDATE, body, hdr)
    upj = up.get("json") or {}
    good = bool(upj.get("success"))
    # ⚠ 2026-11-24 实测：预售被拒时 success 仍是 true，真正的拒绝在
    #   result.failEnablePreSaleSkuList[]（errorCode 2000079 = 近7天关闭过预售）
    fail_pre = [x for x in (((upj.get("result") or {}).get("failEnablePreSaleSkuList")) or [])
                if int(x.get("productSkuId") or 0) == sku_id]
    if fail_pre:
        good = False
        print("    !! 预售被拒 code=%s msg=%s"
              % (fail_pre[0].get("errorCode"), fail_pre[0].get("errorMsg")), flush=True)
    log({"event": "write", "code": code, "size": size, "ok": good, "spu": spu, "sku": sku_id,
         "before": before, "diffs": {c["warehouseId"][-6:]: c["stockDiff"] for c in change},
         "presale": (None if zero_only else qty),
         "delivery": (None if zero_only else delivery_ts(day)),
         "resp": str(up.get("body"))[:160]})

    await asyncio.sleep(0.8)
    r2 = await HX.call(cap, HX.URL_QUERY, {"productId": spu, "productSkuIdList": sorted(sku_size)}, hdr)
    js2 = r2.get("json") or {}
    rows2 = []
    HX.walk_stock(js2, rows2)
    after = {x["wh"]: int(x["stock"]) for x in rows2 if int(x.get("skuId") or 0) == sku_id}
    # ★ 预售状态必须回读核对，不能只看 success。
    #   核对的是**目标仓那一行**的 inTransitPreSaleStock（按 --wh 决定哪行）。
    side_q = wh_side or "hx"
    pre_on, pre_qty, pre_day = None, None, None
    pre_both = {}
    for ps in ((js2.get("result") or {}).get("productStockList") or []):
        if int(ps.get("productSkuId") or 0) != sku_id:
            continue
        pre_on = bool(ps.get("enablePreSale"))
        pre_day = ps.get("preSaleStockDeliveryDay")
        for whs in ps.get("warehouseStockList") or []:
            nm = str((whs.get("warehouseInfo") or {}).get("warehouseName") or "")
            v = whs.get("inTransitPreSaleStock")
            pre_both[nm] = v
            if "恒信" in nm and side_q == "hx":
                pre_qty = v
            elif ("加州三" in nm or "加州3仓" in nm) and side_q == "ca3":
                pre_qty = v
    ok_all = good
    if not zero_only:
        ok_all = bool(good and pre_on and int(pre_qty or 0) == int(qty))
    log({"event": "verify", "code": code, "size": size, "ok": ok_all,
         "after": after, "enablePreSale": pre_on, "preSaleQty": pre_qty,
         "preSaleByWh": pre_both,
         "preSaleDay": pre_day})
    if ok_all:
        print("    [OK] 回读一致：库存已清零、预售 %s=%s、补货日 %s"
              % (pre_qty, "开" if pre_on else "关", pre_day), flush=True)
    else:
        print("    [BAD] 回读不一致！库存 %s 预售开=%s 量=%s（期望量=%s）"
              % (json.dumps(after, ensure_ascii=False), pre_on, pre_qty, qty), flush=True)
    return "ok" if ok_all else "fail"
    return "ok" if good else "fail"


async def main_async(a):
    import api_switch_mode as api
    hdr = api.load_headers(mallid=a.mallid or "")
    cap = await HX.attach(a.port)
    if cap is None:
        print("!! 没有可用页面")
        return 3
    try:
        try:
            await cap.send("Network.enable", {}, timeout=15)
        except Exception:
            pass
        rc = await do_code(cap, hdr, a.code, a.size, a.qty, a.zero_only, a.day, a.apply,
                           a.wh)
        print("=>", rc)
        return 0 if rc in ("ok", "dry", "nothing") else 1
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", default="")
    ap.add_argument("--mallid", default="")
    ap.add_argument("--code", required=True)
    ap.add_argument("--size", required=True)
    ap.add_argument("--qty", type=int, default=0)
    ap.add_argument("--day", default="", help="补货日 YYYY-MM-DD，默认今天（=最远可点）")
    ap.add_argument("--zero-only", action="store_true", help="只清零，不开预售")
    ap.add_argument("--wh", choices=("hx", "ca3"), default="hx",
                    help="预售量填哪个仓：hx=恒信加州3（LM739 头程），"
                         "ca3=帕沃尔加州三仓（14450 头程）。默认 hx")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not a.zero_only and a.qty <= 0:
        ap.error("开预售必须给 --qty > 0（或加 --zero-only 只清零）")
    raise SystemExit(asyncio.run(main_async(a)))


if __name__ == "__main__":
    main()
