# -*- coding: utf-8 -*-
"""挖「下单代发」按钮背后的 JS：找它到底 POST 什么到哪。

页面里 ④ 显示：
  - 「提交」按钮  → `<form action="hwc_shop.php?act=operate">` + `name=act value=batch`
  - 「下单代发」  → `<input type="button" ... name=btnSubmit1>`（**没有 form**）
⇒ 下单代发一定是 JS 里 hand-rolled 的 ajax。去 `js/common.js` / `js/listtable.js` /
  `transport2.js` 里搜「下单代发」「xiadan」「btnSubmit」。

已看到的 act 候选：`xiadan_df`（下单代发）、`daochu_shop_order`（导出）、`update_order`。
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\pw_pages"
JS_FILES = ["js/common.js", "js/listtable.js", "./js/transport2.js", "./js/utils2.js"]


def main():
    pw = SS.PW()
    pw.login()
    os.makedirs(OUT, exist_ok=True)

    # ① 先把 hwc_shop.php 页面里「下单代发」按钮前后 1200 字挖出来
    html = pw.get("hwc_shop.php?act=shop_order&pb=1")
    txt = html if isinstance(html, str) else str(html)
    i = txt.find("下单代发")
    if i > 0:
        print("=== 页面里「下单代发」上下文 ===")
        print(txt[max(0, i - 700):i + 500])
        print()

    # ② 下载 JS 找 xiadan 相关
    for f in JS_FILES:
        try:
            js = pw.get(f.lstrip("./"))
        except Exception as ex:  # noqa: BLE001
            print("%-24s 取不到 %s" % (f, str(ex)[:50]))
            continue
        s = js if isinstance(js, str) else str(js)
        with io.open(os.path.join(OUT, os.path.basename(f)), "w", encoding="utf-8") as fd:
            fd.write(s)
        hits = []
        for kw in ("下单代发", "xiadan", "btnSubmit", "act=batch", "act: 'batch'",
                   "pl_xiadan", "operation="):
            for m in re.finditer(re.escape(kw), s):
                j = m.start()
                hits.append((kw, s[max(0, j - 150):j + 250].replace("\n", " ")))
                break
        if hits:
            print("\n=== %s（%d 字节）命中 %d 处 ===" % (f, len(s), len(hits)))
            for kw, seg in hits:
                print("  [%s] %s" % (kw, seg[:340]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
