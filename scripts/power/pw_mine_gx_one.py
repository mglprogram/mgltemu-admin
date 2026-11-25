# -*- coding: utf-8 -*-
"""只更新 **BHY 店铺**的订单（`gx_one('634418228526171', 0)`）。

页面结构（2026-11-25 实测 `hwc_shop.php?act=update_order_news`）：
    每行： <input type="checkbox" name="checkboxes[]" value="<shopId>">
           <input type="hidden" name="shop_id[]" value="<shopId>">
           <a onclick="gx_one('<shopId>',0)">立即更新</a>
    BHY 的 shopId = 634418228526171

先读 `gx_one` 函数体拿到真实接口，再只对 BHY 调一次。
"""
import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

BHY_SHOP = "634418228526171"
PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"


def find(pw, po) -> bool:
    body = {"act": "query_shop_order", "deliver_no": po, "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    raw = pw.post("hwc_shop.php?is_ajax=1", body)
    try:
        d = json.loads(raw if isinstance(raw, str) else json.dumps(raw))
    except Exception:  # noqa: BLE001
        return False
    return po in (d.get("content") or "")


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=update_order_news")
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t), "| 含 BHY:", BHY_SHOP in t)

    # 挖 gx_one 函数体
    m = re.search(r"function\s+gx_one\s*\((.*?)\{(.*?)\n\s*\}", t, re.S)
    if m:
        print("\n=== gx_one 函数体 ===")
        print(m.group(0)[:1200])
    else:
        print("\n没在 HTML 里找到 gx_one，找外链 JS")
        js = re.findall(r'src="([^"]*\.js[^"]*)"', t)
        print("   JS:", js[:8])

    # 找相关 ajax 片段
    for kw in ("gx_one", "update_order_one", "checkboxes", "shop_id[]"):
        for mm in list(re.finditer(re.escape(kw), t))[:2]:
            i = mm.start()
            seg = re.sub(r"\s+", " ", t[max(0, i - 250):i + 350])
            print("\n--- %s @%d ---\n%s" % (kw, i, seg[-420:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
