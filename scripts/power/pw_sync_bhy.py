# -*- coding: utf-8 -*-
"""只同步 **BHY 一个店铺**的订单（`act=update_shop_news`）。

页面 JS（2026-11-25 实测 `hwc_shop.php?act=update_order_news`）：
    gx_one(shop_id, force) → gx_start_ids([shop_id], force)
      → $.ajax POST ./hwc_shop.php?act=update_shop_news
           &shop_id=<id>&pici=<批次>&force=0&is_ajax=1
           error==2 ⇒ confirm 后带 force=1 重试

BHY shop_id = 634418228526171
"""
import json
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


def sync_shop(pw, shop_id: str, force: int = 0, pici: str = "1", log=print) -> dict:
    url = ("hwc_shop.php?act=update_shop_news&shop_id=%s&pici=%s&force=%d&is_ajax=1"
           % (shop_id, pici, force))
    raw = pw.post(url, {})
    try:
        d = json.loads(raw if isinstance(raw, str) else json.dumps(raw))
    except Exception:  # noqa: BLE001
        d = {"error": -1, "msg": str(raw)[:200]}
    log("   sync shop=%s force=%d → error=%s msg=%s"
        % (shop_id, force, d.get("error"), str(d.get("msg"))[:80]))
    return d


def main():
    pw = SS.PW()
    pw.login()
    print("同步前存在？", find(pw, PO))

    print("\n① 建议先「更新订单(新)」页面（加载店铺表）")
    pw.get("hwc_shop.php?act=update_order_news")

    print("\n② 只同步 BHY")
    d = sync_shop(pw, BHY_SHOP, force=0)
    if d.get("error") == 2:
        print("   需要确认 → force=1 重试")
        d = sync_shop(pw, BHY_SHOP, force=1)
    elif d.get("error") not in (0, None):
        print("   首次失败 → force=1 重试")
        d = sync_shop(pw, BHY_SHOP, force=1)

    print("\n③ 轮询订单是否回来（最多 3 分钟）")
    for i in range(12):
        time.sleep(15)
        if find(pw, PO):
            print("   ✅ 第 %d 次轮询出现了" % (i + 1))
            return 0
        print("   …%ds" % ((i + 1) * 15))
    print("\n❌ 仍未拉回 —— 该单在 TEMU 侧可能已流转到别处")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
