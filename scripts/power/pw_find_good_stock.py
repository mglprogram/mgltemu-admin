# -*- coding: utf-8 -*-
"""找一个「库存充足」的未下单订单，验证接口版成功路径。

从当前未下单列表里挑，用 `pw_hwc_stock` 或 order_pos 里同一套库存判断
（先用最简版：直接挨个试，直到有一单状态变成「下单成功」且**错误提醒里没有
「仓库没有库存」**）。

⚠ 会真下单，但只对「未下单」的单动手，已成功的不碰。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402
import pw_ship_api as S  # noqa: E402

PO_RE = re.compile(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})")


def list_pending(pw, limit=15):
    q = H.query_orders(pw, "", start="2026-11-20", end="2026-11-25", page=1)
    html = q.get("html") or ""
    out = []
    for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S):
        if "未下单" not in tr:
            continue
        po = PO_RE.search(tr)
        if not po:
            continue
        # 店铺类型
        plat = "shein" if "Shein" in tr or "shein" in tr else "temu"
        # 仓库（发货仓库字段）
        wh = "kansas仓" if "kansas" in tr.lower() else "加州3仓"
        out.append((po.group(1), oid, plat, wh))
        if len(out) >= limit:
            break
    return out


def main():
    pw = H._pw()
    cands = list_pending(pw)
    print("未下单候选 %d 单：" % len(cands))
    for c in cands:
        print("   %-26s id=%-9s %-6s %s" % c)
    ok = 0
    for po, oid, plat, wh in cands:
        r = S.ship(pw, po, wh, plat, apply=True, log=print)
        if r.get("ok") and "没有库存" not in str(r.get("api_msg")):
            print("\n✅ 成功样本：%s（%s/%s）track=%s"
                  % (po, wh, plat, r.get("track")))
            ok += 1
            break
        print("   …继续下一单\n")
    if not ok:
        print("\n没找到库存充足的样本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
