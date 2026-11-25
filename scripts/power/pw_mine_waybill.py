# -*- coding: utf-8 -*-
"""挖「批量获取跟踪号」的接口（`hwc_shop.php?act=shop_order` 页里的操作下拉）。

★ 用户 2026-11-25 说明：
    「这一单我已经买了面单，这边批量获取跟踪号也是要先在平台买面单才能拉到的」
    ⇒ 前提：平台(TEMU)已买面单 → 帕沃尔「批量获取跟踪号」才能拉到。

本脚本只读：解析 `hwc_shop.php?act=shop_order&pb=1` 里 `select_types` 的所有选项，
找出「获取跟踪号 / 获取单号 / waybill」对应哪个值。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def login_retry(n=5):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(8)
    raise last


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def main():
    pw = login_retry()
    for url in ("hwc_shop.php?act=shop_order&pb=1",
                "hwc_shop.php?act=shop_order&pb=0"):
        print("\n=== %s ===" % url)
        t = pw.get(url)
        t = t if isinstance(t, str) else str(t)
        print("长度:", len(t))

        # select_types 的全部选项
        m = re.search(r'<select[^>]*name="select_types"[^>]*>(.*?)</select>', t, re.S)
        if m:
            print("  select_types 选项：")
            for v, txt in re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>',
                                     m.group(1)):
                print("    %-26s %s" % (v, clean(txt)))
        else:
            print("  没找到 select_types")

        # 其他 select 里含「获取/单号/track」的
        print("  其它含跟踪号字样的选项：")
        for v, txt in re.findall(r'<option[^>]*value="([a-z_0-9]*)"[^>]*>([^<]{1,26})</option>',
                                 t):
            if re.search(r"获取|单号|跟踪|面单|track|waybill", txt, re.I):
                print("    %-26s %s" % (v, clean(txt)))

        # JS 里的获取函数
        for fn in ("get_waybill", "huoqu", "get_track", "waybill", "qiangzhi_shuaxin"):
            for mm in list(re.finditer(r"function\s+%s\s*\(" % fn, t))[:1]:
                i = mm.start()
                print("  [%s] %s" % (fn, re.sub(r"\s+", " ", t[i:i + 400])[:380]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
