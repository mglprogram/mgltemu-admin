# -*- coding: utf-8 -*-
"""找编辑页里往 `sku[]` 填值的 JS（编辑弹窗的保存/确认逻辑）。

编辑页表单（2026-11-25 实测，订单 3013984）：
    <input type="hidden" name="sku[]" class="sku" value="'+html[i].sku+'">   ← 模板串，运行时由 JS 填
    <input name="ck_sku[]" value="BHY-YOGA-4SetA-L">                        ← 平台SKU（只读）
⇒ 必须找到那个往 `.sku` 写值的函数，才知道「编辑」弹窗提交时带什么。

方法：扫页面内联 <script> 里所有出现 `sku` 赋值的片段。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

OID = sys.argv[1] if len(sys.argv) > 1 else "3013984"


def main():
    pw = SS.PW()
    pw.login()
    ed = pw.get("hwc_shop.php?act=edit_order&id=%s" % OID)
    txt = ed if isinstance(ed, str) else str(ed)

    scripts = re.findall(r"<script[^>]*>(.*?)</script>", txt, re.S)
    print("内联脚本 %d 段，总长 %d" % (len(scripts), sum(len(s) for s in scripts)))
    joined = "\n".join(scripts)

    # 所有出现 .sku 赋值 / sku[] 的地方
    pats = [r"\.sku['\"]?\s*\)?\s*\.val\(", r"\[name=['\"]?sku", r"\.sku\b",
            r"edit_sku", r"huan_sku", r"change_sku"]
    shown = set()
    for p in pats:
        for m in re.finditer(p, joined):
            i = m.start()
            key = joined[max(0, i - 60):i + 60]
            if key in shown:
                continue
            shown.add(key)
            print("\n--- %s @%d ---" % (p, i))
            print(joined[max(0, i - 260):i + 380].strip()[:640])
            if len(shown) > 14:
                return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
