# -*- coding: utf-8 -*-
"""挖 `edit_order('huoqumd')` / `edit_order('tuihui_danhao')` 的真实接口。

页面（2026-11-25 实测 `deliver.php?act=list&pb=2`）：
    <input type="button" value="批量获取单号" onclick="edit_order('huoqumd')">
    <input type="button" value="回传跟踪号"   onclick="edit_order('tuihui_danhao')">
表单：<form method="post" action="deliver.php?act=operate" name="listForm">

本脚本读 pb=2 页里 `edit_order` 函数体，找 `huoqumd` / `tuihui_danhao` 分支。
"""
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


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
    t = pw.get("deliver.php?act=list&pb=2&uselastfilter=1")
    t = t if isinstance(t, str) else str(t)
    print("页面长度:", len(t))

    # edit_order 全文
    m = re.search(r"function\s+edit_order\s*\(([^)]*)\)\s*\{", t)
    if not m:
        # 可能在外链 JS
        print("页面内没有 edit_order，列 JS：")
        print(re.findall(r'src="([^"]*\.js[^"]*)"', t)[:10])
        return 1
    start = m.start()
    # 粗略取到下一个顶层 function
    nxt = t.find("\nfunction ", start + 10)
    seg = t[start:nxt if nxt > 0 else start + 6000]
    print("edit_order 长度:", len(seg))

    for kw in ("huoqumd", "tuihui_danhao"):
        i = seg.find(kw)
        print("\n" + "=" * 60)
        if i < 0:
            print("没找到分支:", kw)
            continue
        print("--- %s 分支 ---" % kw)
        print(seg[max(0, i - 300):i + 1500])

    # 顺带看有没有 qumd / waybill 的 ajax
    print("\n=== 页面里所有 ajax（含 qu/waybill/danhao）===")
    for mm in re.finditer(r"\$\.(post|ajax)\s*\(", t):
        i = mm.start()
        s = re.sub(r"\s+", " ", t[i:i + 300])
        if re.search(r"qu|waybill|danhao|md", s, re.I):
            print("  •", s[:270])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
