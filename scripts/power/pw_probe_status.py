# -*- coding: utf-8 -*-
"""找出「下单失败」的查询参数（`status` 值）。

做法：直接试几个候选 status 值，看哪个能返回带「下单失败」的行。
  候选：''（全部）、'2'、'3'、'fail'、'failed'、'error'、'0'、'1'
以及 `pb`/`pb_str` 的组合。
只读。
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def probe(pw, **kw) -> tuple:
    body = {"act": "query_shop_order", "deliver_no": "", "shop_id": "", "ck_id": "",
            "status": "", "start_date": "2026-11-20", "end_date": "2026-11-25",
            "country": "", "shop_types": "", "pb_str": "", "page": "1"}
    body.update(kw)
    d = json.loads(pw.post("hwc_shop.php?is_ajax=1", body))
    c = d.get("content") or ""
    rows = re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', c, re.S)
    fails = sum(1 for _o, tr in rows if "下单失败" in tr)
    return len(rows), fails


def main():
    pw = SS.PW()
    pw.login()
    print("尝试不同 status / pb_str 组合：\n")
    combos = []
    for st in ("", "0", "1", "2", "3", "4", "fail", "failed", "error"):
        combos.append({"status": st})
    for pbs in ("", "0", "1", "2", "fail", "error"):
        combos.append({"pb_str": pbs})

    for kw in combos:
        try:
            n, f = probe(pw, **kw)
        except Exception as ex:  # noqa: BLE001
            print("  %-24s ERR %s" % (json.dumps(kw), str(ex)[:60]))
            continue
        mark = " ★" if f else ""
        print("  %-24s 行=%-4d 下单失败=%-4d%s" % (json.dumps(kw), n, f, mark))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
