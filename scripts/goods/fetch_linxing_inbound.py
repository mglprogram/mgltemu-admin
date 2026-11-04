#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_linxing_inbound.py — 领星入库单（在途）按 SKU 聚合，给「在途列」提供可点的单号。

为什么要它（2026-11-04 用户需求）
--------------------------------
领星库存接口 `/integratedInventory/pageOpen` 只会给一个 `transportAmount`（在途件数），
**不告诉你在途在哪张单上**。用户要的是「点一下就看到这个尺码在哪几张入库单里、什么时候到」。

做法
----
  ① `/openapi/v1/inboundOrder/pageList` 分页拉全部入库单（实测 346 张，每页 50，7 页）
  ② `/openapi/v1/inboundOrder/detail` 批量取明细（**一次最多 50 个单号**，100 → code=10002）
     返回里 `inboundSkuVOList` 有 productName / quantity / receivedQuantity
  ③ 只留「还没到齐」的单：quantity - receivedQuantity > 0，且没取消
  ④ 按 `款号-套系-尺码`（复用 fetch_stock_track.split_sku_any）聚合成
     {"款|套系|尺码": [{no, qty, inbound, expected, status}, ...]}

⚠ 每次批量 50 单要 **8~9 秒**（接口本身慢，不是网络），346 单 ≈ 1 分钟。
   所以它放在定时任务里跑、结果落盘给前端读，不在页面里现查。

产出
----
    temu-admin/public/data/linxing-inbound.json
    {
      "generatedAt": "...", "orders": 346, "open": N,
      "total": {"款|套系|尺码": 待入库件数},
      "map":   {"款|套系|尺码": [{no, qty, inbound, expected, status}]}
    }

退出码：0 成功；2 凭证/接口不可用；4 落盘失败
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "temu-admin" / "public" / "data" / "linxing-inbound.json"

sys.path.insert(0, str(ROOT / "scripts" / "oms"))
sys.path.insert(0, str(ROOT / "scripts" / "oms" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

PAGE_PATH = "/openapi/v1/inboundOrder/pageList"
DETAIL_PATH = "/openapi/v1/inboundOrder/detail"

PAGE_SIZE = 50          # 实测最大 50
DETAIL_BATCH = 50       # 实测上限 50（100 → code=10002）
MAX_ORDERS = 1500       # 保险：单号太多就截断（正常情况下 350 左右）

#: 入库单状态 → 中文（领星 status 是 int，实测 1 = 待到货）
STATUS_TEXT = {0: "草稿", 1: "待到货", 2: "收货中", 3: "已上架", 4: "已取消"}


def split_sku(sku: str):
    """复用 fetch_stock_track 的通用解析，保证和库存那边「款|套系|尺码」口径一致。"""
    try:
        from fetch_stock_track import split_sku_any as _f  # noqa: PLC0415
        return _f(sku)
    except Exception:  # noqa: BLE001
        return None


def load_creds() -> dict:
    from fetch_linxing_stock import load_creds as _f  # noqa: PLC0415
    return _f()


def num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def list_orders(c) -> list[str]:
    nos: list[str] = []
    page = 1
    while len(nos) < MAX_ORDERS:
        d = c.post(PAGE_PATH, {"page": page, "pageSize": PAGE_SIZE}).get("data") or {}
        recs = d.get("records") or []
        for r in recs:
            if isinstance(r, dict) and r.get("inboundOrderNo"):
                nos.append(r["inboundOrderNo"])
        if len(recs) < PAGE_SIZE:
            break
        page += 1
    return nos


def fetch_details(c, nos: list[str]) -> list[dict]:
    """批量取明细；单批失败重试一次，还失败就跳过（不整批报废）。"""
    out: list[dict] = []
    for i in range(0, len(nos), DETAIL_BATCH):
        batch = nos[i:i + DETAIL_BATCH]
        for attempt in range(2):
            try:
                arr = c.post(DETAIL_PATH, {"inboundOrderNoList": batch}).get("data") or []
                out += [o for o in arr if isinstance(o, dict)]
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 0:
                    time.sleep(2)
                    continue
                print("[inbound] 第 %d 批失败，跳过：%s" % (i // DETAIL_BATCH + 1, str(e)[:90]))
        if (i // DETAIL_BATCH + 1) % 2 == 0:
            print("[inbound] 进度 %d/%d 单" % (min(i + DETAIL_BATCH, len(nos)), len(nos)))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="领星入库单（在途）按 SKU 聚合")
    ap.add_argument("--dry", action="store_true", help="只打印统计，不落盘")
    args = ap.parse_args(argv)

    try:
        from lib.oms_client import OmsClient, OmsError  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print("[inbound] 领星客户端不可用：%s" % str(e)[:150])
        return 2

    creds = load_creds()
    if "LM739" not in creds:
        print("[inbound] LM739 缺凭证，跳过")
        return 2
    k, s = creds["LM739"]

    try:
        c = OmsClient(k, s, timeout=90, verbose=False)
        nos = list_orders(c)
        print("[inbound] 入库单 %d 张，开始取明细（每批 %d 张）" % (len(nos), DETAIL_BATCH))
        orders = fetch_details(c, nos)
    except OmsError as e:
        print("[inbound] 拉取失败：%s" % str(e)[:160])
        return 2
    except Exception as e:  # noqa: BLE001
        print("[inbound] 异常：%s" % str(e)[:160])
        return 2

    today = date.today().isoformat()
    m: dict[str, list[dict]] = defaultdict(list)
    tot: dict[str, float] = defaultdict(float)
    open_orders = 0

    for o in orders:
        st = o.get("status")
        if st in (4,):           # 已取消
            continue
        expected = str(o.get("expectedDate") or "")
        wh = str(o.get("whName") or o.get("whCode") or "")
        for sk in (o.get("inboundSkuVOList") or []):
            name = str(sk.get("productName") or "")
            p = split_sku(name)
            if not p:
                continue
            key = "%s|%s|%s" % p
            qty = num(sk.get("quantity"))
            got = num(sk.get("receivedQuantity"))
            left = qty - got
            if left <= 0:
                continue
            m[key].append({
                "no": str(o.get("inboundOrderNo") or ""),
                "qty": int(left),
                "inbound": int(qty),
                "expected": expected,
                "status": STATUS_TEXT.get(st, str(st) if st is not None else ""),
                "wh": wh,
                "late": bool(expected) and expected < today,
            })
            tot[key] += left
        open_orders += 1

    for k2 in m:
        m[k2].sort(key=lambda x: (x["expected"] or "9999-99-99"))

    payload = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "orders": len(nos),
        "open": open_orders,
        "skus": len(m),
        "total": {k2: int(v) for k2, v in tot.items()},
        "map": {k2: v for k2, v in m.items()},
        "rule": {
            "note": "只统计还没到齐的入库单（quantity - receivedQuantity > 0，排除已取消）。"
                    "在途件数以领星库存接口的 transportAmount 为准，这里给的是「在途在哪几张单」。",
            "source": "领星 OMS %s + %s（LM739 恒信）" % (PAGE_PATH, DETAIL_PATH),
        },
    }

    if args.dry:
        print(json.dumps({x: y for x, y in payload.items() if x != "map"},
                         ensure_ascii=False, indent=2))
        return 0

    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print("[inbound] 落盘失败：%s" % str(e)[:200])
        return 4

    print("[inbound] 已写 %s（%d 张单，%d 个 SKU 有待入库，%.1f KB）"
          % (OUT.relative_to(ROOT), open_orders, len(m), OUT.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
