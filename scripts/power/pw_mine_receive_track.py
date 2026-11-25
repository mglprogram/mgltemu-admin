# -*- coding: utf-8 -*-
"""挖 `pw_track`（批量获取单号）+ `pw_receive`（确认订单）的后端接口。

两个页面：
  确认订单：  deliver.php?act=list&pb=1      （「待确认订单」）
  获取单号：  hwc_shop.php?act=shop_order&pb=1 里勾选后「获取单号」

本脚本只读：拉这两个页面，挖出「确认订单」「获取单号」按钮的 onclick / 表单 action。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PAGES = [
    ("确认订单", "deliver.php?act=list&pb=1"),
    ("全部订单", "deliver.php?act=list&pb=all"),
    ("hwc 待发货", "hwc_shop.php?act=shop_order&pb=1"),
]


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def main():
    pw = SS.PW()
    pw.login()
    for label, url in PAGES:
        print("\n" + "=" * 60)
        print("【%s】%s" % (label, url))
        r = pw.get(url)
        t = r if isinstance(r, str) else str(r)
        print("长度:", len(t))

        # 表单
        for m in re.finditer(r"<form[^>]*>", t):
            tag = m.group(0)
            if "javascript" in tag or "act=" in tag:
                print("  FORM:", tag[:170])

        # 按钮 / 操作下拉
        print("  按钮/操作:")
        for m in re.finditer(r"<(button|input)[^>]*(value|onclick)=\"[^\"]*\"[^>]*>", t):
            tag = m.group(0)
            if re.search(r"确认|获取|单号|操作|提交|批量", tag):
                print("    ", tag[:170])

        # select 里的操作项
        print("  操作选项:")
        for m in re.finditer(r'<option value="([a-z_0-9]+)"[^>]*>([^<]{1,20})</option>', t):
            v, txt = m.group(1), clean(m.group(2))
            if re.search(r"确认|获取|单号|发货|提交", txt):
                print("    %-20s %s" % (v, txt))

        # act 列表
        acts = sorted(set(re.findall(r"act=([a-z_0-9]+)", t)))
        print("  act:", acts[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
