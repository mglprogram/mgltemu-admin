# -*- coding: utf-8 -*-
"""换 Fnsku 时**平台SKU 与 Fnsku 是两套字段**——先弄清哪个是真 Fnsku。

订单编辑页解析结果（2026-11-25，订单 3013984）：
    huan_id[]        3308126                  ← 产品主键（数字）
    ck_sku[]         BHY-YOGA-4SetA-L         ← **平台SKU**（不是 Fnsku！）
    pname[]          4pcs Women Tennis ...
    products_nums[]  1
    pvalue[]         0.00

而 `pw_hwc_flow.fix_one_sku` 是把 `ck_sku[]` 覆盖成新 Fnsku ——
用户 2026-11-25 说「把原来的 sku 覆盖换成那个有库存的」，
库存页里那一列是 **Fnsku**（如 `D14450HD6AL`），不是 `BHY-YOGA-4SetA-L`。

本脚本：对比
  · 编辑页的 `ck_sku[]` 值
  · 库存页 `stock.php?act=list` 的 Fnsku 列表
  · 订单列表里「下单错误提醒」提到的 sku（`H320187483`）
判断到底该改哪个字段。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OID = "3013984"


def main():
    pw = SS.PW()
    pw.login()

    # ① 库存页：查 YOGA-4SetA-L 的所有 Fnsku + 各仓库存
    from urllib.parse import quote  # noqa: PLC0415
    q = pw.get("stock.php?act=list&seller_sku=%s" % quote("YOGA-4SetA-L"))
    txt = q if isinstance(q, str) else str(q)
    print("库存页长度:", len(txt))
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', txt, re.S)
    print("\n=== 库存页里含 YOGA-4SetA-L 的行 ===")
    for tr in rows:
        if "YOGA-4SetA" not in tr:
            continue
        cells = [re.sub(r"\s+", " ",
                        re.sub(r"<[^>]+>", " ",
                               c.replace("&nbsp;", " "))).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        cells = [c for c in cells if c != ""]
        if cells:
            print("   ", " | ".join(x[:28] for x in cells[:10]))

    # ② 编辑页 ck_sku
    print("\n=== 编辑页 ck_sku[] ===")
    ed = pw.get("hwc_shop.php?act=edit_order&id=%s" % OID)
    et = ed if isinstance(ed, str) else str(ed)
    print("   ", re.findall(r'name="ck_sku\[\]"[^>]*value="([^"]*)"', et)[:5])
    print("   huan_id:", re.findall(r'name="huan_id\[\]"[^>]*value="([^"]*)"', et)[:5])
    # 看编辑页有没有独立的 Fnsku 输入框
    print("   所有 sku 相关字段:",
          sorted({m for m in re.findall(r'name="([^"]*sku[^"]*)"', et, re.I)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
