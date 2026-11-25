# -*- coding: utf-8 -*-
"""探测帕沃尔后端可用 act（**只读**，登录后逐个试）。

`pw_session.PW` 是纯 urllib + cookie jar，不依赖浏览器。
这里登录后对 `hwc_shop.php` / `deliver.php` 试一批**只读** act，
把每个 act 的返回（JSON / HTML 片段）打出来，找出「下单代发 / 获取单号 / 确认订单」
对应的后端入口。

⚠ 只跑只读 act；写操作（下单代发/审核）留到参数确认后再做。
"""
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

#: 只读 act 候选（按命名规律）
READ_ACTS = [
    ("hwc_shop.php", {"act": "query_shop_order", "pb": "1"}),
    ("hwc_shop.php", {"act": "shop_order", "pb": "1"}),
    ("hwc_shop.php", {"act": "get_shop_order_list", "pb": "1"}),
    ("hwc_shop.php", {"act": "query", "pb": "1"}),
    ("deliver.php", {"act": "get_shop_paisongx", "ids": "", "zdkd": "803",
                     "select_types": "pl_edit_ck_chqd"}),
    ("deliver.php", {"act": "list"}),
]


def main():
    pw = SS.PW()
    pw.login()
    print("登录成功，逐个试只读 act：\n")
    for page, body in READ_ACTS:
        try:
            r = pw.post(page, body)
        except Exception as ex:  # noqa: BLE001
            print("%-16s %-28s ERR %s" % (page, body.get("act"), str(ex)[:80]))
            continue
        txt = r if isinstance(r, str) else str(r)
        head = txt[:160].replace("\n", " ")
        ok = ("success" in txt[:200].lower() or txt.lstrip().startswith("{")
              or txt.lstrip().startswith("["))
        print("%-16s %-28s %s len=%d" % (page, body.get("act"),
                                         "JSON?" if ok else "html", len(txt)))
        print("     %s" % head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
