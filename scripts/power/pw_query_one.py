# -*- coding: utf-8 -*-
"""直接查单个单号（修正版）：确认它到底还在不在 + 当前状态。

之前 `pw_find_one` 全返回 0 行是**查询参数问题**（不是单丢了）：
`pw_diag_query` 同样参数能拿到 53 行 / 总计 46085。
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07055826094713286"
STATUSES = ("下单失败", "已下单", "未下单", "已取消")


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    c = c.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def main():
    pw = SS.PW()
    pw.login()
    body = {"act": "query_shop_order", "deliver_no": PO, "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-01", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    raw = pw.post("hwc_shop.php?is_ajax=1", body)
    d = json.loads(raw if isinstance(raw, str) else json.dumps(raw))
    c = d.get("content") or ""
    print("content 长度:", len(c), "| 含单号:", PO in c)

    rows = re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', c, re.S)
    print("行数:", len(rows))
    for oid, tr in rows:
        if PO not in tr:
            continue
        st = [s for s in STATUSES if s in tr]
        cells = [clean(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        print("★ 找到 id=%s 状态=%s" % (oid, st))
        for i, x in enumerate(cells):
            if x:
                print("   [%2d] %s" % (i, x[:110]))
        return 0
    print("\n❌ 这个单号在 2026-11-01~25 范围里查不到")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
