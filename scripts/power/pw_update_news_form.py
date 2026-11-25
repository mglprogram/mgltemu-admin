# -*- coding: utf-8 -*-
"""只同步 **BHY 一个店铺**的订单（`act=update_order_news` 页面里按店铺勾选）。

用户 2026-11-25：「更新 BHY 店铺就行」

做法：
  ① GET hwc_shop.php?act=update_order_news  看页面结构（店铺勾选框 / 按钮）
  ② 找到「按店铺更新」的提交方式（表单 or ajax），只勾 BHY
  ③ 提交后轮询订单是否回来
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


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=update_order_news")
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t))

    print("\n=== 表单 ===")
    for m in re.finditer(r"<form[^>]*>", t):
        print("  ", m.group(0)[:200])

    print("\n=== 按钮 / 链接（含更新/同步） ===")
    for m in re.finditer(r"<(button|input|a)[^>]*>[^<]*", t):
        tag = m.group(0)
        if not re.search(r"更新|同步|开始|执行|shop|update", tag, re.I):
            continue
        print("  ", tag[:180])

    print("\n=== 店铺相关复选框 / 隐藏域 ===")
    seen = set()
    for tag in re.findall(r"<input[^>]*>", t, re.I):
        nm = re.search(r'name="([^"]*)"', tag)
        if not nm:
            continue
        n = nm.group(1)
        if n in seen:
            continue
        if re.search(r"shop|mall|store|ck", n, re.I):
            seen.add(n)
            print("  %-20s %s" % (n, tag[:140]))

    print("\n=== BHY 附近的 HTML ===")
    for m in list(re.finditer(r"BHY", t))[:3]:
        i = m.start()
        print("  ...", clean(t[max(0, i - 200):i + 200])[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
