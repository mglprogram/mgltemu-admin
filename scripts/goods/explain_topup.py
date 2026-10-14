# -*- coding: utf-8 -*-
"""逐项列出某款「加库存」的依据（口径可复核）。

回答：为什么给这一款加这些尺码、各加多少。

⚠ 口径必须与 `run_stock_topup.py` / `enrich_restock.py` 一致：
  加量依据 = **加州仓 + kansas 仓**的现货之和（用户 2026-10-12 修正）；加库存动作只落加州仓；
  ≥ 充足阈值 → 默认加量；否则 现货×比例（不足保底）；且不超过该合计现货。

用法：python scripts/goods/explain_topup.py BHY BHY-102303-6SetA
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
import temu_stock_topup as T  # noqa: E402

GOODS = ROOT / "temu-admin" / "public" / "data" / "goods.json"
CFG = ROOT / "scripts" / "power" / "data" / "temu_restock.json"


def main() -> int:
    store = (sys.argv[1] if len(sys.argv) > 1 else "BHY").upper()
    code = sys.argv[2] if len(sys.argv) > 2 else "BHY-102303-6SetA"
    cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    t = cfg["加库存"]
    keys = t.get("仓库关键字") or ["加州", "kansas"]
    if isinstance(keys, str):
        keys = [keys]

    print("== 口径（%s → 加库存）==" % CFG.name)
    for k in ("TEMU库存上限", "仓内现货下限", "默认加量", "充足阈值", "不足比例",
              "不足保底"):
        print("   %-12s %s" % (k, t.get(k)))
    print("   %-12s %s" % ("仓库关键字", keys))
    print("   加量规则     : 加州+kansas 现货 ≥ %s → 加 %s；否则 ×%s（不足 %s 按 %s）；"
          "且不超过该合计现货"
          % (t["充足阈值"], t["默认加量"], t["不足比例"], t["不足保底"], t["不足保底"]))

    goods = json.loads(GOODS.read_text(encoding="utf-8"))
    print("\n== 数据来源 ==")
    print("   TEMU 可售库存 : goods.json（官方导出），生成于 %s" % goods.get("generatedAt"))
    src = ((goods.get("lowStock") or {}).get("restock") or {}).get("sources") or {}
    print("   看板快照      : built_at=%s fetchedAt=%s live=%s"
          % ((src.get("看板") or {}).get("builtAt"), (src.get("看板") or {}).get("fetchedAt"),
             (src.get("看板") or {}).get("live")))
    print("   门槛          : TEMU 可售 ≤ %s" % t["TEMU库存上限"])

    rows = [r for r in (goods.get("stores", {}).get(store, {})
                        .get("lowStock", {}).get("rows", [])) if r["code"] == code]
    if not rows:
        print("\n%s 不在低库存清单里（TEMU 可售都 > %s 或已不在此店）"
              % (code, t["TEMU库存上限"]))
        return 0

    print("\n== %s 各尺码判定 ==" % code)
    print("  %-5s %8s %7s %12s %8s %7s  %s"
          % ("尺码", "TEMU", "过门槛", "加州+kansas", "日均", "建议加", "算式"))
    total = 0
    for r in sorted(rows, key=lambda x: x["size"]):
        byw = [w for w in (r.get("whByWarehouse") or []) if w.get("仓库")]
        used = sum(w.get("代发库存") or 0 for w in byw
                   if any(k in (w.get("仓库") or "") for k in keys))
        ca = sum(w.get("代发库存") or 0 for w in byw if "加州" in (w.get("仓库") or ""))
        ks = sum(w.get("代发库存") or 0 for w in byw if "kansas" in (w.get("仓库") or ""))
        gate = (r.get("stock") or 0) <= t["TEMU库存上限"]
        calc = "不在加州/kansas 台账里"
        add = 0
        if gate and used > 0:
            want = (t["默认加量"] if used >= t["充足阈值"]
                    else max(t["不足保底"], used * t["不足比例"]))
            add = T.round_half_up(want)
            cap = ""
            if add > used:
                add = int(used)
                cap = "；但不超过该合计现货"
            calc = ("%s%s ⇒ %d"
                    % ("加州 %d + kansas %d = %d ≥ %s ⇒ 默认 %s"
                       % (ca, ks, used, t["充足阈值"], t["默认加量"])
                       if used >= t["充足阈值"]
                       else "加州 %d + kansas %d = %d < %s ⇒ %d×%s=%s，保底 %s"
                            % (ca, ks, used, t["充足阈值"], used, t["不足比例"],
                               round(used * t["不足比例"], 1), t["不足保底"]),
                       cap, add))
            total += add
        elif not gate:
            calc = "TEMU 可售 %s > %s，不进计划" % (r.get("stock"), t["TEMU库存上限"])
        print("  %-5s %8s %7s %12s %8s %7s  %s"
              % (r["size"], r.get("stock"), "✅" if gate else "❌", int(used),
                 r.get("dailyUse") if r.get("dailyUse") is not None else "—",
                 add if gate and used else "—", calc))
    print("  ---- 按此口径合计加库存 %d 件 ----" % total)
    print("\n注：面板「低库存款」页的「仓内现货」列是**全部海外仓合计**，"
          "与本脚本用的「加州+kansas」不同，所以两个数不一样是正常的。")
    print("    没日均数据的款，面板标「仓里有货(未定量)」且不给量（脚本也只加「可加库存」）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
