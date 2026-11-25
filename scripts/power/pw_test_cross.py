# -*- coding: utf-8 -*-
"""修 3 单 CROSS_WH 验证「换 Fnsku + 改仓」逻辑（用户 2026-11-25 追加口径）。

CROSS_WH 定义：目标 Fnsku 有货，但货在**另一个仓**（如 加州3仓 → kansas仓）
⇒ 必须 换 Fnsku（三件套）**并且** 改仓渠道，再重下单。

本脚本从 `_logs/pw_fail_plans.json` 取前 N 个 CROSS_WH 计划执行。
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_fix_with_wh as W  # noqa: E402
import pw_session as SS  # noqa: E402

PLANS = r"D:\AI-workplace\TEMU\_logs\pw_fail_plans.json"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def main():
    plans = json.load(open(PLANS, encoding="utf-8"))
    cross = [p for p in plans if p.get("plan") == "CROSS_WH" and p.get("po")]
    # 去重：同一个目标 Fnsku 只修一单（库存少，避免抢同一件）
    seen, todo = set(), []
    for p in cross:
        key = p.get("target_fnsku")
        if key in seen:
            continue
        seen.add(key)
        todo.append(p)
        if len(todo) >= N:
            break

    print("准备验证 %d 单（每个目标 Fnsku 只取一单）：\n" % len(todo))
    for p in todo:
        print("  %-20s %s → %s（%s，库存 %s）"
              % (p["po"], p.get("wh"), p.get("target_wh"),
                 p.get("target_seller"), p.get("stock")))

    pw = SS.PW()
    pw.login()
    ok, bad = [], []
    for p in todo:
        print("\n▶ %s" % p["po"])
        kw = re.sub(r"^[A-Z]{2,4}-", "", p.get("sku") or "")
        try:
            r = W.pick_and_fix(pw, p["po"], kw, apply=True, log=print)
        except Exception as ex:  # noqa: BLE001
            bad.append([p["po"], "异常 %s" % str(ex)[:90]])
            continue
        if r.get("ok"):
            ok.append(p["po"])
        else:
            bad.append([p["po"], r.get("why") or r.get("err") or "失败"])

    print("\n=== 验证结果 ===")
    print("  成功 %d / 失败 %d" % (len(ok), len(bad)))
    for p, why in bad:
        print("    ✗ %-20s %s" % (p, str(why)[:90]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
