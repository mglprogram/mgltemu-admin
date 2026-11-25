# -*- coding: utf-8 -*-
"""从 stock.php 页面 HTML 里抠出**搜索表单**的字段名 + 分页参数。

目标：知道「Seller sku」输入框提交时的 name，以及怎么翻页。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("stock.php?act=list")
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t))

    print("\n=== 所有 <form> ===")
    for m in re.finditer(r"<form[^>]*>", t):
        print("  ", m.group(0)[:180])

    print("\n=== 非 checkbox 的 input / select 字段 ===")
    seen = set()
    for m in re.finditer(r"<(input|select)[^>]*>", t):
        tag = m.group(0)
        if "checkbox" in tag.lower():
            continue
        nm = re.search(r'name="([^"]*)"', tag)
        if not nm:
            continue
        name = nm.group(1)
        if name in seen:
            continue
        seen.add(name)
        print("  %-20s %s" % (name, tag[:110]))

    print("\n=== 分页相关文本 ===")
    for kw in ("共", "页", "page"):
        for m in list(re.finditer(kw, t))[:3]:
            i = m.start()
            seg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t[max(0, i - 60):i + 120]))
            print("  [%s] %s" % (kw, seg.strip()[:150]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
