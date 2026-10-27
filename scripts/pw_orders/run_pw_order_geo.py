# -*- coding: utf-8 -*-
"""帕沃尔海外仓订单地理分布 —— 采集 + 聚合 + 产出看板数据。

背景（2026-10-14 用户要求）
--------------------------
「帮我导出 1 月到现在所有的订单，然后点击面单 → 有对应的州-市；帮我汇总我的订单主要出单的州。」

帕沃尔后台的「导出Excel」**没有**收件州/市，但每一单的详情页
（`deliver.php?act=order_info&id=<出库单id>`）的「交货信息」里有完整的
`<城市> <州>` + 邮编 —— 所以口径是：**出库单清单走导出，州-市逐单抓**。

四步（可单独跑，互不依赖，产物都在 --out 目录）
------------------------------------------------
  list    两店的出库单清单           → orders_list.jsonl
  geo     逐单抓「收件地址」          → orders_geo.jsonl
  export  帕沃尔自带「导出Excel」     → pw_export_<店>_<n>.xlsx（取 数量/下单时间/仓库/渠道）
  build   聚合成看板数据 + 明细导出   → temu-admin/public/data/order-geo.json
                                       订单导出_两店_<起>_<止>.xlsx（含州/市/邮编）
                                       订单分布_汇总.md / order_geo_states.csv

用法
----
  python scripts/pw_orders/run_pw_order_geo.py list
  python scripts/pw_orders/run_pw_order_geo.py geo --workers 6
  python scripts/pw_orders/run_pw_order_geo.py export
  python scripts/pw_orders/run_pw_order_geo.py build
  python scripts/pw_orders/run_pw_order_geo.py all          # 串起来全跑

  # 只跑单店 / 自定义区间
  python scripts/pw_orders/run_pw_order_geo.py list --shops BHY --start 2026-01-01 --end 2026-10-14

⚠ 速率（2026-10-14 实测，本机直连）
  · 列表页 300 行/请求，~0.4s/请求；8 并发下 ~70~100 行/s
  · 详情页 ~0.13s/单；6 并发下 ~30 单/s（3.1 万单 ≈ 17 分钟）
  · 服务端会偶发限流：连续几十个请求后单请求延迟飙到 ~30s，**不要**因此加大并发
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # 仓库根
sys.path.insert(0, os.path.join(HERE, "lib"))

from pw_session import PW, PWError, parse_file_count                    # noqa: E402
from pw_order_geo import (CODE, STATES, aggregate, parse_detail_products,  # noqa: E402
                          parse_addtime, parse_order_info, parse_rows)

DATA_DIR = HERE
DEFAULT_OUT = os.path.join(ROOT, "out", "pw-orders")
DEFAULT_ADMIN = os.path.join(ROOT, "temu-admin")

try:
    if not sys.stdout.isatty():
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

_local = threading.local()


def load_shops() -> dict:
    with open(os.path.join(DATA_DIR, "shops.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def session() -> PW:
    """线程内单例会话（每个 worker 各自登录，避免共用一条连接串行化）。"""
    pw = getattr(_local, "pw", None)
    if pw is None:
        pw = PW()
        pw.login()
        _local.pw = pw
    return pw


# ------------------------------------------------------------------ list
def month_slices(start: str, end: str) -> list[tuple[str, str]]:
    """把 [start, end] 切成自然月区间。

    ⚠ 为什么必须按月切（2026-10-14 实测踩坑）：
      **全区间深分页会让服务端卡死** —— BHY 一次查 2026-01-01~10-14 共 35 页，
      前 21 页正常（0.4~2s/页），**第 22 页起单页请求挂到 120s 超时都不返回**，
      重试 4 次仍是超时（不是本机网络问题，同期 BEL 的 71 页能跑完）。
      按月切之后每个月只有 3~9 页、offset 很浅，**每页秒回**，
      10 个月 10 次调用总共 2 分钟抓完 10407 单。
    """
    out = []
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    while (y, m) <= (ey, em):
        last = (date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)).isoformat()
        lo = max("%04d-%02d-01" % (y, m), start)
        hi = min(last, end)
        if lo <= hi:
            out.append((lo, hi))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def cmd_list(a) -> None:
    shops = load_shops()
    todo = {k: v for k, v in shops.items() if not a.shops or k in a.shops}
    fn = os.path.join(a.out, "orders_list.jsonl")
    seen: dict[str, dict] = {}
    if os.path.exists(fn):
        for line in open(fn, encoding="utf-8"):
            try:
                r = json.loads(line)
                seen[r["order_id"]] = r
            except Exception:  # noqa: BLE001
                pass
        print("[list] 已有 %d 单（续跑）" % len(seen))

    t0 = time.time()
    for shop, info in todo.items():
        shop_id = info["shop_id"]
        for (s, e) in month_slices(a.start, a.end):
            first = session().list_page(shop_id, s, e, 1, a.page_size)
            pages = int(first.get("page_count") or 1)
            rows = parse_rows(first.get("content") or "", shop_id, shop)
            allrows = list(rows)

            def grab(p: int, _s=s, _e=e):
                for attempt in range(3):
                    try:
                        j = session().list_page(shop_id, _s, _e, p, a.page_size)
                        return parse_rows(j.get("content") or "", shop_id, shop)
                    except Exception as ex:                          # noqa: BLE001
                        if attempt == 2:
                            print("[list] %s %s~%s 第 %d 页失败：%s"
                                  % (shop, _s, _e, p, str(ex)[:90]), flush=True)
                            return []
                        time.sleep(2 * (attempt + 1))

            if pages > 1:
                with ThreadPoolExecutor(a.workers) as ex:
                    futs = [ex.submit(grab, p) for p in range(2, pages + 1)]
                    for fut in as_completed(futs):
                        allrows.extend(fut.result())

            before = len(seen)
            for r in allrows:
                seen.setdefault(r["order_id"], r)
            print("[list] %s %s~%s：%d 页 / %d 行 / 新增 %d 单（累计 %d）"
                  % (shop, s, e, pages, len(allrows), len(seen) - before, len(seen)), flush=True)
            # 每片都落盘：中途断掉也不丢已完成的部分
            with open(fn, "w", encoding="utf-8") as f:
                for r in seen.values():
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("[list] 总耗时 %.0fs，累计 %d 单 -> %s" % (time.time() - t0, len(seen), fn))


# ------------------------------------------------------------------ geo
def fetch_geo(order_id: str) -> dict:
    for attempt in range(3):
        try:
            html = session().order_info(order_id)
            city, state, zipc = parse_order_info(html)
            # 顺手把「添加时间」解析出来（2026-10-27）：增量新单不在导出表里，
            # 没有它就进不了「近 N 天」这种滚动窗口（见 parse_addtime 注释）。
            return {"order_id": order_id, "city": city, "state": state,
                    "state_code": CODE.get(state, ""), "zip": zipc,
                    "addtime": parse_addtime(html), "ok": bool(state)}
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return {"order_id": order_id, "city": "", "state": "", "state_code": "",
                        "zip": "", "addtime": "", "ok": False, "err": str(e)[:120]}
            time.sleep(1.5 * (attempt + 1))
    return {}


def cmd_geo(a) -> None:
    listfn = os.path.join(a.out, "orders_list.jsonl")
    ids, order = [], {}
    for line in open(listfn, encoding="utf-8"):
        r = json.loads(line)
        if r["order_id"] not in order:
            ids.append(r["order_id"])
            order[r["order_id"]] = r
    outfn = os.path.join(a.out, "orders_geo.jsonl")
    # 旧 geo 记录没有 addtime（2026-10-27 才加）。但**不能无脑全量重抓**（3 万单）。
    # 只有「不在导出表里」的单才需要详情页的添加时间 —— 导出表里已有的单
    # 在 build 时能从 addtime 列拿到日期。
    exports, _ = read_exports(os.path.join(a.out, "pw_export_*.xls*"))
    covered = set(exports.keys())            # {(shop, ref_no)}
    done = set()
    if os.path.exists(outfn):
        for line in open(outfn, encoding="utf-8"):
            try:
                r = json.loads(line)
                o = order.get(r["order_id"]) or {}
                key = (o.get("shop"), o.get("ref_no"))
                if r.get("addtime") or key in covered or not r.get("ok"):
                    done.add(r["order_id"])
            except Exception:  # noqa: BLE001
                pass
    todo = [i for i in ids if i not in done]
    if a.limit:
        todo = todo[:a.limit]
    print("[geo] 订单 %d / 已完成 %d / 本次 %d，并发 %d"
          % (len(ids), len(done), len(todo), a.workers), flush=True)
    if not todo:
        return

    t0 = time.time()
    n = bad = 0
    with open(outfn, "a", encoding="utf-8") as f, ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fetch_geo, o): o for o in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            bad += 0 if rec.get("ok") else 1
            if n % 500 == 0:
                f.flush()
                el = time.time() - t0
                rate = n / max(el, 1e-9)
                print("[geo] %d/%d  %.1f 单/s  ETA %.0fs  失败 %d"
                      % (n, len(todo), rate, (len(todo) - n) / max(rate, 1e-9), bad), flush=True)
    print("[geo] 完成 %d 单（未解析出州 %d）耗时 %.0fs -> %s"
          % (n, bad, time.time() - t0, outfn))


# ------------------------------------------------------------------ export
def cmd_export(a) -> None:
    shops = load_shops()
    todo = {k: v for k, v in shops.items() if not a.shops or k in a.shops}
    manifest = []
    for shop, info in todo.items():
        pw = session()
        j = pw.create_export(info["shop_id"], a.start, a.end, a.max_per_file)
        if j.get("error"):
            print("[export] %s 建任务失败：%s" % (shop, j))
            continue
        did = j["daochu_id"]
        nfiles = parse_file_count(j.get("msg", ""))
        print("[export] %s 任务 %s：%d 个文件（%s）" % (shop, did, nfiles, j.get("msg")), flush=True)
        for k in range(nfiles):
            url = pw.export_file_url(did, k)
            ext = os.path.splitext(url)[1] or ".xlsx"
            dest = os.path.join(a.out, "pw_export_%s_%d%s" % (shop, k + 1, ext))
            size = pw.download(url, dest)
            print("[export] %s %d/%d -> %s (%d 字节)" % (shop, k + 1, nfiles, dest, size), flush=True)
            manifest.append({"shop": shop, "shop_id": info["shop_id"], "daochu_id": did,
                             "part": k + 1, "file": os.path.basename(dest), "bytes": size})
    with open(os.path.join(a.out, "pw_export_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"range": {"start": a.start, "end": a.end}, "files": manifest},
                  f, ensure_ascii=False, indent=1)
    print("[export] 共 %d 个文件" % len(manifest))


# ------------------------------------------------------------------ products
def fetch_products(order_id: str) -> dict:
    for attempt in range(3):
        try:
            items = parse_detail_products(session().order_detail(order_id))
            return {"order_id": order_id, "items": items, "ok": bool(items)}
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return {"order_id": order_id, "items": [], "ok": False, "err": str(e)[:120]}
            time.sleep(1.5 * (attempt + 1))
    return {}


def cmd_products(a) -> None:
    """逐单抓「详情弹窗」的产品表 → orders_products.jsonl。

    ⚠ 为什么必须逐单抓：帕沃尔自带导出**一单只出一行、只带第一个产品**，
      而抽样实测 **9.5% 的单挂了 2 个产品**（见 lib/pw_order_geo.parse_detail_products 注释）。
      产品维度的口径只能走这里。
    """
    listfn = os.path.join(a.out, "orders_list.jsonl")
    ids, order = [], {}
    for line in open(listfn, encoding="utf-8"):
        r = json.loads(line)
        if r["order_id"] not in order:
            ids.append(r["order_id"])
            order[r["order_id"]] = r
    outfn = os.path.join(a.out, "orders_products.jsonl")
    done = set()
    if os.path.exists(outfn):
        for line in open(outfn, encoding="utf-8"):
            try:
                rec = json.loads(line)
                if rec.get("ok"):
                    done.add(rec["order_id"])
            except Exception:  # noqa: BLE001
                pass
    todo = [i for i in ids if i not in done]
    if a.limit:
        todo = todo[:a.limit]
    print("[products] 订单 %d / 已抓 %d / 本次 %d，并发 %d"
          % (len(ids), len(done), len(todo), a.workers), flush=True)
    if not todo:
        return

    t0 = time.time()
    n = bad = items_n = multi = 0
    with open(outfn, "a", encoding="utf-8") as f, ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fetch_products, o): o for o in todo}
        for fut in as_completed(futs):
            rec = fut.result()
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if rec.get("ok"):
                items_n += len(rec["items"])
                multi += 1 if len(rec["items"]) > 1 else 0
            else:
                bad += 1
            if n % 500 == 0:
                f.flush()
                el = time.time() - t0
                rate = n / max(el, 1e-9)
                print("[products] %d/%d  %.1f 单/s  ETA %.0fs  失败 %d  一单多品 %d"
                      % (n, len(todo), rate, (len(todo) - n) / max(rate, 1e-9), bad, multi),
                      flush=True)
    print("[products] 完成 %d 单（产品行 %d，一单多品 %d，失败 %d）耗时 %.0fs -> %s"
          % (n, items_n, multi, bad, time.time() - t0, outfn))


# ------------------------------------------------------------------ build
def read_jsonl(fn: str) -> list[dict]:
    if not os.path.exists(fn):
        return []
    out = []
    for line in open(fn, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def read_exports(pattern: str) -> tuple[dict, list[str]]:
    """帕沃尔自带导出 → {(店, 参考单号): {qty, addtime, warehouse, channel, ...}}。"""
    import openpyxl
    rows: dict[tuple, dict] = {}
    files = sorted(glob.glob(pattern))
    for fn in files:
        base = os.path.basename(fn)
        shop = next((s for s in load_shops() if "_%s_" % s in base), "?")
        wb = openpyxl.load_workbook(fn, read_only=True, data_only=True)
        for ws in wb.worksheets:
            it = ws.iter_rows(values_only=True)
            try:
                header = [str(x or "").strip() for x in next(it)]
            except StopIteration:
                continue
            idx = {h: i for i, h in enumerate(header)}
            if "参考单号" not in idx:
                continue

            def cell(row, name):
                i = idx.get(name)
                return row[i] if i is not None and i < len(row) else None

            for r in it:
                ref = str(cell(r, "参考单号") or "").strip()
                if not ref:
                    continue
                e = rows.setdefault((shop, ref), {"qty": 0, "addtime": "", "channel": "",
                                                  "warehouse": "", "tracking": "",
                                                  "weight": 0.0, "amount": 0.0, "skus": [],
                                                  "product": "", "fnsku": ""})
                try:
                    q = int(float(cell(r, "数量") or 0))
                except Exception:  # noqa: BLE001
                    q = 0
                e["qty"] += q
                for key, col in (("addtime", "下单时间"), ("channel", "出货渠道"),
                                 ("warehouse", "出库仓库"), ("tracking", "出库单号"),
                                 ("product", "产品名称"), ("fnsku", "SKU")):
                    if not e[key] and cell(r, col):
                        e[key] = str(cell(r, col)).strip()
                for key, col in (("weight", "计费重量"), ("amount", "总金额")):
                    try:
                        e[key] += float(cell(r, col) or 0)
                    except Exception:  # noqa: BLE001
                        pass
                if cell(r, "SKU"):
                    e["skus"].append("%s×%d" % (str(cell(r, "SKU")).strip(), q))
        wb.close()
    return rows, files


def build_recent_state_products(rows, days: int = 7, end: date | None = None,
                                top_n: int = 3) -> dict:
    """近 N 天 · **每个州**的畅销产品 Top N（用户 2026-10-27 要求）。

    与 all-time 的 `states[].topProducts` 区别：这里是**滚动窗口**，看的是「最近卖什么」，
    用来指导当下备货/ad，而不是历史分布。

    口径与 product_state_agg 一致：
      · 一单 = 一个出库单；一单多品时，每个产品各算 1 单；
      · 同款不同尺码在**同一单**里只算 1 次（否则 SetA-L + SetA-XL 会虚高）；
      · 排序按**单量**（orders），同时给件数（qty）便于核对。
    """
    end_d = end or date.today()
    start_d = end_d - timedelta(days=max(1, days) - 1)
    lo, hi = start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d")
    win = [r for r in rows
           if lo <= (r.get("addtime") or "")[:10] <= hi and (r.get("state") in CODE)]

    st_orders: collections.Counter = collections.Counter()
    st_styles: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    st_qty: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in win:
        st = r["state"]
        st_orders[st] += 1
        seen: set = set()
        for it in (r.get("items") or []):
            prod = (it.get("product") or "").strip()
            if not prod:
                continue
            sty = style_of(prod)
            # 件数按每个尺码的实际数量累加；单量才按「同单一款一次」去重
            st_qty[st][sty] += int(it.get("qty") or 0)
            if sty in seen:
                continue
            seen.add(sty)
            st_styles[st][sty] += 1

    out = []
    for st, n in st_orders.most_common():
        tops, used_codes = [], set()
        for sty, c in st_styles[st].most_common():
            m = re.search(r"\d{4,6}", sty)
            code = m.group(0) if m else sty.split("-", 1)[-1]
            # 按**数字短码**去重：不同款可能撞码（如 BS2-202504-5SetA / -5SetB 都显示 202504），
            # 同一州 Top3 里出现两个一样的数字看起来像 bug，所以取 3 个**不同**短码。
            if code in used_codes:
                continue
            used_codes.add(code)
            tops.append({"name": sty, "code": code,
                         "orders": c, "qty": st_qty[st].get(sty, 0)})
            if len(tops) >= top_n:
                break
        out.append({"name": st, "code": CODE.get(st, ""), "orders": n, "topProducts": tops})
    return {
        "start": start_d.strftime("%Y-%m-%d"),
        "end": hi,
        "days": max(1, days),
        "orderCount": len(win),
        "stateCount": len(out),
        "states": out,
    }


def load_oms_rows(out_dir: str, ref_seen: set, stores=None) -> list[dict]:
    """把领星 OMS 的订单并进来（用户 2026-10-15：「把捉完的数据更新到订单分布和数据大屏」）。

    两条数据源：
      · 帕沃尔 POWER —— 2026-01-01 ~ 10-14 的历史（逐单抓的州/市）
      · 领星 OMS（恒信仓）—— 2026-08-24 起，每天 15:00（= 00:00 PT）增量

    **合并且去重的唯一键是参考单号**：POWER 的 `ref_no` 与 OMS 的 `platformOrderNo`
    都是 TEMU 的 `PO-211-…`，重复的直接跳过。
    默认**全部店铺**（用户 2026-10-15：「要在全部的页面下」）—— OMS 里除了 BHY/BEL
    还有 Temu-BK / TK-* (TikTok) / BS (SHEIN) / Savonire_US / BD，每行带 `platform` 区分。
    """
    fn = os.path.join(out_dir, "oms_orders.jsonl")
    if not os.path.exists(fn):
        return []
    rows, dup, skipped = [], 0, 0
    for line in open(fn, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:                                          # noqa: BLE001
            continue
        if stores and (r.get("storeName") or "") not in stores:
            skipped += 1
            continue
        ref = r.get("platformOrderNo") or ""
        if ref and ref in ref_seen:
            dup += 1
            continue                       # POWER 已经抓过这一单
        items = [{"product": p.get("name") or "", "fnsku": p.get("sku") or "",
                  "qty": p.get("qty") or 0, "value": ""}
                 for p in (r.get("products") or [])]
        packs = r.get("packs") or [{}]
        rows.append({
            "order_id": r.get("outboundOrderNo"), "shop": r.get("storeName") or "",
            "ref_no": ref, "state": r.get("provinceName") or "",
            "state_code": r.get("provinceCode") or "", "city": r.get("cityName") or "",
            "zip": r.get("postCode") or "", "qty": sum(i["qty"] for i in items),
            "addtime": r.get("orderCreateTime") or "", "warehouse": r.get("whCode") or "",
            "channel": r.get("logisticsCarrier") or "",
            "tracking": (r.get("logisticsTrackNos") or [""])[0],
            "weight": round(packs[0].get("weight") or 0.0, 2),
            "amount": round(r.get("costTotal") or 0.0, 2),
            "items": items, "product": items[0]["product"] if items else "",
            "fnsku": items[0]["fnsku"] if items else "", "skus": "", "source": "oms",
            # salesPlatform: 34=Temu / 31=Shein / 20=TikTok（接口给的是数字码）
            "platform": PLATFORM_CODE.get(str(r.get("salesPlatform")), "其他"),
        })
    print("[build] 领星 OMS：并入 %d 单（与帕沃尔重复跳过 %d，非 BHY/BEL 跳过 %d）"
          % (len(rows), dup, skipped))
    return rows


def count_oms_other_stores(out_dir: str, stores, ref_seen: set) -> int:
    """OMS 里不属于本页口径（BEL/BHY）的单量 —— 只用于在页面上如实说明，不参与统计。"""
    if stores is None:
        return 0
    fn = os.path.join(out_dir, "oms_orders.jsonl")
    if not os.path.exists(fn):
        return 0
    n = 0
    for line in open(fn, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:                                          # noqa: BLE001
            continue
        if (r.get("storeName") or "") not in stores:
            ref = r.get("platformOrderNo") or ""
            if not (ref and ref in ref_seen):
                n += 1
    return n


def cmd_build(a) -> None:
    # 近 N 天窗口的右端：默认就是 --end（默认今天）
    end_d = datetime.strptime(a.end, "%Y-%m-%d").date()
    shops = load_shops()
    lst = read_jsonl(os.path.join(a.out, "orders_list.jsonl"))
    geo = {g["order_id"]: g for g in read_jsonl(os.path.join(a.out, "orders_geo.jsonl"))}
    exports, files = read_exports(os.path.join(a.out, "pw_export_*.xls*"))
    prods = {p["order_id"]: p for p in read_jsonl(os.path.join(a.out, "orders_products.jsonl"))
             if p.get("ok")}
    print("[build] 订单 %d / 已抓地址 %d / 导出文件 %d（%d 个参考单号）/ 已抓产品 %d 单"
          % (len(lst), len(geo), len(files), len(exports), len(prods)))
    if not prods:
        print("[build] [warn] 没有 orders_products.jsonl —— 产品×州 会退化成"
              "「导出表里的第一个产品」，一单多品会被漏掉（实测约 9.5%）")
        print("[build]        补抓：run_pw_order_geo.py products")

    merged, seen = [], set()
    for o in lst:
        oid = o["order_id"]
        if oid in seen:
            continue
        seen.add(oid)
        g = geo.get(oid) or {}
        e = exports.get((o["shop"], o["ref_no"]), {})
        p = prods.get(oid) or {}
        pitems = p.get("items") or []
        merged.append({
            "order_id": oid, "shop": o["shop"], "ref_no": o["ref_no"],
            "state": g.get("state", ""), "state_code": g.get("state_code", ""),
            "city": g.get("city", ""), "zip": g.get("zip", ""),
            "qty": e.get("qty") or sum(int(it.get("qty") or 0) for it in pitems),
            # 日期优先用导出表（官方口径）；增量新单不在导出里 ⇒ 用详情页「添加时间」
            "addtime": e.get("addtime") or g.get("addtime", ""),
            "warehouse": e.get("warehouse", ""), "channel": e.get("channel", ""),
            "tracking": e.get("tracking", ""), "weight": round(e.get("weight", 0.0), 2),
            "amount": round(e.get("amount", 0.0), 2),
            # 产品口径优先用详情弹窗（一单可多品，带每品数量）；没有就退回导出表的单个产品
            "items": pitems or ([{"product": e.get("product"), "fnsku": e.get("fnsku"),
                                  "qty": e.get("qty", 0), "value": ""}] if e.get("product") else []),
            "product": (pitems[0]["product"] if pitems else e.get("product", "")),
            "fnsku": (pitems[0]["fnsku"] if pitems else e.get("fnsku", "")),
            "skus": " | ".join(e.get("skus", [])),
        })
    withgeo = [m for m in merged if m["state"] in CODE]
    rate = 100.0 * len(withgeo) / max(len(merged), 1)
    print("[build] 合并 %d 单，其中 %d 单定位到州（%.1f%%）" % (len(merged), len(withgeo), rate))

    # 并入领星 OMS 的增量单（去重键 = 参考单号 / 平台单号）
    # ⚠ 两个域各自的产物目录不同：本域是 out/pw-orders，OMS 域是 out/oms。
    #   别写成 a.out（第一次就踩了，静默并入 0 单）。
    # ⚠ 范围：OMS 里除了 BEL/BHY 还有 Temu-BK / TK-*(TikTok) / BS(SHEIN) / Savonire_US / BD。
    #   本页口径是**两家 TEMU 本土店**，所以只并 BEL/BHY；其余店铺的单留在 OMS 数据源里，
    #   数量如实报出来（omsOtherStores），要看全部把 --oms-stores 传空/给全。
    oms_stores = None if a.oms_stores == "all" else tuple(
        s.strip() for s in (a.oms_stores or "BEL,BHY").split(",") if s.strip())
    oms_out = a.oms_out or os.path.join(os.path.dirname(a.out), "oms")
    ref_seen = {m["ref_no"] for m in merged if m.get("ref_no")}
    oms_rows = load_oms_rows(oms_out, ref_seen, oms_stores)
    oms_other = count_oms_other_stores(oms_out, oms_stores, ref_seen)
    if oms_other:
        print("[build] 领星 OMS：另有 %d 单来自其他店铺（TikTok/SHEIN 等），**未计入本页**"
              % oms_other)
    if oms_rows:
        have = {m["order_id"] for m in merged}
        add = [r for r in oms_rows if r["order_id"] not in have]
        merged += add
        withgeo += [r for r in add if r["state"] in CODE]
        print("[build] 并入后 %d 单，定位到州 %d（%.1f%%）"
              % (len(merged), len(withgeo), 100.0 * len(withgeo) / max(len(merged), 1)))
    if not withgeo:
        raise SystemExit("[build] 没有任何州数据 —— geo 步骤没跑完？")
    oms_n = sum(1 for m in withgeo if m.get("source") == "oms")

    stores = {}
    for shop, info in shops.items():
        recs = [m for m in withgeo if m["shop"] == shop]
        agg = aggregate(recs)
        attach_products(agg["states"], recs, top_n=STATE_PRODUCT_N)
        stores[shop] = {
            "shopId": info["shop_id"],
            "orders": len([m for m in merged if m["shop"] == shop]),
            "geoOrders": len(recs),
            "states": agg["states"], "topCities": agg["topCities"], "months": agg["months"],
            "topProducts": build_top_products(recs, limit=PRODUCT_LIMIT),
        }
        print("  %s(%s)：%d 单，定位 %d，头号州 %s"
              % (shop, info["shop_id"], stores[shop]["orders"], len(recs),
                 agg["states"][0]["name"] if agg["states"] else "-"))

    agg = aggregate(withgeo)
    attach_products(agg["states"], withgeo, top_n=STATE_PRODUCT_N)
    all_products = build_top_products(withgeo, limit=PRODUCT_LIMIT)
    data = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "range": {"start": a.start, "end": a.end},
        "source": ("帕沃尔海外仓 powerwarehouse.net · 发货列表 deliver.php + 逐单收件地址"
                   " + [详情]弹窗产品表；并合领星 OMS(恒信仓) /openapi/v1/outboundOrder 增量单"
                   if oms_n else
                   ("帕沃尔海外仓 powerwarehouse.net · 发货列表 deliver.php + 逐单收件地址"
                    " + [详情]弹窗产品表")),
        "sourceNote": ("帕沃尔覆盖 2026-01-01~2026-10-14；2026-08-24 起并入领星 OMS"
                       "（恒信仓）BEL/BHY 的出库单，按参考单号 PO-211-… 去重，"
                       "实际并入 %d 单%s" % (oms_n, ("；另有 %d 单是 OMS 其他店铺"
                       "（TikTok/SHEIN 等），未计入本页" % oms_other) if oms_other else ""))
                      if oms_n else "",
        "omsOrders": oms_n,
        "omsOtherStores": oms_other,
        "totals": {"orders": len(merged), "geoOrders": len(withgeo),
                   "stateCount": len(agg["states"]), "cityCount": len(agg["topCities"]),
                   "qty": agg["qty"],
                   "styleCount": len({p["name"] for p in all_products}) or None},
        "states": agg["states"], "topCities": agg["topCities"], "months": agg["months"],
        # 产品（款）维度：每款的总单量 + 它自己最集中的几个州 + 地域集中度 lift
        "topProducts": all_products,
        "stores": stores,
        # 近 7 天 · 每个州的畅销产品 Top3（用户 2026-10-27 要求；滚动窗口，看"最近卖什么"）
        "recent7": build_recent_state_products(withgeo, days=a.recent_days, end=end_d),
    }
    print("[build] 近 %d 天（%s ~ %s）：%d 单 / %d 州"
          % (data["recent7"]["days"], data["recent7"]["start"], data["recent7"]["end"],
             data["recent7"]["orderCount"], data["recent7"]["stateCount"]))
    data["totals"]["styleCount"] = product_state_agg(withgeo)["styles"].__len__()
    os.makedirs(os.path.join(a.admin, "public", "data"), exist_ok=True)
    out_json = os.path.join(a.admin, "public", "data", "order-geo.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("[build] -> %s (%.1f KB)" % (out_json, os.path.getsize(out_json) / 1024))

    # 产品明细索引（面板「产品查询」用，页面按需加载，别塞进大屏那份）
    pidx = build_product_index(withgeo)
    pidx_json = os.path.join(a.admin, "public", "data", "order-geo-products.json")
    with open(pidx_json, "w", encoding="utf-8") as f:
        json.dump({"generatedAt": data["generatedAt"], "range": data["range"],
                   "productCount": len(pidx), "products": pidx},
                  f, ensure_ascii=False, separators=(",", ":"))
    print("[build] -> %s (%.1f KB, %d 个款)"
          % (pidx_json, os.path.getsize(pidx_json) / 1024, len(pidx)))

    write_states_csv(os.path.join(a.out, "order_geo_states.csv"), agg["states"])
    xfn = write_orders_xlsx(os.path.join(a.out, "订单导出_两店_%s_%s.xlsx" % (a.start, a.end)),
                            merged)
    mdfn = write_summary(os.path.join(a.out, "订单分布_汇总.md"), a, agg, stores, merged,
                         withgeo)

    # ---- 产品 × 州（用户 2026-10-14 追加要求）----
    pfn = write_products_xlsx(os.path.join(a.out, "产品_州分布_%s_%s.xlsx" % (a.start, a.end)),
                              withgeo)
    pmd = write_products_md(os.path.join(a.out, "产品_州分布_汇总.md"), withgeo)
    p_agg = product_state_agg(withgeo)
    print("\n[build] 产品 × 州：%d 个款 / %d 个尺码产品（一单多品 %d 单）"
          % (len(p_agg["styles"]), len(p_agg["products"]), p_agg["multiItems"]))
    print("[build] -> %s\n[build] -> %s" % (pfn, pmd))
    print("款 Top 5（括号内为头号州）:")
    for i, (sty, v) in enumerate(sorted(p_agg["styles"].items(),
                                        key=lambda kv: -kv[1]["orders"])[:5], 1):
        t = v["states"].most_common(1)
        print("  %2d. %-26s %5d 单   %s" % (i, sty, v["orders"],
                                            "%s(%d)" % t[0] if t else "-"))

    print("\n[build] -> %s\n[build] -> %s\n[build] -> %s" % (csv_written, xfn, mdfn))
    print("\n主要出单州 Top 10（两店合计，共 %d 单）:" % len(withgeo))
    for i, s in enumerate(agg["states"][:10], 1):
        print("  %2d. %-20s %-2s %7d 单  %5.2f%%"
              % (i, s["name"], s["code"], s["orders"], s["share"] * 100))


csv_written = ""


# ------------------------------------------------------------------ 产品 × 州
SIZE_RE = re.compile(r"-(?:XXS|XS|S|M|L|XL|XXL|XXXL|2XL|3XL|4XL|5XL)$", re.I)


def style_of(product: str) -> str:
    """把「产品名称」去掉末尾尺码 → **款**。

    `BH-102105-3BHL-XL` → `BH-102105-3BHL`；`BS2-202504-5SetB-S` → `BS2-202504-5SetB`。
    为什么要有这一层：尺码粒度有 789 个名字，逐尺码看州分布没有意义（同款各尺码
    基本同分布）；按款看才是「这款卖到哪」的可读口径。
    """
    return SIZE_RE.sub("", (product or "").strip())


def product_state_agg(rows) -> dict:
    """rows: [{state, items:[{product, fnsku, qty}]}] → 款级 / 尺码级的「产品 × 州」聚合。

    ⚠ 口径（重要）：
      · 一「单」= 一个出库单（= 一个包裹）；
      · **一单可能挂多个产品**（实测约 9.5%），产品明细取自列表页 [详情] 弹窗；
      · 「款被多少单买过」按**订单去重**：一单里同款不同尺码只算 1 次
        （否则 BS2-207202-5SetB-L + -XL 这种会把该款算成 2 单）；
      · 「产品单量」= 该产品被多少个**订单**买过，与上面的去重口径一致；
      · 因此 products/styles 的订单数之和 ≥ 出库单总数（多品单会重复计入不同产品），
        而 `stateOrder`（各州单量）是**不去重**的订单数，两者不可直接相加比较。
    """
    styles: dict[str, dict] = collections.defaultdict(
        lambda: {"orders": 0, "qty": 0, "states": collections.Counter(),
                 "shops": collections.Counter(), "products": collections.Counter()})
    products: dict[str, dict] = collections.defaultdict(
        lambda: {"orders": 0, "qty": 0, "states": collections.Counter(), "style": "",
                 "fnsku": collections.Counter(), "shops": collections.Counter()})
    pair: collections.Counter = collections.Counter()          # (款, 州) → 单量
    state_order: collections.Counter = collections.Counter()
    no_product = multi_items = 0

    for r in rows:
        st = r.get("state") or ""
        if st not in CODE:
            continue
        state_order[st] += 1
        items = [it for it in (r.get("items") or []) if (it.get("product") or "").strip()]
        if not items:
            no_product += 1
            continue
        if len(items) > 1:
            multi_items += 1

        seen_styles: set = set()
        for it in items:
            prod = it["product"].strip()
            sty = style_of(prod)
            q = int(it.get("qty") or 0)
            # 尺码级：一单一个产品算 1 单（同单同产品去重）
            p = products[prod]
            p["orders"] += 1
            p["qty"] += q
            p["states"][st] += 1
            p["style"] = sty
            p["shops"][r.get("shop", "")] += 1
            if it.get("fnsku"):
                p["fnsku"][it["fnsku"]] += 1
            # 款级：同一单里同款只算一次
            s = styles[sty]
            s["products"][prod] += 1
            s["shops"][r.get("shop", "")] += 1
            # ⚠ 件数按**每个尺码的实际数量**累加（2026-10-27 修正）：
            #   原来只在「第一次遇到该款」时 += q，导致一单里同款多尺码
            #   （SetA-L + SetA-XL）只算到第一个尺码的数量，件数偏低。
            s["qty"] += q
            if sty not in seen_styles:
                seen_styles.add(sty)
                s["orders"] += 1
                s["states"][st] += 1
                pair[(sty, st)] += 1

    return {"styles": styles, "products": products, "pair": pair,
            "stateOrder": state_order, "noProduct": no_product, "multiItems": multi_items}


def _top_states(states: collections.Counter, n: int = 5) -> list[dict]:
    total = sum(states.values()) or 1
    return [{"name": s, "code": CODE.get(s, ""), "orders": c, "share": round(c / total, 6)}
            for s, c in states.most_common(n)]


# 每个州/每款最多带几个产品/州（控制 order-geo.json 体积）
PRODUCT_LIMIT = 60
# ⚠ 每个州要带够候选：前端地图只显示「畅销款 Top3」，但款号会去重
#   （BH-102105-3BHL 与 BH-102105-3BGR 都显示成 102105），
#   取 5 个有时去重后凑不满 3 个，所以这里给 8 个。
STATE_PRODUCT_N = 8

# 领星 OMS 的 salesPlatform 是数字码（文档里那份枚举按 1 起数）：
# 20=TikTok / 31=Shein / 34=Temu，实测 34 全是 BHY/BEL 的 TEMU 单。
PLATFORM_CODE = {"34": "Temu", "31": "SHEIN", "20": "TikTok"}


def build_product_index(rows, city_n: int = 8) -> list[dict]:
    """**全部**款的产品明细索引（面板「产品查询」用）—— 比 build_top_products 多带月度与城市。

    为什么要单独一份：`order-geo.json` 的 `topProducts` 只放 Top60、每款只带 6 个州，
    是给地图 tooltip 用的；查询要能查到**任意一款**，并且看得到「这个款在哪几个月、
    哪些城市出单」。

    体积控制：州留全部（实测每款平均 ~20 个）、城市 Top8、月份全留（10 个），
    整份约 250KB，由页面**按需加载**，不拖累数据大屏。

    口径同 product_state_agg：一单里同款不同尺码只算 1 单。
    """
    st: dict[str, dict] = collections.defaultdict(
        lambda: {"orders": 0, "qty": 0, "states": collections.Counter(),
                 "months": collections.Counter(), "cities": collections.Counter(),
                 "shops": collections.Counter(), "fnskus": collections.Counter()})
    overall: collections.Counter = collections.Counter()
    n_orders = 0
    for r in rows:
        state = r.get("state") or ""
        if state not in CODE:
            continue
        n_orders += 1
        overall[state] += 1
        mon = (r.get("addtime") or "")[:7]
        city = (r.get("city") or "").strip()
        seen = set()
        for it in (r.get("items") or []):
            p = (it.get("product") or "").strip()
            if not p:
                continue
            sty = style_of(p)
            if sty in seen:
                continue
            seen.add(sty)
            e = st[sty]
            e["orders"] += 1
            e["qty"] += int(it.get("qty") or 0)
            e["states"][state] += 1
            e["shops"][r.get("shop", "")] += 1
            if it.get("fnsku"):
                e["fnskus"][it["fnsku"]] += 1
            if re.match(r"^\d{4}-\d{2}$", mon):
                e["months"][mon] += 1
            if city:
                e["cities"][(state, city)] += 1

    total_state = n_orders or 1
    out = []
    for sty, v in sorted(st.items(), key=lambda kv: -kv[1]["orders"]):
        n = v["orders"] or 1
        top_state, top_cnt = v["states"].most_common(1)[0]
        m = re.search(r"\d{4,6}", sty)
        out.append({
            "name": sty,
            # 数字短码（与前端 utils/sale-code.ts 同一套规则）：102105 / 202504 / 1026
            "code": m.group(0) if m else sty.split("-", 1)[-1],
            "orders": v["orders"],
            "qty": v["qty"],
            "share": round(v["orders"] / total_state, 6),
            "topState": top_state,
            "topStateCode": CODE.get(top_state, ""),
            "topStateShare": round(top_cnt / n, 4),
            "lift": round((top_cnt / n) / max(overall.get(top_state, 0) / total_state, 1e-9), 2),
            "shops": {k: c for k, c in v["shops"].most_common()},
            # Fnsku 也放进来：帕沃尔后台是按 Fnsku 找货的，用户手上有 Fnsku 时能直接搜
            "fnskus": [k for k, _ in v["fnskus"].most_common(6)],
            "states": [{"name": s, "code": CODE.get(s, ""), "orders": c,
                        "share": round(c / n, 4)} for s, c in v["states"].most_common()],
            "months": [{"month": mm, "orders": c} for mm, c in sorted(v["months"].items())],
            "cities": [{"name": c, "state": s, "code": CODE.get(s, ""), "orders": k}
                       for (s, c), k in v["cities"].most_common(city_n)],
        })
    return out


def state_product_map(rows) -> dict:
    """州 → Counter(款 → 单量)。口径与 product_state_agg 一致（同单同款去重）。"""
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        st = r.get("state") or ""
        if st not in CODE:
            continue
        seen = set()
        for it in (r.get("items") or []):
            p = (it.get("product") or "").strip()
            if not p:
                continue
            sty = style_of(p)
            if sty in seen:
                continue
            seen.add(sty)
            out[st][sty] += 1
    return out


def attach_products(states: list[dict], rows, top_n: int = STATE_PRODUCT_N) -> None:
    """给每个州挂上「在该州最畅销的款 Top N」——地图 tooltip 与州明细表直接用这个。"""
    sp = state_product_map(rows)
    for s in states:
        c = sp.get(s["name"]) or collections.Counter()
        tot = sum(c.values()) or 1
        s["topProducts"] = [{"name": k, "orders": v, "share": round(v / tot, 4)}
                            for k, v in c.most_common(top_n)]


def build_top_products(rows, limit: int = PRODUCT_LIMIT) -> list[dict]:
    """款维度榜单：总单量 + 自己的 Top6 州 + 头号州/占比/集中度 lift。

    lift = 该款在头号州的占比 ÷ 大盘在该州的占比。x1 = 与大盘同构；
    x≥3 基本就是「专供某州」的款（例：BLA-102104-5SetA 在 PR 是 x9.9）。
    """
    agg = product_state_agg(rows)
    total_state = sum(agg["stateOrder"].values()) or 1
    overall = {s: c / total_state for s, c in agg["stateOrder"].items()}
    ranked = sorted(agg["styles"].items(), key=lambda kv: -kv[1]["orders"])[:limit]
    out = []
    for sty, v in ranked:
        tot = v["orders"] or 1
        top, cnt = v["states"].most_common(1)[0]
        out.append({
            "name": sty,
            "orders": v["orders"],
            "qty": v["qty"],
            "share": round(v["orders"] / total_state, 6),
            "states": _top_states(v["states"], 6),
            "topState": top,
            "topStateCode": CODE.get(top, ""),
            "topStateShare": round(cnt / tot, 4),
            "lift": round((cnt / tot) / max(overall.get(top, 1e-9), 1e-9), 2),
            "shops": {k: n for k, n in v["shops"].most_common()},
        })
    return out


def write_products_xlsx(fn: str, rows) -> str:
    """产品 × 州 多表工作簿：款级交叉表 / 款 Top 州 / 尺码级 / 订单明细。"""
    import openpyxl
    agg = product_state_agg(rows)
    styles, products, pair = agg["styles"], agg["products"], agg["pair"]
    top_states = [s for s, _ in agg["stateOrder"].most_common(20)]

    wb = openpyxl.Workbook()

    # ① 款 × 州 交叉表（Top20 州 + 其他）
    ws = wb.active
    ws.title = "款-州交叉表"
    ws.append(["款", "总单量", "件数", "覆盖州数"] + top_states + ["其他"])
    for sty, v in sorted(styles.items(), key=lambda kv: -kv[1]["orders"]):
        others = sum(c for s, c in v["states"].items() if s not in top_states)
        ws.append([sty, v["orders"], v["qty"], len(v["states"])]
                  + [v["states"].get(s, 0) for s in top_states] + [others])
    ws.freeze_panes = "B2"

    # ② 款 Top 州（人读）
    ws2 = wb.create_sheet("款Top州")
    ws2.append(["款", "总单量", "件数", "Top1州", "Top1占比", "Top2州", "Top2占比",
                "Top3州", "Top3占比", "Top3累计", "覆盖州数", "店铺"])
    for sty, v in sorted(styles.items(), key=lambda kv: -kv[1]["orders"]):
        top = _top_states(v["states"], 3)
        top += [{"name": "", "share": 0}] * (3 - len(top))
        ws2.append([sty, v["orders"], v["qty"],
                    top[0]["name"], top[0]["share"], top[1]["name"], top[1]["share"],
                    top[2]["name"], top[2]["share"], round(sum(t["share"] for t in top[:3]), 6),
                    len(v["states"]),
                    "/".join("%s%d" % (k, n) for k, n in v["shops"].most_common())])
    ws2.freeze_panes = "A2"

    # ③ 尺码级产品明细
    ws3 = wb.create_sheet("产品明细")
    ws3.append(["产品名称", "款", "Fnsku", "店铺", "订单数", "件数", "覆盖州数",
                "Top1州", "Top1单量", "Top1占比", "Top2州", "Top2单量", "Top3州", "Top3单量"])
    for prod, v in sorted(products.items(), key=lambda kv: -kv[1]["orders"]):
        top = _top_states(v["states"], 3)
        top += [{"name": "", "orders": 0, "share": 0}] * (3 - len(top))
        ws3.append([prod, v["style"], "/".join(v["fnsku"].keys()), 
                    "/".join("%s%d" % (k, n) for k, n in v["shops"].most_common()),
                    v["orders"], v["qty"], len(v["states"]),
                    top[0]["name"], top[0]["orders"], top[0]["share"],
                    top[1]["name"], top[1]["orders"], top[2]["name"], top[2]["orders"]])
    ws3.freeze_panes = "A2"

    # ④ 订单明细（含全部产品 + 州）
    ws4 = wb.create_sheet("订单明细")
    ws4.append(["序号", "店铺", "参考单号", "出库单号", "产品名称(全部)", "款", "Fnsku",
                "产品数", "州", "州缩写", "城市", "邮编", "数量", "下单时间", "出库仓库"])
    for i, r in enumerate(sorted(rows, key=lambda x: (x.get("shop", ""), x.get("addtime") or "")), 1):
        items = r.get("items") or []
        prods = [it.get("product", "") for it in items if it.get("product")]
        ws4.append([i, r.get("shop", ""), r.get("ref_no", ""), r.get("tracking", ""),
                    " | ".join(prods) or r.get("product", ""),
                    " | ".join(style_of(p) for p in prods) or style_of(r.get("product", "")),
                    " | ".join(it.get("fnsku", "") for it in items if it.get("fnsku")),
                    len(items) or 1,
                    r.get("state", ""), r.get("state_code", ""), r.get("city", ""),
                    r.get("zip", ""), r.get("qty", 0), r.get("addtime", ""), r.get("warehouse", "")])

    wb.save(fn)
    return fn


def write_products_md(fn: str, rows, topn: int = 30) -> str:
    """可读汇总：款 Top N 的州分布 + 州→款 Top3 反查。"""
    agg = product_state_agg(rows)
    styles, products = agg["styles"], agg["products"]
    ranked = sorted(styles.items(), key=lambda kv: -kv[1]["orders"])
    style_sum = sum(v["orders"] for _, v in ranked) or 1
    n_orders = len([r for r in rows if r.get("state") in CODE]) or 1
    # ⚠ multiItems / n_orders 已经是「比例」，下面格式化时**不再乘 100**（踩过：会变成 1088.7%）
    multi_rate = 100.0 * agg["multiItems"] / n_orders

    lines = ["# 产品 × 州 分布汇总", "",
             "口径：一「单」= 一个帕沃尔出库单（= 一个包裹）；一单可能挂**多个产品**",
             "（实测 %.1f%%），产品明细取自列表页 [详情] 弹窗（`act=order_info&type=view`）。"
             % multi_rate,
             "有产品的单 **%d** 个（%d 单没匹配到产品）。"
             % (n_orders - agg["noProduct"], agg["noProduct"]), "",
             "尺码级产品 %d 个，归并成 **%d 个款**。" % (len(products), len(styles)), "",
             "> 口径提醒：「某款被多少单买过」按**订单去重**（一单里同款不同尺码只算 1 次），",
             "> 所以各款单量之和 %d **≥** 出库单总数 %d（多品单会分别计入不同款）。"
             % (style_sum, n_orders),
             "",
             "## 款 Top %d 的州分布" % topn, ""]
    total = style_sum
    for i, (sty, v) in enumerate(ranked[:topn], 1):
        top = _top_states(v["states"], 6)
        lines.append("### %d. %s · %d 单（占全部 %.1f%%）" % (i, sty, v["orders"],
                                                            100.0 * v["orders"] / total))
        lines.append("")
        lines.append("| 州 | 单量 | 占该款 |")
        lines.append("|---|---|---|")
        for s in top:
            lines.append("| %s (%s) | %d | %.1f%% |" % (s["name"], s["code"], s["orders"],
                                                        s["share"] * 100))
        lines.append("")
        lines.append("该款尺码：%s" % "、".join("%s(%d)" % (p, c)
                                              for p, c in v["products"].most_common()))
        lines.append("")

    # 地域集中度：Top1 州占比 vs 大盘该州占比（lift），最能看出「这款是卖给谁」
    total_state = sum(agg["stateOrder"].values()) or 1
    overall = {s: c / total_state for s, c in agg["stateOrder"].items()}
    conc = []
    for sty, v in ranked:
        n = v["orders"]
        if n < 200:
            continue
        top, cnt = v["states"].most_common(1)[0]
        base = overall.get(top, 1e-9)
        conc.append((sty, n, top, cnt / n, base, (cnt / n) / base))
    conc.sort(key=lambda x: -x[5])
    lines += ["## 地域最集中的款（头号州占比 / 大盘同州占比 = 集中度，样本 ≥200 单）", "",
              "| 款 | 单量 | 头号州 | 该州占比 | 大盘该州占比 | 集中度 |", "|---|---|---|---|---|---|"]
    for sty, n, top, share, base, lift in conc[:20]:
        lines.append("| %s | %d | %s (%s) | %.1f%% | %.1f%% | **x%.1f** |"
                     % (sty, n, top, CODE.get(top, ""), share * 100, base * 100, lift))
    lines += ["", "> 集中度 = 该款在头号州的占比 ÷ 大盘在该州的占比。x1 = 与大盘同构；",
              "> **x≥3 说明这款几乎只在一个州卖**（例：BLA-102104-5SetA x9.9 → 波多黎各专供款）。",
              ""]

    lines += ["## 反查：各州的主力款 Top3", ""]
    for st in [s for s, _ in agg["stateOrder"].most_common(15)]:
        rows_st = [(sty, c) for (sty, s), c in agg["pair"].items() if s == st]
        rows_st.sort(key=lambda x: -x[1])
        tot = agg["stateOrder"][st]
        lines.append("- **%s (%s)** %d 单 → %s" % (
            st, CODE.get(st, ""), tot,
            "、".join("%s(%d, %.0f%%)" % (a, b, 100.0 * b / tot) for a, b in rows_st[:3])))
    lines.append("")

    with open(fn, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fn


def write_states_csv(fn: str, states) -> None:
    global csv_written
    with open(fn, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["排名", "州", "缩写", "订单数", "占比", "件数", "主要城市"])
        for i, s in enumerate(states, 1):
            w.writerow([i, s["name"], s["code"], s["orders"], "%.2f%%" % (s["share"] * 100),
                        s["qty"],
                        "、".join("%s(%d)" % (c["name"], c["orders"]) for c in s["cities"][:3])])
    csv_written = fn


def write_orders_xlsx(fn: str, merged) -> str:
    """全量订单明细（含州/市/邮编）—— 用户要的「导出 1 月到现在所有订单」。"""
    import openpyxl
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("订单明细")
    ws.append(["序号", "店铺", "参考单号", "出库单号", "州", "州缩写", "城市", "邮编", "数量",
               "计费重量(lb)", "出库仓库", "出货渠道", "下单时间", "金额", "SKU", "帕沃尔出库单ID"])
    for i, m in enumerate(sorted(merged, key=lambda x: (x["shop"], x["addtime"] or "")), 1):
        ws.append([i, m["shop"], m["ref_no"], m["tracking"], m["state"], m["state_code"],
                   m["city"], m["zip"], m["qty"], m["weight"], m["warehouse"], m["channel"],
                   m["addtime"], m["amount"], m["skus"], m["order_id"]])
    wb.save(fn)
    return fn


def write_summary(fn: str, a, agg, stores, merged, withgeo) -> str:
    lines = ["# 订单分布汇总（%s ~ %s）" % (a.start, a.end), "",
             "数据源：帕沃尔海外仓(powerwarehouse.net) 发货列表 + 逐单「收件地址」。",
             "已定位 %d / %d 单（%.1f%%）；一单 = 一个出库单（一个包裹）。"
             % (len(withgeo), len(merged), 100.0 * len(withgeo) / max(len(merged), 1)), ""]

    # 月度趋势（最能看出「哪个州的量是哪一阵子来的」）
    if agg["months"]:
        lines += ["## 月度趋势（两店合计）", "",
                  "| 月份 | 单量 | 头号州 | 第二 | 第三 |", "|---|---|---|---|---|"]
        for m in agg["months"]:
            t = list(m["topStates"]) + [{"name": "-", "orders": 0}] * 2
            lines.append("| %s | %d | %s | %s | %s |" % (
                m["month"], m["orders"],
                *["%s(%d)" % (x["name"], x["orders"]) if x["orders"] else "-" for x in t[:3]]))
        lines.append("")

    # 城市 Top 20
    if agg["topCities"]:
        lines += ["## 城市 Top 20（两店合计）", "",
                  "| 排名 | 城市 | 州 | 订单数 |", "|---|---|---|---|"]
        for i, c in enumerate(agg["topCities"][:20], 1):
            lines.append("| %d | %s | %s (%s) | %d |" % (i, c["name"], c["state"], c["code"],
                                                        c["orders"]))
        lines.append("")

    for key in ("ALL", *stores.keys()):
        if key == "ALL":
            states, total, title = agg["states"], len(withgeo), "两店合计"
        else:
            states, total = stores[key]["states"], stores[key]["geoOrders"]
            title = "%s（%s）" % (key, stores[key]["shopId"])
        lines += ["## %s · %d 单" % (title, total), "",
                  "| 排名 | 州 | 订单数 | 占比 | 累计 | 主要城市 |",
                  "|---|---|---|---|---|---|"]
        acc = 0.0
        for i, s in enumerate(states[:20], 1):
            acc += s["share"]
            lines.append("| %d | %s (%s) | %d | %.2f%% | %.1f%% | %s |" % (
                i, s["name"], s["code"], s["orders"], s["share"] * 100, acc * 100,
                "、".join("%s(%d)" % (c["name"], c["orders"]) for c in s["cities"][:3])))
        lines.append("")
    with open(fn, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fn


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description="帕沃尔订单地理分布采集/聚合")
    ap.add_argument("cmd", choices=["list", "geo", "products", "export", "build", "all"])
    ap.add_argument("--out", default=DEFAULT_OUT, help="中间产物目录")
    ap.add_argument("--oms-out", default="",
                    help="领星 OMS 域产物目录（默认 <--out 的同级>/oms），里面读 oms_orders.jsonl")
    ap.add_argument("--oms-stores", default="BEL,BHY",
                    help="只并这些 OMS 店铺进本页（默认 BEL,BHY）；传 all 表示全并")
    ap.add_argument("--recent-days", type=int, default=7,
                    help="「近 N 天 · 各州畅销产品」的窗口天数（默认 7）")
    ap.add_argument("--admin", default=DEFAULT_ADMIN, help="temu-admin 目录")
    ap.add_argument("--shops", nargs="*", default=[], help="只跑指定店（BEL/BHY）")
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--page-size", type=int, default=300, help="列表每页行数（服务端上限 ~300）")
    ap.add_argument("--workers", type=int, default=6, help="并发（别再往上加，服务端会限流）")
    ap.add_argument("--limit", type=int, default=0, help="geo 只抓前 N 单（调试用）")
    ap.add_argument("--max-per-file", type=int, default=10000,
                    help="自带导出每个文件最大行数（20000 会让服务端 500）")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    steps = {"list": cmd_list, "geo": cmd_geo, "products": cmd_products,
             "export": cmd_export, "build": cmd_build}
    if a.cmd == "all":
        for name in ("list", "geo", "products", "export", "build"):
            print("\n===== %s =====" % name, flush=True)
            steps[name](a)
    else:
        steps[a.cmd](a)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PWError as e:
        print("[FAIL] %s" % e, file=sys.stderr)
        raise SystemExit(2)
