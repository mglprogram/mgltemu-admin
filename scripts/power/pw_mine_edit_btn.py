# -*- coding: utf-8 -*-
"""挖「编辑」按钮：订单列表里 `[编辑]` 点击后弹窗，弹窗里能选 Fnsku。

订单列表（2026-11-25 实测）：
    <a href="javascript:void(0)" onclick="edit_order('xxxx')">[编辑]</a>
    （与「下单代发」的 `edit_order('xiadan_df')` 同名，靠参数区分分支）

本脚本：读 `edit_order` 里**非 xiadan_df/pl_huishou/pl_shanchu** 的那个分支，
看它打开哪个弹窗 / 调哪个接口。同时列出页面上所有 `edit_order(` 的实参。
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

    # ① 页面上所有 edit_order(...) 的实参
    args = sorted(set(re.findall(r"edit_order\('([^']+)'\)", t)))
    print("\n=== edit_order 的所有实参 ===")
    print("  ", args)

    # ② [编辑] 按钮到底调什么
    print("\n=== 列表行里的「编辑」链接 ===")
    for m in list(re.finditer(r"\[编辑\]", t))[:3]:
        i = m.start()
        seg = re.sub(r"\s+", " ", t[max(0, i - 320):i + 80])
        print("  ...", seg[-380:])

    # ③ 找弹窗相关的 div / 函数
    print("\n=== 可能的弹窗 / 编辑函数 ===")
    for fn in ("edit_sku", "show_edit", "open_edit", "edit_product", "select_sku",
               "huan_sku", "change_sku", "edit_item"):
        if re.search(r"function\s+%s\s*\(" % fn, t):
            mm = re.search(r"function\s+%s\s*\(.*?\{" % fn, t, re.S)
            i = mm.start()
            print("\n--- %s ---\n%s" % (fn, re.sub(r"\s+", " ", t[i:i + 900])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
