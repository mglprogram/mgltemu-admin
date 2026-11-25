# -*- coding: utf-8 -*-
"""列出 pb=2（待打包）里的订单，找出适合验证「批量获取单号」的样本。

优先挑：非 TK 店、有 Fnsku、还没运单号的行。
打印 order id / 参考单号 / 店铺 / Fnsku / 是否有运单号。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


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
            time.sleep(12)
    raise last


def main():
    pw = login_retry()
    for pb in ("2", "3"):
        t = pw.get("deliver.php?act=list&pb=%s&uselastfilter=1" % pb)
        t = t if isinstance(t, str) else str(t)
        print("\n" + "=" * 62)
        print("=== pb=%s 长度 %d ===" % (pb, len(t)))

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
        shown = 0
        for tr in rows:
            m = re.search(r'name="checkboxes\[\]"[^>]*value="(\d+)"', tr)
            if not m:
                continue
            oid = m.group(1)
            txt = clean(tr)
            po = re.search(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})", txt)
            fn = re.search(r"D\d{8,}|[0-9]{10,13}|2IN[0-9A-Z]+", txt)
            shop = re.search(r"(TK-LA|TK-CBT|BHY|BEL|KinglamanSport|BS|BD|BAN|BE)\b", txt)
            print("  id=%-9s %-22s %-14s %-16s %s"
                  % (oid, (po.group(1) if po else "-")[:22],
                     (shop.group(1) if shop else "-"),
                     (fn.group(0) if fn else "-")[:16],
                     txt[:60]))
            shown += 1
            if shown >= 15:
                break
        if not shown:
            print("  （无行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
