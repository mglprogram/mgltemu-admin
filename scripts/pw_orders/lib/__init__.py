# -*- coding: utf-8 -*-
"""scripts/pw_orders —— 帕沃尔海外仓订单地理分布域。

目录结构（与 scripts/ 下其它业务域一致：入口 run_*.py + lib/ + 配置 + tests/）
    run_pw_order_geo.py   入口：list / geo / export / build / all
    lib/pw_session.py     帕沃尔(powerwarehouse.net)会话：登录 + 直连 + 连接阶段重试
    lib/pw_order_geo.py   纯解析/聚合：发货列表行、订单详情收件地址、州-市汇总
    shops.json            两家 TEMU 店的帕沃尔店铺ID（入库）
    pw_account.local.json 帕沃尔账号（**不入库**，.gitignore 挡；也可用 PW_USER/PW_PASS）
    tests/                离线单测（不碰网络）

产物（默认 out/pw-orders/）：
    orders_list.jsonl / orders_geo.jsonl / pw_export_*.xlsx / 订单分布_汇总.md / ...
看板产物：temu-admin/public/data/order-geo.json

⚠ 别在域下建 data/ 子目录：根 .gitignore 里的 `data/` 没有前导斜杠，
  会命中任意层级的 data/，放进去就永远进不了库。
"""
