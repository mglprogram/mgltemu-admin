# -*- coding: utf-8 -*-
"""用无头 Chrome 验证面板页面（紫鸟窗口有访问策略，访问不了公网看板）。

流程：起无头 Chrome（远程调试）→ 开页面 → 过密码门禁 → 求值探针 → 截图 → 关闭。

用法：python headless_check.py <URL> [密码]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
from cdp_transport import Cdp, create_target, http_targets  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333
HERE = Path(__file__).resolve().parent

url = sys.argv[1]
# 密码只从参数或环境变量取 —— 千万不要把明文写进文件（这个仓库是公开的！）
pw = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("DASH_PASSWORD", "")
shot = Path(sys.argv[3]) if len(sys.argv) > 3 else None

proc = subprocess.Popen([
    CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
    "--user-data-dir=" + str(Path.home() / ".dsh-chrome-check"),
    "--no-first-run", "--no-default-browser-check",
    "--disable-gpu", "--window-size=1600,1000", "about:blank",
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    time.sleep(5)
    tid = create_target(PORT, "about:blank", background=False)
    time.sleep(2)
    ws = next((t.get("webSocketDebuggerUrl") for t in http_targets(PORT)
               if t.get("id") == tid), None)
    if not ws:
        print("no ws")
        raise SystemExit(2)

    c = Cdp(ws)
    c.send("Page.enable", {})

    def ev(expr, timeout=40):
        r = c.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
            "userGesture": True}, timeout=timeout)
        return ((r.get("result") or {}).get("result") or {}).get("value")

    c.send("Page.navigate", {"url": url})
    for _ in range(24):
        time.sleep(1.5)
        if ev("document.readyState") == "complete":
            break
    time.sleep(6)

    if ev("!!document.getElementById('gp')"):
        c.send("Runtime.evaluate", {"expression":
            "document.getElementById('gp').value = %s" % json.dumps(pw)})
        c.send("Runtime.evaluate", {"expression":
            "document.getElementById('gb').click()", "userGesture": True})
        time.sleep(9)

    probe = (HERE / "probe_fluxall.js")
    if probe.exists():
        print("DEFAULT:", ev(probe.read_text(encoding="utf-8").strip()))

    if shot:
        r = c.send("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        import base64
        shot.write_bytes(base64.b64decode((r.get("result") or {})["data"]))
        print("shot ->", shot)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()