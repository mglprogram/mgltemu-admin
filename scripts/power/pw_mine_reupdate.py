# -*- coding: utf-8 -*-
"""挖「删除重新更新」的接口 —— 用它把被写坏的地址恢复。

困扰：我 `edit_update` 整表单回传把地址写成了 "0 0"（州0不存在）。
帕沃尔自带 `[删除重新更新]` 按钮，会从平台重新拉取订单（地址恢复原样）。

本脚本读订单列表页里该按钮的 onclick 函数名 + 函数体。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"


def main():
    pw = SS.PW()
    pw.login()
    r = pw.get("hwc_shop.php?act=shop_order&pb=1")
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t))

    # 找「删除重新更新」附近的 onclick
    for kw in ("删除重新更新", "重新更新", "delete_update", "reupdate", "chongxin"):
        for m in list(re.finditer(kw, t))[:2]:
            i = m.start()
            seg = t[max(0, i - 400):i + 120]
            print("\n--- %s @%d ---" % (kw, i))
            print(seg.replace("\n", " ")[-450:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
