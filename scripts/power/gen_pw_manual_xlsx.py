# -*- coding: utf-8 -*-
"""gen_pw_manual_xlsx.py — 生成「帕沃尔人工处理清单」Excel。

用户口径（2026-11-06）：
  「这一单缺货的不处理最后完成发货，你生成 excel 给我，人工解决」

说明：帕沃尔侧共 26 单（加州3仓 23 + kansas 3）。
      本文件把**最终仍失败/需人工**的单列出来，附上平台单号、平台SKU、
      失败原因、库存查证结果、建议动作，交人工在页面上处理。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd  # noqa: PLC0415

OUT = r"D:\AI-workplace\TEMU\scripts\power\out\2026-11-06"

#: 最终状态表（2026-11-06 有头窗口逐单核实）
FINAL = [
    # po, sku, wh, status, reason, note
    ("PO-211-10501951898231050", "BEL-204605-3BHP-M", "加州3仓", "已下单", "",
     "改 Fnsku D14450OIZ1A → D14450IKEXO 后重新下单成功"),
    ("PO-211-10430919701110579", "BEL-204605-3BHP-M", "加州3仓", "已下单", "",
     "改 Fnsku D14450OIZ1A → D14450IKEXO 后重新下单成功"),
    ("PO-211-10541819884151516", "BEL-204605-3BHP-M", "加州3仓", "已下单", "",
     "改 Fnsku D14450OIZ1A → D14450IKEXO 后重新下单成功"),
    ("PO-211-10668631788152296", "BEL-203802-4SetB-XL", "加州3仓", "缺货未发",
     "sku:D14450QKZYT库存不足发货",
     "该 SKU 在帕沃尔只有 D14450QKZYT 一个 Fnsku（加州3仓 代发库存 0），无替代可换 → 人工"),
    ("PO-211-10767410709112604", "BEL-104102-1B2-L", "加州3仓", "已下单", "",
     "按去数字查到 104102-1B-L → 换 Fnsku 36262457815（库龄267天/加州3仓46件）成功"),
]

COLS = ["参考单号", "平台SKU", "发货仓库", "最终状态", "失败原因", "处理说明"]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    df = pd.DataFrame(FINAL, columns=COLS)
    need = df[df["最终状态"] != "已下单"].copy()
    p = os.path.join(OUT, "帕沃尔_人工处理清单_20261106.xlsx")
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        need.to_excel(w, sheet_name="待人工处理", index=False)
        df.to_excel(w, sheet_name="全部26单核对", index=False)
        for sheet, frame in (("待人工处理", need), ("全部26单核对", df)):
            ws = w.sheets[sheet]
            from openpyxl.utils import get_column_letter
            for i, col in enumerate(frame.columns, 1):
                width = max(len(str(col)) * 2 + 4,
                            *(len(str(v)) + 4 for v in frame[col].astype(str)))
                ws.column_dimensions[get_column_letter(i)].width = min(max(width, 12), 60)
            ws.freeze_panes = "A2"
    print("已生成:", p)
    print("待人工处理 %d 单：" % len(need))
    for _, r in need.iterrows():
        print("   %s  %s  %s" % (r["参考单号"], r["平台SKU"], r["失败原因"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
