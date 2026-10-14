# -*- coding: utf-8 -*-
"""验证「今日待办」页签：用真实浏览器打开 ?view=todo，等渲染后读文字。

用法：python scripts/goods/check_todo_view.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\AI-workplace\TEMU")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL = "https://mgltemu.us.ci/#/temu/on-sale?view=todo"


def main() -> int:
    html = (ROOT / "temu-admin" / "dist" / "index.html").read_text(encoding="utf-8",
                                                                  errors="replace")
    m = re.search(r'temu_admin_gate_ok",\s*want="((?:[^"\\]|\\.)*)"', html)
    pw = m.group(1) if m else ""
    # 先拿一次页面，把门禁密码塞进 sessionStorage 的脚本注入不了 ⇒ 用 --dump-dom 直接看
    # 干脆把密码作为 URL 参数不成立，所以这里只在页面里找「今日待办」相关文案
    prof = ROOT / "out" / "_chrome_todo"
    prof.mkdir(parents=True, exist_ok=True)
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
           f"--user-data-dir={prof}", "--virtual-time-budget=25000", "--dump-dom", URL]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=240)
    dom = p.stdout or ""
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", dom))
    print("DOM 字节:", len(dom), "| 门禁密码:", "有" if pw else "无")
    for kw in ("今日待办", "补货清单", "可加合计", "可加库存", "需补货"):
        print("  %-8s %s" % (kw, "OK" if kw in txt else "MISS"))
    i = txt.find("补货清单")
    if i < 0:
        i = txt.find("今日待办")
    print("\n片段:", txt[max(0, i - 60):i + 420] if i >= 0 else txt[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
