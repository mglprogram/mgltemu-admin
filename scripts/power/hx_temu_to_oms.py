#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hx_temu_to_oms.py — 恒信仓发货：**TEMU 订单页筛仓 → 导出 → 领星 OMS 下单** 一条命令。

用户 2026-11-21 口径（原话）：
    「先到 TEMU 订单页面筛选出「恒信仓」的订单（卖家后台订单列表按发货仓=恒信加州3筛，
      BHY 下拉写「恒信加州3」、BEL 写「恒信加州3仓」）→ 导出这批订单 →
      拿这批平台单号去领星 OMS 批量搜索 → 选 Temu自动分仓下单 → 封装成 skill」

四步（stage）：
    ① temu_export   TEMU 导出页 export-order.html 设「库存扣减仓库=<店对应的恒信仓>」
                    + 订单日期 + 订单状态 → 确认导出 → 等记录完成 → 下载 → 转 xlsx
    ② pos          从导出表取平台单号（列：订单号/父订单号/parentOrderSn…），去重落 pos txt
    ③ oms          领星 OMS：按单号批量搜索 → 只发「待处理」的 → 分配 Temu自动分仓
                    → 确认并审核 → 回读（调 `scripts/oms/hx_ship_gw.js`）
    ④ report       出 xlsx：发货成功 / 未转入处理中（失败订单）/ 步骤说明 / 判断口径

写操作：
    · 领星侧（分配/审核）不给 `--confirm` 一律跳过（`--dry` 全只读）
    · TEMU 导出本身是只读动作，会给平台留一条导出记录
退出码：0 全 OK｜42 有未完成/失败｜2 参数或文件问题
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))

import cdp_transport as C  # noqa: E402

SPEC = os.path.join(HERE, "data", "hx_temu_to_oms.json")
GW_JS = os.path.join(WS, "scripts", "oms", "hx_ship_gw.js")
EXPORT_URL = "https://agentseller-us.temu.com/mmsos/export-order.html"

#: 各店在 TEMU「库存扣减仓库」下拉里的恒信仓名（2026-11-21 实测 BHY 选项：
#: 全部 / kansas / 新泽西2仓 / 加州三仓 / 恒信加州3 / 乐航加州3仓）
HX_WH_BY_STORE = {
    "BHY": "恒信加州3",
    "BEL": "恒信加州3仓",
    "KLS": "恒信加州3仓",
}
#: 导出表里可能出现的平台单号列名
_PO_COLUMNS = ("订单号", "父订单号", "平台订单号", "orderNo", "parentOrderSn", "Parent Order Sn", "Order No.")
#: 店后缀 → 紫鸟窗口名（ziniao_power_map 的 export_suffix 与店后缀不一致时用这张表兜底）
STORE_WINDOW = {
    "BHY": "TM-BHY-YJF",
    "BEL": "TM-BEL-MGL",
    "KLS": "TM-KinglamanSport",
}


def _log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- ① TEMU 导出

def _find_store_port(store: str) -> int:
    """店后缀 → 紫鸟窗口 CDP 端口（复用 run_temu_sales 的前缀探测 + ziniao_power_map）。

    ⚠ 2026-11-22：ziniao_power_map.json 里 KinglamanSport 的 export_suffix 是
    `KinglamanSport`（不是 KLS），所以 `_store_by_suffix('KLS')` 查不到 ⇒ 用本 skill
    自己的窗口名表兜底（STORE_WINDOW）。
    """
    import run_temu_sales as R  # noqa: PLC0415

    s = store.upper()
    m = R._store_by_suffix(store) or R._store_by_suffix(s)
    if not m and s in STORE_WINDOW:
        return R._find_store_port(STORE_WINDOW[s])
    if not m:
        return 0
    return R._find_store_port(m.get("ziniao_window_name") or m.get("power_name") or store)


