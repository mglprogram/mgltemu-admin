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
OUT = DATA / "presale-watch.json"
PRESALE_DAYS = 7


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


def parse_dt(s: str):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s)[:19], f)
        except Exception:  # noqa: BLE001
            continue
    return None


def build() -> dict:
    now = datetime.now()
    gidx, gsizes = goods_index()
    dmap = daily_index()
    items = []
    for r in load_ledger():
        opened = parse_dt(r.get("openedAt") or "")
        age = (now - opened).days if opened else None
        code = r.get("code") or ""
        base = code.split("-", 1)[1] if "-" in code else code
        it = gidx.get((r.get("store"), code)) or {}
        cur = (gsizes.get((r.get("store"), code)) or {}).get(r.get("size"))
        daily = dmap.get((base, r.get("size"))) or 0
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
    items.sort(key=lambda x: (x["status"], x["code"], x["size"]))
    by = {"预售中": 0, "已过预售": 0}
    for x in items:
        by[x["status"]] += 1
    return {
        "generatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "presaleDays": PRESALE_DAYS,
        "rule": {
            "note": "开预售满 7 天自动进入「已过预售」；同一 SKU 7 天内只允许开启一次，"
                    "满 7 天可重新开预售时该记录可清除",
            "openedFrom": str(LEDGER),
        },
        "counts": by,
        "platforms": ["TEMU", "SHEIN"],
        "stores": sorted({x["store"] for x in items if x.get("store")}),
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
    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入:", OUT, "| 预售中 %d / 已过预售 %d" % (
        data["counts"]["预售中"], data["counts"]["已过预售"]))
    if a.do_print:
        for x in data["items"]:
            print("  %-5s %-3s %-22s %-4s SPU %-12s 开于 %-19s %s"
                  % (x["platform"], x["store"], x["code"], x["size"], x["spu"], x["openedAt"], x["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
