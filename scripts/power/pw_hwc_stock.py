# -*- coding: utf-8 -*-
"""pw_hwc_stock.py — 库存页(stock.php)只读查询：按 Seller sku 列出每个 Fnsku 在各仓的可用/代发/库龄。

用户教的修法（2026-11-06）：
    查的 SKU 要去掉变体末尾纯数字（104102-1B2-L 这样）；
    命中后选**库龄天数最高**、且该仓**有代发库存**的 Fnsku，
    再去订单编辑页把 ck_sku[] 覆盖成那个 Fnsku，然后重新下单代发。

列位置（实测 stock.php?act=list 的 tr，先剥掉注释掉的旧列）：
    0 序号 / 1 Fnsku / 2-4 Seller sku / 5 仓库名称 / 6 代发途中 / **7 代发库存**
    / 8 中转途中 / 9 中转库存 / 10 待处理库存 / 11 10天销量 / 12 30天销量
    / **13 库龄(天)** / 14 体积 / 15 库存预警 / 16 入仓时间 / 17 操作
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "pw_orders", "lib"))
import pw_session as S  # noqa: E402
import urllib.parse

BRANDS = ("BEL-", "BHY-", "BH-", "BK-", "BS2-", "BS-", "BLA-", "BLA2-", "BK2-", "BH2-")


def relax(sku: str) -> str:
    """平台SKU → 库存页 Seller sku（去品牌前缀 + 去变体末尾纯数字）。"""
    s = str(sku or "").strip()
    up = s.upper()
    for pre in BRANDS:
        if up.startswith(pre):
            s = s[len(pre):]
            break
    parts = s.split("-")
    if len(parts) >= 3:
        head, var, size = "-".join(parts[:-2]), parts[-2], parts[-1]
        v2 = re.sub(r"[\d.]+$", "", var)
        return "%s-%s-%s" % (head, v2 or var, size)
    return s


def _clean(x: str) -> str:
    x = re.sub(r"<br\s*/?>", " ", x or "")
    x = re.sub(r"<[^>]+>", " ", x)
    return re.sub(r"[ \t\r\n\u00a0]+", " ", x.replace("&nbsp;", " ")).strip()


def stock_rows(pw: S.PW, seller_sku: str) -> list:
    """查一个 Seller sku，返回 [{fnsku, seller, wh, avail, age, in_at}]。"""
    url = "stock.php?act=list&code=%s&keywords=&ck_sku=&ck=&pb=1" % urllib.parse.quote(seller_sku)
    h = pw.get(url, referer="stock.php?act=list")
    # ★ 库存页也保留大量被注释掉的旧列，不剥注释会导致列整体错位（实测踩过）
    h = re.sub(r"<!--.*?-->", "", h, flags=re.S)
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S):
        tds = [_clean(t) for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(tds) < 14:
            continue
        fn = tds[1]
        if not re.match(r"^[A-Z0-9]{8,}$", fn or ""):
            continue
        out.append({
            "fnsku": fn,
            "seller": tds[2],
            "wh": tds[5],
            "avail": tds[7],          # 代发库存
            "avail2": tds[6],         # 代发途中
            "age": tds[13],           # 库龄(天)
            "in_at": tds[16] if len(tds) > 16 else "",
        })
    return out


def pick(rows: list, wh: str = "", min_avail: int = 1) -> list:
    """候选排序：本仓有货 → 库龄最高。"""
    def num(x):
        m = re.match(r"-?[\d.]+", x or "")
        return float(m.group(0)) if m else -1.0
    cand = []
    for r in rows:
        a = num(r["avail"])
        if a < min_avail:
            continue
        if wh and wh not in (r["wh"] or ""):
            continue
        cand.append(dict(r, avail_n=a, age_n=num(r["age"])))
    cand.sort(key=lambda r: -r["age_n"])
    return cand


if __name__ == "__main__":
    pw = S.PW()
    pw.login()
    for sku in sys.argv[1:]:
        q = relax(sku)
        rows = stock_rows(pw, q)
        print("=" * 8, sku, "→ Seller sku", q, "(%d 条)" % len(rows))
        for r in sorted(rows, key=lambda x: x["fnsku"]):
            print("   fn=%-16s wh=%-8s 可用=%-6s 库龄=%-8s seller=%s"
                  % (r["fnsku"], r["wh"], r["avail"], r["age"], r["seller"]))
        json.dump(rows, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "out", "_peek", "stock_%s.json" % q), "w",
                             encoding="utf-8"), ensure_ascii=False, indent=1)
