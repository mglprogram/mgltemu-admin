# -*- coding: utf-8 -*-
"""对比「下单成功」与「下单失败」两单的关键字段：发货仓库 / 出货渠道 / 状态。

成功样本：PO-211-07257044664950105（人工点页面下的，页面提示"下单成功"）
失败样本：PO-211-07055826094713286（接口调 act=xiadan_df 返回 error=1）

只要看出「失败那单缺了哪个字段」，就知道下单前必须先做什么。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

OK_PO = "PO-211-07257044664950105"
BAD_PO = "PO-211-07055826094713286"


def row_html(html: str, po: str) -> str:
    m = re.search(r'(<tr class="tr_html_\d+[^"]*">(?:(?!</tr>).)*?%s(?:(?!</tr>).)*?</tr>)'
                  % re.escape(po), html, re.S)
    if m:
        return m.group(1)
    m = re.search(r'(<tr class="tr_html_\d+[^"]*">.*?</tr>)', html, re.S)
    return m.group(1) if m else ""


def cells(row: str) -> list:
    out = []
    for td in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S):
        t = re.sub(r"<[^>]+>", " ", td)
        t = re.sub(r"&nbsp;", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        out.append(t)
    return out


def main():
    p = H._pw()
    for label, po in (("✅ 成功样本", OK_PO), ("❌ 失败样本", BAD_PO)):
        q = H.query_orders(p, po)
        html = q.get("html") or ""
        row = row_html(html, po)
        cs = cells(row)
        print("\n=== %s  %s ===" % (label, po))
        for i, c in enumerate(cs[:16]):
            if c:
                print("   [%2d] %s" % (i, c[:110]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
