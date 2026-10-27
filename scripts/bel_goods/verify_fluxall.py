# -*- coding: utf-8 -*-
"""端到端验证 flux-boost 面板：过密码门禁 → 读店铺/板块切换 → 切「全部」再读。

用法：python verify_fluxall.py <端口> [密码]

⚠ 2026-10-27 修：临时 tab 的关闭原来写在文件末尾（成功路径），中间任何一步抛错
就永久残留。收尾移进 `finally`（用户口径「每执行一次定时任务，打开的新页面任务
完成自己关闭」）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
from cdp_transport import Cdp, close_target, create_target, http_targets  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
URL = "https://mgltemu.us.ci/#/temu/flux-boost"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_fluxall.py <port> [password]")
        return 2
    port = int(sys.argv[1])
    # 密码只从参数或环境变量取 —— 千万不要把明文写进文件（这个仓库是公开的！）
    pw = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DASH_PASSWORD", "")

    tid = create_target(port, "about:blank", background=True)
    try:
        time.sleep(2)
        ws = next((t.get("webSocketDebuggerUrl") for t in http_targets(port)
                   if t.get("id") == tid), None)
        if not ws:
            print("no ws")
            return 2

        c = Cdp(ws)
        c.send("Page.enable", {})

        def ev(expr, timeout=40):
            r = c.send("Runtime.evaluate", {
                "expression": expr, "returnByValue": True, "awaitPromise": True,
                "userGesture": True}, timeout=timeout)
            res = (r.get("result") or {}).get("result") or {}
            return res.get("value")

        c.send("Page.navigate", {"url": URL})
        for _ in range(24):
            time.sleep(1.5)
            if ev("document.readyState") == "complete":
                break
        time.sleep(6)

        # 过门禁
        if ev("!!document.getElementById('gp')"):
            c.send("Runtime.evaluate", {"expression":
                "document.getElementById('gp').value = %s" % json.dumps(pw),
                "returnByValue": True})
            c.send("Runtime.evaluate", {"expression":
                "document.getElementById('gb').click()", "returnByValue": True,
                "userGesture": True})
            time.sleep(8)

        probe = (HERE / "probe_fluxall.js").read_text(encoding="utf-8").strip()
        print("=== 默认（应为「全部」选中）===")
        print(ev(probe))

        # 点 BEL 店（第 2 个 radio）
        c.send("Runtime.evaluate", {"expression": """(() => {
  const gs = document.querySelectorAll('.flux-switch .el-radio-group');
  const btns = gs[0].querySelectorAll('.el-radio-button');
  btns[1].querySelector('input').click();
  return 'clicked BEL';
})()""", "returnByValue": True, "userGesture": True})
        time.sleep(5)
        print("=== 点 BEL 店后 ===")
        print(ev(probe))
        return 0
    finally:
        if close_target(port, tid):
            print("temp tab closed")
        else:
            print("temp tab close FAILED (target still listed):", tid)


if __name__ == "__main__":
    raise SystemExit(main())
