# -*- coding: utf-8 -*-
"""跨状态 + 跨页找一单：确认 `PO-211-07055826094713286` 还在不在帕沃尔。

背景：我上一轮对 3013984 操作后，`edit_update` 报「订单状态不支持修改」、
`xiadan_df` 报「参数错误」，随后默认列表里查不到该单。
可能是：被删了 / 换了状态页 / 只是没在当前页。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"
STATUSES = ("下单失败", "已下单", "未下单", "已取消")


def main():
    p = H._pw()
    found = False
    for pb in ("", "0", "1"):
        for page in (1, 2, 3):
            q = H.query_orders(p, PO, start="2026-11-01", end="2026-11-25",
                               page=page, pb=pb)
            html = q.get("html") or ""
            n_rows = len(q["rows"])
            hit = PO in html
            print("pb=%-3s page=%d 命中=%-5s rows=%d"
                  % (pb if pb else "空", page, hit, n_rows))
            if not hit:
                continue
            found = True
            for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>',
                                      html, re.S):
                if PO not in tr:
                    continue
                st = [s for s in STATUSES if s in tr]
                cells = [re.sub(r"\s+", " ",
                                re.sub(r"<[^>]+>", " ", c)).strip()
                         for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                print("   ★ id=%s 状态=%s" % (oid, st))
                for i, c in enumerate(cells):
                    if c:
                        print("      [%2d] %s" % (i, c[:110]))
            break
        if found:
            break
    if not found:
        print("\n❌ 各状态各页都找不到 —— 这单已被删除，需要点「更新订单」重新拉取")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
