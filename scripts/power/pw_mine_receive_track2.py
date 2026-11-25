# -*- coding: utf-8 -*-
"""挖「确认订单（待确认→已确认）」与「获取跟踪号」的后端接口。

用户 2026-11-25：用 `PO-211-07055826094713286` 做样本，完成这两步的接口化。

两个页面：
  待确认订单： deliver.php?act=list&pb=1
  全部订单：   deliver.php?act=list&pb=all
  发货页：     hwc_shop.php?act=shop_order&pb=1

本脚本只读：解析按钮 onclick / 表单 / 操作下拉，找出 act。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PAGES = [
    ("待确认订单", "deliver.php?act=list&pb=1"),
    ("全部订单", "deliver.php?act=list&pb=all"),
    ("发货页", "hwc_shop.php?act=shop_order&pb=1"),
]


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def login_with_retry(n=6):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            print("  登录重试 %d/%d（%s）" % (i + 1, n, str(ex)[:50]))
            time.sleep(10)
    raise last


def main():
    pw = login_with_retry()
    print("登录 OK")
    for label, url in PAGES:
        print("\n" + "=" * 62)
        print("【%s】%s" % (label, url))
        try:
            r = pw.get(url)
        except Exception as ex:  # noqa: BLE001
            print("  取页失败:", str(ex)[:70])
            continue
        t = r if isinstance(r, str) else str(r)
        print("  长度:", len(t))

        for m in re.finditer(r"<form[^>]*>", t):
            print("  FORM:", m.group(0)[:170])

        print("  按钮/操作（含关键字的）:")
        for m in re.finditer(r"<(button|input)[^>]*>", t):
            tag = m.group(0)
            if re.search(r"确认|获取|单号|批量|提交|操作", tag):
                print("    ", tag[:165])

        print("  操作下拉选项:")
        for m in re.finditer(r'<option[^>]*value="([a-z_0-9]+)"[^>]*>([^<]{1,24})</option>',
                             t):
            v, txt = m.group(1), clean(m.group(2))
            if re.search(r"确认|获取|单号|发货|提交|track|receive", txt + v, re.I):
                print("    %-22s %s" % (v, txt))

        acts = sorted(set(re.findall(r"act=([a-z_0-9]+)", t)))
        print("  act:", acts[:25])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
