# -*- coding: utf-8 -*-
"""把 Python 抓下来的运营数据导出成前端可直接 fetch 的静态 JSON（data 层）。

产物全部写进 temu-admin/public/data/，Vite 构建时原样拷进 dist，
Cloudflare Pages 直接静态托管，不需要后端。

数据来源：
  data/bhy/bhy_flux_boost_price.json       流量加速尺码申报价（fetch_flux_boost_price.py）
  data/bel/bel_products.db / bhy_products.db  在售商品 + 限流明细
  data/panel_data.json                     看板聚合数据

用法：
  python build_api_data.py            # 全量导出
  python build_api_data.py --flux-only
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
DATA = ROOT / "data"
API_DIR = ROOT / "temu-admin" / "public" / "data"

FLUX_JSON = DATA / "bhy" / "bhy_flux_boost_price.json"
FLUX_CSV = DATA / "bhy" / "bhy_flux_boost_price.csv"

DB = {
    "BEL": (DATA / "bel" / "bel_products.db", "traffic_limited"),
    "BHY": (DATA / "bhy" / "bhy_products.db", "traffic_limited_bhy"),
}

SIZE_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "XXXL", "2XL", "3XL"]


def log(msg: str) -> None:
    print(msg, flush=True)


def money(v) -> float | None:
    """'$27.73' / 27.73 / None -> 27.73"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\$?\s*([\d.]+)", str(v))
    return float(m.group(1)) if m else None


def size_key(s: str) -> int:
    u = (s or "").upper()
    return SIZE_ORDER.index(u) if u in SIZE_ORDER else 99


def write(name: str, payload) -> Path:
    API_DIR.mkdir(parents=True, exist_ok=True)
    path = API_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  {name}  {path.stat().st_size / 1024:.1f} KB")
    return path


# ---------------------------------------------------------------- 流量加速
FLUX_DIR = DATA / "flux"

# 抓取产物：data/flux/<store>_<section>.json（fetch_flux_all.py 生成）
FLUX_SECTIONS = {
    "accelerating": "已开启",
    "pending": "待开启",
}
FLUX_STORE_LABEL = {"BEL": "BEL 店", "BHY": "BHY 店"}

# 前三段是抓取产物；weak 不是独立产物，而是从「已开启」里**拆出来**的：
# 已开启里凡是没有尺码级明细的（页面上只有「查看详情」、没有「查看效果」），
# 说明加速器开关虽开着但没达生效条件（行内文案通常是「流量加速器动力不足, 建议提效」），
# 单独成一个区块，免得在「已开启」里显示成一排「—」被误读成缺数据。
FLUX_SECTION_ORDER = ["accelerating", "weak", "pending"]


def is_weak_accel(rec: dict) -> bool:
    """已开启但**没有**加速明细 —— 判据：没有尺码价。

    页面上这类行只有「查看详情」入口、没有「查看效果」；
    多数带「流量加速器动力不足」文案，少数 optimize 为 `-`
    （2026-10-13 实测 BEL 7+1 个 / BHY 5+2 个）。统一按「有无尺码价」判。
    """
    return not rec.get("skus")


