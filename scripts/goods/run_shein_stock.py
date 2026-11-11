# -*- coding: utf-8 -*-
"""run_shein_stock.py — SHEIN 商家库存 pipeline（一条命令串完 4 段）。

    ① refresh  刷库存源（帕沃尔两仓 + 领星恒信可用/在途；7天日均沿用现有值）
    ② plan     算清零/补货/达标/预警（v1 分档 或 v2 服务水平+ROP+ABC），落 xlsx
    ③ apply    调 OpenAPI 提交（OVERWRITE 0 / ADD 差额），≤100 个/批
    ④ report   回读结果 + 补货预警表，可选推飞书群

用法：
    # 只看（默认全部 4 段，apply 是 dry-run）
    python scripts\\goods\\run_shein_stock.py --store BD --policy v2

    # 真改
    python scripts\\goods\\run_shein_stock.py --store BD --policy v2 --apply

    # 只跑某几段 / 跳过帕沃尔抓取
    ... --stages refresh,plan
    ... --skip-pw-refresh
    # 店铺导出表自动找（Downloads 里最新那份匹配 BD-/BAN- 前缀的）
    ... --export auto
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import shein_stock_api as api        # noqa: E402
import shein_stock_sync as sync      # noqa: E402
import shein_zero_plan as zp        # noqa: E402

DOWNLOADS = Path.home() / "Downloads"
OUT_DIR = Path(r"D:\together\全托管\SHEIN")
LEDGER_DIR = Path(r"D:\together\Inventory table")
STAGES = ("refresh", "plan", "apply", "report")


def find_ledger() -> Path | None:
    """`--store-daily auto`：取 D:\\together\\Inventory table 里最新那份库存管理表。"""
    files = sorted(LEDGER_DIR.glob("十部半托管-库存管理表*.xlsx"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def find_export(store: str) -> Path | None:
    """在 Downloads 里找「属于该店铺主体」的商家库存导出（最新那份）。

    不能看商家SKU前缀 —— 一个导出里会同时出现 BAN-/BK-/BW- 多个品牌段，
    前缀判断会挑错文件。这里改问接口：拿候选文件的几个平台SKU 去该店 key 查，
    `code=0` 说明这批 SKU 归这家主体管。
    """
    pat = "merchant-stock-export-*.xlsx"
    cands = sorted(DOWNLOADS.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands:
        if "_清零" in p.name or "_v2" in p.name:
            continue
        try:
            exp = zp.load_export(str(p))
        except Exception:
            continue
        if not exp:
            continue
        # 每个品牌段各取一个 SKU 试
        probe, seen = [], set()
        for e in exp:
            pref = e["seller"].split("-", 1)[0]
            if pref in seen:
                continue
            seen.add(pref)
            probe.append(e["sku"])
            if len(probe) >= 3:
                break
        try:
            got = api.query(probe, store)
        except BaseException:      # 主体不匹配时 query 会 SystemExit，逐份往下试
            continue
        if got:
            return p
    return None


def write_plan_xlsx(plan: dict, path: Path) -> Path:
    """把本轮计划落成 xlsx（明细 / 预警 两个 sheet），方便人复核。"""
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "明细"
    ws.append(["动作", "商家SKU", "平台SKU", "可售天数", "日均", "仓内库存", "线上现有", "目标", "增量", "规则"])
    for z in plan["zero"]:
        ws.append(["清零", z["seller"], z["sku"], z.get("days"), round(z.get("daily") or 0, 2),
                   z.get("total"), z["now"], 0, -z["now"], "可售<=1天"])
    for t in plan["topup"]:
        ws.append(["补货", t["seller"], t["sku"], t.get("days"), round(t.get("daily") or 0, 2),
                   t.get("total"), t["now"], t["target"], t["delta"], t.get("rule")])
    for o in plan["ok"]:
        ws.append(["不动", o["seller"], o["sku"], o.get("days"), round(o.get("daily") or 0, 2),
                   o.get("total"), o["now"], o.get("target"), 0, "已达标/本店无销量"])
    for w, col in zip((8, 26, 18, 11, 9, 11, 11, 9, 9, 70), ws.iter_cols(min_row=1, max_row=1)):
        ws.column_dimensions[col[0].column_letter].width = w

    ws2 = wb.create_sheet("补货预警")
    ws2.append(["商家SKU", "平台SKU", "可售天数", "日均(全渠道)", "仓内", "在途", "线上", "ROP", "在库+在途"])
    for a in plan["alerts"]:
        ws2.append([a["seller"], a["sku"], a.get("days"), round(a.get("daily") or 0, 2),
                    a.get("total"), a.get("transit", ""), a.get("now"),
                    a.get("rop", ""), a.get("on_hand", "")])
    for col in ws2.columns:
        ws2.column_dimensions[col[0].column_letter].width = 16
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SHEIN 库存 pipeline（refresh → plan → apply → report）")
    ap.add_argument("--store", required=True, help="SHEIN 主体/店铺：BD / BAN / BS …")
    ap.add_argument("--export", default="auto", help="商家库存导出 xlsx，auto=自动找最新那份")
    ap.add_argument("--policy", default="v2", choices=["v1", "v2"])
    ap.add_argument("--store-daily", default="",
                    help="库存管理表 xlsx（总销售表）；v2 必填，不给则退回 v1")
    ap.add_argument("--stages", default=",".join(STAGES), help="要跑的段，逗号分隔")
    ap.add_argument("--days", type=float, default=1.0, help="清零线（<=N 天清零）")
    ap.add_argument("--bands", default="3:1,7:2,inf:3", help="v1 分档")
    ap.add_argument("--gap", type=float, default=50.0, help="v1 仓余降档阈值")
    ap.add_argument("--share", type=float, default=1.0, help="SHEIN 可占仓内比例")
    ap.add_argument("--lead-days", type=float, default=45.0)
    ap.add_argument("--local-days", type=float, default=3.0)
    ap.add_argument("--z", dest="z_value", type=float, default=1.65)
    ap.add_argument("--dos", default="7,3,1")
    ap.add_argument("--abc", default="0.2,0.5")
    ap.add_argument("--min-listing", type=float, default=5.0,
                    help="本店无销量时保底挂单量（件），默认 5；0=关闭")
    ap.add_argument("--safety-days", type=float, default=1.0)
    ap.add_argument("--min-units", type=float, default=5.0)
    ap.add_argument("--alert-days", type=float, default=2.0,
                    help="v1 补货预警阈值（可售 < N 天且无在途），v2 用 ROP")
    ap.add_argument("--apply", action="store_true", help="真提交（不给就是 dry-run）")
    ap.add_argument("--push", action="store_true", help="推飞书群")
    ap.add_argument("--skip-pw-refresh", action="store_true")
    ap.add_argument("--no-refresh", action="store_true")
    a = ap.parse_args(argv)

    stages = [s.strip() for s in a.stages.split(",") if s.strip()]
    bad = [s for s in stages if s not in STAGES]
    if bad:
        raise SystemExit("未知阶段 %s（可选 %s）" % (bad, STAGES))
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = OUT_DIR / ("%s-库存" % a.store.upper())
    out_dir.mkdir(parents=True, exist_ok=True)
    print("[pipeline] store=%s policy=%s stages=%s apply=%s"
          % (a.store.upper(), a.policy, ",".join(stages), a.apply), flush=True)

    # ---- ① refresh ----
    if "refresh" in stages:
        if a.no_refresh:
            print("[① refresh] 跳过（--no-refresh）")
        else:
            sync.refresh(skip_pw=a.skip_pw_refresh)
            print("[① refresh] done: %s" % sync.data_ages())

    # ---- 导出表 ----
    export = a.export
    if export == "auto":
        p = find_export(a.store)
        if not p:
            raise SystemExit("Downloads 里没找到 %s 店的 merchant-stock-export-*.xlsx" % a.store.upper())
        export = str(p)
        print("[导出] 自动选用 %s" % p.name)

    # ---- ② plan ----
    ledger = a.store_daily
    if ledger == "auto":
        p = find_ledger()
        ledger = str(p) if p else ""
        print("[台账] 自动选用 %s" % (p.name if p else "(没找到)"))
    if a.policy == "v2" and not ledger:
        print("[② plan] ⚠ v2 需要 --store-daily（库存管理表），本次自动退回 v1")
    plan_res = sync.build(
        export, a.store, a.days, a.gap, 1.0, sync.parse_bands(a.bands),
        a.safety_days, a.min_units, True, a.share, a.alert_days,
        ledger, "总销售表", a.policy, a.lead_days, a.local_days, a.z_value,
        tuple(float(x) for x in a.dos.split(",")), tuple(float(x) for x in a.abc.split(",")),
        a.min_listing)
    plan = dict(zero=plan_res[0], topup=plan_res[1], ok=plan_res[2],
                noinfo=plan_res[3], decisions=plan_res[4], alerts=plan_res[5])
    print("[② plan] 清零 %d / 补货 %d（+%d 件）/ 达标 %d / 无数据 %d / 预警 %d"
          % (len(plan["zero"]), len(plan["topup"]), sum(t["delta"] for t in plan["topup"]),
             len(plan["ok"]), len(plan["noinfo"]), len(plan["alerts"])), flush=True)
    plan_file = write_plan_xlsx(plan, out_dir / ("计划_%s.xlsx" % ts))
    print("[② plan] 计划表 → %s" % plan_file)

    # ---- ③ apply ----
    res_all: list[dict] = []
    if "apply" in stages:
        if not a.apply:
            print("[③ apply] dry-run（加 --apply 才提交）")
        else:
            if plan["zero"]:
                rows = [dict(sku=z["sku"], warehouse=z["wh"], quantity=0, reason="可售<=1天清零")
                        for z in plan["zero"]]
                res_all += api.change(rows, a.store, "OVERWRITE", apply=True)
            if plan["topup"]:
                rows = [dict(sku=t["sku"], warehouse=t["wh"], quantity=t["delta"],
                             reason="pipeline 补货") for t in plan["topup"]]
                res_all += api.change(rows, a.store, "ADD", apply=True)
            ok_n = sum(1 for x in res_all if x["ok"])
            print("[③ apply] 提交 %d，成功 %d，失败 %d"
                  % (len(res_all), ok_n, len(res_all) - ok_n), flush=True)
            for x in res_all:
                if not x["ok"]:
                    print("   FAIL %s %s %s" % (x["sku"], x["code"], x["msg"]))

            # ---- ④ report：回读 ----
            ids = [z["sku"] for z in plan["zero"]] + [t["sku"] for t in plan["topup"]]
            live = api.query(ids, a.store) if ids else {}
            bad = 0
            print("[④ report] 回读：")
            for z in plan["zero"]:
                u = (live.get(z["sku"]) or {}).get("usable")
                good = u == 0
                bad += 0 if good else 1
                print("   %-18s 可用%-5s 期望 0 %s" % (z["sku"], u, "✓" if good else "✗"))
            for t in plan["topup"]:
                u = (live.get(t["sku"]) or {}).get("usable")
                good = (u or 0) >= t["target"]
                bad += 0 if good else 1
                print("   %-18s 可用%-5s 期望>=%-4s %s" % (t["sku"], u, t["target"], "✓" if good else "✗"))
            print("[④ report] 未达标 %d" % bad)

    # ---- ④ report（预警 + 推送，apply 与否都跑）----
    if "report" in stages:
        out = sync.save_alerts(plan["alerts"], str(out_dir / ("补货预警_%s.xlsx" % ts)))
        if out:
            print("[④ report] 补货预警 → %s" % out)
        if a.push:
            # 只在真有改动时推送，避免每天 6 条「无事发生」
            if plan["zero"] or plan["topup"]:
                ok, why = sync.push_summary(a, plan["zero"], plan["topup"], plan["alerts"], a.apply)
                print("[④ report] 推送 %s %s" % ("OK" if ok else "FAIL", why))
            else:
                print("[④ report] 无改动，跳过推送（预警 %d 条已落表）" % len(plan["alerts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
