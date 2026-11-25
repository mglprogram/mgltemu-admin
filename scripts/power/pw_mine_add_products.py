# -*- coding: utf-8 -*-
"""挖「添加订单明细」选品器（`deliver.php?act=add_products&type=view`）的**搜索接口**。

目的：拿到 `D14450HD6AL` 对应的**真实产品行数据**（huan_id / ck_sku / pname），
因为只改 `ck_sku[]` 文本会报「SKU:状态异常无法下单」
—— 必须三件套一起换。

页面入口（编辑页 `add_products()`）：
    ./deliver.php?act=add_products&type=view&user_id=<uid>&user_name=<name>&ck=<仓库id>
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

USER_ID = "14450"
CK = "22"          # 加州3仓

URLS = [
    "deliver.php?act=add_products&type=view&user_id=%s&user_name=&ck=%s" % (USER_ID, CK),
    "deliver.php?act=add_products&user_id=%s&ck=%s" % (USER_ID, CK),
]


def main():
    pw = SS.PW()
    pw.login()
    for u in URLS:
        r = pw.get(u)
        t = r if isinstance(r, str) else str(r)
        print("\n=== %s ===\n长度 %d" % (u, len(t)))
        if len(t) < 500:
            print("  ", t[:200])
            continue
        # 函数名
        fns = sorted(set(re.findall(r"function\s+([a-z_0-9]+)\s*\(", t, re.I)))
        print("  函数:", fns)
        # 搜索输入框
        for tag in re.findall(r"<input[^>]*>", t, re.I)[:20]:
            if re.search(r"name=|id=", tag):
                print("   ", tag[:130])
        # ajax
        print("  ajax:")
        for m in list(re.finditer(r"\$\.(post|ajax|get)\s*\(", t))[:10]:
            i = m.start()
            print("    •", re.sub(r"\s+", " ", t[i:i + 240])[:220])
        break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
