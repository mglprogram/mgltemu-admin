# -*- coding: utf-8 -*-
"""帕沃尔接口化盘点：哪些动作已是 HTTP、哪些还在点页面。

已知接口（2026-11-25 挖到并验证）：
    查单        POST hwc_shop.php?is_ajax=1  act=query_shop_order
    设仓渠道    POST deliver.php  act=get_shop_paisongx
               → POST hwc_shop.php  act=operate (act=batch)
    下单代发    POST hwc_shop.php?act=xiadan_df&id=<id>&is_ajax=1
    换 Fnsku    GET  hwc_shop.php?act=edit_order&id=<id>
               → POST hwc_shop.php  act=edit_update
    选品器      GET  deliver.php?act=add_products&type=view&...&keywords=<Fnsku>
    库存查询    GET  stock.php?act=list&keywords=<关键词>
    删单        POST hwc_shop.php  act=deletes_shop_order
    单店同步    POST hwc_shop.php?act=update_shop_news&shop_id=<id>&is_ajax=1

待挖接口（目前仍点页面）：
    · 待确认订单 → 确认订单（pw_receive）
    · 批量获取单号（pw_track）
    · 出库单 / deliver.php 列表
"""
import io
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

P = r"D:\AI-workplace\TEMU\scripts\power\pw_hwc_flow.py"
t = io.open(P, encoding="utf-8").read()

print("=== pw_hwc_flow 函数分类 ===")
for m in re.finditer(r"^def ([a-z_0-9]+)\((.*?)\)", t, re.M | re.S):
    name = m.group(1)
    body_start = m.end()
    nxt = t.find("\ndef ", body_start)
    body = t[body_start:nxt if nxt > 0 else len(t)]
    http = ("SS.PW()" in body or "pw.post(" in body or "pw.get(" in body
            or "urllib.request" in body)
    print("  %-24s %s" % (name, "HTTP" if http else "DOM"))

print("\n=== 页面里出现过的 act（可接口化的线索）===")
acts = sorted(set(re.findall(r"act=([a-z_0-9]+)", t)))
print("  ", acts)
