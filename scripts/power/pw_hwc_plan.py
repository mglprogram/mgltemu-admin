# -*- coding: utf-8 -*-
"""pw_hwc_plan.py — 为「下单失败/缺货」订单生成 SKU 换货计划（只读）。

用户教的规则（2026-11-06，已固化）：
    1. 库存页按 Seller sku 查（去掉变体末尾纯数字）
    2. 选**该仓有代发库存**、且**库龄天数最高**的 Fnsku
    3. 换到订单编辑页 ck_sku[]，再重新下单代发
    4. 阈值：代发库存必须 > 0（严格要求时 > 10，见 powerwarehouse-ops skill）

输出 plan.json：
    [{"po":..., "id":..., "sku":"BHY-...", "wh":"加州3仓", "err":"...",
      "fnsku":"...", "avail":n, "age":x, "why":""}]
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))

import pw_hwc_stock as ST  # noqa: E402
import pw_session as S  # noqa: E402

OUT = os.path.join(HERE, "out", "2026-11-06")
#: 订单里已经带上的 Fnsku（错误提醒里会写 -sku:<fnsku>库存不足发货，那是**该单当前**的 Fnsku）
CUR = re.compile(r"sku:([A-Z0-9]{8,})")


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(OUT, "hwc_all_fail.json")
    rows = json.load(open(src, encoding="utf-8"))
    bad = [x for x in rows if x["status"] != "已下单"]
    print("失败单 %d" % len(bad))

    pw = S.PW()
    pw.login()
    cache = {}
    # ★ 库存必须**按订单逐单扣减**：同一 SKU 两个订单不能都分到只剩 1 件的那个 Fnsku，
    #   否则第二单必然再失败（实测 204207-4SetA-S 就踩到这个）。
    left = {}
    plan = []
    for x in bad:
        sku = (x.get("sku") or "").split(" / ")[0].strip()
        wh = x.get("wh") or ""
        cur_fn = (CUR.search(x.get("err") or "") or [None, ""])[1]
        q = ST.relax(sku)
        key = (q, wh)
        if key not in cache:
            try:
                cache[key] = ST.stock_rows(pw, q)
            except Exception as e:  # noqa: BLE001
                cache[key] = []
                print("   [warn] 查库存失败 %s: %s" % (q, str(e)[:60]))
            left[key] = {c["fnsku"]: float(ST.pick([c], "", 0)[0]["avail_n"] if ST.pick([c], "", 0) else 0)
                         for c in cache[key]}
        allrows = cache[key]
        # 本仓候选（按库龄降序），且库存余量 > 0、不是订单当前那个 Fnsku
        cand = [c for c in ST.pick(allrows, wh, 1)
                if c["fnsku"] != cur_fn and left[key].get(c["fnsku"], 0) > 0]
        pick = cand[0] if cand else None
        if pick:
            left[key][pick["fnsku"]] -= 1
        item = {"po": x["po"], "id": x["id"], "sku": sku, "wh": wh,
                "err": (x.get("err") or "")[:120], "cur_fn": cur_fn,
                "fnsku": pick["fnsku"] if pick else "",
                "avail": pick["avail"] if pick else "",
                "age": pick["age"] if pick else "",
                "why": "" if pick else "该仓无（余量足够的）有代发库存 Fnsku",
                "cands": [{"fn": c["fnsku"], "wh": c["wh"], "avail": c["avail"],
                           "age": c["age"]} for c in cand[:4]],
                "all": [{"fn": c["fnsku"], "wh": c["wh"], "avail": c["avail"],
                         "age": c["age"]} for c in allrows]}
        plan.append(item)
        print("  %-28s %-8s %-22s 现Fn=%s → 新Fn=%s (代发%s/库龄%s)"
              % (item["po"], wh, sku, cur_fn or "-", item["fnsku"] or "无",
                 item["avail"], item["age"]))

    p = os.path.join(OUT, "hwc_fix_plan.json")
    json.dump(plan, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ok = [x for x in plan if x["fnsku"]]
    print("\n可修 %d / 失败 %d；计划落盘 %s" % (len(ok), len(plan) - len(ok), p))
    from collections import Counter
    print("仓库分布:", dict(Counter(x["wh"] for x in ok)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
