# -*- coding: utf-8 -*-
"""无头 Chrome 打开面板 → 过门禁 → 切到「动力不足」→ 读表格 → 截图。

用法：python verify_weak.py <URL> <密码> <截图路径> [探针JS]
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
from cdp_transport import Cdp, create_target, http_targets  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9334
HERE = Path(__file__).resolve().parent

url, pw = sys.argv[1], sys.argv[2]
shot = Path(sys.argv[3]) if len(sys.argv) > 3 else None

proc = subprocess.Popen([
    CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
    "--user-data-dir=" + str(Path.home() / ".dsh-chrome-check2"),
    "--no-first-run", "--no-default-browser-check",
    "--disable-gpu", "--window-size=1600,1100", "about:blank",
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    time.sleep(5)
    tid = create_target(PORT, "about:blank", background=False)
    time.sleep(2)
    ws = next((t.get("webSocketDebuggerUrl") for t in http_targets(PORT)
               if t.get("id") == tid), None)
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

    # 切到「动力不足」
    print("click:", ev((HERE / "click_weak.js").read_text(encoding="utf-8").strip()))
    time.sleep(5)
    print("AFTER:", ev((HERE / "probe_weak.js").read_text(encoding="utf-8").strip()))

    if shot:
        r = c.send("Page.captureScreenshot", {"format": "png"})
        shot.write_bytes(base64.b64decode((r.get("result") or {})["data"]))
        print("shot ->", shot)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()