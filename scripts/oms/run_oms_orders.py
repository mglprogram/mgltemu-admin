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

# 只要这两家店的订单（用户 2026-10-15 明确：「只能抓 BHY 和 BEL 店铺的订单」）。
#
# ⚠ 关于「全部」的误会：用户上一句「要在全部的页面下」指的是 OMS 界面上的
#   **状态页签「全部」**（相对 待处理/待获取平台面单/处理中/已发货/已取消/异常），
#   不是「全部店铺」。店铺口径始终是 BHY + BEL两家，对应界面「店铺」筛选里那两项
#   （BHY 634418228526171 / BEL 634418227477419，与帕沃尔那边同一组店铺ID）。
#
# 采集时**必须先把窗口内的单全部下钻详情**才能知道店铺 —— 列表接口的 storeName
# 恒为空（见 oms_client 顶部）。所以这里是「拉全量、只留这两家」，不是少发请求。
COLLECT_STORES = ("BHY", "BEL")

# 用户点名的两个 TEMU 本土店。**只用于 build 阶段合并进「订单分布」**，
WANT_STORES = ("BHY", "BEL")

# 用哪个美国时区定义「一天」。选**太平洋时间**：它是全美最晚跨 0 点的时区，
# 00:00 PT 时美国全境（ET/CT/MT/PT）都已过完前一天，抓到的「美国前一天」是完整的。
# 00:00 PT = 15:00 中国时间（夏令时 PDT，UTC-7）。见 us_day_bounds_cn()。
US_TZ = "America/Los_Angeles"
CN_TZ = "Asia/Shanghai"

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


def us_day_bounds_cn(day: date) -> tuple[str, str]:
    """把「美国某一天」换算成接口要的**中国时间**窗口。

    为什么必须换算：接口的 orderCreateTime 是**中国时间**（实测：14:05 中国时间时
    最新单是 12:21，若是美国时间就成"未来"了），但业务口径是「美国前一天」。
    e.g. 美国 2026-10-14 一整天 = 中国 2026-10-14 15:00 ~ 2026-10-15 14:59:59（PDT）。
    """
    from datetime import time as _t
    from zoneinfo import ZoneInfo
    cn, us = ZoneInfo(CN_TZ), ZoneInfo(US_TZ)
    lo = datetime.combine(day, _t(0, 0, 0), us).astimezone(cn)
    hi = datetime.combine(day, _t(23, 59, 59), us).astimezone(cn)
    return lo.strftime("%Y-%m-%d %H:%M:%S"), hi.strftime("%Y-%m-%d %H:%M:%S")


def last_done_us_day(now_cn: datetime | None = None) -> date:
    """「**已经过完**的那个美国日」= 太平洋时区的昨天。

    直觉写法 `(pt - 1秒).date()` 是**错的**：那样在 PT 当天 05:30 跑会返回「今天」
    （10-16），而正确结果是「昨天」（10-15）—— 因为 10-15 已经过完了。
    只有 pt.date() - 1 天这个口径，在 PT 一天里的任何时刻答案都稳定。

    ⚠ 边界：若任务**提前**在 PT 23:5x 跑（= 中国 14:5x），返回的会是前天。
    所以 sync 默认 `--days 2`（多带一天），漏/早一天都能自愈。
    """
    from zoneinfo import ZoneInfo
    pt = (now_cn or datetime.now()).astimezone(ZoneInfo(US_TZ))
    return pt.date() - timedelta(days=1)


def month_slices(start: date, end: date):
    """按月切窗口。回填**按月**比逐天快得多：实测数据从 2026-08 起，
    逐天要几百次列表调用且早期全空。"""
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


