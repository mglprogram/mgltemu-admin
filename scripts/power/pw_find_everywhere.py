# -*- coding: utf-8 -*-
"""全站扫一遍：`PO-211-07055826094713286` 出现在哪个页面 / 哪个 act 下。

已下单的单不在 `hwc_shop.php?act=shop_order` 列表里，
也不在 `deliver.php?act=list&pb=*` 里 —— 需要把站点其余菜单都试一遍。

做法：逐个 get 常见 act，检测页面是否含该单号；命中就打印它那行。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"

#: 站点菜单里可能列单的地方
CANDIDATES = [
    "deliver.php?act=list",
    "deliver.php?act=ps_list",
    "deliver.php?act=ps_list&pb=all",
    "deliver.php?act=list_zh",
    "deliver.php?act=rk_list",
    "deliver.php?act=tc_list",
    "deliver.php?act=xj_list",
    "deliver.php?act=bill_list",
    "hwc_shop.php?act=xj_list",
    "hwc_shop.php?act=shop_order_day",
    "hwc_shop.php?act=sale_info",
    "hwc_shop.php?act=stock_info",
    "hwc_index.php",
    "user.php",
]


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
    hits = []
    for u in CANDIDATES:
        try:
            t = pw.get(u)
            t = t if isinstance(t, str) else str(t)
        except Exception as ex:  # noqa: BLE001
            print("  %-42s 失败 %s" % (u, str(ex)[:40]))
            continue
        hit = PO in t
        print("  %-42s 长度=%-7d 含单号=%s" % (u, len(t), hit))
        if hit:
            hits.append((u, t))

    for u, t in hits:
        print("\n" + "=" * 62)
        print("★ 命中页面：", u)
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
            if PO not in tr:
                continue
            cells = [clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for i, x in enumerate(cells):
                if x:
                    print("   [%2d] %s" % (i, x[:110]))
            # 按钮 / 链接
            for m in re.finditer(r'onclick="([a-z_0-9]+)\(([^)]*)\)"', tr):
                print("   onclick:", m.group(1), "(", m.group(2)[:40], ")")
            for m in re.finditer(r'href="([^"]*(?:act|waybill|track)[^"]*)"', tr):
                print("   href:", m.group(1)[:110])
            break
    if not hits:
        print("\n都没命中 —— 该单可能已流转到「出库/已完成」状态，或被平台取消")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
