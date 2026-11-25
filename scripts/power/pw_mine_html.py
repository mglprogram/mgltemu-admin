# -*- coding: utf-8 -*-
"""从**已登录的 HTML 页面**里挖出「下单代发 / 获取单号 / 确认订单」的后端入口。

思路（不用浏览器抓包）：
  `pw_session.PW` 登录后 `GET hwc_shop.php?act=shop_order&pb=1` 能拿到完整 HTML，
  页面里的**按钮表单 / JS 函数**就写着提交到哪个 act、带什么参数。
  ① 抓页面 HTML → 正则挖 `act=xxx`、`name=xxx`、`onclick` 里的函数名
  ② 把 JS 文件也下下来，挖里面拼的 act 与字段
"""
import io
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OUT_DIR = r"D:\AI-workplace\TEMU\_logs\pw_pages"


def main():
    pw = SS.PW()
    pw.login()
    print("登录成功")

    html = pw.get("hwc_shop.php?act=shop_order&pb=1")
    txt = html if isinstance(html, str) else str(html)
    print("页面长度:", len(txt))
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    with io.open(os.path.join(OUT_DIR, "shop_order.html"), "w", encoding="utf-8") as f:
        f.write(txt)

    # ① 所有 act=
    acts = sorted(set(re.findall(r"act=([a-z_0-9]+)", txt)))
    print("\n① 页面里出现的 act:", acts)

    # ② 表单 action / name
    forms = re.findall(r'<form[^>]*action="([^"]+)"[^>]*name="([^"]*)"', txt)
    print("\n② 表单:", forms[:10])

    # ③ 按钮 onclick 里的函数
    onclicks = sorted(set(re.findall(r'onclick="([a-zA-Z_0-9]+)\(', txt)))
    print("\n③ onclick 函数:", onclicks[:20])

    # ④ 关键中文按钮附近的 JS
    for kw in ("下单代发", "获取单号", "确认订单", "批量操作", "修改仓库渠道"):
        i = txt.find(kw)
        if i < 0:
            continue
        seg = txt[max(0, i - 300):i + 200]
        print("\n④ 找到「%s」：" % kw)
        print("   ", seg.replace("\n", " ")[:380])

    # ⑤ 引用的 JS 文件
    js = sorted(set(re.findall(r'src="([^"]*\.js[^"]*)"', txt)))
    print("\n⑤ 引用的 JS:", js[:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
