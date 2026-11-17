# -*- coding: utf-8 -*-
"""run_shein_pool.py — SHEIN 四店「池子分配」pipeline（用户 2026-11-14 口径）

和 `run_shein_stock.py`（单店）的区别：**先把四家店的计划都算出来，再统一分池子，
最后逐店下发** —— 避免「同一款在四家店同时被清零」，也避免「四家各按仓内全量挂」的超卖。

    ① refresh  刷库存源（跟单店同一个函数）
    ② plan     逐店 build()（v1/v2 照旧算法，只取「需求」）
    ③ alloc    分池：在途豁免 → 保活分摊 / 降到配额 / 单款优先（见 shein_pool.py）
    ④ apply    逐店下发：OVERWRITE 0（清零）/ SUB（降到配额）/ ADD（补货）
    ⑤ report   回读校验 + 清零台账 + 补货预警 + 可选推飞书

用法：
    python scripts\\goods\\run_shein_pool.py --stores BD,BAN,BS,BE            # 只看
    python scripts\\goods\\run_shein_pool.py --stores BD,BAN,BS,BE --apply    # 真下发
    ... --skip-pw-refresh --push
    ... --pool-config config\\shein_pool.json
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts"))

import shein_stock_api as api        # noqa: E402
import shein_stock_sync as sync      # noqa: E402
import shein_pool as pool            # noqa: E402
import run_shein_stock as rs         # noqa: E402

OUT_DIR = Path(r"D:\together\全托管\SHEIN")
BANDS = "3:1,7:2,inf:3"


def write_xlsx(acts: list, pool_rows: list, path: Path) -> Path:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "分配"
    ws.append(["店", "动作", "商家SKU", "平台SKU", "可售天数", "日均", "本店日均",
               "仓内池子", "线上现有", "目标", "增量", "模式", "说明"])
    for x in acts:
        d = (x["target"] or 0) - (x["now"] or 0)
        ws.append([x["store"], x["action"], x["seller"], x["sku"], x.get("days"),
                   round(x.get("daily") or 0, 2), round(x.get("store_daily") or 0, 2),
                   x.get("total"), x["now"], x["target"], d, x.get("mode"), x.get("note")])
    for w, col in zip((7, 8, 26, 18, 10, 9, 10, 10, 10, 8, 8, 11, 66),
                      ws.iter_cols(min_row=1, max_row=1)):
        ws.column_dimensions[col[0].column_letter].width = w

    ws2 = wb.create_sheet("池子")
    ws2.append(["款-变体-尺码", "域", "参与店(优先级)", "池子", "在途", "全渠道日均",
                "ETA(天)", "可售天数", "模式", "说明"])
    for r in pool_rows:
        k = r["key"]
        ws2.append(["-".join(str(x).upper() for x in k), r["domain"], r["stores"],
                    r["pool"], r["transit"], r["daily_all"], r.get("eta"),
                    r["days"], r["mode"], r["note"]])
    for col in ws2.columns:
        ws2.column_dimensions[col[0].column_letter].width = 16
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["J"].width = 70
    for w in (ws, ws2):
        for c in w[1]:
            c.font = Font(bold=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SHEIN 四店池子分配（refresh → plan → alloc → apply → report）")
    ap.add_argument("--stores", default="BD,BAN,BS,BE")
    ap.add_argument("--export", default="auto")
    ap.add_argument("--policy", default="v2", choices=["v1", "v2"])
    ap.add_argument("--store-daily", default="auto")
    ap.add_argument("--pool-config", default="", help="池子配置（默认 config/shein_pool.json）")
    ap.add_argument("--force-zero", default="", help="手工点名清零（逗号分隔）")
    ap.add_argument("--min-listing", type=float, default=5.0)
    ap.add_argument("--z-abc", default="1.88,1.65,1.28",
                    help="按本店 ABC 分档的服务水平系数 z（A,B,C），默认 1.88/1.65/1.28")
    ap.add_argument("--lead-days", type=float, default=45.0,
                    help="没有头程 ETA 时的交期兜底（天）")
    ap.add_argument("--share", type=float, default=1.0)
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--skip-pw-refresh", action="store_true")
    ap.add_argument("--out", default="", help="产出目录（默认 D:\\together\\全托管\\SHEIN\\池子分配）")
    ap.add_argument("--apply", action="store_true", help="真下发（不给就是 dry-run）")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)

    cfg = pool.load_config(a.pool_config)
    if not cfg.get("enabled", True):
        print("[pool] 配置 enabled=false —— 请改用 run_shein_stock.py 逐店跑")
        return 0
    stores = [s.strip().upper() for s in a.stores.split(",") if s.strip()]
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path(a.out) if a.out else OUT_DIR / "池子分配"
    print("[pool] stores=%s policy=%s apply=%s min_keep=%s 保活线 ETA<=%s天"
          % (",".join(stores), a.policy, a.apply, cfg.get("min_keep"),
             cfg.get("eta_keep_days")), flush=True)

    # ---- ① refresh ----
    if a.no_refresh:
        print("[① refresh] 跳过（--no-refresh）")
    else:
        sync.refresh(skip_pw=a.skip_pw_refresh)
        print("[① refresh] done: %s" % sync.data_ages())

    ledger = a.store_daily
    if ledger == "auto":
        p = rs.find_ledger()
        ledger = str(p) if p else ""
        print("[台账] 自动选用 %s" % (p.name if p else "(没找到)"))
    if a.policy == "v2" and not ledger:
        print("[② plan] ⚠ v2 需要库存管理表，本次退回 v1")

    panel = sync.load_panel()
    comps = pool.component_styles()
    forced = [x.strip() for x in a.force_zero.split(",") if x.strip()]

    # ---- ② plan（逐店）----
    plans: dict[str, dict] = {}
    for st in stores:
        export = a.export
        if export == "auto":
            try:
                ep = rs.find_export(st)
            except BaseException:      # noqa: BLE001  主体不匹配时 find_export 内部已兜
                ep = None
            if not ep:
                print("[② plan %s] ⚠ Downloads 里没有该店的 merchant-stock-export，跳过" % st)
                continue
            export = str(ep)
        print("[② plan %s] 导出 %s" % (st, Path(export).name), flush=True)
        res = sync.build(export, st, 1.0, 50.0, 1.0, sync.parse_bands(BANDS),
                         1.0, 5.0, True, a.share, 2.0, ledger, "总销售表", a.policy,
                         a.lead_days, 3.0, 1.65, (7.0, 3.0, 1.0), (0.2, 0.5),
                         a.min_listing, True, 1.3, 0.7, 1.4, 0.7, True, "",
                         True, forced, True,
                         tuple(float(x) for x in a.z_abc.split(",")))
        plans[st] = dict(zero=res[0], topup=res[1], ok=res[2], noinfo=res[3],
                         decisions=res[4], alerts=res[5])
        print("[② plan %s] 清零候选 %d / 补货 %d / 达标 %d / 无数据 %d / 预警 %d"
              % (st, len(res[0]), len(res[1]), len(res[2]), len(res[3]), len(res[5])), flush=True)
    if not plans:
        print("[pool] 没有任何店铺跑出计划，结束")
        return 0

    # ---- ③ alloc（分池）----
    rows_by_store = {st: p["decisions"] for st, p in plans.items()}
    eta = sync.load_eta()
    # TEMU 三店日均（≤1天时先给 TEMU 留量，SHEIN 让货）
    prio = {}
    if ledger:
        for tier in cfg.get("priority_tiers") or []:
            name = str(tier.get("name") or "?")
            sts = tuple(tier.get("stores") or ())
            try:
                d = sync.load_temu_daily(ledger, sts)
            except Exception as e:  # noqa: BLE001
                print("[pool] ⚠ 读 %s 日均失败（按 0 处理）：%s" % (name, e))
                d = {}
            prio[name] = d
            print("[pool] 优先级 %s：%d 个「款-尺码」（%s）"
                  % (name, len(d), ",".join(sts)), flush=True)
    alloc, pool_rows = pool.allocate(rows_by_store, panel, cfg, comps, eta=eta, prio=prio)
    modes = defaultdict(int)
    for r in pool_rows:
        modes[r["mode"]] += 1
    print("[③ alloc] 池子 %d 个：%s" % (len(pool_rows), dict(modes)), flush=True)

    # 组装动作：优先用池子分配结果，面板查不到的（无库存兜底）沿用单店结论
    acts: list[dict] = []
    for st, plan in plans.items():
        seen = set()
        for r in plan["decisions"]:
            sku = r["sku"]
            if sku in seen:
                continue
            seen.add(sku)
            got = alloc.get((st, sku))
            if not got:
                continue
            acts.append(dict(store=st, seller=r["seller"], sku=sku,
                             wh=got.get("wh") or r.get("wh"), now=int(r.get("now") or 0),
                             target=got["target"], action=got["action"], mode=got["mode"],
                             note=got["note"], days=r.get("days"), total=r.get("total"),
                             daily=r.get("daily"), store_daily=r.get("store_daily")))
        legacy = {}
        for z in plan["zero"]:
            legacy[z["sku"]] = dict(target=0, action="清零", mode="legacy",
                                    note=z.get("rule") or "可售<=1天（池子外兜底）")
        for t in plan["topup"]:
            legacy[t["sku"]] = dict(target=t["target"], action="补货", mode="legacy",
                                    note=t.get("rule") or "池子外兜底")
        for r in list(plan["zero"]) + list(plan["topup"]):
            sku = r["sku"]
            if sku in seen:
                continue
            seen.add(sku)
            f = legacy[sku]
            acts.append(dict(store=st, seller=r["seller"], sku=sku, wh=r.get("wh"),
                             now=int(r.get("now") or 0), target=f["target"],
                             action=f["action"], mode=f["mode"], note=f["note"],
                             days=r.get("days"), total=r.get("total"),
                             daily=r.get("daily"), store_daily=r.get("store_daily")))

    cnt = defaultdict(int)
    for x in acts:
        cnt[x["action"]] += 1
    print("[③ alloc] 待执行 %d 条：%s" % (len(acts), dict(cnt)), flush=True)
    for x in sorted(acts, key=lambda y: (y["store"], y["action"], y["seller"])):
        if x["action"] == "不动":
            continue
        print("   %-3s %-4s %-24s %4d → %4d  [%s] %s"
              % (x["store"], x["action"], x["seller"], x["now"], x["target"],
                 x["mode"], (x["note"] or "")[:60]), flush=True)
    plan_file = write_xlsx(acts, pool_rows, out_dir / ("池子分配_%s.xlsx" % ts))
    print("[③ alloc] 计划表 → %s" % plan_file)

    results: dict[tuple, list] = {}
    live: dict[str, dict] = {}

    # ---- ④ apply ----
    if not a.apply:
        print("[④ apply] dry-run（加 --apply 才提交）")
    else:
        todo = defaultdict(lambda: defaultdict(list))
        for x in acts:
            if x["action"] == "清零":
                todo[x["store"]]["OVERWRITE"].append(
                    dict(sku=x["sku"], warehouse=x["wh"], quantity=0, reason="池子分配：清零"))
            elif x["action"] == "降量":
                todo[x["store"]]["SUB"].append(
                    dict(sku=x["sku"], warehouse=x["wh"], quantity=x["now"] - x["target"],
                         reason="池子分配：降到配额"))
            elif x["action"] == "补货":
                todo[x["store"]]["ADD"].append(
                    dict(sku=x["sku"], warehouse=x["wh"], quantity=x["target"] - x["now"],
                         reason="池子分配：补货"))
        for st in [s for s in stores if s in todo]:
            for typ, rows in todo[st].items():
                if not rows:
                    continue
                r = api.change(rows, st, typ, apply=True)
                results[(st, typ)] = r
                ok_n = sum(1 for x in r if x["ok"])
                print("[④ apply %s] %s 提交 %d 成功 %d 失败 %d"
                      % (st, typ, len(r), ok_n, len(r) - ok_n), flush=True)
                for x in r:
                    if not x["ok"]:
                        print("   FAIL %s %s %s" % (x["sku"], x["code"], x["msg"]))

    # ---- ⑤ report：回读 + 台账 + 预警 ----
    touched = [x for x in acts if x["action"] != "不动"]
    bad = 0
    if touched:
        for attempt in range(1, 4):
            bad = 0
            for st in {x["store"] for x in touched}:
                rows = [x for x in touched if x["store"] == st]
                got = api.query([x["sku"] for x in rows], st) if a.apply else {}
                live.update({"%s|%s" % (st, k): v for k, v in (got or {}).items()})
                if not a.apply:
                    continue
                for x in rows:
                    u = (got.get(x["sku"]) or {}).get("usable")
                    ok = (u == 0) if x["target"] == 0 else ((u or 0) == x["target"])
                    if not ok:
                        bad += 1
            print("[⑤ report] 回读第 %d 轮：未达标 %d" % (attempt, bad), flush=True)
            if bad == 0 or not a.apply or attempt == 3:
                break
            time.sleep(20)
        if a.apply:
            for st in {x["store"] for x in touched}:
                rows = [x for x in touched if x["store"] == st]
                print("[⑤ report %s] 逐行：" % st)
                for x in rows:
                    u = (live.get("%s|%s" % (st, x["sku"])) or {}).get("usable")
                    ok = (u == 0) if x["target"] == 0 else ((u or 0) == x["target"])
                    print("   %-24s 可用%-5s 期望%-6s %s"
                          % (x["seller"], u, x["target"], "✓" if ok else "✗"))

    if a.apply:
        for st in plans:
            zs = [x for x in acts if x["store"] == st and x["action"] == "清零"
                  and (x["now"] or 0) > 0 and (results.get((st, "OVERWRITE")) or [])]
            if not zs:
                continue
            ok_skus = {x["sku"] for x in (results.get((st, "OVERWRITE")) or []) if x["ok"]}
            rows_ok = [dict(seller=x["seller"], sku=x["sku"], wh=x["wh"],
                            now=x["now"], days=x.get("days"), daily=x.get("daily"),
                            total=x.get("total"), rule=x.get("note"))
                       for x in zs if x["sku"] in ok_skus]
            conf = {x["sku"] for x in rows_ok
                    if (live.get("%s|%s" % (st, x["sku"])) or {}).get("usable") == 0}
            if rows_ok:
                ad, up, tot = sync.record_clear(st, rows_ok, confirmed=conf)
                print("[⑤ report %s] 清零台账：新增 %d / 更新 %d / 共 %d（回读确认 %d）"
                      % (st, ad, up, tot, len(conf)))
        try:
            import subprocess
            subprocess.call([sys.executable,
                             str(ROOT / "scripts" / "goods" / "build_presale_watch.py")])
        except Exception as e:  # noqa: BLE001
            print("[⑤ report] ⚠ 面板 JSON 重建失败：%s" % e)

    alerts = []
    for st, p in plans.items():
        for x in p["alerts"]:
            alerts.append(dict(x, seller="%s:%s" % (st, x["seller"])))
    out = sync.save_alerts(alerts, str(out_dir / ("补货预警_%s.xlsx" % ts)))
    if out:
        print("[⑤ report] 补货预警 %d 条 → %s" % (len(alerts), out))

    # 只有「真的改动了线上」才推送：清零候选里大量是线上本来就 0 的行（不动）
    changed = [x for x in acts if x["action"] in ("补货", "降量")
               or (x["action"] == "清零" and (x["now"] or 0) > 0)]
    if a.push and changed:
        ns = argparse.Namespace(store="+".join(stores), alert_days=2.0)
        zero = [x for x in acts if x["action"] == "清零"]
        topup = [x for x in acts if x["action"] == "补货"]
        trim = [x for x in acts if x["action"] == "降量"]
        zero_p = [dict(seller=x["seller"], days=x.get("days"), now=x["now"]) for x in zero]
        topup_p = [dict(seller=x["seller"], delta=x["target"] - x["now"],
                        target=x["target"], now=x["now"]) for x in topup + trim]
        ok, why = sync.push_summary(ns, zero_p, topup_p, alerts, a.apply)
        print("[⑤ report] 推送 %s %s" % ("OK" if ok else "FAIL", why))
    elif a.push:
        print("[⑤ report] 无改动，跳过推送（预警 %d 条已落表）" % len(alerts))

    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
