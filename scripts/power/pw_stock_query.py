# -*- coding: utf-8 -*-
"""库存查询：用 `keywords` 通用搜索（用户确认最稳）。

`stock.php?act=list` 的搜索表单（2026-11-25 实测）：
    <form action="javascript:searchOrder()" name="searchForm">
      keywords   ← **通用搜索**（最稳）
      ck_sku     ← Seller sku
      code       ← Fnsku
      ku_types / ck  ← 库存类型 / 仓库

用法：
    python pw_stock_query.py YOGA-4SetA-L
    python pw_stock_query.py D14450HD6AL          # 也能按 Fnsku
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

WANT_COLS = ("Fnsku", "Seller sku", "产品名称", "英文品名", "仓库名称",
             "代发库存", "中转库存", "待处理库存", "10天销量")


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def _num(s: str) -> float:
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except Exception:  # noqa: BLE001
        return -1.0


def query(pw, kw: str, page_size: int = 200) -> list:
    """`keywords` 通用搜索，返回 `[dict]`（含表头映射）。"""
    from urllib.parse import quote  # noqa: PLC0415
    html = pw.get("stock.php?act=list&keywords=%s&page_size=%d"
                  % (quote(kw), page_size))
    txt = html if isinstance(html, str) else str(html)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S)

    head = None
    for tr in rows:
        cs = [clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        if any("Fnsku" in c for c in cs):
            head = cs
            break
    if not head:
        return []
    out = []
    for tr in rows:
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 6:
            continue
        d = dict(zip(head, cells))
        if kw.lower() not in " ".join(cells).lower():
            continue
        out.append(d)
    return out


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "YOGA-4SetA-L"
    pw = SS.PW()
    pw.login()
    rows = query(pw, kw)
    print("keywords=%s → %d 行\n" % (kw, len(rows)))
    print("  %-14s %-20s %-10s %-8s %s"
          % ("Fnsku", "Seller sku", "仓库", "代发库存", "产品名"))
    for d in sorted(rows, key=lambda x: -_num(x.get("代发库存", "0"))):
        print("  %-14s %-20s %-10s %-8s %s"
              % (d.get("Fnsku", "")[:14], d.get("Seller sku", "")[:20],
                 d.get("仓库名称", "")[:10], d.get("代发库存", "-"),
                 d.get("产品名称", "")[:26]))
    good = [d for d in rows if _num(d.get("代发库存", "0")) > 0]
    if good:
        b = max(good, key=lambda x: _num(x.get("代发库存", "0")))
        print("\n✅ 有货可换：%s（%s / %s，代发库存 %s）"
              % (b.get("Fnsku"), b.get("Seller sku"), b.get("仓库名称"),
                 b.get("代发库存")))
    else:
        print("\n⚠ 该款没有任何 Fnsku 有代发库存")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
