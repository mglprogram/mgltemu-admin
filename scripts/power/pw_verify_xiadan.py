# -*- coding: utf-8 -*-
"""确认 `act=xiadan_df` 到底有没有真下单。

现象（2026-11-25）：
    POST hwc_shop.php?act=xiadan_df&id=3013984&is_ajax=1
    → {"error": 1, "msg": "下单成功", "html": ""}
    但列表里这单状态仍是「未下单」

注意 `error: 1` 而 `msg: 下单成功` —— 与页面 JS 的判据**相反**：
    页面里 `if(error > 0){ layer.alert(msg); return false; }`
⇒ 按页面口径，`error=1` 是**失败**（提示"下单成功"只是文案）。
真正成功应该是 `error: 0` 且 `html` 里有内容。

本脚本：
  ① 打印该单完整响应
  ② 用**页面同一个接口**再查一次状态
  ③ 对比页面点击时的响应（需人工看）
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"


def status_of(html: str, po: str) -> str:
    m = re.search(r'class="deliver_no_(\d+)"[^>]*>%s</td>(.*?)</tr>' % re.escape(po),
                  html, re.S)
    seg = m.group(2) if m else html
    for st in ("下单失败", "下单成功", "已下单", "未下单"):
        if st in seg:
            return st
    return "?"


def main():
    p = H._pw()
    q = H.query_orders(p, PO)
    print("行:", q["rows"][:2])
    print("状态:", status_of(q.get("html") or "", PO))
    oid = (q["rows"] or [{}])[0].get("id")
    if not oid:
        print("拿不到订单 id")
        return 1
    print("\n再下一次（看响应结构）：")
    r = p.post("hwc_shop.php?act=xiadan_df&id=%s&is_ajax=1" % oid, {})
    print("  raw:", str(r)[:400])
    q2 = H.query_orders(p, PO)
    print("  再查状态:", status_of(q2.get("html") or "", PO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
