# -*- coding: utf-8 -*-
"""领星 OMS「库存流水」→ 反推【产品上架】时刻，统计上架时间分布。

用户 2026-11-03：
    「去恒信的 LM739 的库存流水 → 找变化量为加库存的行（找近一个月的数据），
      这个行为被称为产品上架 → 然后帮我计算出一个平均的上架时间，
      然后按照这个时间去帮我重新设定更新时间」

口径
----
* 接口：`POST /openapi/v1/integratedInventory/pageStockFlow`（"分页查询库存流水"）
  - pageSize 上限 **100**（200 → code=10001 每页条数最大）
  - `startTime` / `endTime` 生效（YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS 都吃）
  - 排序是 operateTime **倒序**，所以最新的在最前
* 「产品上架」= `changeAmount > 0` 且关联单据是**入库单**（relateOrderTypeName=入库单）。
  一票入库单会**按 SKU 各出一行**（同一秒），所以必须按 `relateOrderNo` 聚合，
  取**最早的那一秒**作为这票货的上架时刻；不聚合会把 SKU 数当成票数。
* 统计的是**时刻**（HH:MM），因为要看"仓库一般几点把货上架"，
  不是"到港后几天上架"（那是 shipment-track 里到港→上架的天数，两者别混）。

用法
----
    python scripts/oms/fetch_stock_flow_shelf.py            # 近 30 天，打印统计
    python scripts/oms/fetch_stock_flow_shelf.py --days 60
    python scripts/oms/fetch_stock_flow_shelf.py --dump out/oms/shelf_flow.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "oms" / "lib"))

FLOW_PATH = "/openapi/v1/integratedInventory/pageStockFlow"
PAGE_SIZE = 100          # 实测上限（200 → code=10001）
ACCOUNT = "LM739"        # 恒信（ZOO WAREHOUSE ⑥美西 / USW01）


def log(msg: str) -> None:
    print(msg, flush=True)


def load_account(tag: str = ACCOUNT) -> tuple[str, str]:
    p = ROOT / "scripts" / "oms" / "oms_accounts.local.json"
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    for a in data.get("accounts") or []:
        if a.get("tag") == tag:
            return a["appKey"], a["appSecret"]
    raise SystemExit(f"oms_accounts.local.json 里没有 {tag}")


def fetch_flow(days: int = 30, workers: int = 4) -> tuple[list[dict], list[int]]:
    """把近 N 天的库存流水全量翻下来（只保留正向变动）。"""
    from oms_client import OmsClient  # noqa: E402

    key, secret = load_account()
    client = OmsClient(key, secret, verbose=False)

    end = date.today()
    start = end - timedelta(days=days)
    win = {"startTime": f"{start} 00:00:00", "endTime": f"{end} 23:59:59"}

    first = client.post(FLOW_PATH, {"page": 1, "pageSize": PAGE_SIZE, **win})
    total = int((first.get("data") or {}).get("total") or 0)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    log(f"① 库存流水 {start} ~ {end}：{total} 条 / {pages} 页")

    rows: list[dict] = list((first.get("data") or {}).get("records") or [])
    failed: list[int] = []

    def grab(p: int, tries: int = 6, base_wait: float = 1.5) -> list[dict]:
        # ⚠ 库存流水接口**限流很紧**：8 路并发跑 139 页时大片 429（实测第一轮掉 54 页、
        #   第二轮 4 路仍掉 10 页）。429 绝不能当"这页没数据"——少几页就等于漏掉
        #   某一天的入库流水，而上架样本本来一天只有 4~14 票，漏一天就把均值带偏。
        #   所以：指数退避重试 → 仍失败的页记进 failed，最后由串行补抓一遍。
        for attempt in range(tries):
            try:
                r = client.post(FLOW_PATH, {"page": p, "pageSize": PAGE_SIZE, **win},
                                retries=1)
                return (r.get("data") or {}).get("records") or []
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "429" in msg and attempt < tries - 1:
                    time.sleep(base_wait * (attempt + 1))
                    continue
                log(f"   ! 第 {p} 页失败：{msg[:80]}")
                failed.append(p)
                return []
        return []

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for recs in pool.map(grab, range(2, pages + 1)):
            rows += recs
            done += 1
            if done % 40 == 0:
                log(f"   {done}/{pages - 1}")

    if failed:
        log(f"   补抓 {len(failed)} 页（串行 + 慢速）…")
        still = []
        for n, p in enumerate(sorted(set(failed)), 1):
            got = grab(p, tries=8, base_wait=4.0)
            if got:
                rows += got
            else:
                still.append(p)
            time.sleep(0.8)
            if n % 10 == 0:
                log(f"     补抓 {n}/{len(set(failed))}")
        if still:
            log(f"   !! 仍有 {len(still)} 页没取到：{still[:20]}")

    # 跨页可能重叠（翻页期间有新流水插入），按 recordId 去重
    seen: dict[str, dict] = {}
    for r in rows:
        seen[str(r.get("recordId"))] = r
    out = list(seen.values())
    log(f"   实取 {len(out)} 条（去重后）")
    return out, sorted(set(failed))


def shelf_events(rows: list[dict]) -> list[dict]:
    """正向变动 + 入库单 → 按入库单聚合，取最早时刻 = 这票货上架的时刻。"""
    per: dict[str, dict] = {}
    for r in rows:
        try:
            amt = float(str(r.get("changeAmount") or "0").replace(",", ""))
        except ValueError:
            continue
        if amt <= 0:
            continue
        if "入库" not in str(r.get("relateOrderTypeName") or ""):
            continue
        no = str(r.get("relateOrderNo") or "")
        t = str(r.get("operateTime") or "")
        if not no or not t:
            continue
        e = per.get(no)
        if e is None:
            per[no] = {"inboundNo": no, "first": t, "last": t, "skuRows": 1,
                       "qty": int(amt), "wh": str(r.get("whName") or "")}
        else:
            e["skuRows"] += 1
            e["qty"] += int(amt)
            if t < e["first"]:
                e["first"] = t
            if t > e["last"]:
                e["last"] = t
    return sorted(per.values(), key=lambda x: x["first"])


def stats(events: list[dict]) -> dict:
    """按 HH:MM 统计上架时刻。"""
    mins = sorted(int(e["first"][11:13]) * 60 + int(e["first"][14:16]) for e in events)
    if not mins:
        return {}
    out = {
        "n": len(mins),
        "mean": statistics.mean(mins),
        "median": statistics.median(mins),
        "min": mins[0],
        "max": mins[-1],
    }
    for p in (50, 75, 85, 90, 95):
        out[f"p{p}"] = mins[min(len(mins) - 1, int(len(mins) * p / 100))]
    return out


def hhmm(m: float) -> str:
    m = int(round(m))
    return "%02d:%02d" % (m // 60, m % 60)


def main() -> int:
    ap = argparse.ArgumentParser(description="库存流水反推产品上架时刻")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dump", default="")
    a = ap.parse_args()

    rows, missing = fetch_flow(a.days)
    ev = shelf_events(rows)
    if not ev:
        log("没有正向入库流水（加库存）——检查时间窗或账号")
        return 2

    log(f"② 产品上架（变化量为正 + 关联入库单）：{len(ev)} 票 / "
        f"{sum(e['skuRows'] for e in ev)} 条 SKU 行"
        + (f"　⚠ 有 {len(missing)} 页没取到，样本可能不全" if missing else ""))
    st = stats(ev)
    log(f"③ 上架时刻：均值 {hhmm(st['mean'])}｜中位 {hhmm(st['median'])}"
        f"｜P75 {hhmm(st['p75'])}｜P90 {hhmm(st['p90'])}｜P95 {hhmm(st['p95'])}"
        f"｜最早 {hhmm(st['min'])}｜最晚 {hhmm(st['max'])}")

    hours = Counter(int(e["first"][11:13]) for e in ev)
    log("   小时分布 " + " ".join(f"{h}点{c}" for h, c in sorted(hours.items())))

    byday: dict[str, list[str]] = {}
    for e in ev:
        byday.setdefault(e["first"][:10], []).append(e["first"])
    log(f"   有上架的日期 {len(byday)} 天，单日均 {len(ev) / len(byday):.1f} 票")
    for d in sorted(byday)[-8:]:
        v = sorted(byday[d])
        log(f"     {d}  {len(v):>2} 票  {v[0][11:16]} ~ {v[-1][11:16]}")

    if a.dump:
        p = Path(a.dump)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "days": a.days, "stats": st, "missingPages": missing,
                                 "events": ev},
                                ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"   → {a.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
