# -*- coding: utf-8 -*-
"""把「≤7 天预售窗口」判定接到 presale 数据里。

对每条 TEMU 行（一个 SKU）做三件事：
  ① 从看板聚合里取该尺码的 `近7天日均` / `代发库存`；
  ② 从 linxing-inbound 的 `map` 取该尺码在途的入库单号列表；
  ③ 用 shipment-track 的到架日算出「7 天内能不能接上」。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

API = Path(r"D:\AI-workplace\TEMU\temu-admin\public\data")


def load_track() -> dict:
    """{入库单号: shipment-track 行}（读不到就空，判定退回领星 expected）。"""
    try:
        d = json.loads((API / "shipment-track.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {str(r.get("inboundNo") or ""): r for r in (d.get("rows") or []) if r.get("inboundNo")}


def load_inbound_map() -> dict:
    """{`产品|变体|尺码`: [入库单]}（只含还没到齐的单）。"""
    try:
        d = json.loads((API / "linxing-inbound.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return d.get("map") or {}


def key_of(product: str, variant: str, size: str) -> str:
    return "%s|%s|%s" % (product, variant, size)


def load_total_stock() -> dict:
    """`款号|套系|尺码` → 总库存（恒信可用 + 帕沃尔加州3 + 帕沃尔Kansas，原始件数）。

    与「库存跟踪 → 领星总库存」那列同一个口径（用户 2026-11-05 明确）。
    两份文件任何一份读不到都只影响对应加数，不阻断。
    """
    out: dict = {}

    def add(key: str, field: str, qty: float) -> None:
        if not key:
            return
        cur = out.setdefault(key, {"avail": 0.0, "ca3": 0.0, "ks": 0.0})
        cur[field] += float(qty or 0)

    try:
        lx = json.loads((API / "linxing-stock.json").read_text(encoding="utf-8"))
        for it in lx.get("items") or []:
            add(key_of(str(it.get("code") or ""), str(it.get("variant") or ""),
                       str(it.get("size") or "")), "avail", it.get("avail"))
    except Exception:  # noqa: BLE001
        pass
    try:
        pwm = json.loads((API / "pw-stock.json").read_text(encoding="utf-8"))
        for it in pwm.get("items") or []:
            wh = str(it.get("wh") or "")
            f = "ca3" if "加州" in wh else ("ks" if "ansas" in wh else "")
            if not f:
                continue
            add(key_of(str(it.get("code") or ""), str(it.get("variant") or ""),
                       str(it.get("size") or "")), f, it.get("stock"))
    except Exception:  # noqa: BLE001
        pass
    for v in out.values():
        v["total"] = v["avail"] + v["ca3"] + v["ks"]
    return out


def load_stock_track_transit() -> dict:
    """`款号|套系|尺码` → 库存跟踪（十部）那列的**在途**。

    用户 2026-11-05：「销量分析的在途 =（库存跟踪的在途 + 帕沃尔在途）」。
    这份就是库存跟踪页显示的 `transit`（九部看板 whTransit + 领星），
    与页面同源，保证两边数字一致。
    """
    out: dict = {}
    try:
        d = json.loads((API / "stock-track.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    src = (d.get("sources") or {}).get("ten") or {}
    for st in src.get("styles") or []:
        for v in st.get("variants") or []:
            for r in v.get("rows") or []:
                q = float(r.get("transit") or 0)
                if q <= 0:
                    continue
                key = key_of(str(r.get("code") or ""), str(r.get("variant") or ""),
                             str(r.get("size") or ""))
                if key:
                    out[key] = out.get(key, 0.0) + q
    return out


def load_pw_transit() -> dict:
    """`款号|套系|尺码` → 帕沃尔在途件数（加州3 + Kansas，原始件数）。

    ⚠ 帕沃尔「代发途中」**没有到架日**（明细里只有添加时间），所以只用于扣补货量，
    不计入 ≤7 天预售窗口 —— 见 attach() 里的说明。
    """
    out: dict = {}
    try:
        d = json.loads((API / "pw-stock.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return out
    for it in d.get("items") or []:
        q = float(it.get("transit") or 0)
        if q <= 0:
            continue
        key = key_of(str(it.get("code") or ""), str(it.get("variant") or ""),
                     str(it.get("size") or ""))
        if key:
            out[key] = out.get(key, 0.0) + q
    return out


def attach(rows: list, agg: dict, inbound: dict, track: dict, today: date,
           days: int = 7, window_mod=None, stock_map: dict | None = None) -> dict:
    """给每条 row 补上 `presaleWindow`：**开预售 / 加库存 / TEMU库存过多 / 要补货**。

    ★ 2026-11-05 用户口径：「我说的这两列去我销量分析中已售罄和即将售罄，
      根据这两列数据去开预售还是加库存，还是 TEMU 库存过多的判断」。
      —— 总库存用「恒信可用 + 帕沃尔两仓」；日均用看板近7天日均；
         恒信在途按货件跟踪的到架日判 7 天内能不能上架。
    """
    import presale_window as W  # noqa: PLC0415

    wm = window_mod or W
    stock_map = stock_map if stock_map is not None else load_total_stock()
    pw_transit = load_pw_transit()
    st_transit = load_stock_track_transit()
    opened, restock, too_much = [], [], []
    for r in rows:
        key = r.get("probeSku") or ""            # `202504-5SetA-L` 形态
        parts = key.rsplit("-", 1)
        if len(parts) != 2:
            r["presaleWindow"] = {"verdict": "无数据", "note": "款式键不完整，算不了"}
            continue
        model, size = parts
        prod, _, variant = model.partition("-")
        ikey = key_of(prod, variant, size)

        src = agg.get(key) or {}
        daily_raw = float(src.get("近7天日均") or 0)
        st = stock_map.get(ikey) or {}
        stock_raw = float(st.get("total") or 0)
        f = float(r.get("factor") or 1) or 1.0
        daily, stock = daily_raw / f, stock_raw / f            # 折 TEMU 单位
        pw_t_raw = float(pw_transit.get(ikey) or 0)            # 帕沃尔在途（原始件数）

        orders = inbound.get(ikey) or []
        batches = W.split_inbound(orders, track)
        res = W.judge_window(daily, stock, batches, today, days=days)

        # ★ 2026-11-05 用户口径：在途 = 库存跟踪的在途 + 帕沃尔在途
        #   （原先是飞书覆盖甘特/领星总表，与库存跟踪页对不上）
        st_raw = float(st_transit.get(ikey) or 0)
        transit_all = st_raw + pw_t_raw
        res["stockTrackTransitRaw"] = round(st_raw, 1)
        res["transitTotalRaw"] = round(transit_all, 1)
        res["transitTotal"] = round(transit_all / f, 2)
        if transit_all > 0:
            r["transitRaw"] = round(transit_all, 1)
            r["transit"] = round(transit_all / f, 2)
            r["transitSource"] = "库存跟踪在途 + 帕沃尔在途"
        # 到货日：按这批在途的**入库单号**去货件跟踪取到架日，取最近的一批
        etas = sorted(b["eta"].isoformat() for b in batches
                      if b.get("eta") and b.get("qty"))
        if etas:
            r["arrivalDate"] = etas[0]
            r["arrivalSource"] = "货件跟踪·预计到架"
            try:
                from datetime import date as _d  # noqa: PLC0415
                r["arrivalDays"] = (_d.fromisoformat(etas[0]) - today).days
            except Exception:  # noqa: BLE001
                pass

        # ── 三选一：开预售 / 加库存 / TEMU库存过多
        avail = r.get("available")
        avail_days = r.get("availDays")
        too_much_flag = (avail_days is not None and float(avail_days) >= 30
                         and daily > 0)
        if res["verdict"] == "可开":
            action = "开预售"
            # 开预售要申报的量 = 7 天缺口（折 TEMU 单位）
            res["addQty"] = int(round(res["need7"]))
        elif too_much_flag:
            action = "库存过多"
            res["addQty"] = 0
        elif res["verdict"] in ("现货够",) and daily > 0 and (avail is not None):
            # 仓里有货且撑得过 7 天，但 TEMU 申报的可售撑不到阈值 → 把申报补上
            cap = max(0.0, stock - float(avail))
            want = max(0.0, daily * 30 - float(avail))
            action = "加库存" if (cap > 0 and want > 0) else "不用动"
            res["addQty"] = int(round(min(want, cap)))
        elif res["verdict"] == "量不够":
            action = "加库存" if stock > 0 else "要补货"
            cap = max(0.0, stock - float(avail or 0))
            want = max(0.0, daily * 30 - float(avail or 0))
            res["addQty"] = int(round(min(want, cap))) if cap > 0 else 0
        else:
            action = "要补货"
            res["addQty"] = 0

        res.update({
            "action": action,
            "dailyRaw": round(daily_raw, 2), "daily": round(daily, 2),
            "stockRaw": round(stock_raw, 1), "stock": round(stock, 1),
            "factor": f, "days": days,
            # 补货量扣掉帕沃尔在途（在途迟早进仓，算供给；与「海外仓库存=现货+在途」一致）
            "refill30": int(max(0, round(daily_raw * 30 - stock_raw - pw_t_raw))),
            "pwTransitRaw": round(pw_t_raw, 1),
            # 帕沃尔在途没有到架日 → 只显示、不进 7 天窗口（开错预售＝平台超卖）
            "pwTransitNote": ("帕沃尔在途 %g 件（无到架日，未计入 7 天窗口）" % pw_t_raw)
                             if pw_t_raw > 0 else "",
            # 这批在途对应哪几张入库单（点「到货」进货件跟踪核对）
            "inboundNos": [b.get("no") for b in batches if b.get("no")],
            "inboundQty": round(sum(float(b.get("qty") or 0) for b in batches), 1),
            "transitNote": "在途＝领星 LM739 transportAmount（不是库存跟踪页那个含九部看板的数）",
            "source": "总库存(恒信可用+帕沃尔两仓) + 看板近7天日均 × 货件跟踪到架日",
        })
        r["presaleWindow"] = res
        if action == "开预售":
            opened.append(r.get("skuCode") or key)
        elif action == "加库存":
            restock.append(r.get("skuCode") or key)
        elif action == "库存过多":
            too_much.append(r.get("skuCode") or key)
    return {"days": days, "opened": opened, "count": len(opened),
            "restock": restock, "tooMuch": too_much,
            "note": "≤%d 天预售：总库存=恒信可用+帕沃尔两仓，日均=看板近7天日均，"
                    "到架日=货件跟踪；另判加库存 / TEMU库存过多（可售≥30天）" % days}


