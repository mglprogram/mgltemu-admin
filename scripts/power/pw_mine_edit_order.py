# -*- coding: utf-8 -*-
"""挖 `edit_order('xiadan_df')` 这个 JS 函数，拿到「下单代发」的**完整 POST 参数**。

页面证据（2026-11-25）：
    <input type="button" value="下单代发" onclick="edit_order('xiadan_df')" />
    <form action="hwc_shop.php?act=operate" name="listForm">
      <input type="hidden" name="act" value="batch" />
⇒ `edit_order(x)` 大概率是：取勾选的 ids → 往 listForm 里塞 act/operation/go 之类
   → submit 到 `hwc_shop.php?act=operate`。

本脚本把 `edit_order` 函数体整段打印出来。
"""
import io
import os
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\pw_pages"
JS_FILES = ["js/common.js", "js/listtable.js", "js/transport2.js", "js/utils2.js",
            "themes/default/js/Table.js", "js/listtable.js"]


def main():
    pw = SS.PW()
    pw.login()
    os.makedirs(OUT, exist_ok=True)

    found = False
    for f in dict.fromkeys(JS_FILES):
        try:
            js = pw.get(f.lstrip("./"))
        except Exception:  # noqa: BLE001
            continue
        s = js if isinstance(js, str) else str(js)
        with io.open(os.path.join(OUT, os.path.basename(f)), "w", encoding="utf-8") as fd:
            fd.write(s)
        m = re.search(r"function\s+edit_order\s*\(", s)
        if not m:
            continue
        found = True
        # 抓整段（到下一个 function 或文件末尾）
        start = m.start()
        nxt = s.find("function ", start + 10)
        seg = s[start:nxt if nxt > 0 else start + 4000]
        print("=== %s → edit_order 函数体（%d 字）===" % (f, len(seg)))
        print(seg[:2600])
        print()
    if not found:
        print("没在 JS 里找到 edit_order，打印页面内联脚本：")
        html = pw.get("hwc_shop.php?act=shop_order&pb=1")
        txt = html if isinstance(html, str) else str(html)
        for m in re.finditer(r"function\s+edit_order\s*\(", txt):
            start = m.start()
            nxt = txt.find("function ", start + 10)
            print(txt[start:nxt if nxt > 0 else start + 3000][:2600])
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
