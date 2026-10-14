# -*- coding: utf-8 -*-
"""ziniao_port.py — 按紫鸟窗口名探 CDP 端口（goods 域薄封装）。

与 scripts/power/lib/ziniao_store.probe_port_by_window 同口径（前缀匹配窗口标题 +
netstat 找监听端口 + 验 CDP），本域自带一份，避免跨域 import power 包。
"""
from __future__ import annotations

import time

import json
import subprocess
from urllib.request import urlopen


def _run(cmd, timeout=25):
    si = None
    flags = 0
    if hasattr(subprocess, "STARTUPINFO"):
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        flags = 0x08000000          # CREATE_NO_WINDOW（本调用不会再派生控制台）
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, startupinfo=si,
                          creationflags=flags)


def _is_cdp(port: int, timeout: float = 3.0) -> bool:
    try:
        with urlopen("http://127.0.0.1:%d/json/version" % int(port), timeout=timeout) as r:
            v = json.loads(r.read().decode("utf-8", "replace"))
        return bool(v.get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001
        return False


def probe_port(window_name: str, attempts: int = 3) -> int:
    """窗口名 = 紫鸟窗口标题前缀（如 TM-BEL-MGL）；找不到返回 0。

    ⚠ 内部 `_is_cdp` 会打 HTTP，**偶发超时**（实测 2026-10-13：BEL 一轮全部失败、
    紧接着再探就好了）⇒ 整体重试 `attempts` 次，别把瞬时网络问题当成"窗口不存在"。
    """
    for k in range(max(1, attempts)):
        port = _probe_port_once(window_name)
        if port:
            return port
        if k < attempts - 1:
            time.sleep(2)
    return 0


def _probe_port_once(window_name: str) -> int:
    """窗口名 = 紫鸟窗口标题前缀（如 TM-BEL-MGL）；找不到返回 0。"""
    if not window_name:
        return 0
    ps = ("Get-Process ziniaobrowser -ErrorAction SilentlyContinue | "
          "Where-Object { $_.MainWindowTitle -like '%s*' } | "
          "Select-Object -ExpandProperty Id" % window_name)
    out = _run(["powershell", "-NoProfile", "-Command", ps]).stdout or ""
    pids = {int(x) for x in out.split() if x.strip().isdigit()}
    if not pids:
        return 0
    net = _run(["netstat", "-ano", "-p", "tcp"], timeout=20).stdout or ""
    for line in net.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        try:
            if int(parts[4]) not in pids:
                continue
            port = int(parts[1].rsplit(":", 1)[1])
        except Exception:  # noqa: BLE001
            continue
        if 10000 < port < 70000 and _is_cdp(port):
            return port
    return 0


if __name__ == "__main__":
    import sys
    print(probe_port(sys.argv[1] if len(sys.argv) > 1 else "TM-BEL-MGL"))
