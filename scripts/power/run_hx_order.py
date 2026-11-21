#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_hx_order.py — 恒信仓 → 领星 OMS「智能下单」通用流水线（BHY / BEL / KLS 三店通用）。

用户 2026-11-20：
  「实现恒信仓-》领星智能下单」「这个脚本是三个店铺通用的」「把这个脚本封装成 skill，pipeline 化」

步骤顺序的唯一定义在 `data/hx_order_pipeline.json → steps`，本文件只负责「怎么跑」。

用法
----
    python scripts\\power\\run_hx_order.py --split <split.json>                    # 只看计划
    python scripts\\power\\run_hx_order.py --split <split.json> --confirm          # 真跑（分配+审核+回读+出表）
    python scripts\\power\\run_hx_order.py --pos-file <txt> --stage ship --confirm
    python scripts\\power\\run_hx_order.py --split <split.json> --stage verify     # 只回读
    python scripts\\power\\run_hx_order.py --split <split.json> --store KLS --stage report

退出码：0=全部有终态（处理中/已发货）；42=有未完成/失败；2=参数/文件问题。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "data", "hx_order_pipeline.json")
WRITE_STEPS = ("gw_assign", "gw_approve")


def _read(p: str, dflt=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dflt


def load_spec() -> dict:
    return _read(SPEC, {}) or {}


def load_pos(split: str, pos_file: str, pos: str) -> list:
    out = []
    if pos:
        out += [x.strip() for x in pos.replace("\n", ",").split(",") if x.strip()]
    if pos_file and os.path.exists(pos_file):
        with open(pos_file, encoding="utf-8") as f:
            out += [x.strip() for x in f if x.strip()]
    if split and os.path.exists(split):
        d = _read(split, {})
        for r in ((d.get("groups") or {}).get("HX") or []):
            o = str(r.get("ord") or "").strip()
            if o:
                out.append(o)
    return list(dict.fromkeys(out))


def load_skus(split: str) -> dict:
    """PO → [{sku, qty}]（只用于报告，不用于改单）。"""
    out = {}
    if split and os.path.exists(split):
        d = _read(split, {})
        for r in ((d.get("groups") or {}).get("HX") or []):
            o = str(r.get("ord") or "").strip()
            if not o:
                continue
            out.setdefault(o, []).append({"sku": r.get("sku", ""), "qty": int(r.get("qty") or 1)})
    return out


def _hx_lock(log=print):
    """领星 OMS 单窗口锁（键 HX-OMS），跨店/跨进程串行。"""
    try:
        pp = os.path.dirname(HERE)
        if pp not in sys.path:
            sys.path.insert(0, pp)
        from window_lock import window_lock  # noqa: PLC0415
    except Exception as ex:                       # noqa: BLE001
        log("[锁] 无窗口锁模块（%s）→ 不加锁" % str(ex)[:100])
        import contextlib  # noqa: PLC0415
        return contextlib.nullcontext()
    return window_lock(os.environ.get("HX_LOCK_KEY", "HX-OMS"), "hx-order",
                       wait_seconds=float(os.environ.get("HX_LOCK_WAIT", "900")),
                       log=lambda m: log("   [锁] %s" % m))


def _node_gw(stage: str, ctx: dict, log=print, extra=None) -> tuple:
    js = os.path.join(os.path.dirname(HERE), "oms", "hx_ship_gw.js")
    args = [js, "--stage", stage, "--out", os.path.abspath(ctx["out_dir"])]
    if ctx.get("store"):
        args += ["--store", ctx["store"]]
    if ctx.get("split"):
        args += ["--split", os.path.abspath(ctx["split"])]
    if ctx.get("pos_file"):
        args += ["--pos-file", os.path.abspath(ctx["pos_file"])]
    if stage in WRITE_STEPS and ctx.get("confirm") and not ctx.get("dry"):
        args += ["--apply"]
    if ctx.get("no_sku_change"):
        args += ["--no-sku-change"]
    args += list(extra or [])
    log("   $ node %s" % " ".join(args))
    try:
        p = subprocess.run(["node"] + args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=7200, cwd=os.path.dirname(os.path.dirname(HERE)))
    except FileNotFoundError:
        raise RuntimeError("本机没有 node") from None
    except subprocess.TimeoutExpired:
        return 124, "超时 7200s"
    tail = (p.stdout or "")[-4000:]
    if p.returncode != 0:
        tail += "\n[stderr]\n" + (p.stderr or "")[-1500:]
    return p.returncode, tail


def _art(ctx: dict, name: str, dflt=None):
    return _read(os.path.join(ctx["out_dir"], name), dflt)


# ------------------------------------------------------------------ 各步

def step_gw_plan(ctx, log=print):
    rc, tail = _node_gw("gw_plan", ctx, log)
    for ln in (tail or "").strip().splitlines()[-4:]:
        log("   " + ln[:200])
    if rc != 0:
        raise RuntimeError("gw_plan 失败 rc=%s" % rc)
    return {"gw_plan": (_art(ctx, "gw_plan.json", {}) or {}).get("count", 0)}


def step_gw_carriers(ctx, log=print):
    rc, tail = _node_gw("gw_carriers", ctx, log)
    for ln in (tail or "").strip().splitlines()[-3:]:
        log("   " + ln[:200])
    if rc != 0:
        raise RuntimeError("gw_carriers 失败 rc=%s" % rc)
    return {"gw_carriers": len(_art(ctx, "gw_carriers.json", {}) or {})}


def step_gw_assign(ctx, log=print):
    rc, tail = _node_gw("gw_assign", ctx, log)
    for ln in (tail or "").strip().splitlines()[-12:]:
        log("   " + ln[:200])
    rows = _art(ctx, "gw_assign.json", []) or []
    ok = len([x for x in rows if x.get("ok")])
    bad = [x for x in rows if not x.get("ok")]
    if rc != 0 and not rows:
        raise RuntimeError("gw_assign 失败 rc=%s" % rc)
    return {"gw_assign": ok, "total": len(rows), "failed": len(bad)}


def step_gw_approve(ctx, log=print):
    rc, tail = _node_gw("gw_approve", ctx, log)
    for ln in (tail or "").strip().splitlines()[-12:]:
        log("   " + ln[:200])
    rows = _art(ctx, "gw_approve.json", []) or []
    return {"gw_approve": len([x for x in rows if x.get("ok")]), "total": len(rows)}


def step_gw_verify(ctx, log=print):
    rc, tail = _node_gw("gw_verify", ctx, log)
    for ln in (tail or "").strip().splitlines()[-6:]:
        log("   " + ln[:200])
    doc = _art(ctx, "hx_oms_ship.json", {}) or {}
    ok = doc.get("ok") or []
    bad = doc.get("fail") or []
    res = {"ok": len(ok), "fail": len(bad), "rows": len(doc.get("rows") or [])}
    ctx["result"] = res
    return {"gw_verify": res}


def step_report(ctx, log=print):
    """出表：成功 + 未转入处理中（单独一张）+ 步骤说明 + 判断口径。"""
    try:
        import openpyxl  # noqa: PLC0415
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side  # noqa: PLC0415
    except Exception as ex:                       # noqa: BLE001
        log("   [report] 没装 openpyxl（%s）→ 跳过出表" % str(ex)[:80])
        return {"report": None}

    doc = _art(ctx, "hx_oms_ship.json", {}) or {}
    rows = {x.get("po"): x for x in (doc.get("rows") or [])}
    skus = ctx.get("skus") or {}
    spec = load_spec()
    wh = spec.get("warehouse", {})
    date = time.strftime("%Y-%m-%d")

    wb = openpyxl.Workbook()
    fill = PatternFill("solid", fgColor="DDEBF7")
    thin = Side(style="thin", color="BFBFBF")
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)

    def fmt(ws, ncol, widths):
        for c in range(1, ncol + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = Font(bold=True)
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for r in range(1, ws.max_row + 1):
            for c in range(1, ncol + 1):
                ws.cell(row=r, column=c).border = bd
                ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
        for col, w in widths.items():
            ws.column_dimensions[col].width = w
        ws.freeze_panes = "A2"

    ok_rows, bad_rows = [], []
    for po in ctx["pos"]:
        r = rows.get(po) or {}
        st = r.get("status")
        line = [
            po, r.get("orderNo", ""),
            " / ".join(f"{s['sku']}x{s['qty']}" for s in skus.get(po, [])),
            wh.get("name", ""), r.get("statusName", ""), r.get("track", ""),
            r.get("cause", ""),
        ]
        (ok_rows if st in (2, 3) else bad_rows).append(line)

    ws = wb.active
    ws.title = "未转入处理中"
    ws.append(["序号", "参考单号", "领星订单号", "平台SKU", "发货仓", "领星状态", "面单/异常原因"])
    for i, r in enumerate(bad_rows, 1):
        ws.append([i] + r)
    if not bad_rows:
        ws.append(["-", "无", "", "", "", "全部已转入处理中", ""])
    fmt(ws, 7, {"A": 8, "B": 26, "C": 20, "D": 32, "E": 22, "F": 18, "G": 56})

    ws2 = wb.create_sheet("发货成功")
    ws2.append(["序号", "参考单号", "领星订单号", "平台SKU", "发货仓", "领星状态", "运单号"])
    for i, r in enumerate(ok_rows, 1):
        ws2.append([i] + r[:5] + [r[5]])
    if not ok_rows:
        ws2.append(["-", "无", "", "", "", "", ""])
    fmt(ws2, 7, {"A": 8, "B": 26, "C": 20, "D": 32, "E": 22, "F": 18, "G": 24})

    ws3 = wb.create_sheet("下单步骤")
    ws3.append(["步骤", "操作", "说明"])
    for s in ctx.get("steps_desc") or []:
        ws3.append(list(s))
    fmt(ws3, 3, {"A": 8, "B": 70, "C": 60})

    ws4 = wb.create_sheet("判断口径")
    ws4.append(["领星状态 / 报错", "含义与处理"])
    for k in [
        ("处理中 / 已发货 (2/3)", "下单成功，可取运单号"),
        ("待获取平台面单 (1)", "审核已过但面单未回填（渠道=上传物流面单时需等面单上传）"),
        ("异常原因「暂未查询到相关面单」", "平台侧没有面单；先在平台完成出单，再回读"),
        ("异常原因「生成出库单失败」", "恒信仓无库存 → 换仓/补货"),
        ("「平台SKU未配对」", "主数据问题：先配对再审核，重试无效"),
        ("USW01 与 Temu自动分仓", "USW01=系统发货仓；Temu自动分仓=平台选仓策略，同时存在不冲突"),
    ]:
        ws4.append(list(k))
    fmt(ws4, 2, {"A": 44, "B": 72})

    name = "恒信仓领星下单_%s%s.xlsx" % (ctx.get("store") or "", "_" + date)
    path = os.path.join(ctx["out_dir"], name)
    wb.save(path)
    log("   [report] %s（成功 %d / 未转入 %d）" % (path, len(ok_rows), len(bad_rows)))
    return {"report": path, "ok": len(ok_rows), "not_done": len(bad_rows)}


STEPS = {
    "gw_plan": step_gw_plan,
    "gw_carriers": step_gw_carriers,
    "gw_assign": step_gw_assign,
    "gw_approve": step_gw_approve,
    "gw_verify": step_gw_verify,
    "report": step_report,
}

#: 出表「下单步骤」sheet 的固定说明（从店铺到领星，与 SKILL.md 一致）
STEPS_DESC = [
    ("1", "TEMU 卖家中心 → 订单管理 → 在线下单（订单列表）",
     "agentseller-us.temu.com/mmsos/orders.html?fulfillmentMode=0&queryType=2&sortType=1&timeZone=UTC%2B8..."),
    ("2", "页签「待发货」→ 展开筛选 → 库存扣减仓 = 恒信加州3 → 查询",
     "拉每页 500 条，看清本批全部单"),
    ("3", "勾选本批订单（可按订单号搜索，空格/逗号分隔，最多100个）", "跨页勾选可全选"),
    ("4", "导出订单 → 取 平台单号 / SKU货号 / 件数", "落紫鸟店铺下载目录 订单导出*.xlsx"),
    ("5", "生成 split.json：{\"groups\":{\"HX\":[{\"ord\",\"sku\",\"qty\"}]}}", "脚本 out\\kls_mk_split.py"),
    ("6", "领星 OMS（LM739）紫鸟窗口 TK-Savonire-YJF 已登录", "oms.xlwms.com/platform/order/list"),
    ("7", "一条命令：run_hx_order.py --split <split.json> --store <店> --confirm",
     "步骤：gw_plan → gw_carriers → gw_assign → gw_approve → gw_verify → report"),
    ("8", "gw_assign：发货仓 = Temu自动分仓（系统仓 USW01 美西）/ 物流渠道 = 上传物流面单",
     "**不改 SKU**；平台SKU未配对则跳过该单交人工"),
    ("9", "gw_approve：确认并审核", "batchApprove；失败自动转待处理重试一轮"),
    ("10", "gw_verify 回读：处理中/已发货 = 成功", "待获取平台面单 = 未完成，进「未转入处理中」表"),
]

BROWSER_STEPS = ("gw_assign", "gw_approve", "gw_verify")


def main(argv=None) -> int:
    spec = load_spec()
    ap = argparse.ArgumentParser(description="恒信仓 → 领星智能下单（三店通用）")
    ap.add_argument("--pos", default="", help="平台单号，逗号分隔")
    ap.add_argument("--pos-file", default="", help="单号 txt（一行一个）")
    ap.add_argument("--split", default="", help="拆仓 split.json（取 groups.HX）")
    ap.add_argument("--store", default="", help="BHY / BEL / KLS（仅记录用途）")
    ap.add_argument("--stage", default="smart", help="阶段：%s / all / 留空=全部" % "、".join(spec.get("stages") or {}))
    ap.add_argument("--only", default="", help="只跑这些步（逗号分隔）")
    ap.add_argument("--frm", default="", help="从这步开始")
    ap.add_argument("--to", default="", help="到这步结束")
    ap.add_argument("--out-dir", default="", help="产物目录")
    ap.add_argument("--confirm", action="store_true", help="写操作闸门")
    ap.add_argument("--dry", action="store_true", help="只出计划（写步骤跳过）")
    ap.add_argument("--allow-sku-change", action="store_true",
                    help="危险：允许把产品行替换成系统SKU（默认禁止，2026-11-20 用户令）")
    a = ap.parse_args(argv)

    pos = load_pos(a.split, a.pos_file, a.pos)
    if not pos:
        print("[FAIL] 没有单号：给 --pos / --pos-file / --split", file=sys.stderr)
        return 2

    out_dir = a.out_dir or os.path.join(HERE, "out", time.strftime("%Y-%m-%d"), "hx" + (a.store or ""))
    os.makedirs(out_dir, exist_ok=True)

    # 选步骤
    order = [s["name"] for s in (spec.get("steps") or [])]
    stages = spec.get("stages") or {}
    if a.stage and a.stage != "all":
        if a.stage not in stages:
            print("[FAIL] 未知 stage：%s（可选 %s）" % (a.stage, "、".join(stages)), file=sys.stderr)
            return 2
        want = list(stages[a.stage])
    else:
        want = list(order)
    if a.only:
        want = [x.strip() for x in a.only.split(",") if x.strip()]
    if a.frm:
        want = [x for x in order if order.index(x) >= order.index(a.frm)]
    if a.to:
        want = [x for x in want if x in order and order.index(x) <= order.index(a.to)]

    ctx = {
        "pos": pos, "split": a.split, "pos_file": a.pos_file, "store": a.store,
        "out_dir": out_dir, "confirm": a.confirm, "dry": a.dry,
        "no_sku_change": not a.allow_sku_change, "skus": load_skus(a.split),
        "steps_desc": STEPS_DESC,
    }
    print("[恒信智能下单] %d 单｜店 %s｜步骤 %s" % (len(pos), a.store or "-", " → ".join(want)))

    need_lock = any(s in BROWSER_STEPS for s in want)
    lockf = _hx_lock() if need_lock else None
    t0 = time.time()
    run = {"argv": sys.argv, "store": a.store, "confirm": a.confirm, "dry": a.dry,
           "steps": {}, "started": time.strftime("%Y-%m-%d %H:%M:%S")}
    failed = 0
    try:
        if lockf:
            lockf.__enter__()
        for name in want:
            fn = STEPS.get(name)
            if not fn:
                print("[步骤] %s ✗ 未知步骤" % name)
                failed += 1
                continue
            if name in WRITE_STEPS and (a.dry or not a.confirm):
                print("[步骤] %s ⊘ 跳过（写操作，缺 --confirm 或 --dry）" % name)
                run["steps"][name] = {"skipped": True}
                continue
            print("[步骤] %s …" % name)
            st = time.time()
            try:
                r = fn(ctx, print)
                el = round(time.time() - st, 1)
                print("[步骤] %s ✅ %ss %s" % (name, el, json.dumps(r, ensure_ascii=False)[:300]))
                run["steps"][name] = {"ok": True, "elapsed": el, "result": r}
            except Exception as ex:               # noqa: BLE001
                el = round(time.time() - st, 1)
                print("[步骤] %s ✗ %ss %s" % (name, el, str(ex)[:300]))
                run["steps"][name] = {"ok": False, "elapsed": el, "error": str(ex)[:800]}
                failed += 1
    finally:
        if lockf:
            try:
                lockf.__exit__(None, None, None)
            except Exception:
                pass

    run["elapsed_s"] = round(time.time() - t0, 1)
    run["summary"] = {"total": len(pos), **(ctx.get("result") or {})}
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=1)
    print("已存 %s" % os.path.join(out_dir, "run.json"))
    if a.dry or not a.confirm:
        print("[闸门] 未给 --confirm（或 --dry）：写步骤全部跳过，以上只是计划")
        return 0
    res = ctx.get("result") or {}
    if failed or res and (res.get("fail") or 0) > 0:
        return 42
    return 0


if __name__ == "__main__":
    sys.exit(main())
