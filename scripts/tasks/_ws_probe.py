# -*- coding: utf-8 -*-
"""_ws_probe.py — 探针：确认 DSH 会话真的起在「发货」工作区。

只打印 cwd 与环境，不碰任何业务。用来验证
`dsh_task.py --cwd <工作区>` 是否把会话落到了那个工作区。
"""
import os
import sys

print("cwd=%s" % os.getcwd())
print("DSH_AGENT_INNER=%s" % os.environ.get("DSH_AGENT_INNER"))
print("ship_ws_exists=%s" % os.path.isdir("D:\\AI-workplace\\发货"))
sys.exit(0)
