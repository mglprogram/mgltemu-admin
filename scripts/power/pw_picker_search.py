# -*- coding: utf-8 -*-
"""在选品器里按 keywords 搜索 Fnsku，拿到正确的产品三件套（pid / pname / ck_sku）。

选品器 URL：
    deliver.php?act=add_products&type=view&user_id=14450&user_name=&ck=22
搜索参数：`keywords`（页面 input name=keywords，走 searchOrder()）
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

KW = sys.argv[1] if len(sys.argv) > 1 else "D14450HD6AL"
CK = sys.argv[2] if len(sys.argv) > 2 else "22"


def parse(t: str) -> list:
    out = []
    blocks = re.split(r'(?=<input type="checkbox" name="checkboxes")', t)
    for b in blocks:
        m = re.match(r'<input type="checkbox" name="checkboxes"\s+class="checkboxes"\s+'
                     r'value="(\d+)"', b)
        if not m:
            continue
        head = b[:1600]

        def g(name):
            mm = re.search(r'name="%s" class="[^"]*" value="([^"]*)"' % re.escape(name),
                           head)
            return mm.group(1) if mm else ""

        out.append({"pid": m.group(1), "pname": g("pname[]"),
                    "ck_sku": g("ck_sku[]"), "pname_en": g("pname_en[]"),
                    "fh_weight": g("fh_weight[]"),
                    "products_type": g("products_type[]")})
    return out


def main():
    pw = SS.PW()
    pw.login()
    base = "deliver.php?act=add_products&type=view&user_id=14450&user_name=&ck=%s" % CK
    variants = [
        "%s&keywords=%s" % (base, KW),
        "%s&keywords=%s&search=1" % (base, KW),
        "%s&keyword=%s" % (base, KW),
    ]
    for u in variants:
        r = pw.get(u)
        t = r if isinstance(r, str) else str(r)
        rows = parse(t)
        hits = [x for x in rows
                if KW.lower() in (x["pname"] + x["ck_sku"] + x["pname_en"]).lower()]
        print("\n%s\n  行长=%d 行数=%d 命中=%d"
              % (u.split("&keywords")[-1] if "&keywords" in u else u[-40:],
                 len(t), len(rows), len(hits)))
        for h in hits:
            print("   pid=%-8s pname=%-24s ck_sku=%-14s w=%s type=%s"
                  % (h["pid"], h["pname"][:24], h["ck_sku"][:14],
                     h["fh_weight"], h["products_type"]))
        if hits:
            return 0
    print("\n都没命中 —— 可能需要 POST 或另有搜索接口")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
