# -*- coding: utf-8 -*-
"""一次性补丁：把 BE 加进 hx_ship 的 disabled_stores，并留痕原因。

2026-11-15 用户令：「BE 店铺的库存只在帕沃尔仓，在恒信领星发货直接跳过」。
"""
import json
import os

P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "power", "data", "temu_fulfill_pipeline.json")


def main() -> int:
    d = json.loads(open(P, encoding="utf-8").read())
    for st in d["steps"]:
        if st.get("name") != "hx_ship":
            continue
        off = [str(x) for x in (st.get("disabled_stores") or [])]
        if "BE" not in off:
            off.append("BE")
        st["disabled_stores"] = off
        st["_2026-11-15_加_BE"] = (
            "★ 用户 2026-11-15 令：「BE 店铺的库存只在帕沃尔仓，在恒信领星发货直接跳过」"
            "⇒ 本步对 BE 禁用（BE 的单永远不进领星 OMS）；拆仓也只查帕沃尔"
            "（bel_split_by_stock.PW_ONLY_STORES），BE 的「恒信仓」sheet 由 "
            "gen_shortage_fail_xlsx 一并去掉。")
        print("disabled_stores:", json.dumps(off, ensure_ascii=False))
    open(P, "w", encoding="utf-8", newline="\n").write(
        json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    print("written", P)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
