# -*- coding: utf-8 -*-
"""看 pb=1 页面里 `PO-211-07055826094713286` 那一行的**原始 HTML**，
搞清勾选框叫什么、订单 id 在哪。

用户截图显示这单确实在「待确认订单」页，但我的 `checkboxes` 解析命中 0 行。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"


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
    for pb in ("1", "2"):
        print("\n" + "=" * 62)
        print("=== pb=%s ===" % pb)
        t = pw.get("deliver.php?act=list&pb=%s&uselastfilter=1" % pb)
        t = t if isinstance(t, str) else str(t)
        print("页面长度:", len(t), "| 含单号:", PO in t)
        if PO not in t:
            continue

        # 单号前后 2500 字原始 HTML
        i = t.find(PO)
        seg = t[max(0, i - 1800):i + 900]
        seg = re.sub(r"\n\s*\n", "\n", seg)
        print("\n--- 原始 HTML（单号前后）---")
        print(seg)

        # 该行里所有 input
        print("\n--- 该行附近所有 input ---")
        for m in re.finditer(r"<input[^>]*>", t[max(0, i - 1200):i + 1200]):
            print("   ", m.group(0)[:170])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