def _flux_accelerating(rec: dict) -> dict:
    """已开启板块：算尺码级价格对比。

    同时保留 optimize / tags / writeActions —— 「动力不足」区块要用它们展示原因。
    """
    skus = sorted(rec.get("skus", []), key=lambda x: size_key(x.get("size")))
    origs = [money(s.get("orig")) for s in skus]
    afters = [money(s.get("after")) for s in skus]
    o = next((x for x in origs if x is not None), None)
    a = next((x for x in afters if x is not None), None)
    drop = round(o - a, 2) if (o is not None and a is not None) else None
    return {
        "spuId": rec.get("spuId"),
        "title": rec.get("title"),
        "category": rec.get("category"),
        "state": rec.get("state"),
        "periodFrom": rec.get("periodFrom"),
        "periodTo": rec.get("periodTo"),
        "sizeCount": len(skus),
        "origPrice": o,
        "afterPrice": a,
        "drop": drop,
        "dropPct": round(drop / o * 100, 1) if (o and drop is not None) else None,
        "skus": [{
            "skc": s.get("skc"),
            "color": s.get("color"),
            "size": s.get("size"),
            "origPrice": money(s.get("orig")),
            "afterPrice": money(s.get("after")),
        } for s in skus],
        # 「动力不足」区块展示用（stats 用于看这些款还有没有在正常出单）
        "tags": rec.get("tags"),
        "optimize": rec.get("optimize"),
        "stats": rec.get("stats") or {},
        "writeActions": rec.get("writeLinksPresent") or [],
    }


def _flux_pending(rec: dict) -> dict:
    """待开启板块：没有尺码价，保留流量/转化指标与优化方式。"""
    return {
        "spuId": rec.get("spuId"),
        "title": rec.get("title"),
        "category": rec.get("category"),
        "site": rec.get("site"),
        "firstListed": rec.get("firstListed"),
        "tags": rec.get("tags"),
        "stats": rec.get("stats") or {},
        "optimize": rec.get("optimize"),
        "hasViewDetail": "查看详情" in (rec.get("readOnlyLinks") or []),
        "writeActions": rec.get("writeLinksPresent") or [],
        "sizeCount": 0,
        "skus": [],
    }


