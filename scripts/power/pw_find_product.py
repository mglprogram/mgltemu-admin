# -*- coding: utf-8 -*-
"""在“添加订单明细”选品器里搜 Fnsku，拿到**正确的产品三件套**。

选品器（`deliver.php?act=add_products&type=view&user_id=14450&ck=22`）每行结构：
    <input type="checkbox" name="checkboxes" value="118319">         ← 产品 id
    <input type="hidden" name="pname[]" class="pname_118319" value="BH-102204-6SetA-S">
    <input type="hidden" name="ck_sku[]" class="ck_sku_118319" value="18742087416">
    <input type="hidden" name="fh_weight[]" class="fh_weight_118319" value="1.60">

用法：
    python pw_find_product.py D14450HD6AL
    python pw_find_product.py YOGA-4SetA-L
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BASE_URL = "deliver.php?act=add_products&type=view&user_id=14450&user_name=&ck=22"
KW = sys.argv[1] if len(sys.argv) > 1 else "D14450HD6AL"


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get(BASE_URL)
    t = r if isinstance(r, str) else str(r)
    print("选品器长度:", len(t))

    # 按 checkboxes 切行块：每个产品 id 后面跟着一串 hidden
    blocks = re.split(r'(?=<input type="checkbox" name="checkboxes")', t)
    found = []
    for b in blocks:
        m = re.match(r'<input type="checkbox" name="checkboxes"\s+class="checkboxes"\s+'
                     r'value="(\d+)"', b)
        if not m:
            continue
        pid = m.group(1)
        head = b[:1400]

        def get(name):
            mm = re.search(r'name="%s" class="[^"]*" value="([^"]*)"' % re.escape(name),
                           head)
            return mm.group(1) if mm else ""

        rec = {"pid": pid,
               "pname": get("pname[]"),
               "ck_sku": get("ck_sku[]"),
               "pname_en": get("pname_en[]"),
               "fh_weight": get("fh_weight[]"),
               "products_type": get("products_type[]")}
        found.append(rec)

    print("产品行 %d 个" % len(found))
    hits = [x for x in found if KW.lower() in (x["pname"] + x["ck_sku"]).lower()]
    print("\n含「%s」的 %d 行：" % (KW, len(hits)))
    for h in hits:
        print("   pid=%-8s pname=%-22s ck_sku=%-14s weight=%s"
              % (h["pid"], h["pname"][:22], h["ck_sku"][:14], h["fh_weight"]))

    if not hits:
        print("\n（选品器一页只有部分产品，需要按 keywords 搜索）")
        print("提示：页面搜索框 name=keywords，走 searchOrder()")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
