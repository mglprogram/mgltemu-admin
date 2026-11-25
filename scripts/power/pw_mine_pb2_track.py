# -*- coding: utf-8 -*-
"""从 `deliver.php?act=list&pb=2`（待打包）挖「批量获取单号」的接口。

用户 2026-11-25 截图指出：
    https://www.powerwarehouse.net/deliver.php?act=list&pb=2   ← 批量获取单号的页面
    而「待确认订单」pb=1 的下拉是：确认订单 / 批量修改渠道

本脚本只读：解析 pb=2 页的 select_types 全部选项 + 按钮，找「获取单号/获取跟踪号」。
"""
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


def login_retry(n=8):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            print("  登录重试 %d/%d %s" % (i + 1, n, str(ex)[:50]))
            time.sleep(12)
    raise last


def main():
    pw = login_retry()
    url = "deliver.php?act=list&pb=2&uselastfilter=1"
    print("=== %s ===" % url)
    t = pw.get(url)
    t = t if isinstance(t, str) else str(t)
    print("长度:", len(t), "| 含样本单:", PO in t)

    print("\nselect_types 全部选项：")
    m = re.search(r'<select[^>]*name="select_types"[^>]*>(.*?)</select>', t, re.S)
    if m:
        for v, txt in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>',
                                 m.group(1)):
            print("   %-28s %s" % (v, clean(txt)))
    else:
        print("  没找到 select_types")

    print("\n所有含「获取/单号/跟踪」的元素：")
    for m2 in re.finditer(r"<[^>]*(?:获取|单号|跟踪)[^>]*>", t):
        print("   ", m2.group(0)[:170])

    print("\n按钮：")
    for m2 in re.finditer(r'<input[^>]*type="(?:button|submit)"[^>]*>', t):
        tag = m2.group(0)
        if re.search(r"获取|单号|提交|批量", tag):
            print("   ", tag[:170])

    print("\n关键 JS 函数：")
    for fn in ("get_waybill", "huoqu", "waybill_all", "get_all_waybill",
               "batch_waybill", "ps_list", "qu_single"):
        mm = re.search(r"function\s+%s\s*\([^)]*\)\s*\{" % fn, t)
        if mm:
            i = mm.start()
            print("\n  [%s]\n  %s" % (fn, re.sub(r"\s+", " ", t[i:i + 700])[:660]))

    # 找表单提交目标
    print("\n表单：")
    for m2 in re.finditer(r"<form[^>]*>", t):
        print("   ", m2.group(0)[:170])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
