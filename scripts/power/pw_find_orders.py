# -*- coding: utf-8 -*-
"""按单号查订单：**跨状态 + 跨页 + 宽日期**，必须查得到。

问题（2026-11-25）：9 单里 7 单「查不到」，因为 `query_orders` 只查了
  status=、page=1、今天~今天。而帕沃尔列表默认只显示「未下单/下单失败」一页 50 条，
  已下单的在别的页/别的状态下。

修法：不传 status（跨全部），从 page=1 翻到最后一页，日期拉到近 30 天。
本脚本：给一批单号，逐个翻页找，打印每单的状态/错误/平台SKU/Fnsku。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402

POS = [
    "GSU1A864F000J0G", "GSU1A837F00NV1N", "GSU1A851T00NT2R",
    "GSU1A825Q000HF0", "GSU1A842200MB59", "GSU1AY53000N07F",
    "GSU1AY11100MK5J", "GSU1AX42B0023LC", "GSU1AX13B001N8C",
]
START, END = "2026-10-26", "2026-11-25"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def parse(html: str) -> dict:
    """`{po: {...}}` 从一页 HTML 里解析所有行。"""
    out = {}
    for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S):
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        po = cells[1] if len(cells) > 1 else ""
        if not po:
            continue
        st = ""
        for s in ("下单失败", "已下单", "未下单", "已取消"):
            if s in tr:
                st = s
                break
        sku = ""
        wh = ""
        err = ""
        for c in cells:
            m = re.search(r"平台SKU\s*[:：]\s*(\S+)", c)
            if m and not sku:
                sku = m.group(1)
            m = re.search(r"发货仓库\s*[:：]\s*(\S+)", c)
            if m and m.group(1).strip(":：") and not wh:
                wh = m.group(1)
            if "下单失败" in c and "条" in c:
                err = c
        out[po] = {"id": oid, "po": po, "status": st, "platform_sku": sku,
                   "wh": wh, "err": err[:160]}
    return out


def main():
    pw = H._pw()
    want = set(POS)
    found = {}
    seen_po = set()
    for page in range(1, 31):
        q = H.query_orders(pw, "", start=START, end=END, page=page)
        html = q.get("html") or ""
        rows = parse(html)
        if not rows:
            print("第 %d 页无行 → 停" % page)
            break
        new = [p for p in rows if p not in seen_po]
        seen_po.update(rows)
        for p in new:
            if p in want:
                found[p] = rows[p]
        print("第 %d 页 %d 行（累计 %d），已找到 %d/%d"
              % (page, len(rows), len(seen_po), len(found), len(want)))
        if len(found) >= len(want):
            break

    print("\n=== 结果 ===")
    for po in POS:
        d = found.get(po)
        if not d:
            print("  %-18s ❌ 30 页内没找到" % po)
            continue
        print("  %-18s id=%-8s %-6s %-18s %s"
              % (po, d["id"], d["status"], d["platform_sku"], d["err"][:60]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
