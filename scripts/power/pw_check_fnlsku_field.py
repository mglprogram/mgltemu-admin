# -*- coding: utf-8 -*-
"""看订单编辑页里 `sku[]` 字段（这才是真 Fnsku）。

关键发现（2026-11-25）：
    编辑页有两套 SKU 字段：
      `ck_sku[]`  = 平台SKU  （BHY-YOGA-4SetA-L）
      `sku[]`     = **Fnsku** （H320187483 / D14450HD6AL …）
    用户说的「把原来的 sku 覆盖换成那个有库存的」指的是 `sku[]`。

库存页里同款可换的 Fnsku（YOGA-4SetA-L）：
    D14450HD6AL    BS2-YOGA-4SetA-L   加州3仓 172   ← 有货
    H320187483     BK-YOGA-4SetA-L    kansas 1      ← 没货（当前用的）
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OID = sys.argv[1] if len(sys.argv) > 1 else "3013984"


def main():
    pw = SS.PW()
    pw.login()
    ed = pw.get("hwc_shop.php?act=edit_order&id=%s" % OID)
    txt = ed if isinstance(ed, str) else str(ed)
    print("页面长度:", len(txt))

    for name in ("sku[]", "ck_sku[]", "huan_id[]", "pname[]", "products_nums[]",
                 "pvalue[]", "ck_id", "zdkd", "chqd"):
        vals = re.findall(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), txt)
        sel = re.findall(r'name="%s"[^>]*>(.*?)</select>' % re.escape(name), txt, re.S)
        picked = ""
        if sel:
            m = re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', sel[0], re.I)
            picked = m.group(1) if m else ""
        print("   %-16s input=%s%s" % (name, vals[:4], (" select=%s" % picked) if sel else ""))

    # 找 sku[] 输入框的完整 html（判断是否是文本框 / 下拉）
    m = re.search(r'<[^>]*name="sku\[\]"[^>]*>', txt)
    print("\nsku[] 标签:", m.group(0)[:200] if m else "(没有)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