def temu_export(store: str, day: date, out_dir: str, wh_name: str = "", confirm_download: bool = True,
                set_date: bool = False) -> str:
    """在 TEMU 导出页按「库存扣减仓库=<wh_name>」导出 → 落 out_dir/<店>_恒信仓_<day>.xlsx。

    2026-11-21 用户口径：「不去筛选日期，直接选完仓直接导出」→ set_date 默认 False，
    日期交给平台默认区间（实测=前一天）；失败的订单由 ③ 的 OMS 回读判出来。
    返回 "OK saved=<path>" 或 "FAIL/ERR ..."
    """
    from temu_sales_driver import export_orders_with_warehouse  # noqa: PLC0415

    wh_name = wh_name or HX_WH_BY_STORE.get(store.upper(), "")
    if not wh_name:
        return "FAIL: 店 %s 没有配置恒信仓名（见 HX_WH_BY_STORE）" % store
    port = _find_store_port(store)
    if not port:
        return "FAIL: 探不到 %s 的紫鸟窗口 CDP 端口（先 ziniao-cli store open --id <storeId>）" % store

    os.makedirs(out_dir, exist_ok=True)
    fname = "%s_恒信仓_%s.xlsx" % (store, day.strftime("%Y-%m-%d"))
    dst = os.path.join(out_dir, fname)
    return export_orders_with_warehouse(port, day, dst, wh_name,
                                        confirm_download=confirm_download, set_date=set_date)


# ---------------------------------------------------------------- ② 取单号

def read_pos_from_xlsx(path: str):
    """从导出表取平台单号（多列名兼容，去重保序）。"""
    from openpyxl import load_workbook  # noqa: PLC0415

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header = None
    idx = -1
    out = []
    seen = set()
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if header is None:
            # 表头通常在第 1 行；前 8 行里找平台单号列
            for col in _PO_COLUMNS:
                if col in cells:
                    header = cells
                    idx = cells.index(col)
                    break
            if header is None and i < 8:
                continue
            if header is None:
                break
            continue
        if idx >= len(row):
            continue
        po = str(row[idx] or "").strip()
        if po and po not in seen and po.lower() not in ("none", "nan"):
            seen.add(po)
            out.append(po)
    wb.close()
    if header is None or idx < 0:
        raise RuntimeError("导出表里找不到平台单号列（找过 %s）" % "/".join(_PO_COLUMNS))
    return out


# ---------------------------------------------------------------- ③ 领星 OMS

