# -*- coding: utf-8 -*-
"""把飞书「TEMU二组 Bottom 代发库存销量」导成前端静态 JSON。

产物：temu-admin/public/data/stale-stock.json
页面：/product/stale-stock（产品-库存 → 滞销库存）

数据源（都在同一个飞书 Base 里）：
  主表 `Bottom代发库存销量`  —— 26 款的**当前值**（库存/销量/健康度）
  `每日款明细`              —— 每款每天一行，取近 N 天做趋势
  `每日快照`                —— 每天一行汇总，做总量趋势

口径（与 temu-bottom-stock-sync 的通知/报表保持一致，别另起一套）：
  · 库存 = 领星 OMS 加州3仓 + 帕沃尔 Kansas 仓
  · 销量 = 领星 OMS 出库单（近 5 天实测 / 近 3 天）
  · 可售天数 = (代发库存 + 代发途中) / (5天销量 / 5)
  · 有动销 = 5天销量 > 0；无动销 = 5天销量 = 0
  · 希音款（数据状态 = 未找到SKU / 明春夏季发半托管 / SO在售）**单独一档**，
    不能算「滞销」—— 它们在帕沃尔/领星里根本不存在，是抓不到数据而非卖不动。

用法：
  python build_stale_stock_data.py              # 全量导出
  python build_stale_stock_data.py --dry-run    # 只打印统计，不落盘
  python build_stale_stock_data.py --days 30    # 趋势取近 30 天
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(r"D:\AI-workplace\TEMU")
API_DIR = ROOT / "temu-admin" / "public" / "data"
OUT_NAME = "stale-stock.json"

sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
import feishu_bitable as FB  # noqa: E402

BASE = "TdicbBul0a8pGUs3Hxqc427rngg"
TBL_MAIN = "tblmPk4RyzBbGlkb"       # Bottom代发库存销量
TBL_DETAIL = "tblv6HHuCWtOKN6l"     # 每日款明细
TBL_SNAP = "tblw91H2fAzJZbzi"       # 每日快照

#: 抓不到数据的款：数据状态命中这些值就算「未匹配」，不算滞销
MISSING_STATUS = {"未找到SKU", "明春夏季发半托管", "SO在售"}

#: 健康度排序权重（列表默认按「压货严重程度」排，积压排最前）
HEALTH_ORDER = {"积压": 0, "无销量": 1, "偏紧": 2, "健康": 3, "严重缺货": 4}


def log(msg: str) -> None:
    print(msg, flush=True)


def txt(v) -> str:
    """飞书文本字段可能是 [{text: ...}] 数组。"""
    if isinstance(v, list) and v and isinstance(v[0], dict):
        return str(v[0].get("text") or "").strip()
    return "" if v is None else str(v).strip()


def num(v) -> float:
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def day_of(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d")


def is_missing(status: str, link: str) -> bool:
    if status in MISSING_STATUS:
        return True
    return "希音" in link


#: 帕沃尔导出文件（sync.py 每天 17:00 抓下来的）。
#: Kansas 仓的**分仓库存只在这里**，飞书主表只存了「加州+Kansas」的合计，
#: 所以要看 Kansas 有多少、就必须读这个文件。
PW_EXPORT = Path.home() / ".agents" / "skills" / "temu-bottom-stock-sync" / "stock_latest.xlsx"
#: 帕沃尔的仓库名（导出表「仓库名称」列）。加州那部分已经由领星维护，这里只要 Kansas。
PW_WH_KANSAS = "kansas"


def load_target_cores() -> dict:
    """读 temu-bottom-stock-sync 的 config.json，拿 {链接名称: [产品代号...]}。

    只用来把 Kansas 分仓库存精确对上款 —— 那个 config 是「26 款 ↔ 产品代号」
    的**唯一权威映射**，别在这里再维护一份。
    """
    p = (Path.home() / ".agents" / "skills" / "temu-bottom-stock-sync"
         / "config.json")
    if not p.exists():
        log("  [warn] 找不到 %s —— Kansas 将退回按名字匹配" % p)
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        log("  [warn] 读 config.json 失败（%s）" % str(e)[:80])
        return {}
    out = {}
    for t in cfg.get("targets") or []:
        link = str(t.get("link") or "").strip()
        cores = t.get("cores") or []
        if link and cores:
            out[link] = [str(c) for c in cores]
    return out


def fetch_kansas() -> tuple[dict, str]:
    """从帕沃尔导出里取**Kansas 仓**的逐款库存 → ({产品代号: 件数}, 文件时间)。

    为什么要单独读：
      飞书的「代发库存」= 领星加州 + 帕沃尔 Kansas **合计**，
      看的人分不清哪部分是 Kansas 的（Kansas 的货通常要额外安排发货）。
      这个函数把 Kansas 那部分单独拆出来给页面展示。

    ⚠️ 只取 Kansas，不要取加州 —— 加州库存以领星为准，两边重复算会翻倍。
    """
    if not PW_EXPORT.exists():
        log("  [warn] 找不到帕沃尔导出 %s —— Kansas 列将为空" % PW_EXPORT)
        return {}, ""

    import openpyxl  # noqa: PLC0415

    sys.path.insert(0, str(Path.home() / ".agents" / "skills"
                           / "temu-bottom-stock-sync" / "scripts"))
    try:
        from sync import core_of  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        log("  [warn] 无法复用 sync.core_of（%s），Kansas 列跳过" % str(e)[:80])
        return {}, ""

    wb = openpyxl.load_workbook(PW_EXPORT, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    hdr = [str(h or "").strip().lower() for h in next(rows)]

    def col(*names):
        for n in names:
            if n.lower() in hdr:
                return hdr.index(n.lower())
        return None

    i_sku = col("seller sku", "seller_sku", "sellersku")
    i_wh = col("仓库名称", "warehouse")
    i_df = col("代发库存")
    if None in (i_sku, i_wh, i_df):
        wb.close()
        log("  [warn] 帕沃尔导出缺列（sku/仓库/代发库存），Kansas 列跳过")
        return {}, ""

    out: dict[str, float] = {}
    for r in rows:
        if i_sku >= len(r) or not r[i_sku]:
            continue
        wh = str(r[i_wh] or "").strip().lower()
        if PW_WH_KANSAS not in wh:
            continue
        sku = re.sub(r"^[-]+", "", str(r[i_sku]).strip())
        try:
            c = core_of(sku)
        except Exception:  # noqa: BLE001
            continue
        out[c] = out.get(c, 0.0) + num(r[i_df])
    wb.close()

    stamp = datetime.fromtimestamp(PW_EXPORT.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    log("  Kansas 仓：%d 款，合计 %d 件（导出 %s）"
        % (len(out), int(sum(out.values())), stamp))
    return out, stamp


def fetch_main() -> list[dict]:
    recs = FB.list_records(BASE, TBL_MAIN)
    rows = []
    for fl in recs:
        link = txt(fl.get("链接名称"))
        if not link:
            continue
        status = txt(fl.get("数据状态"))
        d5 = num(fl.get("5天销量"))
        d3 = num(fl.get("3天销量"))
        df = num(fl.get("代发库存"))
        dt = num(fl.get("代发途中"))
        days = fl.get("可售天数")
        days = None if days in (None, "") else num(days)
        health = txt(fl.get("库存健康度")) or "无销量"
        rows.append({
            "link": link,
            "cat": txt(fl.get("款式类别")),
            "stock": round(df, 1),
            "transit": round(dt, 1),
            "pipeline": round(df + dt, 1),
            "sales5": round(d5, 1),
            "sales3": round(d3, 1),
            "days": None if days is None else round(days, 1),
            "health": health,
            "status": status,
            "missing": is_missing(status, link),
            # 有动销 = 5天销量 > 0；日销用于排序/展示
            "active": d5 > 0,
            "daily": round(d5 / 5.0, 2),
        })
    return rows


def fetch_detail(days: int) -> tuple[dict, dict]:
    """返回 (逐款每日趋势, 每日汇总趋势)。"""
    recs = FB.list_records(BASE, TBL_DETAIL)
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()

    per_sku: dict[str, list] = {}
    per_day: dict[str, dict] = {}
    for fl in recs:
        d = fl.get("日期")
        if not isinstance(d, (int, float)):
            continue
        day = day_of(d)
        if day < cutoff:
            continue
        link = txt(fl.get("款号"))
        if not link:
            continue
        stock = num(fl.get("代发库存"))
        s5 = num(fl.get("5天销量"))
        per_sku.setdefault(link, []).append(
            {"d": day, "stock": round(stock, 1), "sales5": round(s5, 1)})

        agg = per_day.setdefault(day, {"stock": 0.0, "sales5": 0.0, "n": 0})
        agg["stock"] += stock
        agg["sales5"] += s5
        agg["n"] += 1

    for k in per_sku:
        per_sku[k].sort(key=lambda r: r["d"])

    trend_day = [
        {"d": d, "stock": round(v["stock"], 1), "sales5": round(v["sales5"], 1),
         "skus": v["n"]}
        for d, v in sorted(per_day.items())
    ]
    return per_sku, trend_day


def fetch_snapshot(days: int) -> list[dict]:
    recs = FB.list_records(BASE, TBL_SNAP)
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    out = []
    for fl in recs:
        d = fl.get("日期")
        if not isinstance(d, (int, float)):
            continue
        day = day_of(d)
        if day < cutoff:
            continue
        out.append({
            "d": day,
            "stock": round(num(fl.get("代发库存")), 1),
            "transit": round(num(fl.get("代发途中")), 1),
            "sales5": round(num(fl.get("5天销量")), 1),
            "sales3": round(num(fl.get("3天销量")), 1),
            "healthy": int(num(fl.get("健康款数"))),
            "overstock": int(num(fl.get("积压款数"))),
            "nosale": int(num(fl.get("无销量款数"))),
        })
    out.sort(key=lambda r: r["d"])
    return out


def _match_kansas(link: str, kansas: dict, cores=None) -> float:
    """把帕沃尔的 Kansas 库存对上飞书的「链接名称」。

    优先用 config 里的 **product cores** 匹配（最准），其次退回按名字/斜杠拆：
      1. cores 全部加起来（一个飞书款可能对应多个产品代号）
      2. 完全同名
      3. 合并款（如 `1040-3BHL/3BHW`）→ 按 `/` 拆开逐个查
      4. 都对不上 → 0（宁可为 0 也不要瞎凑，显示 0 比显示错数好）

    ⚠️ 为什么要用 cores：飞书的 `104504-3B/3BHG` 对应的实物代号是
       `104504-3BHG`（178 件），光按名字拆 `/` 会因为 `104504-3B` 查不到而漏掉。
    """
    if not kansas:
        return 0.0
    if cores:
        hit = [kansas.get(c, 0.0) for c in cores if c in kansas]
        if hit:
            return sum(hit)
    if link in kansas:
        return kansas[link]
    if "/" in link:
        return sum(kansas.get(x.strip(), 0.0) for x in link.split("/"))
    return 0.0


def build(days: int) -> dict:
    log("读飞书主表 ...")
    rows = fetch_main()
    log("  主表 %d 款" % len(rows))

    log("读每日款明细（近 %d 天）..." % days)
    per_sku, trend_day = fetch_detail(days)
    log("  趋势 %d 天，%d 款有历史" % (len(trend_day), len(per_sku)))

    log("读每日快照（近 %d 天）..." % days)
    snap = fetch_snapshot(days)
    log("  快照 %d 天" % len(snap))

    log("读帕沃尔导出取 Kansas 仓库存 ...")
    kansas, kansas_stamp = fetch_kansas()

    real = [r for r in rows if not r["missing"]]
    missing = [r for r in rows if r["missing"]]

    # 把 Kansas 分仓库存挂到每款上（用 config 的 product cores 匹配，最准）
    cores_of = load_target_cores()
    for r in rows:
        r["kansas"] = round(_match_kansas(r["link"], kansas, cores_of.get(r["link"])), 1)

    active = [r for r in real if r["active"]]
    inactive = [r for r in real if not r["active"]]

    # 排序：滞销优先看「压货多」的 → 库存降序；有动销按 5天销量降序
    active.sort(key=lambda r: (-r["sales5"], -r["stock"], r["link"]))
    inactive.sort(key=lambda r: (-r["stock"], -r["transit"], r["link"]))

    for r in rows:
        r["trend"] = per_sku.get(r["link"]) or []

    total_stock = sum(r["stock"] for r in real)
    total_transit = sum(r["transit"] for r in real)
    total_s5 = sum(r["sales5"] for r in real)
    total_kansas = sum(r["kansas"] for r in real)
    inactive_stock = sum(r["stock"] for r in inactive)
    inactive_transit = sum(r["transit"] for r in inactive)
    inactive_kansas = sum(r["kansas"] for r in inactive)

    by_cat: dict[str, dict] = {}
    for r in real:
        c = r["cat"] or "未分类"
        a = by_cat.setdefault(c, {"cat": c, "skus": 0, "stock": 0.0,
                                  "sales5": 0.0, "inactive": 0})
        a["skus"] += 1
        a["stock"] += r["stock"]
        a["sales5"] += r["sales5"]
        if not r["active"]:
            a["inactive"] += 1
    cats = sorted(by_cat.values(), key=lambda x: -x["stock"])
    for c in cats:
        c["stock"] = round(c["stock"], 1)
        c["sales5"] = round(c["sales5"], 1)

    health_counts: dict[str, int] = {}
    for r in real:
        health_counts[r["health"]] = health_counts.get(r["health"], 0) + 1

    return {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "飞书「TEMU二组 Bottom 代发库存销量」",
        "days": days,
        "summary": {
            "skus": len(rows),
            "matched": len(real),
            "missing": len(missing),
            "active": len(active),
            "inactive": len(inactive),
            "stock": round(total_stock, 1),
            "transit": round(total_transit, 1),
            "pipeline": round(total_stock + total_transit, 1),
            "sales5": round(total_s5, 1),
            "sales3": round(sum(r["sales3"] for r in real), 1),
            "daily": round(total_s5 / 5.0, 1),
            "inactiveStock": round(inactive_stock, 1),
            "inactiveTransit": round(inactive_transit, 1),
            "inactivePipeline": round(inactive_stock + inactive_transit, 1),
            # Kansas 仓分仓库存（帕沃尔导出里单独抓，2026-11-02 加）
            "kansas": round(total_kansas, 1),
            "inactiveKansas": round(inactive_kansas, 1),
            "kansasExportAt": kansas_stamp,
            # 无动销款把货全部卖完还要多少天（按有动销款的日均）
            "inactiveClearDays": (
                round((inactive_stock + inactive_transit) / (total_s5 / 5.0), 1)
                if total_s5 > 0 else None
            ),
        },
        "healthCounts": health_counts,
        "active": active,
        "inactive": inactive,
        "missing": missing,
        "categories": cats,
        "trend": trend_day,
        "snapshots": snap,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="趋势取近 N 天（默认 30）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    data = build(a.days)
    s = data["summary"]
    log("")
    log("合计 %d 款（匹配 %d / 未匹配 %d）" % (s["skus"], s["matched"], s["missing"]))
    log("  有动销 %d 款   无动销 %d 款" % (s["active"], s["inactive"]))
    log("  库存 %s  途中 %s  合计 %s" % (s["stock"], s["transit"], s["pipeline"]))
    log("  其中 Kansas 仓 %s 件（无动销款占 %s 件）"
        % (s["kansas"], s["inactiveKansas"]))
    log("  5天销量 %s（日均 %s）" % (s["sales5"], s["daily"]))
    log("  无动销压货 %s 件（含途中），清完需 %s 天"
        % (s["inactivePipeline"], s["inactiveClearDays"]))

    if a.dry_run:
        log("\n[dry-run] 未落盘")
        return 0

    API_DIR.mkdir(parents=True, exist_ok=True)
    out = API_DIR / OUT_NAME
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    log("\n已写出 %s（%.1f KB）" % (out, out.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
