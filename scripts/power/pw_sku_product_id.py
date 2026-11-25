# -*- coding: utf-8 -*-
"""查 Fnsku 对应的**产品 id（huan_id）** —— 换 Fnsku 的实质是换 huan_id[]。

推断依据（2026-11-25）：
  · 订单编辑页产品行：`huan_id[]` (hidden, 3308373) + `ck_sku[]` (BHY-YOGA-4SetA-L)
  · 库存页每行有 `<input type="checkbox" name="checkboxes[]" value="132180">`
  · 订单 `huan_id` 与库存页 checkbox value 同量级 ⇒ **同一套产品 id**

本脚本：库存页按 keywords 搜，把每行的 `checkbox value` 与 Fnsku/Seller sku/库存 对齐。
"""
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

KW = sys.argv[1] if len(sys.argv) > 1 else "YOGA-4SetA-L"


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c)
    return re.sub(r"\s+", " ", c.replace("&nbsp;", " ")).strip()


def main():
    pw = SS.PW()
    pw.login()
    from urllib.parse import quote  # noqa: PLC0415
    r = pw.get("stock.php?act=list&keywords=%s" % quote(KW))
    t = r if isinstance(r, str) else str(r)
    print("页面长度:", len(t), "| keywords:", KW)

    print("\n  %-10s %-14s %-20s %-10s %s"
          % ("产品id", "Fnsku", "Seller sku", "仓库", "代发库存"))
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        if KW.lower() not in tr.lower():
            continue
        m = re.search(r'name="checkboxes\[\]"[^>]*value="(\d+)"', tr)
        pid = m.group(1) if m else ""
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 8:
            continue
        # 表头顺序：序号 Fnsku Seller sku 产品名称 英文品名 产品分类 仓库 货架 代发途中 代发库存 ...
        fnsku = cells[1] if len(cells) > 1 else ""
        seller = cells[2] if len(cells) > 2 else ""
        wh = cells[6] if len(cells) > 6 else ""
        stock = cells[9] if len(cells) > 9 else ""
        print("  %-10s %-14s %-20s %-10s %s" % (pid, fnsku[:14], seller[:20], wh[:10], stock))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
