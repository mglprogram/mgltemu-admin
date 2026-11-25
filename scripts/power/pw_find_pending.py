# -*- coding: utf-8 -*-
"""找一单帕沃尔「未下单」的订单，用于验证 `xiadan_df` 接口。

只读：查 2026-11-20~25 的订单列表，挑出状态=未下单 的行。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

PO_RE = re.compile(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})")


def main():
    p = H._pw()
    q = H.query_orders(p, "", start="2026-11-18", end="2026-11-25", page=1)
    html = q.get("html") or ""
    print("返回长度:", len(html))
    rows = re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S)
    print("行数:", len(rows))
    picked = []
    for oid, tr in rows:
        st = "未下单" if "未下单" in tr else ("已下单" if "已下单" in tr else "?")
        if st != "未下单":
            continue
        po = PO_RE.search(tr)
        sku = re.search(r"[A-Z]{2,4}-\d{6}-[A-Za-z0-9]+-[A-Z]{1,3}", tr)
        picked.append((oid, po.group(1) if po else "", st))
        print("   %-9s %-26s %-12s %s"
              % (oid, picked[-1][1], sku.group(0) if sku else "", st))
        if len(picked) >= 10:
            break
    if not picked:
        print("近几天没有「未下单」的单")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
