# -*- coding: utf-8 -*-
"""挖「更新订单」接口 —— 用它把被我误删的订单从平台重新拉回来。

帕沃尔页面顶部有两个入口：「更新订单」/「更新订单(新)」。
本脚本读页面里的链接 href / onclick，找出后端 act。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=shop_order&pb=1")
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t))

    for kw in ("更新订单", "update_order", "更新"):
        for m in list(re.finditer(kw, t))[:6]:
            i = m.start()
            seg = t[max(0, i - 320):i + 160]
            seg = re.sub(r"\s+", " ", seg)
            print("\n--- %s @%d ---" % (kw, i))
            print(seg[-420:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
