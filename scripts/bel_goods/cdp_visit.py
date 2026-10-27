# -*- coding: utf-8 -*-
"""用 CDP 在紫鸟窗口里开一个临时 tab，访问指定 URL，等加载后求值并关闭。

用法：python cdp_visit.py <端口> <URL> <JS文件>

⚠ 2026-10-27 修：原来「关临时 tab」写在**成功路径末尾**，只要中间任何一步抛错
（最常见：`Runtime.evaluate` 求值超时 / 页面没渲染完），这个 tab 就永远留在紫鸟
窗口里。用户口径「每执行一次定时任务，打开的新页面任务完成自己关闭」⇒ 收尾放进
`finally`，无论成功失败都关。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
from cdp_transport import Cdp, close_target, create_target, http_targets  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    if len(sys.argv) < 4:
        print("usage: cdp_visit.py <port> <url> <jsfile>")
        return 2
    port = int(sys.argv[1])
    url = sys.argv[2]
    jsfile = Path(sys.argv[3])

    # 建后台 tab
    tid = create_target(port, "about:blank", background=True)
    print("target:", tid)
    try:
        time.sleep(2)

        ws = None
        for t in http_targets(port):
            if t.get("id") == tid:
                ws = t.get("webSocketDebuggerUrl")
                break
        if not ws:
            print("找不到新 tab 的 ws")
            return 2

        c = Cdp(ws)
        c.send("Page.enable", {})

        def ev(expr, timeout=30):
            r = c.send("Runtime.evaluate", {
                "expression": expr, "returnByValue": True, "awaitPromise": True},
                timeout=timeout)
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
        return 0
    finally:
        # 必须走 finally：抛错时也不能把 tab 留在窗口里
        if close_target(port, tid):
            print("temp tab closed")
        else:
            print("temp tab close FAILED (target still listed):", tid)


if __name__ == "__main__":
    raise SystemExit(main())
