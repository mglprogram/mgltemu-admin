# -*- coding: utf-8 -*-
"""查 `PO-211-07055826094713286` 现在在帕沃尔哪个状态页 + 有没有单号。

刚下的单会经历：
    hwc_shop 下单代发（已下单）→ deliver.php 待确认（pb=1）→ 确认 → 获取单号

本脚本只读，依次查：
  ① hwc_shop 全部订单（按单号）
  ② deliver.php 待确认 pb=1
  ③ deliver.php 全部 pb=all
打印它在哪些页出现、状态是什么。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"
PAGES = [
    ("hwc 全部订单", "hwc_shop.php?act=shop_order&pb=1"),
    ("deliver 待确认", "deliver.php?act=list&pb=1"),
    ("deliver 全部", "deliver.php?act=list&pb=all"),
]


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def login_retry(n=5):
    last = None
    for i in range(n):
        try:
            p = SS.PW()
            p.login()
            return p
        except Exception as ex:  # noqa: BLE001
            last = ex
            print("  重试 %d/%d %s" % (i + 1, n, str(ex)[:50]))
            time.sleep(8)
    raise last


def main():
    pw = login_retry()
    for label, url in PAGES:
        print("\n=== %s ===" % label)
        try:
            t = pw.get(url)
            t = t if isinstance(t, str) else str(t)
        except Exception as ex:  # noqa: BLE001
            print("  取页失败:", str(ex)[:70])
            continue
        if PO not in t:
            print("  该页未出现（长度 %d）" % len(t))
            continue
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
            if PO not in tr:
                continue
            cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            ids = re.findall(r'name="(?:checkboxes\[\]|ids\[\]|checkboxes)"[^>]*value="(\d+)"',
                             tr)
            print("  ★ id=%s" % (ids[:1] or ["?"]))
            for i, c in enumerate(cells):
                if c:
                    print("    [%2d] %s" % (i, c[:110]))
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
