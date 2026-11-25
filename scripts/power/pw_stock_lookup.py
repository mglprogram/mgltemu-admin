# -*- coding: utf-8 -*-
"""查库存页：给定一个款号/Seller sku，列出**所有 Fnsku + 各仓代发库存**，挑有货的。

用户口径（2026-11-25）：
    「你先要去库存链接查 哪个 sku 有货」
    stock.php?act=list → 看 seller sku / Fnsku → 哪个仓有代发库存

用法：
    python pw_stock_lookup.py YOGA-4SetA-L
    python pw_stock_lookup.py 204203-4SetC        # 只给款号也行
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def lookup(pw, kw: str) -> list:
    """按关键词查库存页，返回 `[{fnsku, seller_sku, pname, wh, stock}]`。"""
    from urllib.parse import quote  # noqa: PLC0415
    html = pw.get("stock.php?act=list&seller_sku=%s" % quote(kw))
    txt = html if isinstance(html, str) else str(html)

    # 表头定位列
    head = re.search(r"<tr[^>]*>(.*?)</tr>", txt, re.S)
    cols = [clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>",
                                         head.group(1) if head else "", re.S)]
    idx = {name: i for i, name in enumerate(cols)}
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S):
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 6 or kw.split("-")[0].lower() not in " ".join(cells).lower():
            continue
        row = {
            "fnsku": cells[idx.get("Fnsku", 1)] if len(cells) > 1 else "",
            "seller_sku": cells[idx.get("Seller sku", 2)] if len(cells) > 2 else "",
            "pname": cells[idx.get("中文品名", idx.get("产品名称", 3))]
            if len(cells) > 3 else "",
            "wh": cells[idx.get("仓库名称", 5)] if len(cells) > 5 else "",
        }
        for key in ("代发库存", "可用库存", "库存"):
            if key in idx and idx[key] < len(cells):
                row["stock"] = cells[idx[key]]
                break
        row.setdefault("stock", cells[-5] if len(cells) > 5 else "")
        out.append(row)
    return out


def best(rows: list) -> dict:
    """挑代发库存最多的那一行。"""
    def n(r):
        try:
            return float(str(r.get("stock") or 0).replace(",", ""))
        except Exception:  # noqa: BLE001
            return -1
    return max(rows, key=n) if rows else {}


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "YOGA-4SetA-L"
    pw = SS.PW()
    pw.login()
    rows = lookup(pw, kw)
    print("命中 %d 行（关键词 %s）：\n" % (len(rows), kw))
    print("  %-14s %-18s %-12s %s" % ("Fnsku", "Seller sku", "仓库", "代发库存"))
    for r in rows:
        print("  %-14s %-18s %-12s %s"
              % (r["fnsku"][:14], r["seller_sku"][:18], r["wh"][:12], r["stock"]))
    b = best(rows)
    if b:
        print("\n✅ 建议换到：%s（%s / %s 库存 %s）"
              % (b["fnsku"], b["seller_sku"], b["wh"], b["stock"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
