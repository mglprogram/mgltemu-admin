# -*- coding: utf-8 -*-
"""挖「已下单」页（`pb=2`）的**获取跟踪号 / 批量获取单号**接口。

用户 2026-11-25：
    「这边批量获取跟踪号也是要先在平台买面单才能拉到的」
    ⇒ 前提：TEMU 已买面单；帕沃尔这边「批量获取跟踪号」把运单号拉回来。
    样本单：PO-211-07055826094713286（已下单 + 已买面单）

本脚本只读：解析 pb=2 页的操作下拉 / 按钮 onclick / JS 函数。
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


def login_retry(n=6):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(10)
    raise last


def main():
    pw = login_retry()
    for pb in ("2", "3"):
        url = "hwc_shop.php?act=shop_order&pb=%s" % pb
        print("\n" + "=" * 62)
        print("=== pb=%s ===" % pb)
        t = pw.get(url)
        t = t if isinstance(t, str) else str(t)
        print("长度:", len(t), "| 含样本单:", PO in t)

        m = re.search(r'<select[^>]*name="select_types"[^>]*>(.*?)</select>', t, re.S)
        if m:
            print("  select_types 选项：")
            for v, txt in re.findall(
                    r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1)):
                print("    %-26s %s" % (v, clean(txt)))

        print("  按钮 onclick：")
        for m2 in re.finditer(r'<input[^>]*type="button"[^>]*>', t):
            tag = m2.group(0)
            if re.search(r"获取|单号|跟踪|面单|waybill|track", tag, re.I):
                print("    ", tag[:170])
        for m2 in re.finditer(r'onclick="([a-z_0-9]+)\(', t):
            print("    fn:", m2.group(1))

        print("  含「获取/跟踪/单号」的选项文本：")
        for v, txt in re.findall(
                r'<option[^>]*value="([a-z_0-9]*)"[^>]*>([^<]{1,26})</option>', t):
            if re.search(r"获取|跟踪|单号|面单", txt):
                print("    %-24s %s" % (v, clean(txt)))

        # 关键 JS 函数
        for fn in ("waybill_all", "get_waybill", "huoqu_waybill", "waybill"):
            mm = re.search(r"function\s+%s\s*\([^)]*\)\s*\{" % fn, t)
            if mm:
                i = mm.start()
                print("\n  [%s] %s" % (fn, re.sub(r"\s+", " ", t[i:i + 800])[:760]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
