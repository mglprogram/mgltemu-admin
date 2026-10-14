# -*- coding: utf-8 -*-
"""用 CDP 在紫鸟窗口里开一个临时 tab，访问指定 URL，等加载后求值并关闭。

用法：python cdp_visit.py <端口> <URL> <JS文件>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
from cdp_transport import Cdp, create_target, http_targets, http_version  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

port = int(sys.argv[1])
url = sys.argv[2]
jsfile = Path(sys.argv[3])

# 建后台 tab
tid = create_target(port, "about:blank", background=True)
print("target:", tid)
time.sleep(2)

ws = None
for t in http_targets(port):
    if t.get("id") == tid:
        ws = t.get("webSocketDebuggerUrl")
        break
if not ws:
    print("找不到新 tab 的 ws")
    raise SystemExit(2)

c = Cdp(ws)
c.send("Page.enable", {})


def ev(expr, timeout=30):
    r = c.send("Runtime.evaluate", {
        "expression": expr, "returnByValue": True, "awaitPromise": True}, timeout=timeout)
    return (r.get("result") or {}).get("result", {}).get("value")


c.send("Page.navigate", {"url": url})
# 等 DOM 就绪 + 前端渲染
for _ in range(20):
    time.sleep(1.5)
    st = ev("document.readyState")
    if st == "complete":
        break
time.sleep(6)  # 给 SPA 渲染时间

print("URL:", ev("location.href"))
print("TITLE:", ev("document.title"))
src = jsfile.read_text(encoding="utf-8").strip()
print("RESULT:", ev(src, timeout=40))

# 关掉临时 tab
try:
    Cdp(http_version(port)["webSocketDebuggerUrl"]).send(
        "Target.closeTarget", {"targetId": tid})
    print("temp tab closed")
except Exception as e:  # noqa: BLE001
    print("close failed:", str(e)[:80])