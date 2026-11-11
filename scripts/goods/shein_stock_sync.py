# -*- coding: utf-8 -*-
"""shein_stock_sync.py — SHEIN 按「领星可售天数」双向同步库存。

规则（用户 2026-11-11 确认）：
    可售天数 = 仓内总库存（恒信可用 + 帕沃尔加州3 + Kansas）÷ 7天日均
    · 可售天数 >= 3  → 目标 = ceil(7天日均 × 3)，只加不减：ADD(目标 - 现有)，现有>=目标则不动
    · 可售天数 <  3  → 清零：OVERWRITE 0
    · 7天日均 = 0 或面板查不到该款-尺码 → 不处理（列进 unreachable）

数据源（先刷再算）：
    fetch_pw_stock.py     → pw-stock.json（帕沃尔加州3 + Kansas）
    fetch_stock_track.py  → stock-track.json（领星 LM739 + 并入帕沃尔）

用法：
    # 预览（默认，不提交）
    python scripts\\goods\\shein_stock_sync.py --export "<商家库存导出.xlsx>" --store BAN

    # 真提交（提交前自动再刷一次数据，提交后自动回读）
    python scripts\\goods\\shein_stock_sync.py --export "..." --store BAN --apply

    # 不刷数据 / 只刷领星
    ... --no-refresh | --skip-pw-refresh
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import shein_stock_api as api  # noqa: E402
import shein_zero_plan as zp   # noqa: E402

STOCK_TRACK = ROOT / "temu-admin" / "public" / "data" / "stock-track.json"


def refresh(skip_pw: bool = False) -> None:
    steps = []
    if not skip_pw:
        steps.append(("帕沃尔", [str(ROOT / "scripts" / "goods" / "fetch_pw_stock.py")]))
    steps.append(("领星+合并", [str(ROOT / "scripts" / "goods" / "fetch_stock_track.py")]))
    for name, cmd in steps:
        print("[refresh] %s ← %s" % (name, " ".join(cmd)), flush=True)
        t0 = time.time()
        rc = subprocess.call([sys.executable] + cmd)
        print("[refresh] %s 退出码 %s，%.0fs" % (name, rc, time.time() - t0), flush=True)
        if rc != 0:
            print("[refresh] ⚠ %s 刷新失败，继续（数据可能是旧的）" % name, flush=True)


def load_panel(path: Path) -> dict:
    """全部款-尺码（不限天数）：{'-款-套系-尺码': {daily,total,days,avail}}"""
    data = json.loads(path.read_text(encoding="utf-8"))
    panel: dict[str, dict] = {}
    for _, src in (data.get("sources") or {}).items():
        for st in src.get("styles") or []:
            for v in st.get("variants") or []:
                for r in v.get("rows") or []:
                    code = str(r.get("code") or "").strip()
                    size = str(r.get("size") or "").strip()
                    if not code or not size:
                        continue
                    daily = zp._num(r.get("daily"))
                    total = r.get("lxTotal")
                    total = zp._num(r.get("stock") if total is None else total)
                    k = ("%s-%s-%s" % (code, v.get("variant") or "", size)).lower()
                    rec = dict(daily=daily, total=total, avail=zp._num(r.get("lxAvail")),
                               days=round(total / daily, 2) if daily > 0 else None)
                    prev = panel.get(k)
                    if prev and prev["daily"] >= rec["daily"]:
                        continue
                    panel[k] = rec
    return panel


def build(export: str, store: str, days_target: float, gap: float = 50.0,
          fallback_days: float = 1.0):
    exp = zp.load_export(export)
    panel = load_panel(STOCK_TRACK)

    by_seller: dict[str, dict] = {}
    for e in exp:
        by_seller.setdefault(e["seller"], e)     # 同一商家SKU只留一行（平台SKU相同）

    codes = sorted({e["sku"] for e in by_seller.values()})
    live = api.query(codes, store) if codes else {}

    zero, topup, ok, noinfo, decisions = [], [], [], [], []
    for seller, e in sorted(by_seller.items()):
        body = seller.split("-", 1)[1].lower() if "-" in seller else seller.lower()
        p = panel.get(body)
        now = (live.get(e["sku"]) or {}).get("usable") or 0
        it = dict(seller=seller, sku=e["sku"], now=now, wh=e["whcode"])
        if not p or p["days"] is None or p["daily"] <= 0:
            noinfo.append(it)
            continue
        if p["days"] < days_target:
            (zero if now > 0 else ok).append(dict(it, days=p["days"], daily=p["daily"], total=p["total"]))
            continue
        # 目标：默认 3 天量；**仓总库存只比 3 天量多 <= gap 件时降成 1 天量**（别把仓吸干）
        t3 = int(-(-p["daily"] * days_target // 1))
        remain = p["total"] - t3
        if remain <= gap:
            target = int(-(-p["daily"] * fallback_days // 1))
            rule = "%g天量(仓内只余%g件<=%g)" % (fallback_days, remain, gap)
        else:
            target = t3
            rule = "%g天量(仓内余%g件)" % (days_target, remain)
        decisions.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                              target=target, t3=t3, remain=remain, rule=rule))
        if target > now:
            topup.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                              target=target, delta=target - now, rule=rule))
        else:
            ok.append(dict(it, days=p["days"], daily=p["daily"], total=p["total"],
                           target=target, rule=rule))
    return zero, topup, ok, noinfo, decisions


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SHEIN 库存双向同步（≥N天补 3 天量 / <N天清零）")
    ap.add_argument("--export", required=True)
    ap.add_argument("--store", default="BAN")
    ap.add_argument("--days", type=float, default=3.0, help="阈值，默认 3 天")
    ap.add_argument("--gap", type=float, default=50.0,
                    help="仓总库存 - N天量 <= gap 时降成 1 天量（默认 50 件）")
    ap.add_argument("--fallback-days", type=float, default=1.0, help="上面那种情况用的天数，默认 1")
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--skip-pw-refresh", action="store_true")
    ap.add_argument("--apply", action="store_true", help="真提交；不带就是预览")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个补货（调试用）")
    a = ap.parse_args(argv)

    if not a.no_refresh:
        refresh(skip_pw=a.skip_pw_refresh)

    zero, topup, ok, noinfo, decisions = build(a.export, a.store, a.days, a.gap, a.fallback_days)
    if a.limit:
        topup = topup[:a.limit]

    print("\n[决策明细] 可售>=%g 天的 %d 个：" % (a.days, len(decisions)))
    for d in decisions:
        print("   %-24s 可售%-7s 日均%-6s 仓内%-6s 3天量%-5s 仓余%-6s → %s"
              % (d["seller"], d["days"], round(d["daily"], 2), d["total"], d["t3"], d["remain"], d["rule"]))
    print("\n[结果] 需清零 %d 个 / 需补库存 %d 个 / 已达标 %d 个 / 无销量数据 %d 个"
          % (len(zero), len(topup), len(ok), len(noinfo)))
    if zero:
        print("\n--- 清零 ---")
        for z in zero:
            print("   %-24s %-16s 可售%-6s 现有%-5s → 0" % (z["seller"], z["sku"], z["days"], z["now"]))
    if topup:
        print("\n--- 补库存（目标 = ceil(7天日均 × %g)）---" % a.days)
        for t in topup:
            print("   %-24s %-16s 可售%-7s 日均%-6s 现有%-5s 目标%-5s 加 %s"
                  % (t["seller"], t["sku"], t["days"], round(t["daily"], 2), t["now"], t["target"], t["delta"]))

    if not a.apply:
        print("\n[preview] 加 --apply 才会真的提交")
        return 0

    rows = []
    for z in zero:
        rows.append(dict(sku=z["sku"], warehouse=z["wh"], quantity=0, reason="可售<3天清零"))
    if rows:
        res = api.change(rows, a.store, "OVERWRITE", apply=True)
        for x in res:
            print("   [OVERWRITE %s] %-16s %s %s" % ("OK" if x["ok"] else "FAIL", x["sku"], x["code"], x["msg"]))
    if topup:
        rows = [dict(sku=t["sku"], warehouse=t["wh"], quantity=t["delta"], reason="补3天量(可售>=3)")
                for t in topup]
        res = api.change(rows, a.store, "ADD", apply=True)
        for x in res:
            print("   [ADD %s] %-16s %s %s" % ("OK" if x["ok"] else "FAIL", x["sku"], x["code"], x["msg"]))

    # 回读
    check = [z["sku"] for z in zero] + [t["sku"] for t in topup]
    live = api.query(check, a.store) if check else {}
    print("\n---- 回读 ----")
    bad = 0
    for z in zero:
        u = (live.get(z["sku"]) or {}).get("usable")
        bad += 0 if u == 0 else 1
        print("   %-16s 可用%-5s 期望 0    %s" % (z["sku"], u, "✓" if u == 0 else "✗"))
    for t in topup:
        u = (live.get(t["sku"]) or {}).get("usable")
        good = (u or 0) >= t["target"]
        bad += 0 if good else 1
        print("   %-16s 可用%-5s 期望 >=%-4s %s" % (t["sku"], u, t["target"], "✓" if good else "✗"))
    print("[结果] 未达标 %d 个" % bad)
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
