# -*- coding: utf-8 -*-
"""预售窗口判定（≤7 天）：用「7 天日均 + 总库存 + 货件跟踪到架日」判能不能开预售。

用户 2026-11-06 口径（本版按这句重写）：
    「根据这个尺码的 7 天日均，拿 7 天日均来计算这个尺码的总库存可以卖几天，
      如果卖的天数 ≤ 7 天，去货件跟踪查是否有 7 天内能上架的尺码，如果有即提醒我开预售。」

所以判定只有三步，**不再做「到货量够不够填缺口」的比较**：
    ① 总库存 ÷ 7天日均 = 能卖几天
    ② >7 天        → 不用开预售（"现货够"）
    ③ ≤7 天        → 去货件跟踪看窗口内有没有到架日
                       有 → 提醒开预售（"可开"，到货量只作参考显示）
                       无 → 开不了，先加库存/补货（"到不了"）

三个数的来源（都不新抓，用现成产物）：
    · 7天日均 / 按仓现货 ← 看板快照 `aggregate_by_sku()`（`近7天日均`、`代发库存`）
    · 入库单号逐尺码归属 ← public/data/linxing-inbound.json 的 `map`（键 `产品|变体|尺码`）
    · 到架日             ← public/data/shipment-track.json 的 `predictedShelf`（缺失退回 expectedDate）

★ 单位必须一致：调用方传进来的 daily / stock / 入库单件数**都用同一个单位**
  （attach() 统一用看板原始件数，页面折 TEMU 只用于显示）。
★ 到架日已经过去（逾期没入仓）的单**不计入 7 天窗口**：那批货什么时候能卖是未知数，
  拿它去开预售就是让平台超卖。逾期单单独放在 `overdue` 里给页面提示。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta


def _d(s) -> date | None:
    """`2026-11-21` / `2026-11-21 10:00:00` → date；空/坏值 → None。"""
    t = str(s or "").strip()
    if not t:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(t[:len(fmt) + 2].strip(), fmt).date()
        except Exception:  # noqa: BLE001
            continue
    try:
        return datetime.strptime(t[:10], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


def eta_of(track_row: dict) -> tuple:
    """一张入库单的「到架日」。返回 (date, 口径说明)。

    优先 `predictedShelf`（到港 + 历史上架时效，已经是"货能卖"的日期），
    其次 `arriveDate`（已到港），最后 `expectedDate`（采购方给的大概到货日）。
    """
    if not track_row:
        return None, ""
    for k, label in (("predictedShelf", "预计到架"), ("arriveDate", "已到港"),
                     ("expectedDate", "预计到货")):
        d = _d(track_row.get(k))
        if d:
            return d, label
    return None, ""


def judge_window(daily: float, stock: float, inbound: list, today: date,
                 days: int = 7) -> dict:
    """判定能不能开 ≤`days` 天预售（用户 2026-11-06 口径）。

    daily / stock / inbound[].qty **同一单位**（attach 里统一用看板原始件数）。

    返回：
        verdict     可开 / 现货够 / 到不了 / 无销量
        daysOfStock 总库存能卖几天（= stock ÷ daily）
        need7       days 天需求缺口（同单位，仅参考显示）
        covers      days 天内能上架的量合计（同单位）
        inWindow    落在窗口内的入库单（含各自 eta / 几天后）
        overdue     到架日已过但还没入仓的单（不计入窗口，只提示）
        daysToArrival / firstEta / arrivalQty  最近一批的天数、日期、数量
    """
    daily = float(daily or 0)
    stock = float(stock or 0)
    out = {"verdict": "", "need7": 0.0, "covers": 0.0, "inWindow": [],
           "overdue": [], "overdueQty": 0.0,
           "daysToArrival": None, "arrivalQty": 0.0, "firstEta": "",
           "daysOfStock": None, "note": ""}
    if daily <= 0:
        out.update({"verdict": "无销量", "note": "7 天日均 0，谈不上预售"})
        return out

    days_of_stock = stock / daily
    out["daysOfStock"] = round(days_of_stock, 1)
    out["need7"] = round(max(0.0, daily * days - stock), 1)

    # ① 总库存撑得过 days 天 → 不用开预售
    if days_of_stock > days:
        out.update({"verdict": "现货够",
                    "note": "总库存 %g 件 ÷ 日均 %g = 够卖 %.1f 天（>%d 天）→ 不用开预售"
                            % (stock, daily, days_of_stock, days)})
        return out

    # ② ≤days 天 → 看货件跟踪窗口内有没有能上架的货
    limit = today + timedelta(days=int(days))
    window, overdue = [], []
    for b in (inbound or []):
        eta = b.get("eta")
        if not isinstance(eta, date):
            continue
        if eta < today:                     # 逾期没入仓 → 不算窗口内
            overdue.append(b)
        elif eta <= limit:
            window.append(b)
    window.sort(key=lambda x: (x["eta"], -float(x.get("qty") or 0)))
    overdue.sort(key=lambda x: x["eta"])

    def _brief(b):
        return {"no": b.get("no") or "", "qty": float(b.get("qty") or 0),
                "eta": b["eta"].isoformat(), "etaSource": b.get("etaSource") or "",
                "days": (b["eta"] - today).days}

    out["inWindow"] = [_brief(b) for b in window]
    out["overdue"] = [_brief(b) for b in overdue]
    out["covers"] = round(sum(float(b.get("qty") or 0) for b in window), 1)
    out["overdueQty"] = round(sum(float(b.get("qty") or 0) for b in overdue), 1)
    if window:
        first = window[0]
        out["daysToArrival"] = max(0, (first["eta"] - today).days)
        out["arrivalQty"] = round(float(first.get("qty") or 0), 1)
        out["firstEta"] = first["eta"].isoformat()

    if not window:
        out.update({"verdict": "到不了",
                    "note": "总库存 %g 件够卖 %.1f 天（≤%d 天），但 %d 天内没有能上架的货"
                            % (stock, days_of_stock, days, days)})
        return out
    out.update({"verdict": "可开",
                "note": "总库存 %g 件够卖 %.1f 天（≤%d 天），%d 天内能上架 %g 件（最近 %s）→ 开 %d 天预售"
                        % (stock, days_of_stock, days, days, out["covers"],
                           out["firstEta"], days)})
    return out


def split_inbound(orders: list, track: dict) -> list:
    """把 `linxing-inbound` 里一条 (尺码) 的入库单列表接上货件跟踪的到架日。

    orders: [{"no","qty","inbound","expected","status","wh"}]（qty 已是该尺码的量）
    track:  {入库单号: shipment-track 的行}
    """
    out = []
    for o in orders or []:
        no = str(o.get("no") or "")
        eta, src = eta_of(track.get(no) or {})
        if not eta:                       # 货件跟踪没这单 → 退回领星的 expected
            eta, src = _d(o.get("expected")), "领星预计到货"
        qty = float(o.get("inbound") or o.get("qty") or 0)
        out.append({"no": no, "qty": qty, "eta": eta, "etaSource": src})
    return out
