# -*- coding: utf-8 -*-
"""领星 OMS(WMS) 订单 -> 数据源 采集器。

背景（用户 2026-10-15）
-----------------------
「以后每天晚上 22 点去更新订单；BHY/BEL —— 把这些订单爬取下来，去补充数据源」
页面：https://oms.xlwms.com/platform/order/list

走的不是网页爬取，而是**同一套数据的开放接口**（v1，见 lib/oms_client.py 顶部注释）：
  · list   `/openapi/v1/outboundOrder/pageList`  按 orderCreateTime 切窗口，pageSize≤50
  · detail `/openapi/v1/outboundOrder/detail`    一次≤50 单号，拿到**州/市/邮编/邮编**
           + **产品明细**（sku/productName/quantity）+ 店铺名 + 跟踪号 + 面单 PDF 链接

比帕沃尔（POWER）那条线强在：**一次批量**，而且直接给州-市-邮编和产品，
不用像 POWER 那样逐单 3 万次请求。字段覆盖率实测 200/200 全有。

产物
----
  out/oms/oms_orders.jsonl     一行一单（增量、按出库单号去重、可重复跑）
  temu-admin/public/data/oms-orders.json   给面板用的精简数据（build 子命令）

用法
----
  python scripts/oms/run_oms_orders.py sync                 # 每天跑：拉最近 2 天，增量入库
  python scripts/oms/run_oms_orders.py backfill --start 2026-01-01 --end 2026-10-15
  python scripts/oms/run_oms_orders.py stats                # 看库里有什么
  python scripts/oms/run_oms_orders.py build                # 生成面板数据
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "lib"))

from oms_client import OmsClient, OmsError, PAGE_SIZE_MAX, DETAIL_BATCH_MAX  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "out", "oms")
DEFAULT_ADMIN = os.path.join(ROOT, "temu-admin")

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# 用户点名要的两个店（OMS 里还有 BS / Savonire_US / TK-* 等，默认不收）
WANT_STORES = ("BHY", "BEL")

# 列表接口不返回店铺名，只有详情才有 ⇒ 先按时间窗拉单号，再批量下钻详情
DETAIL_FIELDS = (
    "outboundOrderNo", "whCode", "status", "statusName", "storeName",
    "referOrderNo", "platformOrderNo", "salesPlatform",
    "logisticsChannel", "logisticsCarrier", "logisticsTrackNo", "logisticsTrackNos",
    "countryRegionCode", "provinceCode", "provinceName", "cityName", "postCode",
    "orderCreateTime", "outboundTime", "canceledTime", "exceptionTime", "interceptTime",
    "requestDeliveryTime", "costTotal", "costCurrencyCode", "orderTypeName",
)


def slim_detail(d: dict) -> dict:
    """只留要用的字段 + 产品明细 + 面单链接（原始 JSON 一条 4KB，压到 ~400B）。"""
    out = {k: d.get(k) for k in DETAIL_FIELDS}
    out["products"] = [
        {"sku": p.get("sku"), "name": p.get("productName"),
         "qty": p.get("quantity"), "skuId": p.get("skuId")}
        for p in (d.get("productList") or [])
    ]
    packs = []
    for e in (d.get("expressList") or []):
        packs.append({"mark": e.get("packMark"), "trackNo": e.get("trackNo"),
                      "weight": e.get("weight"), "skuInfo": e.get("pkgSkuNumInfo"),
                      "labelUrl": e.get("fileUrl")})
    out["packs"] = packs
    # 单号列表可能很长（拆单），保留但去重
    out["logisticsTrackNos"] = list(dict.fromkeys(
        [x for x in (d.get("logisticsTrackNos") or []) if x] or
        ([d.get("logisticsTrackNo")] if d.get("logisticsTrackNo") else [])))
    return out


def load_store(fn: str) -> dict:
    """读已入库的单：{outboundOrderNo: 记录}（后写的覆盖先写的 = 可刷新状态）。"""
    out: dict[str, dict] = {}
    if os.path.exists(fn):
        with open(fn, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    out[r["outboundOrderNo"]] = r
                except Exception:  # noqa: BLE001
                    pass
    return out


def save_store(fn: str, store: dict) -> None:
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    tmp = fn + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in sorted(store.values(), key=lambda x: x.get("orderCreateTime") or ""):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, fn)


def day_bounds(d: date) -> tuple[str, str]:
    return d.strftime("%Y-%m-%d 00:00:00"), d.strftime("%Y-%m-%d 23:59:59")


def month_slices(start: date, end: date):
    """按月切窗口。回填**必须按月**：逐天跑要 288 次列表调用（且早期全是空），
    实测数据只从 2026-08 起，按月只要 3 次窗口 + 少量翻页。"""
    import calendar
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last = calendar.monthrange(y, m)[1]
        lo = "%04d-%02d-01 00:00:00" % (y, m)
        hi = "%04d-%02d-%02d 23:59:59" % (y, m, last)
        if "%04d-%02d" % (y, m) == start.strftime("%Y-%m"):
            lo = start.strftime("%Y-%m-%d 00:00:00")
        if "%04d-%02d" % (y, m) == end.strftime("%Y-%m"):
            hi = end.strftime("%Y-%m-%d 23:59:59")
        yield y, m, lo, hi
        m += 1
        if m > 12:
            y, m = y + 1, 1


def collect(cli: OmsClient, start: str, end: str, stores, verbose=True) -> dict:
    """拉一个时间窗的订单并下钻详情，返回 {outboundOrderNo: slim}。"""
    if verbose:
        print("[oms] 列表 orderCreateTime %s ~ %s" % (start, end), flush=True)
    nos = []
    for r in cli.iter_orders(start, end):
        no = r.get("outboundOrderNo")
        if no:
            nos.append(no)
    nos = list(dict.fromkeys(nos))
    if verbose:
        print("[oms] 命中 %d 单，开始批量取详情（每批 %d）" % (len(nos), DETAIL_BATCH_MAX),
              flush=True)
    got: dict[str, dict] = {}
    for i in range(0, len(nos), DETAIL_BATCH_MAX):
        chunk = nos[i:i + DETAIL_BATCH_MAX]
        for d in cli.details(chunk):
            no = d.get("outboundOrderNo")
            if not no:
                continue
            if stores and (d.get("storeName") or "") not in stores:
                continue                      # 只要 BHY/BEL
            got[no] = slim_detail(d)
        if verbose and (i // DETAIL_BATCH_MAX) % 10 == 9:
            print("   ...%d/%d" % (i + DETAIL_BATCH_MAX, len(nos)), flush=True)
    return got


def cmd_sync(a) -> int:
    cli = OmsClient()
    fn = os.path.join(a.out, "oms_orders.jsonl")
    store = load_store(fn)
    before = len(store)
    # 默认回溯 2 天：任务 22:00 跑，覆盖「昨天+今天」，防止当天晚下单的漏掉
    end_d = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today()
    start_d = end_d - timedelta(days=max(0, a.days - 1))
    got = {}
    d = start_d
    while d <= end_d:
        s, e = day_bounds(d)
        got.update(collect(cli, s, e, WANT_STORES))
        d += timedelta(days=1)
    store.update(got)
    save_store(fn, store)
    print("[oms] 本次 %d 单（%s），库内 %d -> %d 单（接口调用 %d 次）"
          % (len(got), "/".join(WANT_STORES), before, len(store), cli.calls))
    return 0


def cmd_backfill(a) -> int:
    cli = OmsClient()
    fn = os.path.join(a.out, "oms_orders.jsonl")
    store = load_store(fn)
    before = len(store)
    start_d = datetime.strptime(a.start, "%Y-%m-%d").date()
    end_d = datetime.strptime(a.end, "%Y-%m-%d").date() if a.end else date.today()
    t0 = time.time()
    for y, m, lo, hi in month_slices(start_d, end_d):
        got = collect(cli, lo, hi, WANT_STORES, verbose=False)
        store.update(got)
        print("[oms] %04d-%02d  %4d 单（库 %d）  %.0fs"
              % (y, m, len(got), len(store), time.time() - t0), flush=True)
    save_store(fn, store)
    print("[oms] 回填完成 %d -> %d 单，用时 %.0fs，接口调用 %d 次"
          % (before, len(store), time.time() - t0, cli.calls))
    return 0


def cmd_stats(a) -> int:
    fn = os.path.join(a.out, "oms_orders.jsonl")
    store = load_store(fn)
    if not store:
        print("[oms] 库为空：%s" % fn)
        return 0
    recs = list(store.values())
    print("[oms] 库内 %d 单  %s" % (len(recs), fn))
    for f in ("storeName", "whCode", "statusName", "provinceName", "salesPlatform"):
        c = collections.Counter(str(r.get(f)) for r in recs)
        print("  %-14s %s" % (f, dict(c.most_common(8))))
    times = sorted(x for x in (r.get("orderCreateTime") for r in recs) if x)
    print("  时间范围 %s ~ %s" % (times[0], times[-1]))
    multi = sum(1 for r in recs if len(r.get("products") or []) > 1)
    print("  一单多品 %d（%.1f%%）" % (multi, 100.0 * multi / len(recs)))
    noaddr = sum(1 for r in recs if not r.get("provinceName"))
    print("  缺州 %d" % noaddr)
    return 0


def cmd_build(a) -> int:
    """给面板用的精简数据：最近 N 天的订单 + 州/产品分布。"""
    fn = os.path.join(a.out, "oms_orders.jsonl")
    store = load_store(fn)
    if not store:
        print("[oms] 库为空，先跑 sync/backfill")
        return 0
    recs = sorted(store.values(), key=lambda x: x.get("orderCreateTime") or "", reverse=True)
    recent = recs[:a.recent]
    states = collections.Counter(r.get("provinceName") or "?" for r in recs)
    styles = collections.Counter()
    for r in recs:
        for p in (r.get("products") or []):
            n = (p.get("name") or "").strip()
            if n:
                styles[n.rsplit("-", 1)[0] if n.rsplit("-", 1)[-1].isalpha() else n] += 1
    data = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "领星 OMS /openapi/v1/outboundOrder（list+detail）· 店铺 %s"
                  % "/".join(WANT_STORES),
        "totals": {
            "orders": len(recs),
            "stores": dict(collections.Counter(r.get("storeName") for r in recs)),
            "states": dict(states.most_common()),
        },
        "topStyles": [{"name": k, "orders": v} for k, v in styles.most_common(50)],
        "recent": [{
            "outboundOrderNo": r.get("outboundOrderNo"),
            "platformOrderNo": r.get("platformOrderNo"),
            "referOrderNo": r.get("referOrderNo"),
            "store": r.get("storeName"),
            "state": r.get("provinceName"),
            "stateCode": r.get("provinceCode"),
            "city": r.get("cityName"),
            "zip": r.get("postCode"),
            "status": r.get("statusName"),
            "carrier": r.get("logisticsCarrier"),
            "trackNo": (r.get("logisticsTrackNos") or [None])[0],
            "createdAt": r.get("orderCreateTime"),
            "products": [p.get("name") for p in (r.get("products") or [])],
        } for r in recent],
    }
    os.makedirs(os.path.join(a.admin, "public", "data"), exist_ok=True)
    out = os.path.join(a.admin, "public", "data", "oms-orders.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("[oms] -> %s (%.1f KB)  %d 单 / %d 州 / 最近 %d 条"
          % (out, os.path.getsize(out) / 1024, len(recs), len(states), len(recent)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="领星 OMS 订单采集")
    ap.add_argument("cmd", choices=["sync", "backfill", "stats", "build"])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--admin", default=DEFAULT_ADMIN)
    ap.add_argument("--days", type=int, default=2, help="sync 回溯天数（含今天）")
    ap.add_argument("--start", default="2026-01-01", help="backfill 起始日")
    ap.add_argument("--end", default="", help="结束日（默认今天）")
    ap.add_argument("--recent", type=int, default=200, help="build 里带多少条最近订单")
    a = ap.parse_args()
    try:
        return {"sync": cmd_sync, "backfill": cmd_backfill,
                "stats": cmd_stats, "build": cmd_build}[a.cmd](a)
    except OmsError as e:
        print("[FAIL] %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
