# -*- coding: utf-8 -*-
"""库存页查询（精确版）：按 **中文品名 / 英文品名** 搜，列出同款所有尺码的所有 Fnsku。

上一版按 seller_sku 搜只命中 1 行（而且搜成了 XXL），因为服务器端过滤规则不明。
这次：不带过滤参数拉全表，在本地按「产品名含关键词」筛，输出：Fnsku / Seller sku /
仓库 / 代发库存 / 中转 / 待处理，按库存降序。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "YOGA-4SetA"
    pw = SS.PW()
    pw.login()
    html = pw.get("stock.php?act=list")
    txt = html if isinstance(html, str) else str(html)
    print("库存页长度:", len(txt))

    # 表头
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S)
    head = None
    for tr in rows:
        cs = [clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        if any("Fnsku" in c for c in cs):
            head = cs
            break
    print("表头:", head)
    if not head:
        return 1

    hits = []
    for tr in rows:
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if not cells:
            continue
        line = " ".join(cells)
        if kw.lower() not in line.lower():
            continue
        d = dict(zip(head, cells))
        hits.append(d)

    print("\n命中 %d 行：\n" % len(hits))
    for d in hits:
        print("  %-14s %-20s %-10s 代发=%-5s 途中=%-4s 待处理=%s"
              % (d.get("Fnsku", "")[:14], d.get("Seller sku", "")[:20],
                 d.get("仓库名称", "")[:10], d.get("代发库存", "-"),
                 d.get("中转途中", "-"), d.get("待处理库存", "-")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
