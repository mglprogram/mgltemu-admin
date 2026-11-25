# -*- coding: utf-8 -*-
"""批量查这批失败订单的**失败原因 + 当前 Fnsku + 可用替代 Fnsku**。

用户给的 9 单（GSU...，SHEIN 店 BE）：
    GSU1A864F000J0G / GSU1A837F00NV1N / GSU1A851T00NT2R / GSU1A825Q000HF0
    GSU1A842200MB59 / GSU1AY53000N07F / GSU1AY11100MK5J / GSU1AX42B0023LC
    GSU1AX13B001N8C

对每单：
  ① 查订单行 → 状态 / 下单错误提醒 / 平台SKU / 仓库渠道
  ② 从错误里抽 `sku:XXXX仓库没有库存` 的 Fnsku
  ③ 用库存页 keywords 搜该款，列出所有 Fnsku 及代发库存，标记可换的
只读，不改任何单。
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402
import pw_stock_query as SQ  # noqa: E402

POS = [
    "GSU1A864F000J0G", "GSU1A837F00NV1N", "GSU1A851T00NT2R",
    "GSU1A825Q000HF0", "GSU1A842200MB59", "GSU1AY53000N07F",
    "GSU1AY11100MK5J", "GSU1AX42B0023LC", "GSU1AX13B001N8C",
]


def clean(c: str) -> str:
    c = re.sub(r"<[^>]+>", " ", c).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", c).strip()


def order_info(pw, po: str) -> dict:
    q = H.query_orders(pw, po)
    html = q.get("html") or ""
    for oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S):
        if po not in tr:
            continue
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        st = ""
        for s in ("下单失败", "已下单", "未下单"):
            if s in tr:
                st = s
        err = ""
        for c in cells:
            if "仓库没有库存" in c or "下单失败" in c and "条" in c:
                err = c
        platform_sku = ""
        for c in cells:
            m = re.search(r"平台SKU\s*[:：]\s*(\S+)", c)
            if m:
                platform_sku = m.group(1)
        wh = ""
        for c in cells:
            m = re.search(r"发货仓库\s*[:：]\s*(\S*)", c)
            if m and m.group(1):
                wh = m.group(1)
        fn = re.search(r"sku:([A-Z0-9]+)仓库没有库存", " ".join(cells))
        return {"po": po, "id": oid, "status": st, "platform_sku": platform_sku,
                "wh": wh, "err": err[:160], "cur_fnsku": fn.group(1) if fn else ""}
    return {"po": po, "id": "", "status": "查不到"}


def main():
    pw = H._pw()
    out = []
    for po in POS:
        d = order_info(pw, po)
        print("\n=== %s ===" % po)
        print("   id=%s 状态=%s 仓库=%s" % (d.get("id"), d.get("status"), d.get("wh")))
        print("   平台SKU=%s | 当前Fnsku=%s" % (d.get("platform_sku"), d.get("cur_fnsku")))
        if d.get("err"):
            print("   错误: %s" % d["err"])
        # 查库存：用平台SKU（去掉店铺前缀）当关键词
        ps = d.get("platform_sku") or ""
        kw = re.sub(r"^[A-Z]{2,4}-", "", ps) if ps else ""
        if kw:
            rows = SQ.query(pw, kw)
            good = [r for r in rows if SQ._num(r.get("代发库存", "0")) > 0]
            print("   库存候选 %d 行，其中代发库存>0 的 %d 行：" % (len(rows), len(good)))
            for r in sorted(good, key=lambda x: -SQ._num(x.get("代发库存", "0")))[:3]:
                print("      %-14s %-20s %-10s %s"
                      % (r.get("Fnsku"), r.get("Seller sku"),
                         r.get("仓库名称"), r.get("代发库存")))
            d["candidates"] = [{"fnsku": r.get("Fnsku"),
                                "seller": r.get("Seller sku"),
                                "wh": r.get("仓库名称"),
                                "stock": r.get("代发库存")} for r in good[:5]]
        out.append(d)

    with open(r"D:\AI-workplace\TEMU\_logs\pw_fail_batch.json", "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n明细 → D:\\AI-workplace\\TEMU\\_logs\\pw_fail_batch.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
