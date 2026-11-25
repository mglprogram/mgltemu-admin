# -*- coding: utf-8 -*-
"""只处理用户给的 9 个单（GSU...），逐个：查库存 → 换 Fnsku → 必要时改仓 → 重下。

★ 用户 2026-11-25：「你先解决我给你的订单，其他的不管」

单号：
    GSU1A864F000J0G  GSU1A837F00NV1N  GSU1A851T00NT2R
    GSU1A825Q000HF0  GSU1A842200MB59  GSU1AY53000N07F
    GSU1AY11100MK5J  GSU1AX42B0023LC  GSU1AX13B001N8C

用法：
    python pw_fix_nine.py            # 先诊断（只读）
    python pw_fix_nine.py --apply    # 真修
"""
import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_fix_by_fnsku as FX  # noqa: E402
import pw_fix_with_wh as W  # noqa: E402
import pw_session as SS  # noqa: E402

POS = [
    "GSU1A864F000J0G", "GSU1A837F00NV1N", "GSU1A851T00NT2R",
    "GSU1A825Q000HF0", "GSU1A842200MB59", "GSU1AY53000N07F",
    "GSU1AY11100MK5J", "GSU1AX42B0023LC", "GSU1AX13B001N8C",
]


def retry(fn, n=3, wait=4):
    last = None
    for _ in range(n):
        try:
            return fn()
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(wait)
    raise last


def main():
    apply_ = "--apply" in sys.argv
    pw = SS.PW()
    pw.login()

    print("=== 9 单诊断 ===\n")
    plans = []
    for po in POS:
        try:
            st = retry(lambda: FX.state(pw, po))
        except Exception as ex:  # noqa: BLE001
            print("  %-20s 查单失败 %s" % (po, str(ex)[:60]))
            plans.append({"po": po, "plan": "QUERY_FAIL", "why": str(ex)[:60]})
            continue
        if not st.get("id"):
            print("  %-20s ❌ 不在帕沃尔（需先同步）" % po)
            plans.append({"po": po, "plan": "NOT_FOUND", "why": "订单不在帕沃尔"})
            continue

        kw = re.sub(r"^[A-Z]{2,4}-", "", st.get("sku") or "")
        try:
            cands = [c for c in retry(lambda: W.stock_candidates(pw, kw))
                     if c["stock_n"] > 0]\
                if kw else []
        except Exception as ex:  # noqa: BLE001
            print("  %-20s 库存查询失败 %s" % (po, str(ex)[:50]))
            plans.append({"po": po, "id": st["id"], "plan": "QUERY_FAIL",
                          "why": str(ex)[:50]})
            continue

        if not cands:
            print("  %-20s %-8s %-20s → ❌ 整款无货（需补货）"
                  % (po, st["status"], st["sku"]))
            plans.append({"po": po, "id": st["id"], "status": st["status"],
                          "sku": st["sku"], "plan": "NEED_RESTOCK"})
            continue

        best = max(cands, key=lambda x: (x["stock_n"], x["wh"] == st["wh"]))
        plan = "SAME_WH" if best["wh"] == st["wh"] else "CROSS_WH"
        print("  %-20s %-8s %-20s 仓=%-8s → %s %s(%s 库存%s) %s"
              % (po, st["status"], st["sku"], st["wh"],
                 best["fnsku"], best["seller"], best["wh"], best["stock"],
                 "【改仓】" if plan == "CROSS_WH" else ""))
        plans.append({"po": po, "id": st["id"], "status": st["status"],
                      "sku": st["sku"], "wh": st["wh"],
                      "target_fnsku": best["fnsku"], "target_wh": best["wh"],
                      "plan": plan})

    with open(r"D:\AI-workplace\TEMU\_logs\pw_nine.json", "w",
              encoding="utf-8") as f:
        json.dump(plans, f, ensure_ascii=False, indent=1)

    todo = [p for p in plans if p["plan"] in ("SAME_WH", "CROSS_WH")]
    print("\n=== 汇总 ===")
    for k in ("SAME_WH", "CROSS_WH", "NEED_RESTOCK", "NOT_FOUND", "QUERY_FAIL"):
        n = len([p for p in plans if p["plan"] == k])
        if n:
            print("  %-12s %d" % (k, n))
    print("\n可修 %d 单" % len(todo))

    if not apply_:
        print("[dry-run] 加 --apply 才真修")
        return 0
    if not todo:
        return 0

    print("\n=== 真修 ===")
    ok, bad = [], []
    for p in todo:
        print("\n▶ %s（%s）" % (p["po"], p["plan"]))
        kw = re.sub(r"^[A-Z]{2,4}-", "", p.get("sku") or "")
        try:
            r = W.pick_and_fix(pw, p["po"], kw, apply=True, log=print)
        except Exception as ex:  # noqa: BLE001
            bad.append([p["po"], "异常 %s" % str(ex)[:80]])
            continue
        if r.get("ok"):
            ok.append(p["po"])
        else:
            bad.append([p["po"], r.get("why") or r.get("err") or "失败"])

    print("\n=== 最终结果 ===")
    print("  成功 %d / 失败 %d" % (len(ok), len(bad)))
    for p in ok:
        print("    ✅ %s" % p)
    for p, why in bad:
        print("    ✗ %-20s %s" % (p, str(why)[:80]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
