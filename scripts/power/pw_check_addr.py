# -*- coding: utf-8 -*-
"""诊断：这单地址到底坏了没？

上一轮 `edit_update` 之后下单报「州0不存在」。可能：
  a) 地址真被清空了（city/city_name/district = 空/0）
  b) 只是编辑页的级联下拉没加载（**页面本来就显示空**），地址其实没动

本脚本：读**订单列表行**里的「收件地址」字段（那里才是生效值），
与编辑页字段对比。只读。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402
import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"
OID = sys.argv[2] if len(sys.argv) > 2 else "3013984"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def main():
    pw = SS.PW()
    pw.login()

    # ① 订单列表里的收件地址（生效值）
    q = H.query_orders(pw, PO)
    html = q.get("html") or ""
    for _oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S):
        if PO not in tr:
            continue
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        print("=== 订单列表行（生效值） ===")
        for i, c in enumerate(cells):
            if c:
                print("  [%2d] %s" % (i, c[:120]))
        break

    # ② 编辑页字段
    ed = pw.get("hwc_shop.php?act=edit_order&id=%s" % OID)
    t = ed if isinstance(ed, str) else str(ed)
    print("\n=== 编辑页地址字段 ===")
    for n in ("country", "city_name", "city", "district", "zip", "address"):
        vals = re.findall(r'name="%s"[^>]*value="([^"]*)"' % re.escape(n), t)
        print("  %-10s %s" % (n, vals[:2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
