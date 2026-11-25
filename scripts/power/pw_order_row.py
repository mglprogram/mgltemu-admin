# -*- coding: utf-8 -*-
"""查单个帕沃尔订单的完整行内容 + 状态 + 下单错误提醒。

用户指定：GSU1A864F000J0G（处理失败的订单）
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "GSU1A864F000J0G"
STATUSES = ("下单失败", "下单成功", "已下单", "未下单")


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip()


def main():
    p = H._pw()
    q = H.query_orders(p, PO)
    html = q.get("html") or ""
    print("解析行:", q["rows"][:3])

    # 按 <tr class="tr_html_xxx"> 切所有行，找含该单号的
    blocks = re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S)
    hit = [(oid, tr) for oid, tr in blocks if PO in tr]
    if not hit:
        print("没找到这单（可能不在当前页/日期范围）")
        return 1
    oid, tr = hit[0]
    print("订单 id:", oid)
    print("状态:", [s for s in STATUSES if s in tr] or "?")
    cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
    for i, c in enumerate(cells):
        if c:
            print("   [%2d] %s" % (i, c[:130]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
