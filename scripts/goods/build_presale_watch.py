# -*- coding: utf-8 -*-
"""构建「修改库存 · 预售」面板数据 → public/data/presale-watch.json
  
用户 2026-11-09 需求：TEMU 运营下加子节点「修改库存-预售」，分两个页签：
  · 预售中   —— 正在预售的「款号-尺码」，带上 SPU、补货日、在途量、开预售时间
  · 已过预售 —— 开到第 7 天自动转进来的记录；**同一尺码满 7 天可重新开预售时清掉**
  一级筛选：TEMU / SHEIN

数据来源
--------
1. 开预售台账（本面板自己的持久化）：`data/presale-ledger.json`
     { "items": [ {store, platform, code, size, spu, qty, date, openedAt, clearedAt} ] }
   —— 开预售成功 / 过期都由这里记录，面板只读它 + 现算状态。
2. 在售款明细 goods.json：款号 → SPU / 商品标题 / TEMU 当前库存
3. （可选）库存跟踪 stock-track.json：7 天日均，用来在面板上提示「建议在途量」

状态口径（用户定的规则）
------------------------
  打开满 7 天（openedAt + 7d ≤ now）→ 从「预售中」移到「已过预售」
  已过预售里，同一「店+款号+尺码」的记录若已满 7 天且平台允许重开 → 视为可清除
  （平台规则：每 SKU 7 天内只允许开启一次）

    python scripts/goods/build_presale_watch.py            # 构建
    python scripts/goods/build_presale_watch.py --print    # 顺便打印统计
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "temu-admin" / "public" / "data"
LEDGER = ROOT / "data" / "presale-ledger.json"
#: SHEIN 侧没有预售，只有「清零」：台账来自 shein_stock_sync.record_clear()
CLEAR_LEDGER = ROOT / "data" / "shein-clear-ledger.json"
OUT = DATA / "presale-watch.json"
PRESALE_DAYS = 7

#: SHEIN 的店铺筛选按钮（用户 2026-11-12 指定：BS / BD / BAN / BE）
SHEIN_STORES = ["BS", "BD", "BAN", "BE"]


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except Exception:  # noqa: BLE001
        return default


def load_ledger() -> list:
    led = _load(LEDGER, {"items": []})
    return list(led.get("items") or [])


def goods_index() -> tuple[dict, dict]:
    """(store, code) → item；以及 (store, code) → {size: stock}"""
    g = _load(DATA / "goods.json", {})
    idx, sizes = {}, {}
    for st, s in (g.get("stores") or {}).items():
        for it in s.get("items") or []:
            idx[(st, it.get("code"))] = it
            m = {}
            for sku in it.get("skus") or []:
                m[sku.get("size")] = sku.get("stock")
            sizes[(st, it.get("code"))] = m
    return idx, sizes


def daily_index() -> dict:
    t = _load(DATA / "stock-track.json", {})
    out = {}
    for src in (t.get("sources") or {}).values():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    out[("%s-%s" % (st["code"], v["variant"]), r["size"])] = float(r.get("daily") or 0)
    return out


def prune_ledger() -> int:
    """把「开预售满 7 天」的记录从台账里清掉（用户 2026-11-10：已过预售超过 7 天的自动清理）。

    口径：openedAt + 7 天 ≤ 现在 ⇒ 从台账删除。平台上该 SKU 的预售早已到期，
    面板「已过预售」只看最近 7 天内的；更早的不再保留。
    """
    led = _load(LEDGER, {"items": []})
    items = list(led.get("items") or [])
    now = datetime.now()
    keep, drop = [], []
    for x in items:
        t = parse_dt(x.get("openedAt") or "")
        if t is not None and (now - t) >= timedelta(days=PRESALE_DAYS):
            drop.append(x)
        else:
            keep.append(x)
    if drop:
        LEDGER.write_text(json.dumps({"items": keep}, ensure_ascii=False, indent=1), encoding="utf-8")
        for x in drop:
            print("  清理：%s %s %s（开于 %s）" % (x.get("store"), x.get("code"), x.get("size"), x.get("openedAt")))
    return len(drop)


def parse_dt(s: str):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s)[:19], f)
        except Exception:  # noqa: BLE001
            continue
    return None


def clear_items() -> list:
    """SHEIN「清零」记录（用户 2026-11-12：SHEIN 没有预售，只有清零）。

    来源 data/shein-clear-ledger.json —— 由 run_shein_stock.py --apply 在
    「可售<=1 天 → OVERWRITE 0」成功后写入（带 SPU/仓/日均/清零前后数量）。
    """
    led = _load(CLEAR_LEDGER, {"items": []})
    out = []
    for r in led.get("items") or []:
        out.append({
            "platform": "SHEIN",
            "store": r.get("store"),
            "code": r.get("code") or "",
            "variant": r.get("variant") or "",
            "size": r.get("size") or "",
            # SHEIN 没有 SPU 概念，用商家SKU 顶上，搜索框还能按它搜
            "spu": r.get("seller") or "",
            "title": "",
            "category": "",
            "qty": 0,
            "date": "",
            "openedAt": r.get("clearedAt") or "",
            "ageDays": None,
            "status": "已清零",
            "clearable": False,
            "temuStock": 0,
            "daily": float(r.get("daily") or 0),
            "suggestQty": 0,
            # —— 清零专用字段 ——
            "seller": r.get("seller") or "",
            "sku": r.get("sku") or "",
            "wh": r.get("wh") or "",
            "before": r.get("before"),
            "total": r.get("total"),
            "days": r.get("days"),
            "clearedAt": r.get("clearedAt") or "",
            "firstClearedAt": r.get("firstClearedAt") or r.get("clearedAt") or "",
            "times": r.get("times") or 1,
            "confirmed": bool(r.get("confirmed")),
        })
    return out


def build() -> dict:
    now = datetime.now()
    gidx, gsizes = goods_index()
    dmap = daily_index()
    items = []
    for r in load_ledger():
        opened = parse_dt(r.get("openedAt") or "")
        # ⚠ 2026-11-12 用户反馈「已开天数不随天数累加」：原来用 (now - opened).days 是
        #   **24 小时向下取整**（11-10 17:36 开到 11-12 13:02 只算 1 天），面板看着不涨。
        #   改成**自然日差**（今天日期 − 开预售日期）：11-10 开 → 11-12 显示 2 天。
        #   「已过预售 / 可清除」仍按 7×24 小时精确算（平台就是按满 7 天锁的），
        #   所以 11-17 00:00 会显示 7 天，但要到 17:36 才转「已过预售」。
        age = (now.date() - opened.date()).days if opened else None
        code = r.get("code") or ""
        base = code.split("-", 1)[1] if "-" in code else code
        it = gidx.get((r.get("store"), code)) or {}
        cur = (gsizes.get((r.get("store"), code)) or {}).get(r.get("size"))
        daily = dmap.get((base, r.get("size"))) or 0
        # 已过预售 = 开满 7 天；超过 7 天的会在 prune_ledger 里被自动清掉，这里只看 7~14 天
        expired = opened is not None and (now - opened) >= timedelta(days=PRESALE_DAYS)
        items.append({
            "platform": (r.get("platform") or "TEMU").upper(),
            "store": r.get("store"),
            "code": code,
            "size": r.get("size"),
            "spu": r.get("spu") or it.get("spuId"),
            "title": it.get("title") or "",
            "category": it.get("category") or "",
            "qty": r.get("qty"),
            "date": r.get("date") or "",
            "openedAt": r.get("openedAt") or "",
            "ageDays": age,
            "status": "已过预售" if expired else "预售中",
            # 已过预售里「满 7 天、可重新开预售」⇒ 可以清掉这条
            "clearable": bool(expired),
            "temuStock": cur,
            "daily": daily,
            "suggestQty": (round(daily * 7) if daily > 0 else 20),
        })
    # SHEIN 侧：只有清零记录，没有预售
    items += clear_items()
    items.sort(key=lambda x: (x["platform"], x["status"], x["code"], x.get("variant") or "", x["size"]))
    by = {"预售中": 0, "已过预售": 0, "已清零": 0}
    for x in items:
        by[x["status"]] += 1
    return {
        "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "presaleDays": PRESALE_DAYS,
        "rule": {
            "note": "TEMU：「已开天数」= 今天日期 − 开预售日期（自然日，会天天涨）；"
                    "开预售满 7×24 小时自动进入「已过预售」；同一 SKU 7 天内只允许开启一次，"
                    "满 7 天可重新开预售时该记录可清除。"
                    "SHEIN：半托管没有预售，只有清零（可售<=1 天 → 线上 OVERWRITE 0），"
                    "记录保留最近 60 天，按 店+款+变体+尺码 去重。",
            "openedFrom": str(LEDGER),
            "clearedFrom": str(CLEAR_LEDGER),
        },
        "counts": by,
        "platforms": ["TEMU", "SHEIN"],
        "stores": sorted({x["store"] for x in items if x.get("store")}),
        "sheinStores": SHEIN_STORES,
        "items": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", nargs=6, metavar=("STORE", "CODE", "SIZE", "SPU", "QTY", "DATE"),
                    help="把一次开预售记进台账：店 款号 尺码 SPU 在途量 补货日期")
    ap.add_argument("--opened-at", default="", help="开预售时间（默认现在）")
    ap.add_argument("--clear", nargs=3, metavar=("STORE", "CODE", "SIZE"),
                    help="把台账里已过预售、可重开的那条清掉")
    ap.add_argument("--print", action="store_true", dest="do_print")
    a = ap.parse_args()
    led = _load(LEDGER, {"items": []})
    items = list(led.get("items") or [])
    if a.append:
        store, code, size, spu, qty, dt = a.append
        items = [x for x in items if not (x.get("store") == store and x.get("code") == code and x.get("size") == size)]
        items.append({"platform": "TEMU", "store": store, "code": code, "size": size, "spu": spu,
                      "qty": int(qty or 0), "date": dt or "",
                      "openedAt": a.opened_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        print("台账 +1：", store, code, size)
    if a.clear:
        store, code, size = a.clear
        before = len(items)
        items = [x for x in items if not (x.get("store") == store and x.get("code") == code and x.get("size") == size)]
        print("台账 清除 %d 条：%s %s %s" % (before - len(items), store, code, size))
    if a.append or a.clear:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    removed = prune_ledger()
    if removed:
        print("自动清理已过期（>7 天）记录 %d 条" % removed)
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入:", OUT, "| 预售中 %d / 已过预售 %d / 已清零 %d（SHEIN）" % (
        data["counts"]["预售中"], data["counts"]["已过预售"], data["counts"]["已清零"]))
    if a.do_print:
        for x in data["items"]:
            print("  %-5s %-3s %-22s %-8s %-4s SPU %-12s %-19s %s"
                  % (x["platform"], x["store"], x["code"], x.get("variant") or "",
                     x["size"], x["spu"], x["openedAt"], x["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
