# -*- coding: utf-8 -*-
"""_inner_probe.py — 探针型 inner：**只**证明链路通了，绝不碰业务。

为什么需要它（2026-11-07）：
    验收「会话落在发货工作区」这件事时，真实 inner（run_fulfill_daily.cmd）
    会**真下单、真买面单（花钱）**。为了看一眼工作区归属而花钱，不划算。
    这个探针只打印 cwd / 环境，然后 exit 0 —— 走的是**完全一样的**
    run_via_dsh → dsh_task.py → dsh headless 这条链，只换掉最后那一步业务。
"""
import os
import sys

print("cwd            = %s" % os.getcwd())
print("DSH_AGENT_INNER = %s" % os.environ.get("DSH_AGENT_INNER"))
print("seller_ws      = %s" % os.path.isdir(r"D:\AI-workplace\发货"))
print("sentinel_root  = %s" % os.environ.get("DSH_SENTINEL_ROOT"))
sys.exit(0)