def collect(cli: OmsClient, start: str, end: str, stores=None, verbose=True) -> dict:
    """拉一个时间窗的订单并下钻详情，返回 {outboundOrderNo: slim}。

    `stores=None` 表示**不过滤**（用户 2026-10-15：「要在全部的页面下」）——
    列表接口没有店铺名，只有详情有，所以先全部下钻、再按需过滤。
    """
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
                continue
            got[no] = slim_detail(d)
        if verbose and (i // DETAIL_BATCH_MAX) % 10 == 9:
            print("   ...%d/%d" % (i + DETAIL_BATCH_MAX, len(nos)), flush=True)
    return got


def cmd_sync(a) -> int:
    """每天跑：抓「刚过完的那个美国日」（默认回溯 2 个美国日，漏跑一次能自愈）。

    任务在 **15:00 中国时间 = 00:00 太平洋时间** 触发，此时美国全境都已过完前一天，
    所以拉到的是完整的一天，不会掺进新一天的单。
    """
    cli = OmsClient()
    fn = os.path.join(a.out, "oms_orders.jsonl")
    store = load_store(fn)
    before = len(store)
    if a.us_date:
        days = [datetime.strptime(a.us_date, "%Y-%m-%d").date()]
    else:
        last = last_done_us_day()
        days = [last - timedelta(days=i) for i in range(max(1, a.days) - 1, -1, -1)]
    got = {}
    for d in days:
        lo, hi = us_day_bounds_cn(d)
        print("[oms] 美国日 %s  →  接口窗口(中国时间) %s ~ %s" % (d, lo, hi), flush=True)
        got.update(collect(cli, lo, hi, COLLECT_STORES))
    store.update(got)
    save_store(fn, store)
    by_store = collections.Counter(v.get("storeName") or "(空)" for v in got.values())
    print("[oms] 本次 %d 单 %s，库内 %d -> %d 单（接口调用 %d 次）"
          % (len(got), dict(by_store.most_common()), before, len(store), cli.calls))
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
        got = collect(cli, lo, hi, COLLECT_STORES, verbose=False)
        store.update(got)
        # ⚠ 每个月落一次盘：回填 4000+ 单要十几分钟，中途挂掉不该丢掉已拉到的部分
        save_store(fn, store)
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


def oms_to_pw_rows(store: dict, stores=None) -> list[dict]:
    """把 OMS 订单转成「订单分布」管线用的行（形状与帕沃尔那套一致）。

    帕沃尔那边的行字段：order_id/shop/ref_no/state/state_code/city/zip/qty/addtime/
    items[{product,fnsku,qty,value}]...。这里把 OMS 的 detail 映射过去，
    `ref_no` 用 **platformOrderNo**（= 帕沃尔的「参考单号」PO-211-…），
    这是两条数据源唯一能对上的键，合并且去重就靠它。
    """
    out = []
    for r in store.values():
        shop = r.get("storeName") or ""
        if stores and shop not in stores:
            continue
        items = [{"product": p.get("name") or "", "fnsku": p.get("sku") or "",
                  "qty": p.get("qty") or 0, "value": ""}
                 for p in (r.get("products") or [])]
        packs = r.get("packs") or [{}]
        out.append({
            "order_id": r.get("outboundOrderNo"), "shop": shop,
            "ref_no": r.get("platformOrderNo") or "",
            "state": r.get("provinceName") or "", "state_code": r.get("provinceCode") or "",
            "city": r.get("cityName") or "", "zip": r.get("postCode") or "",
            "qty": sum(i["qty"] for i in items), "addtime": r.get("orderCreateTime") or "",
            "warehouse": r.get("whCode") or "", "channel": r.get("logisticsCarrier") or "",
            "tracking": (r.get("logisticsTrackNos") or [""])[0],
            "weight": round(packs[0].get("weight") or 0.0, 2),
            "amount": round(r.get("costTotal") or 0.0, 2),
            "items": items,
            "product": items[0]["product"] if items else "",
            "fnsku": items[0]["fnsku"] if items else "",
            "skus": "", "source": "oms",
            "status": r.get("statusName") or "", "labelUrl": packs[0].get("labelUrl") or "",
        })
    return out


def cmd_build(a) -> int:
    """给面板用的精简数据：最近 N 天的订单 + 州/产品分布。"""
    fn = os.path.join(a.out, "oms_orders.jsonl")
    store = load_store(fn)
    if not store:
        print("[oms] 库为空，先跑 sync/backfill")
        return 0
    only = tuple(x.strip() for x in a.stores.split(",") if x.strip()) or WANT_STORES
    recs = [r for r in store.values() if (r.get("storeName") or "") in only]
    recs.sort(key=lambda x: x.get("orderCreateTime") or "", reverse=True)
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
        "source": "领星 OMS /openapi/v1/outboundOrder（list+detail）· 店铺 %s" % "/".join(only),
        "totals": {
            "orders": len(recs),
            # 库里**全部店铺**的分布也带上：用户明确「要在全部的页面下」，一眼能看到别家有多少
            "allStores": dict(collections.Counter(
                r.get("storeName") or "(空)" for r in store.values()).most_common()),
            "ordersAllStores": len(store),
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
    ap.add_argument("--days", type=int, default=2, help="sync 回溯几个**美国日**（含刚过完的那个）")
    ap.add_argument("--us-date", default="", help="sync 指定某个美国日 YYYY-MM-DD（调试用）")
    ap.add_argument("--stores", default="", help="build 只算这些店（逗号分隔），默认 BHY,BEL")
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
