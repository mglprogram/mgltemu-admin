# -*- coding: utf-8 -*-
"""复核「仓内现货」悬浮内容（逐条简单表达式取，避免复杂 IIFE 出错）。

用法：python scripts/goods/verify_wh_popover.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdp_net  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9338
URL = "https://mgltemu.us.ci/#/temu/on-sale?view=low"

GATE_JS = """
(function () {
  var PW = %s;
  function fill() {
    var i = document.getElementById('gp'), b = document.getElementById('gb');
    if (!i || !b) return false;
    i.value = PW; b.click(); return !document.getElementById('gate');
  }
  var n = 0;
  var t = setInterval(function () { n++; if (fill() || n > 60) clearInterval(t); }, 250);
  document.addEventListener('DOMContentLoaded', fill);
})();
"""

VIS_TABLE = ("Array.prototype.filter.call(document.querySelectorAll('.el-table'),"
             "function(x){return x.getBoundingClientRect().width>0})")


async def ev(cap, expr, label=""):
    v, exc = await cap.eval(expr)
    if exc:
        print(f"   [{label}] 异常: {json.dumps(exc, ensure_ascii=False)[:140]}")
    return v


async def main() -> int:
    import subprocess
    prof = ROOT / "out" / "_chrome_pop2"
    p = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
                          f"--user-data-dir={prof}", "--no-first-run", "--disable-gpu",
                          "--window-size=1600,1000", "about:blank"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        try:
            cdp_net.http_json(f"http://127.0.0.1:{PORT}/json/version", timeout=2)
            break
        except Exception:  # noqa: BLE001
            continue
    html = (ROOT / "temu-admin" / "dist" / "index.html").read_text(encoding="utf-8",
                                                                   errors="replace")
    m = re.search(r'temu_admin_gate_ok",\s*want="((?:[^"\\]|\\.)*)"', html)
    pw = json.loads('"' + m.group(1) + '"') if m else ""

    tid = await cdp_net.new_tab(PORT, "about:blank")
    t = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        t = next((x for x in cdp_net.http_json(f"http://127.0.0.1:{PORT}/json/list")
                  if (x.get("id") or x.get("targetId")) == tid), None)
        if t:
            break
    ws = t["webSocketDebuggerUrl"]
    async with cdp_net.NetCapture(ws) as cap:
        await cap.send("Page.addScriptToEvaluateOnNewDocument",
                       {"source": GATE_JS % json.dumps(pw)})
        await cap.send("Page.navigate", {"url": URL}, timeout=120)
        for i in range(15):
            await asyncio.sleep(3)
            n = await ev(cap, "document.querySelectorAll('.el-table__expand-icon').length",
                         "icons")
            if n:
                print(f"第{i+1}次探测：展开箭头 {n} 个")
                break

        print("可见表格数:", await ev(cap, VIS_TABLE + ".length", "visible-tables"))
        print("点第一个可见表的箭头:", await ev(
            cap, "(function(){var t=" + VIS_TABLE + "[0];"
                 "var i=t.querySelector('.el-table__expand-icon');i.click();"
                 "return 'clicked';})()", "click"))
        await asyncio.sleep(2)
        print("展开单元格:", await ev(
            cap, "document.querySelectorAll('.el-table__expanded-cell').length", "cells"))
        print("可悬浮点:", await ev(
            cap, "document.querySelectorAll('.el-table__expanded-cell .goods-wh').length",
            "goods-wh"))

        rect = await ev(cap, "(function(){var e=document.querySelectorAll("
                             "'.el-table__expanded-cell .goods-wh')[0];"
                             "if(!e) return null;var r=e.getBoundingClientRect();"
                             "return [Math.round(r.x+r.width/2),Math.round(r.y+r.height/2)];"
                             "})()", "rect")
        print("悬浮坐标:", rect)
        if rect:
            x, y = rect
            await cap.send("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": x, "y": y}, timeout=20)
            await asyncio.sleep(1)
            await cap.send("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": x + 1, "y": y + 1}, timeout=20)
            await asyncio.sleep(2)
            print("popover 数:", await ev(cap, "document.querySelectorAll('.wh-pop').length",
                                          "pops"))
            txt = await ev(cap, "document.querySelector('.wh-pop') ? "
                                "document.querySelector('.wh-pop').innerText"
                                ".replace(/\\s+/g,' ') : 'none'", "text")
            print("\n--- 悬浮内容 ---")
            print(txt)
    await cdp_net.close_tab(PORT, tid)
    p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