def _legacy_accelerating(store: str) -> dict | None:
    """兼容旧产物：fetch_flux_boost_price.py 写的 <store>_flux_boost_price.json。

    旧格式是**扁平 list**（字段 spu / period_from / period_to / skus[orig,after]），
    新格式是 {store, section, list}。新文件缺失时用它兜底，
    免得分页/登录态问题导致已抓到的数据在面板上消失。
    """
    legacy = DATA / store.lower() / f"{store.lower()}_flux_boost_price.json"
    if not legacy.exists():
        return None
    raw = json.loads(legacy.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return None
    items = []
    for rec in raw:
        items.append(_flux_accelerating({
            "spuId": rec.get("spu"),
            "title": rec.get("title"),
            "state": rec.get("state"),
            "periodFrom": rec.get("period_from"),
            "periodTo": rec.get("period_to"),
            "skus": rec.get("skus", []),
        }))
    return {
        "label": FLUX_SECTIONS["accelerating"],
        "total": len(items),
        "scrapedAt": datetime.fromtimestamp(
            legacy.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "list": items,
        "legacy": True,
    }


def build_flux() -> dict:
    """流量加速总览：两店 × (已开启 / 待开启)。

    每店每板块的抓取产物是 data/flux/<store>_<section>.json，
    这里合并成前端一次就能拿到的结构；缺失时回退到旧格式产物。
    """
    stores: dict[str, dict] = {}
    for store, label in FLUX_STORE_LABEL.items():
        sections = {}
        for section, sec_label in FLUX_SECTIONS.items():
            src = FLUX_DIR / f"{store.lower()}_{section}.json"
            # 「文件存在但 0 条」也要当无数据：抓取跑到了错误页面时会出现这种空壳，
            # 若直接采信就会把上一次的好数据顶掉（2026-10-10 实际踩过）。
            new_items = []
            if src.exists():
                try:
                    new_items = json.loads(src.read_text(encoding="utf-8")).get("list", [])
                except Exception as e:  # noqa: BLE001
                    log(f"  [坏] {src.name} 解析失败：{str(e)[:60]}")
                    new_items = []

            if not new_items:
                fallback = (_legacy_accelerating(store)
                            if section == "accelerating" else None)
                if fallback:
                    why = "为空" if src.exists() else "缺失"
                    log(f"  [回退] {src.name} {why} -> 用旧产物 "
                        f"（{fallback['total']} 个 SPU）")
                    sections[section] = fallback
                    continue
                if src.exists():
                    log(f"  [空] {src.name} 为空且无旧产物可回退")
                else:
                    log(f"  [缺] {src.name}（该板块暂无数据）")
                sections[section] = {"label": sec_label, "total": 0, "priced": 0, "list": []}
                continue
            payload = json.loads(src.read_text(encoding="utf-8"))
            conv = _flux_accelerating if section == "accelerating" else _flux_pending
            items = [conv(r) for r in payload.get("list", [])]

            # 已开启板块：本次没取到尺码价的 SPU，用旧产物（fetch_flux_boost_price.py）
            # 里同一个 SPU 的价回填。TEMU 登录态容易在抓取中途失效，导致后半程
            # 全缺价；有了这层回填就不会把上一次的好数据整体弄丢。
            filled = 0
            if section == "accelerating":
                legacy = _legacy_accelerating(store)
                prev_by = {i["spuId"]: i for i in (legacy or {}).get("list", [])}
                for it in items:
                    if it.get("skus"):
                        continue
                    old = prev_by.get(it["spuId"])
                    if old and old.get("skus"):
                        it["skus"] = old["skus"]
                        it["origPrice"] = old.get("origPrice")
                        it["afterPrice"] = old.get("afterPrice")
                        it["drop"] = old.get("drop")
                        it["dropPct"] = old.get("dropPct")
                        it["sizeCount"] = old.get("sizeCount", 0)
                        it["priceFromPrevRun"] = (legacy or {}).get("scrapedAt")
                        filled += 1
                if filled:
                    log(f"  [回填] {store} 已开启：{filled} 个 SPU 的尺码价取自旧产物")

            if section == "accelerating":
                items.sort(key=lambda x: (x.get("dropPct") is None, -(x.get("dropPct") or 0)))
            else:
                items.sort(key=lambda x: -(x.get("stats", {}).get("exposure") or 0))

            sections[section] = {
                "label": sec_label,
                "total": len(items),
                "priced": sum(1 for i in items if i.get("skus")),
                "backfilled": filled,
                "aborted": bool(payload.get("aborted")),
                "scrapedAt": payload.get("generatedAt"),
                "list": items,
            }

            # 把「已开启」里没有加速明细的拆到独立的「动力不足」区块
            if section == "accelerating":
                weak = [i for i in items if is_weak_accel(i)]
                keep = [i for i in items if not is_weak_accel(i)]
                sections["accelerating"] = dict(sections[section], total=len(keep),
                                                list=keep)
                weak.sort(key=lambda x: -(x.get("stats", {}).get("exposure") or 0))
                sections["weak"] = {
                    "label": "动力不足",
                    "total": len(weak),
                    "priced": 0,
                    "backfilled": 0,
                    "aborted": bool(payload.get("aborted")),
                    "scrapedAt": payload.get("generatedAt"),
                    "list": weak,
                }
                if weak:
                    log(f"  [拆分] {store} 已开启 -> 加速生效 {len(keep)} / "
                        f"动力不足 {len(weak)}")
        stores[store] = {"label": label, "sections": sections}
        # 兜底：accelerating 走了「缺失/为空回退」分支时不会生成 weak，
        # 这里补一个空板块，保证前端三块结构一致、不用到处判空。
        sections.setdefault("weak", {
            "label": "动力不足", "total": 0, "priced": 0,
            "backfilled": 0, "aborted": False, "scrapedAt": None, "list": [],
        })

    total = sum(s["total"] for st in stores.values() for s in st["sections"].values())
    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "TEMU 商家中心 · 商品流量 · 快速筛选「流量加速中 / 流量加速器待开启」",
        "total": total,
        "stores": stores,
    }


# ---------------------------------------------------------------- 限流
FOCUS_JSON = DATA / "focus_watch_latest.json"
TREND_JSON = DATA / "trend_data.json"

STORE_LABEL = {"BEL": "BEL 店", "BHY": "BHY 店"}


def build_limited() -> dict:
    """限流总览：两店限流明细 + 重点 SPU 告警 + 趋势 + 反复限流排行。

    数据源：
      data/<store>/<store>_products.db  表 traffic_limited*（限流明细 + 调价建议）
      data/focus_watch_latest.json      重点 SPU 监控结果
      data/trend_data.json              历史趋势聚合
    """
    stores = {}
    for store, (db, tl) in DB.items():
        if not db.exists():
            log(f"  [跳过] {store}: 找不到 {db}")
            continue
        con = sqlite3.connect(db)
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute(f"""
                SELECT spu_id, sku_code, title, flow_status, declare_price,
                       daily_target_price, activity_target_price, sales7
                FROM {tl}
            """).fetchall()
            items = [{
                "spuId": r["spu_id"],
                "code": r["sku_code"],
                "title": r["title"],
                "flowStatus": r["flow_status"],
                "declarePrice": r["declare_price"],
                "dailyTarget": r["daily_target_price"],
                "activityTarget": r["activity_target_price"],
                "sales7": r["sales7"],
                # 需降价额：申报价 - 建议日常价，越大越该优先处理
                "priceGap": (round(r["declare_price"] - r["daily_target_price"], 2)
                             if (r["declare_price"] is not None
                                 and r["daily_target_price"] is not None) else None),
            } for r in rows]

            flow_dist = {}
            for it in items:
                key = it["flowStatus"] or "未知"
                flow_dist[key] = flow_dist.get(key, 0) + 1

            stores[store] = {
                "label": STORE_LABEL.get(store, store),
                "db": db.name,
                "total": len(items),
                "withDaily": sum(1 for i in items if i["dailyTarget"] is not None),
                "withActivity": sum(1 for i in items if i["activityTarget"] is not None),
                "hasSales": sum(1 for i in items if (i["sales7"] or 0) > 0),
                "flowDist": [{"name": k, "value": v} for k, v in
                             sorted(flow_dist.items(), key=lambda x: -x[1])],
                "items": items,
            }
        finally:
            con.close()

    # 重点 SPU 监控
    watch = []
    if FOCUS_JSON.exists():
        for s in json.loads(FOCUS_JSON.read_text(encoding="utf-8")):
            watch.append({
                "store": s.get("store"),
                "label": STORE_LABEL.get(s.get("store"), s.get("store")),
                "checkedAt": s.get("checked_at"),
                "limitedTotal": s.get("limited_total"),
                "focusTotal": s.get("focus_total"),
                "focusSource": s.get("focus_source"),
                "hitCount": s.get("hit_count"),
                "hits": s.get("hits", []),
                "safe": s.get("safe", []),
            })

    # 历史趋势
    trend = {"days": [], "timeline": [], "recurring": [], "focusTrack": [],
             "resolved": [], "snapshotCount": 0, "firstCheck": None, "lastCheck": None}
    if TREND_JSON.exists():
        t = json.loads(TREND_JSON.read_text(encoding="utf-8"))
        trend = {
            "days": t.get("days", []),
            "timeline": t.get("timeline", []),
            "recurring": t.get("recurring", []),
            "focusTrack": t.get("focus_track", []),
            "resolved": t.get("resolved", []),
            "snapshotCount": t.get("snapshot_count", 0),
            "firstCheck": t.get("first_check"),
            "lastCheck": t.get("last_check"),
        }

    return {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "TEMU 商家中心 · 商品流量视角「待处理商品」+ 重点款监控快照",
        "stores": stores,
        "watch": watch,
        "trend": trend,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--flux-only", action="store_true")
    a = ap.parse_args()

    log(f"导出前端静态数据 -> {API_DIR}")
    write("flux-boost.json", build_flux())
    if not a.flux_only:
        write("limited.json", build_limited())
    log("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())