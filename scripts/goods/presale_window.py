# -*- coding: utf-8 -*-
"""预售窗口判定（≤7 天）：用「7 天日均 + 代发库存 + 货件跟踪到架日」算出能不能开预售。

用户 2026-11-05 口径：
    看板的「7天日均」和「代发库存」→ 算出还能撑几天；
    代发途中那一列的入库单号（IB1…）→ 去货件跟踪查这单什么时候到 / 到架；
    两者合起来判断：这 7 天内能不能靠这批在途货接上 → 能就开 7 天预售。

三个数的来源（都不新抓，用现成产物）：
    · 日均 / 代发库存   ← 看板快照 `aggregate_by_sku()`（`近7天日均`、`代发库存`，按小款号=产品-变体-尺码）
    · 入库单号逐尺码归属 ← public/data/linxing-inbound.json 的 `map`（键 `产品|变体|尺码`）
    · 到架日/到港日      ← public/data/shipment-track.json 的 `predictedShelf`（缺失退回 expectedDate）

判定（纯函数，好测）：
    缺口 need7 = max(0, 日均 × 7 − 代发库存)
    · 现货已够 7 天                 → 不用开预售（"现货够"）
    · 7 天内没有到货                → 不能开 ≤7 天预售（"到不了"）
    · 7 天内到货总量 < 缺口         → 只够撑到到货前，仍断档（"量不够"）
    · 7 天内到货总量 ≥ 缺口         → **可以开 ≤7 天预售**（"可开"）
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
    """判定能不能开 ≤`days` 天预售。

    inbound: [{"no": 入库单号, "qty": 件数, "eta": date 或 None, "etaSource": "预计到架"}]
             已按本尺码拆好（件数就是该尺码在这一单里的量）。

    返回：
        verdict  可开 / 现货够 / 到不了 / 量不够 / 无销量
        need7    7 天内要补的量（考虑代发库存后）
        covers   days 天内能到的量合计
        inWindow 落在窗口内的入库单（含各自 eta）
        daysToArrival 最近一批还有几天（None = 窗口内没有）
    """
    daily = float(daily or 0)
    stock = float(stock or 0)
    out = {"verdict": "", "need7": 0.0, "covers": 0.0, "inWindow": [],
           "daysToArrival": None, "arrivalQty": 0.0, "firstEta": "",
           "daysOfStock": None, "note": ""}
    if daily <= 0:
        out.update({"verdict": "无销量", "note": "7 天日均 0，谈不上预售"})
        return out
    out["daysOfStock"] = round(stock / daily, 1)
    need7 = max(0.0, daily * days - stock)
    out["need7"] = round(need7, 1)
    if need7 <= 0:
        out.update({"verdict": "现货够",
                    "note": "代发库存 %g 件够卖 %.1f 天，不用开预售" % (stock, stock / daily)})
        return out

    limit = today + timedelta(days=int(days))
    window = []
    for b in (inbound or []):
        eta = b.get("eta")
        if not isinstance(eta, date):
            continue
        if eta <= limit:
            window.append(b)
    window.sort(key=lambda x: (x["eta"], -float(x.get("qty") or 0)))
    covers = sum(float(b.get("qty") or 0) for b in window)
    out["inWindow"] = [
        {"no": b.get("no") or "", "qty": float(b.get("qty") or 0),
         "eta": b["eta"].isoformat(), "etaSource": b.get("etaSource") or "",
         "days": (b["eta"] - today).days}
        for b in window
    ]
    out["covers"] = round(covers, 1)
    if window:
        first = window[0]
        out["daysToArrival"] = max(0, (first["eta"] - today).days)
        out["arrivalQty"] = round(float(first.get("qty") or 0), 1)
        out["firstEta"] = first["eta"].isoformat()

    if not window:
        out.update({"verdict": "到不了",
                    "note": "现货只够 %.1f 天，%d 天内没有到货" % (stock / daily, days)})
        return out
    if covers < need7:
        out.update({"verdict": "量不够",
                    "note": "现货 %.1f 天 + %d 天内到 %g 件（缺口 %g 件）→ 仍会断档"
                            % (stock / daily, days, covers, need7)})
        return out
    out.update({"verdict": "可开",
                "note": "现货 %.1f 天，%d 天内到 %g 件 ≥ 缺口 %g 件 → 可开 %d 天预售"
                        % (stock / daily, days, covers, need7, days)})
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
