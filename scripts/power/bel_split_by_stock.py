# -*- coding: utf-8 -*-
"""bel_split_by_stock.py — BEL 批次按「帕沃尔优先」拆成两个平台发货 + 缺货待定。

用户口径（2026-11-05 原话）：
    「根据不带店铺前缀的 sku 去库存跟踪查，优先看这个尺码可用库存》帕沃尔库存，
      然后把订单分成两份，因为要去两个平台发货」

判据（按此顺序落档，任一不满足才往下走）：
    ① **帕沃尔可用 > 0** → A 份：帕沃尔发（仓：加州3仓 / kansas仓）
    ② 帕沃尔没有 → **恒信 OMS 可用 > 0** → B 份：恒信 ZOO ⑥美西发
    ③ 都没有 → C 份「缺货待定」（**不许当有货发出去**）

三条硬约束（都踩过 / 都会造成超卖或错配）：
    1. **匹配用「整串去品牌前缀」**（BH/BK/BS/BS2/BLA/BEL），
       不能用库存 JSON 里的 `code`/`variant`/`size` —— 那三个字段是**有损拆解**：
       `102104A`→`102104`（丢尾字母）、`6SetA1.1`→`6SetA`（丢版本点）。
       按三字段匹配会漏 6 个 SKU，按整串只漏真没货的。
    2. **同款多单要算总需求**：`204203-4SetC-L` 5 单但帕沃尔只有 2 件
       ⇒ 只能放 2 单进帕沃尔、其余进缺货，否则必然超卖。
    3. **帕沃尔只认发货仓**（加州3仓 / kansas仓）；Temu自动分仓那类不计入可用。

用法：
    python scripts/power/bel_split_by_stock.py --file "<导出订单.xlsx>"
    python scripts/power/bel_split_by_stock.py --file ... --out out/split.json --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_WS = os.path.dirname(os.path.dirname(_HERE))

#: 库存快照（看板产出，与 temu-goods-onsale / temu-bottom-stock-sync 同源）
PW_JSON = os.path.join(_WS, "temu-admin", "public", "data", "pw-stock.json")
LX_JSON = os.path.join(_WS, "temu-admin", "public", "data", "linxing-stock.json")

#: 可发货的帕沃尔仓（Temu自动分仓/其它虚拟仓不算可用）
PW_SHIP_WH = ("加州3仓", "kansas仓")

#: 跨系统对码要去掉的品牌前缀（同一款可能挂 BH/BK/BS/BS2/BLA，平台侧是 BEL）
_BRAND = re.compile(r"^(BH|BK|BS2|BS|BLA|BEL)-", re.I)


def norm_sku(s: str) -> str:
    """去店铺/品牌前缀并大写 —— 跨系统唯一可信的对码键（纯函数）。"""
    return _BRAND.sub("", str(s or "").strip().upper())


def build_index(items: list, fields: tuple, wh_filter: tuple = ()) -> dict:
    """按「去前缀整串」建索引（纯函数）。`wh_filter` 非空时只收这些仓。"""
    idx: dict = {}
    for it in items or []:
        if wh_filter and str(it.get("wh") or "") not in wh_filter:
            continue
        for f in fields:
            v = it.get(f)
            if v:
                idx.setdefault(norm_sku(v), []).append(it)
    return idx


def allocate(orders: list, pw_idx: dict, lx_idx: dict) -> dict:
    """把订单分到 A(帕沃尔)/B(恒信)/C(缺货)，**按 SKU 汇总需求防超卖**（纯函数）。

    orders: [{"ord","sku","qty"}]（已按优先级排好序，先到先得）
    返回 {"PW": [...], "HX": [...], "MISS": [...]}；每项带 `why` 说明落档原因。
    """
    want: dict = {}
    for o in orders:
        want.setdefault(norm_sku(o["sku"]), 0)
        want[norm_sku(o["sku"])] += int(o.get("qty") or 1)

    left_pw, left_hx = {}, {}
    for k in want:
        left_pw[k] = sum(int(x.get("stock") or 0) for x in (pw_idx.get(k) or []))
        left_hx[k] = sum(float(x.get("avail") or 0) for x in (lx_idx.get(k) or []))

    out = {"PW": [], "HX": [], "MISS": []}
    for o in orders:
        k = norm_sku(o["sku"])
        q = int(o.get("qty") or 1)
        rec = dict(o, key=k)
        pw_left, hx_left = left_pw.get(k, 0), left_hx.get(k, 0)
        if pw_left >= q:
            left_pw[k] = pw_left - q
            rec["why"] = "帕沃尔可分 %d → 分配后余 %d" % (pw_left, left_pw[k])
            rec["wh"] = "加州3仓"
            out["PW"].append(rec)
        elif hx_left >= q:
            left_hx[k] = hx_left - q
            rec["why"] = "恒信可分 %.0f → 分配后余 %.0f" % (hx_left, left_hx[k])
            rec["wh"] = "ZOO WAREHOUSE ⑥美西"
            out["HX"].append(rec)
        else:
            # 三种情况说清楚，别都写成「两边都无此 SKU」——那会把「被前面单占完」
            # （204203-4SetC-L 只 2 件、本批 5 单）误报成「没有这个 SKU」，排查时看不出来。
            if want.get(k) and (pw_left or hx_left) is not None:
                orig_pw = sum(int(x.get("stock") or 0) for x in (pw_idx.get(k) or []))
                orig_hx = sum(float(x.get("avail") or 0) for x in (lx_idx.get(k) or []))
                if orig_pw or orig_hx:
                    rec["why"] = ("可用已被前面的单分完（本批共需 %d，帕沃尔 %d + 恒信 %.0f）"
                                  % (want[k], orig_pw, orig_hx))
                else:
                    rec["why"] = "两边都无此 SKU（帕沃尔 0 / 恒信 0）"
            else:
                rec["why"] = "两边都无此 SKU"
            rec["wh"] = ""
            out["MISS"].append(rec)
    return out


def read_orders(path: str) -> list:
    """读导出表 → [{"ord","sku","qty"}]（按「要求最晚发货时间」升序，先到先得）。"""
    import pandas as pd  # noqa: PLC0415

    df = pd.read_excel(path, sheet_name=0)
    need_col = "要求最晚发货时间" if "要求最晚发货时间" in df.columns else None
    if need_col:
        df = df.sort_values(need_col, kind="mergesort")
    out = []
    for _, r in df.iterrows():
        out.append({"ord": str(r.get("订单号") or "").strip(),
                    "sku": str(r.get("SKU货号") or "").strip(),
                    "qty": int(r.get("应履约件数") or 1)})
    return [o for o in out if o["ord"]]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="BEL 批次按帕沃尔优先拆成两个平台发货")
    ap.add_argument("--file", required=True, help="TEMU 导出的订单 xlsx")
    ap.add_argument("--out", default="", help="结果 JSON 路径（空＝不落盘）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.file):
        print("[FAIL] 文件不存在：%s" % a.file, file=sys.stderr)
        return 42
    pw = json.load(open(PW_JSON, encoding="utf-8"))
    lx = json.load(open(LX_JSON, encoding="utf-8"))
    pw_idx = build_index(pw.get("items") or [], ("sku", "name"), wh_filter=PW_SHIP_WH)
    lx_idx = build_index(lx.get("items") or [], ("productName", "sku"))

    orders = read_orders(a.file)
    groups = allocate(orders, pw_idx, lx_idx)
    res = {"file": a.file, "total": len(orders),
           "counts": {k: len(v) for k, v in groups.items()},
           "stocks_generatedAt": {"pw": pw.get("generatedAt"), "hx": lx.get("generatedAt")},
           "rule": "①帕沃尔可用>0→A ②恒信avail>0→B ③都不是→C缺货；按SKU汇总需求防超卖",
           "groups": groups}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    names = {"PW": "A 帕沃尔发货（加州3仓 / kansas仓）",
             "HX": "B 恒信发货（ZOO ⑥美西）",
             "MISS": "C 缺货待定（**不许发**）"}
    print("总 %d 单 → A %d / B %d / C %d\n" % (len(orders), len(groups["PW"]),
                                              len(groups["HX"]), len(groups["MISS"])))
    for side in ("PW", "HX", "MISS"):
        print("== %s：%d 单 ==" % (names[side], len(groups[side])))
        for r in groups[side]:
            print("   %-26s %-24s x%s  %s" % (r["ord"], r["key"], r["qty"], r["why"]))
        print()
    if a.out:
        print("已存:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