def run_gw(store: str, out_dir: str, pos: list, confirm: bool, log=_log) -> int:
    """调 hx_ship_gw.js 跑 plan→carriers→assign→approve→verify。"""
    os.makedirs(out_dir, exist_ok=True)
    pos_file = os.path.join(out_dir, "pos.txt")
    with open(pos_file, "w", encoding="utf-8") as f:
        f.write("\n".join(pos) + "\n")
    base = ["node", GW_JS, "--store", store, "--out", out_dir]
    steps = [
        ["--stage", "gw_plan", "--pos-file", pos_file],
        ["--stage", "gw_carriers"],
    ]
    if confirm:
        steps += [
            ["--stage", "gw_assign", "--apply"],
            ["--stage", "gw_approve", "--apply"],
        ]
    steps += [["--stage", "gw_verify", "--verify-wait", "60"]]
    rc = 0
    for s in steps:
        log("[oms] %s" % " ".join(s))
        r = subprocess.run(base + s, capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (r.stdout or "").strip().splitlines()
        for ln in tail[-6:]:
            log("   " + ln)
        if r.returncode != 0:
            log("   ⚠ rc=%s %s" % (r.returncode, (r.stderr or "")[-200:]))
            rc = 42
    return rc


# ---------------------------------------------------------------- ④ 出表

def write_report(store: str, day: date, out_dir: str, log=_log) -> str:
    """读 hx_oms_ship.json + gw_assign.json，出 4 张表。"""
    from openpyxl import Workbook  # noqa: PLC0415

    ship_path = os.path.join(out_dir, "hx_oms_ship.json")
    data = {"ok": [], "fail": [], "rows": []}
    if os.path.exists(ship_path):
        with open(ship_path, encoding="utf-8") as f:
            data = json.load(f)
    assign = []
    ap = os.path.join(out_dir, "gw_assign.json")
    if os.path.exists(ap):
        with open(ap, encoding="utf-8") as f:
            assign = json.load(f)
    bad_assign = {x.get("po"): (x.get("why") or "") for x in assign if not x.get("ok")}

    wb = Workbook()
    ws = wb.active
    ws.title = "发货成功"
    ws.append(["平台单号", "店铺", "OMS状态码", "OMS状态", "物流单号"])
    for x in data.get("ok", []):
        ws.append([x.get("po"), store, x.get("status"), x.get("statusText"), x.get("track")])

    ws2 = wb.create_sheet("未转入处理中（失败订单）")
    ws2.append(["平台单号", "店铺", "OMS状态码", "失败原因"])
    for x in data.get("fail", []):
        why = bad_assign.get(x.get("po")) or x.get("why") or ""
        ws2.append([x.get("po"), store, x.get("status"), why])
    for po, why in bad_assign.items():
        if po not in {y.get("po") for y in data.get("fail", [])}:
            ws2.append([po, store, "", "分配阶段即失败：" + why])

    ws3 = wb.create_sheet("下单步骤")
    ws3.append(["#", "步骤", "系统", "做什么"])
    for i, (name, sysname, desc) in enumerate([
        ("筛选恒信仓", "TEMU 卖家后台", "订单导出页「库存扣减仓库」= %s" % HX_WH_BY_STORE.get(store.upper(), "恒信加州3")),
        ("导出订单", "TEMU 卖家后台", "订单日期区间 + 订单状态=全部 + 已取消=过滤 → 确认导出 → 下载"),
        ("批量搜索", "领星 OMS", "按平台单号批量搜索，只发「待处理」的"),
        ("分配仓库&物流", "领星 OMS", "发货仓=Temu自动分仓，渠道=Temu自动分配渠道，不改SKU"),
        ("确认并审核", "领星 OMS", "batchApprove；失败做一轮转待处理重试"),
        ("回读判定", "领星 OMS", "处理中/已发货=成功；待获取平台面单=未完成"),
    ], 1):
        ws3.append([i, name, sysname, desc])

    ws4 = wb.create_sheet("判断口径")
    for r in [
        ["状态", "结论"],
        ["2 处理中 / 3 已发货", "下单成功"],
        ["1 待获取平台面单", "未完成 → 进失败表"],
        ["异常原因 获取异常", "平台侧没买到面单"],
        ["异常原因 生成出库单失败", "恒信仓无库存"],
        ["异常原因 已修改过sku", "OMS 里平台SKU被清空，无法用定仓定配 → 只能人工换渠道"],
        ["平台SKU未配对", "主数据问题，本流程跳过不改 SKU"],
    ]:
        ws4.append(r)

    dst = os.path.join(out_dir, "恒信仓领星下单_%s_%s.xlsx" % (store, day.strftime("%Y-%m-%d")))
    wb.save(dst)
    log("[report] → %s（成功 %d / 失败 %d）" % (dst, len(data.get("ok", [])), len(data.get("fail", []))))
    return dst


# ---------------------------------------------------------------- main

def build_argparser():
    ap = argparse.ArgumentParser(description="恒信仓：TEMU 筛仓导出 → 领星 OMS Temu自动分仓下单")
    ap.add_argument("--store", required=True, help="BHY / BEL / KLS，或 all（三个 TEMU 店全跑）")
    ap.add_argument("--date", default="", help="订单日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--warehouse", default="", help="TEMU 导出页的仓库名（默认按店：BHY=恒信加州3）")
    ap.add_argument("--xlsx", default="", help="已有导出表：跳过 TEMU 导出，直接取单号")
    ap.add_argument("--stage", default="all", choices=("all", "temu", "pos", "oms", "report"))
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--confirm", action="store_true", help="领星侧真写（分配/审核）")
    ap.add_argument("--dry", action="store_true", help="全只读，只打印计划")
    ap.add_argument("--set-date", action="store_true",
                    help="导出时筛订单日期（默认不筛，按用户 2026-11-21 口径直接选仓导出）")
    ap.add_argument("--temu-fulfill", action="store_true",
                    help="③ 之后调 run_temu_fulfill.py（订单页→领星那一段）")
    ap.add_argument("--store-gap", type=int, default=20,
                    help="--store all 时店与店之间的间隔秒数（默认 20s，各店共用同一扇 OMS 窗口）")
    return ap


def main(argv=None) -> int:
    a = build_argparser().parse_args(argv)

    # ★ 2026-11-22 用户令：「三个 temu 店铺都要跑这个任务」→ --store all
    if a.store.strip().lower() == "all":
        stores = list(HX_WH_BY_STORE.keys())          # BHY / BEL / KLS
        rc_all = 0
        for i, s in enumerate(stores):
            if i and a.store_gap > 0:
                time.sleep(a.store_gap)
            sub = list(argv if argv is not None else sys.argv[1:])
            if "--store" in sub:
                sub[sub.index("--store") + 1] = s
            else:
                sub += ["--store", s]
            # 明细表各自落到自己的目录：去掉显式 --out-dir
            if "--out-dir" in sub:
                k = sub.index("--out-dir")
                del sub[k:k + 2]
            _log("")
            _log("========== [%d/%d] 店 %s ==========" % (i + 1, len(stores), s))
            try:
                r = _run_one(sub)
            except SystemExit as e:                    # argparse 退出
                r = int(e.code or 2)
            if r not in (0,):
                rc_all = rc_all or r
        return rc_all

    return _run_one(argv)


def _run_one(argv, a=None):
    """单店主流程（store=all 时由 main 逐店调这里）。"""
    if a is None:
        a = build_argparser().parse_args(argv)
    store = a.store.upper()
    day = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else date.today()
    out_dir = a.out_dir or os.path.join(HERE, "out", day.strftime("%Y%m%d"), "hx_" + store)
    os.makedirs(out_dir, exist_ok=True)
    run = {"argv": sys.argv, "store": store, "date": day.isoformat(), "out_dir": out_dir,
           "stages": {}, "t": time.time()}

    xlsx = a.xlsx
    if a.stage in ("all", "temu"):
        if xlsx:
            _log("[①] 用现成导出表：%s" % xlsx)
            run["stages"]["temu_export"] = "SKIP use " + xlsx
        else:
            _log("[①] TEMU 导出：店 %s｜日期 %s｜仓库 %s" % (store, day, a.warehouse or HX_WH_BY_STORE.get(store, "")))
            res = temu_export(store, day, out_dir, a.warehouse, confirm_download=not a.dry,
                              set_date=a.set_date)
            _log("   " + res)
            run["stages"]["temu_export"] = res
            if res.startswith("OK saved="):
                xlsx = res.split("OK saved=", 1)[1].split(" ", 1)[0]
            elif res.startswith("DRY"):
                xlsx = ""
            else:
                _save(run, out_dir)
                return 2
        if a.stage == "temu":
            _save(run, out_dir)
            return 0

    pos = []
    if a.stage in ("all", "pos", "oms", "report"):
        if xlsx:
            try:
                pos = read_pos_from_xlsx(xlsx)
            except Exception as e:  # noqa: BLE001
                _log("[②] ✗ %s" % e)
                run["stages"]["pos"] = "FAIL " + str(e)[:120]
                _save(run, out_dir)
                return 2
            with open(os.path.join(out_dir, "pos.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(pos) + "\n")
            _log("[②] 平台单号 %d 个 → %s" % (len(pos), os.path.join(out_dir, "pos.txt")))
            run["stages"]["pos"] = {"count": len(pos)}
        else:
            _log("[②] 没有导出表，跳过")
            run["stages"]["pos"] = "SKIP"
        if a.stage == "pos":
            _save(run, out_dir)
            return 0

    rc = 0
    if a.stage in ("all", "oms", "report"):
        if pos:
            _log("[③] 领星 OMS：%d 单（confirm=%s）" % (len(pos), a.confirm and not a.dry))
            rc = run_gw(store, out_dir, pos, confirm=a.confirm and not a.dry)
            run["stages"]["oms"] = "rc=%s" % rc
        else:
            _log("[③] 无单号，跳过 OMS")

    if a.stage in ("all", "report"):
        try:
            dst = write_report(store, day, out_dir)
            run["stages"]["report"] = dst
        except Exception as e:  # noqa: BLE001
            _log("[④] 出表失败：%s" % str(e)[:160])
            rc = rc or 42

    # ⑤ 交给 temu-fulfill（`run_temu_fulfill.py`）：只补这一步要的 `--only hx_oms_check,report`
    #    ★ 为什么不是跑 `--only hx_ship`：hx_ship 要 split.json 的 HX 组（拆仓结果），
    #      而我们这批单号来自 TEMU 页面筛选；两者重复且会二次改单。
    #      失败单回读 + 出失败表由 hx_oms_check/report 负责（要 --hx-shipped 判据）。
    if a.temu_fulfill:
        tl = [sys.executable, os.path.join(HERE, "run_temu_fulfill.py"), "--store", store, "--only", "report"]
        if a.confirm and not a.dry:
            tl.append("--confirm")
        _log("[⑤] temu-fulfill：%s" % " ".join(tl[-4:]))
        r = subprocess.run(tl, capture_output=True, text=True, encoding="utf-8", errors="replace")
        for ln in (r.stdout or "").strip().splitlines()[-10:]:
            _log("   " + ln)
        run["stages"]["temu_fulfill"] = "rc=%s" % r.returncode
        if r.returncode != 0:
            _log("   ⚠ rc=%s %s" % (r.returncode, (r.stderr or "")[-200:]))
            rc = rc or 42
    _save(run, out_dir)
    return rc


def _save(run, out_dir):
    run["elapsed"] = round(time.time() - run.get("t", time.time()), 1)
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump(run, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    raise SystemExit(main())
