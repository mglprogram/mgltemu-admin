# -*- coding: utf-8 -*-
"""触发帕沃尔「更新订单」（从平台同步），把误删的订单拉回来。

接口： GET hwc_shop.php?act=update_order      （页面「更新订单」）
      GET hwc_shop.php?act=update_order_news （页面「更新订单(新)」）
两者都是页面入口，调用后站点会去平台拉单。

只做同步（读平台 → 写本站），不改任何已有订单。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"


def find(pw, po) -> bool:
    import json  # noqa: PLC0415
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
    print("触发前查单存在？", find(pw, PO))

    for act in ("update_order", "update_order_news"):
        print("\n→ GET hwc_shop.php?act=%s" % act)
        try:
            r = pw.get("hwc_shop.php?act=%s" % act)
            t = r if isinstance(r, str) else str(r)
            txt = re.sub(r"<[^>]+>", " ", t)
            txt = re.sub(r"\s+", " ", txt)
            print("   返回 %d 字节 | 片段: %s" % (len(t), txt[:200]))
        except Exception as ex:  # noqa: BLE001
            print("   异常:", str(ex)[:120])
        time.sleep(5)
        if find(pw, PO):
            print("   ✅ 单已出现！")
            return 0

    print("\n同步后仍未查到，等 60s 再查…")
    for i in range(4):
        time.sleep(15)
        if find(pw, PO):
            print("   ✅ 第 %d 次轮询出现了" % (i + 1))
            return 0
    print("\n❌ 仍未拉回来 —— 需要在 TEMU 后台重新分配仓或手工处理")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
