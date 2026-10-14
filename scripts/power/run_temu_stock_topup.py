# -*- coding: utf-8 -*-
"""run_temu_stock_topup.py — TEMU「立即加库存」补量建议（**只读 dry-run**）。

流程（用户 2026-10-10 定的口径）：
    海外仓库存看板取 近7天日均 / 代发库存  →  按变体换算折成 TEMU 单位
    →  扣掉 TEMU 现有销售可用库存  →  差额 = 该尺码的「销售可用库存增加量」

用法
----
    # 0) 列店（查窗口名/storeId）
    python scripts/power/run_temu_stock_topup.py --list-stores

    # 1) 从紫鸟 BHY 窗口的「销量分析」页实时取数（该窗口必须开着且已登录）
    python scripts/power/run_temu_stock_topup.py --store BHY

    # 2) 页面上取不到时，手工给「SKU货号=现有销售可用库存」（可重复）
    python scripts/power/run_temu_stock_topup.py --store BHY \\
        --sku BHY-202504-10SetA-L=23 --sku BHY-202504-10SetA-XL=30 --sku BHY-202504-10SetA-XXL=57

    # 3) 从 JSON 读（[{ "SKU货号": ..., "可售库存": ..., "SKU ID": ... }, ...]）
    python scripts/power/run_temu_stock_topup.py --store BHY --sku-json in.json

输出：控制台表格 + `out/<日期>/加库存建议_<店>_<时分秒>.md` 与同名 `.json`。
**本脚本绝不点击「立即加库存」/不提交任何东西** —— 写操作留给下一步单独实现。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import temu_stock_topup as T  # noqa: E402

MAP = os.path.join(HERE, "data", "ziniao_power_map.json")


def _load_store_map() -> dict:
    with open(MAP, encoding="utf-8-sig") as f:
        data = json.load(f)
    return {m["power_name"]: m for m in data.get("mapping", []) if m.get("power_platform") == "TEMU"}


def _find_store_port(window_name: str) -> int:
    import ziniao_store  # noqa: WPS433
    return ziniao_store.probe_port_by_window(window_name)


def _parse_sku_arg(s: str) -> dict:
    """`BHY-202504-10SetA-L=23` → {'SKU货号':…, '可售库存':23}（‘=’ 后可留空表示未知）。"""
    code, _, avail = s.partition("=")
    code = code.strip()
    if not code:
        raise ValueError("--sku 格式应为 `SKU货号=现有销售可用库存`：%r" % s)
    val = None
    if avail.strip():
        val = float(avail.strip())
    return {"SKU货号": code, "可售库存": val}


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 加库存建议（dry-run，只读）")
    ap.add_argument("--store", default=None, help="店后缀（BHY / BEL / KinglamanSport…）")
    ap.add_argument("--port", type=int, default=0, help="直接指定 CDP 端口（跳过窗口探测）")
    ap.add_argument("--list-stores", action="store_true", help="列出 TEMU 店（后缀/窗口/storeId）")
    ap.add_argument("--sku", action="append", default=[], help="`SKU货号=现有可售库存`，可重复")
    ap.add_argument("--sku-json", default=None, help="SKU 清单 JSON（[{SKU货号, 可售库存, SKU ID}]）")
    ap.add_argument("--only", action="append", default=[], help="只算这些 SKU货号（可重复/支持逗号分隔）")
    ap.add_argument("--target-days", type=float, default=None, help="目标覆盖天数（默认取配置 30）")
    ap.add_argument("--cfg", default=None, help="口径配置路径")
    ap.add_argument("--refresh", action="store_true", help="看板快照强制重下（忽略缓存）")
    ap.add_argument("--out", default=None, help="输出目录（默认 out/<日期>/）")
    args = ap.parse_args()

    smap = _load_store_map()
    if args.list_stores:
        for sfx, m in sorted(smap.items()):
            print("%-16s window=%-20s storeId=%s" % (sfx, m["ziniao_window_name"],
                                                     m["ziniao_window_store_id"]))
        return 0

    cfg = T.load_cfg(args.cfg)
    log = lambda m: print(m)  # noqa: E731  （走 stdout：stderr 会被 PowerShell 当错误、退码变 1）

    # ---- TEMU 侧取数
    rows, source_note = [], ""
    if args.sku or args.sku_json:
        if args.sku_json:
            with open(args.sku_json, encoding="utf-8-sig") as f:
                raw = json.load(f)
            rows += [{"SKU货号": r.get("SKU货号") or r.get("sku_code"),
                      "可售库存": r.get("可售库存", r.get("available")),
                      "SKU ID": r.get("SKU ID", r.get("sku_id", "")),
                      "商品": r.get("商品", r.get("product", ""))} for r in raw]
        rows += [_parse_sku_arg(s) for s in args.sku]
        source_note = "命令行/JSON 手工给定（现有销售可用库存）"
    else:
        port = args.port
        if not port:
            if not args.store:
                print("!! 要么给 --store/--port 走实时取数，要么用 --sku/--sku-json 手工给数",
                      file=sys.stderr)
                return 2
            if args.store not in smap:
                print("!! 未知店后缀 %s（可用 --list-stores 查）" % args.store, file=sys.stderr)
                return 2
            window = smap[args.store]["ziniao_window_name"]
            port = _find_store_port(window)
            if not port:
                print("!! 未探测到窗口 %s 的 CDP 端口（先开该店紫鸟窗口）" % window, file=sys.stderr)
                return 3
            log("[窗口] %s → CDP 端口 %d" % (window, port))
        rows = [{"SKU货号": r.get("sku_code"), "可售库存": r.get("available"),
                 "SKU ID": r.get("sku_id", ""),
                 "商品": r.get("title") or r.get("product") or ""}
                for r in T.read_live_rows(port)]
        source_note = "紫鸟店窗口 CDP 实时读取（端口 %d，销量分析页当前筛选行）" % port
        if not rows:
            print("!! 页面上没读到 SKU 行（页面未登录/未加载完？）", file=sys.stderr)
            return 3

    only = {x.strip() for a in args.only for x in a.split(",") if x.strip()}
    if only:
        rows = [r for r in rows if r.get("SKU货号") in only]
    if not rows:
        print("!! 没有可计算的 SKU 行", file=sys.stderr)
        return 2

    # ---- 看板取数 + 计算
    snap = T.fetch_snapshot(cfg, force=args.refresh, log=log)
    agg = T.aggregate_by_sku(snap.get("rows") or [])
    items = T.build_plan(rows, agg, cfg, args.target_days)

    T.print_table(items)
    print("")
    print("看板快照 built_at=%s（%s）| 口径：%s" % (
        snap.get("built_at"), "缓存" if snap.get("_from_cache") else "实时下载", cfg.get("口径")))

    # ---- 落盘
    outdir = args.out or os.path.join(HERE, cfg.get("输出目录") or "out",
                                      time.strftime("%Y-%m-%d"))
    os.makedirs(outdir, exist_ok=True)
    tag = "%s_%s" % (args.store or "manual", time.strftime("%H%M%S"))
    md_path = os.path.join(outdir, "加库存建议_%s.md" % tag)
    js_path = os.path.join(outdir, "加库存建议_%s.json" % tag)
    md = T.render_markdown(items, cfg, snap, source_note)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "store": args.store, "source": source_note,
                   "snapshot_built_at": snap.get("built_at"),
                   "口径": cfg.get("口径"), "目标天数": args.target_days or cfg.get("目标天数"),
                   "items": items}, f, ensure_ascii=False, indent=1)
    print("清单: %s" % md_path)
    print("数据: %s" % js_path)
    need = [i for i in items if i.get("结论") == "需加库存"]
    skip = [i for i in items if i.get("结论") == "跳过"]
    print("合计 %d 个 SKU：需加 %d，不用加 %d，跳过 %d" % (
        len(items), len(need), len([i for i in items if i.get("结论") == "不用加"]), len(skip)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
