# -*- coding: utf-8 -*-
"""从帕沃尔导航里**枚举全部菜单 URL**，再逐个检测哪页含指定单号。

比猜 act 靠谱：导航栏就是站点自己的入口清单。
本脚本：
  ① 抓首页/发货页的导航 <a href>
  ② 归一化去重
  ③ 逐个 GET，检测是否含 `PO` 单号
只读。
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


def get_retry(pw, url, n=3):
    last = None
    for _ in range(n):
        try:
            return pw.get(url)
        except Exception as ex:  # noqa: BLE001
            last = ex
            time.sleep(6)
    raise last


def main():
    pw = login_retry()
    print("① 从导航抓菜单…")
    menus = set()
    for seed in ("user.php", "hwc_shop.php?act=shop_order&pb=1", "deliver.php?act=list"):
        try:
            t = get_retry(pw, seed)
            t = t if isinstance(t, str) else str(t)
        except Exception:  # noqa: BLE001
            continue
        for href in re.findall(r'href="([^"#]+\.php[^"]*)"', t):
            if href.startswith(("javascript:", "http")):
                continue
            menus.add(href.replace("&amp;", "&"))
    print("   拿到 %d 个菜单入口" % len(menus))
    for u in sorted(menus):
        print("     ", u)

    print("\n② 逐个检测含单号 %s …" % PO)
    for u in sorted(menus):
        try:
            t = get_retry(pw, u, n=2)
            t = t if isinstance(t, str) else str(t)
        except Exception as ex:  # noqa: BLE001
            print("  %-44s 失败" % u[:44])
            continue
        hit = PO in t
        if hit:
            print("  ★ %-44s 含单号！长度 %d" % (u[:44], len(t)))
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
                if PO not in tr:
                    continue
                cells = [clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
                for i, x in enumerate(cells):
                    if x:
                        print("      [%2d] %s" % (i, x[:100]))
                for m in re.finditer(r'onclick="([a-z_0-9]+)\(([^)]*)\)"', tr):
                    print("      onclick:", m.group(1), "(", m.group(2)[:40], ")")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
