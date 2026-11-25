# -*- coding: utf-8 -*-
"""挖 `gx_start_ids([shop_id], force)` —— 真正触发同步的函数。

调用链：`gx_one(shopId, 0)` → `gx_start_ids([shopId], 0)`
本脚本读 `gx_start_ids` 的函数体，拿它 POST 到哪个 act。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=update_order_news")
    t = r if isinstance(r, str) else str(r)

    for fn in ("gx_start_ids", "gx_start", "gx_check"):
        m = re.search(r"function\s+%s\s*\((.*?)\{(.*?)\n\s*\}\s*\n" % fn, t, re.S)
        if not m:
            m = re.search(r"function\s+%s\s*\((.*?)\{" % fn, t, re.S)
            if m:
                i = m.start()
                seg = t[i:i + 2200]
                print("\n=== %s（前 2200 字）===" % fn)
                print(seg)
            continue
        print("\n=== %s ===" % fn)
        print(m.group(0)[:2200])

    # 所有含 $.post/$.ajax 且带 act 的片段
    print("\n=== 页面里所有 ajax 调用 ===")
    for m in list(re.finditer(r"\$\.(post|ajax|get)\s*\(", t))[:12]:
        i = m.start()
        seg = re.sub(r"\s+", " ", t[i:i + 420])
        print("  •", seg[:340])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
