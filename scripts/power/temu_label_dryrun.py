# -*- coding: utf-8 -*-
"""TEMU 买面单「校验闭环」dry-run：
  校验 → 查物流商 → 算价 → 选最便宜 → 查揽收日 → 拼出 batch_send body（**不下单**）。

用法：
    python scripts/power/temu_label_dryrun.py --store BHY --po PO-211-xxx \
        [--order-sn 211-xxx --goods-id 1 --sku-id 2 --product-sku 3]... [--dims weight,length,width,height]

goods 缺省时从**抓包样本**里取（仅用于结构演示）；真实使用请给 --order-sn/--goods-id/--sku-id。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_HERE, os.path.join(_HERE, "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import temu_buylabel_api as A  # noqa: E402

CAP = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api\send_full_20261123_180613.json"


def _sample_goods():
    d = json.load(open(CAP, encoding="utf-8"))
    r = [x for x in d["requests"] if x["url"].endswith("batch_send")][0]
    body = json.loads(r["postData"])
    return body["send_request_list"][0]["order_send_info_list"]


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 买面单 dry-run（不下单）")
    ap.add_argument("--store", required=True)
    ap.add_argument("--po", required=True, help="parent_order_sn")
    ap.add_argument("--order-sn", action="append", default=[])
    ap.add_argument("--goods-id", default="")
    ap.add_argument("--sku-id", default="")
    ap.add_argument("--product-sku", default="")
    ap.add_argument("--dims", default="1.50,12.30,9.30,3.00", help="weight,length,width,height")
    ap.add_argument("--pick", default="cheapest", choices=("cheapest", "fastest"))
    a = ap.parse_args()

    w, l, wd, h = [x.strip() for x in a.dims.split(",")]
    dims = {"weight": w, "length": l, "width": wd, "height": h,
            "dimension_unit": "in", "weight_unit": "lb"}

    if a.order_sn:
        goods = [{"order_sn": o, "parent_order_sn": a.po,
                  "goods_id": a.goods_id, "sku_id": a.sku_id, "quantity": 1}
                 for o in a.order_sn]
        print("⚠ 使用命令行给的 goods（goods_id/sku_id 需真实才能通过校验）")
    else:
        goods = _sample_goods()
        print("⚠ 未给 --order-sn，使用抓包样本 goods（仅供结构演示）")

    print("\n① 校验 batch_check_request …")
    c = A.check(a.store, a.po, goods, dims)
    cj = c.get("json") or {}
    print("   HTTP %s | success=%s code=%s" % (c.get("status"), cj.get("success"),
                                              cj.get("error_code")))
    if not cj.get("success"):
        print("   ⚠ 校验未通过：", str(cj.get("error_msg") or c.get("body"))[:300])
        return 2

    # 仓：先试抓包里的加州三仓
    wh = {"warehouse_id": "WH-09107063234693163", "warehouse_name": "加州三仓"}
    print("\n② 查可选物流商 query_shipping_provider_optional（%s）…" % wh["warehouse_name"])
    pv = A.providers(a.store, wh["warehouse_id"], goods, dims)
    pj = pv.get("json") or {}
    chans = (((pj.get("result") or {}).get("online_channel_vo_list")) or [])
    print("   HTTP %s | 渠道数 %s" % (pv.get("status"), len(chans)))
    if not chans:
        print("   ⚠ 没有可选渠道：", str(pj.get("error_msg") or pv.get("body"))[:300])
        return 2
    rows = []
    for ch in chans:
        v = ch.get("online_estimated_vo") or {}
        fee = int(v.get("charge_amount_si") or 0)
        total = int(v.get("semi_total_charge_amount_si") or 0) or fee
        # ★ 2026-11-23 修：报价为 0 / 未通过校验的渠道**不参与比价**
        #   （实测有几条渠道返回 charge=0，若一起排序会选中 $0 的假渠道）
        if not ch.get("validate", True) or total <= 0:
            continue
        rows.append((ch, fee, total, int(v.get("delivery_day_max") or 0)))
    if not rows:
        print("   ⚠ 没有一个渠道带有效报价：", str(pj.get("error_msg") or pv.get("body"))[:300])
        return 2
    rows.sort(key=lambda x: (x[1], x[3]) if a.pick == "cheapest" else (x[3], x[1]))
    print("   %-16s %-8s %8s %8s %6s" % ("承运商", "服务码", "运费", "含加价", "最慢天"))
    for ch, fee, total, dmax in rows[:8]:
        print("   %-16s %-8s %8.2f %8.2f %6s" % (ch.get("shipping_company_name"),
                                                 str(ch.get("service_code", ""))[:8],
                                                 fee / 1000.0, total / 1000.0, dmax or "-"))
    best, fee, total, _d = rows[0]
    print("   → 选：%s / %s / $%.2f" % (best.get("shipping_company_name"),
                                        best.get("service_code"), total / 1000.0))

    print("\n③ 查揽收日 query_avail_reserve_period …")
    # ★ 2026-11-23：必须带完整的 ship（含 reserve_time_period 占位），否则接口返仓数 0
    probe = A.build_send_item(best, goods, dims, wh, (0, 0))
    rs = A.reserve(a.store, probe)
    rj = rs.get("json") or {}
    lst = (((rj.get("result") or {}).get("available_reserved_period_list")) or [])
    print("   HTTP %s | 仓数 %s" % (rs.get("status"), len(lst)))
    pick_ts = None
    for it in lst:
        if it.get("channel_id") != best.get("channel_id"):
            continue
        avail = [x for x in (it.get("reserved_time_period_list") or []) if x.get("is_available")]
        print("   仓 %s | 渠道 %s | 可取 %s" % (it.get("warehouse_name"),
                                               it.get("ship_company_name"),
                                               [x.get("pickup_date") for x in avail][:5]))
        if avail:
            ts = int(avail[0].get("timestamp") or 0)
            pick_ts = (ts, ts + 14400)      # 默认 4 小时窗口
    if not pick_ts:
        print("   ⚠ 没拿到揽收时段（仓数=%s）" % len(lst))
        print("   resp:", str(rj)[:400])
        return 2

    item = A.build_send_item(best, goods, dims, wh, pick_ts)
    body = A.build_send_body([item])
    print("\n④ 将要提交的 batch_send body（**未提交**）：")
    print(json.dumps(body, ensure_ascii=False, indent=1)[:4000])
    out = os.path.join(os.path.dirname(CAP), "dryrun_%s_%s.json" % (a.store, a.po))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)
    print("\n已落盘 → %s" % out)
    print("✅ dry-run 完成（没有花钱）。确认无误后加 TEMU_LABEL_API_APPLY=1 才会真下单。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
