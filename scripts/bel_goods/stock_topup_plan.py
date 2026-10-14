# -*- coding: utf-8 -*-
"""「加库存建议」的算法（**纯计算，不做任何写操作**）。

用户口径（2026-10-13 确认）：后台那个「立即加库存」由人手动点，脚本只负责**算清楚加多少**并推飞书。

    目标可售 = min( round(预计未来7日 ÷ 7 × 目标天数), 代发库存折 TEMU 单位 )
    增加量   = max(0, 目标可售 − TEMU 现有销售可用库存)

为什么上限取「代发库存」而不是「30 天需求」：平台只认你申报的可售库存，申报超过海外仓真实存货
= 发不出货 = 缺货处罚。老口径（30天需求 − 代发库存 − 现有可售）会把「仓里只有 4 件」的款算出 +72，
已被否决。

仓库怎么选（用户确认）：
  ① 优先「可售 > 0」的那一行（= 平台现在就在卖的那个仓）
  ② 全为 0 时，优先与海外仓看板**同名且该仓有货**的仓
  ③ 再不行取第一行

另外：**「合作仓对接发货」的款没有「立即加库存」入口**（库存由合作仓推给平台），只会出现在
说明里提醒你去催合作仓，不给增加量。

用法（只看建议）：
    python stock_topup_plan.py            # 读 presale.json 打印建议
    python stock_topup_plan.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
PRESALE_JSON = ROOT / "temu-admin" / "public" / "data" / "presale.json"
CFG_JSON = ROOT / "scripts" / "power" / "data" / "temu_presale.json"
CONV_JSON = ROOT / "scripts" / "power" / "data" / "temu_stock_topup.json"

DEFAULT_DAYS = 30          # 目标覆盖天数
MAX_PER_ITEM = 2000        # 单个尺码一次最多建议加多少（防手滑）


def decide_increase(available, forecast7, stock_temu, days: int = DEFAULT_DAYS):
    """返回 (增加量, 目标可售)。available / forecast7 / stock_temu 都是 TEMU 单位。"""
    if available is None:
        available = 0
    try:
        f7 = float(forecast7 or 0)
        stock = float(stock_temu or 0)
    except (TypeError, ValueError):
        return 0, 0
    if f7 <= 0 or stock <= 0:
        return 0, 0
    need = round(f7 / 7.0 * days)
    target = int(min(need, stock))
    inc = target - int(available)
    if inc <= 0:
        return 0, target
    return min(inc, MAX_PER_ITEM), target


def pick_row(rows: list, panel_stock: dict | None = None) -> int:
    """挑建议填哪个发货仓。rows = [{'warehouse': str, 'available': number}]。"""
    if not rows:
        return -1
    sold = [(i, r) for i, r in enumerate(rows) if (r.get("available") or 0) > 0]
    if sold:
        return max(sold, key=lambda x: x[1].get("available") or 0)[0]
    if panel_stock:
        for i, r in enumerate(rows):
            wh = (r.get("warehouse") or "").replace("帕沃尔·", "").replace("仓", "").strip()
            if not wh:
                continue
            for name, qty in panel_stock.items():
                key = name.replace("帕沃尔·", "").replace("仓", "").strip()
                if qty > 0 and (wh in key or key in wh):
                    return i
    return 0


def _why(row, stock_temu, target, available) -> str:
    if (row.get("forecast7") or 0) <= 0:
        return "预计未来 7 日无销量"
    if (stock_temu or 0) <= 0:
        return "海外仓没有现货"
    if target <= (available or 0):
        return "仓里现货折 TEMU %s ≤ 现有可售 %s → 已经全申报了，加了会超卖" % (
            _n(stock_temu), _n(available))
    return "算出来为 0"


def _n(v):
    if v is None:
        return "—"
    return str(int(v)) if float(v).is_integer() else "%.1f" % v


def build_plan(rows: list, probe_rows: list, cfg: dict, only: str = "", store: str = "") -> list:
    """算建议。rows 需要：store(可省)/skuCode/status/shipMode/available/forecast7/probeStock/probeStockRaw/probeSku。"""
    days = int((cfg.get("加库存") or {}).get("目标覆盖天数") or DEFAULT_DAYS)
    by_sku: dict[str, dict] = {}
    for r in probe_rows or []:
        key = r.get("小款号")
        if not key:
            continue
        by_sku.setdefault(key, {})
        wh = r.get("仓库名称") or ""
        by_sku[key][wh] = by_sku[key].get(wh, 0) + (r.get("代发库存") or 0)

    plan = []
    for row in rows:
        sku = row.get("skuCode") or ""
        # ⚠ 2026-10-14 修：原先只收 actions 含「加库存」的行，导致 presale.topup
        #   恒为 0/空 —— presale 的输入只有「已售罄/即将售罄」那几行，它们的
        #   forecast7 多为 0，decide_increase() 里 f7<=0 直接返回 0，于是永远算不出量。
        #   改为**对所有行都算**（decide_increase 自己会用 f7/stock/available 过滤），
        #   这样「海外仓可补」才是全站口径，而不是只覆盖几个售罄款。
        if only and only not in sku:
            continue
        stock_temu = row.get("probeStock") or 0
        ship = row.get("shipMode") or ""
        wh_map = by_sku.get(row.get("probeSku") or "", {})
        inc, target = decide_increase(row.get("available"), row.get("forecast7"),
                                      stock_temu, days)
        reason = ""
        if inc <= 0:
            reason = _why(row, stock_temu, target, row.get("available"))
        elif ship and "合作仓" in ship:
            reason = "「%s」→ 后台没有「立即加库存」入口（库存由合作仓推给平台，要催合作仓）" % ship
            inc = 0
        # 建议填哪个仓（只在有增加量时才有意义）
        wh_suggest = ""
        if inc > 0:
            rows_wh = [{"warehouse": k, "available": 0} for k in wh_map]
            # 抽屉里才知道各仓可售，这里只能按看板库存给建议：优先有货的仓
            if wh_map:
                wh_suggest = max(wh_map.items(), key=lambda kv: kv[1])[0]
        plan.append({
            "store": row.get("store") or store, "skuCode": sku, "skuId": row.get("skuId"),
            "size": row.get("size"), "status": row.get("status"), "shipMode": ship,
            "available": row.get("available"), "forecast7": row.get("forecast7"),
            "availDays": row.get("availDays"), "stockRaw": row.get("probeStockRaw"),
            "stockTemu": stock_temu, "target": target, "increase": inc, "days": days,
            "warehouseSuggest": wh_suggest, "warehouses": wh_map, "skip": reason,
        })
    return plan


def attach(rows: list, probe_rows: list, cfg: dict, store: str = "") -> dict:
    """把建议挂回每一行（就地加 row['topup']），并返回该店的合计。供导出器调用。"""
    plan = {p["skuCode"]: p for p in build_plan(rows, probe_rows, cfg, store=store)}
    total = 0
    for r in rows:
        p = plan.get(r.get("skuCode"))
        if not p:
            continue
        r["topup"] = {"increase": p["increase"], "target": p["target"],
                      "days": p["days"], "warehouse": p["warehouseSuggest"],
                      "skip": p["skip"]}
        total += p["increase"]
    return {"days": (cfg.get("加库存") or {}).get("目标覆盖天数") or DEFAULT_DAYS,
            "total": total, "items": [p for p in plan.values() if p["increase"] > 0]}


def _probe_rows_from_cache() -> list:
    """从看板快照缓存取原始行（带各仓库库存），失败就返回空 —— 只影响「建议填哪个仓」这一栏。"""
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
        import temu_stock_topup as T
        cfg = json.loads(CONV_JSON.read_text(encoding="utf-8-sig"))
        snap = T.fetch_snapshot(cfg, force=False, log=lambda m: None)
        return snap.get("rows") or []
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description="加库存建议（只算不写）")
    ap.add_argument("--only", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    presale = json.loads(PRESALE_JSON.read_text(encoding="utf-8-sig"))
    cfg = json.loads(CFG_JSON.read_text(encoding="utf-8-sig"))
    rows = [dict(r, store=s.get("store")) for s in presale.get("stores") or []
            for r in s.get("rows") or []]
    plan = build_plan(rows, _probe_rows_from_cache(), cfg, a.only)
    if a.json:
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0
    print("加库存建议（目标覆盖 %s 天，上限 = 海外仓现货）："
          % ((cfg.get("加库存") or {}).get("目标覆盖天数") or DEFAULT_DAYS))
    for p in plan:
        if p["increase"] > 0:
            print("  ▲ %-22s %-6s 可售 %s → %s（+%s）｜仓里 %s 件｜建议填 %s"
                  % (p["skuCode"], p["status"], _n(p["available"]), p["target"],
                     p["increase"], _n(p["stockRaw"]), p["warehouseSuggest"] or "?"))
        else:
            print("  · %-22s %-6s 不加：%s" % (p["skuCode"], p["status"], p["skip"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
