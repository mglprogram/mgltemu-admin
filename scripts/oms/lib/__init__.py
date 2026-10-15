# -*- coding: utf-8 -*-
"""scripts/oms —— 领星 OMS/WMS(xlwms) 订单数据源域。

    run_oms_orders.py     入口：sync（每天增量）/ backfill（回填）/ stats / build
    lib/oms_client.py     v1 OpenAPI 客户端（authcode 签名 + 分页/批量上限）
    oms_account.local.json   appKey/appSecret（**不入库**，.gitignore 挡）
    tests/                离线单测（签名字节级钉死 + 上限 + slim_detail）

产物：
    out/oms/oms_orders.jsonl                    一行一单（增量去重）
    temu-admin/public/data/oms-orders.json      面板用精简数据

定时任务：Windows 计划任务 `TEMU-OMS-Orders-Daily`（每天 22:00）→ scripts/run_oms_orders.cmd
"""
