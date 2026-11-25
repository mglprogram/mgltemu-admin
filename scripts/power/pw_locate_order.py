# -*- coding: utf-8 -*-
"""`PO-211-07055826094713286` 三个页都查不到 —— 定位它到底在哪。

可能：
  · 已下单的单从 hwc_shop「待发货」页移走了（去了 deliver.php 别的 pb）
  · 单号被 token 化（页面渲染时脱敏）
  · 在别的仓/别的店（这个账号有多个店铺）

做法：
  ① 用 `query_shop_order`（接口）直接按单号查，看它到底在不在
  ② 扫 deliver.php 各 pb 值
"""
import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def login_retry(n=5):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(8)
    raise last


def main():
    pw = login_retry()

    print("① query_shop_order 按单号查：")
    body = {"act": "query_shop_order", "deliver_no": PO, "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    d = json.loads(pw.post("hwc_shop.php?is_ajax=1", body))
    c = d.get("content") or ""
    print("   页面含单号:", PO in c, "| 长度", len(c))
    if PO in c:
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", c, re.S):
            if PO not in tr:
                continue
            cells = [clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for i, x in enumerate(cells):
                if x:
                    print("     [%2d] %s" % (i, x[:110]))
            break

    print("\n② deliver.php 各 pb：")
    for pb in ("0", "1", "2", "3", "all", ""):
        u = "deliver.php?act=list" + ("&pb=%s" % pb if pb else "")
        try:
            t = pw.get(u)
            t = t if isinstance(t, str) else str(t)
        except Exception as ex:  # noqa: BLE001
            print("   pb=%-4s 取页失败 %s" % (pb or "空", str(ex)[:50]))
            continue
        print("   pb=%-4s 长度=%-7d 含单号=%s" % (pb or "空", len(t), PO in t))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
