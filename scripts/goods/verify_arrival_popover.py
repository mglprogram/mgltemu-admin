# -*- coding: utf-8 -*-
"""复核「最近到货」悬浮：批次明细（尺码/批次/渠道/件数/到货日）+ 飞书到货表入口。

用 BEL-1026-5SetA 验（飞书那条：1026-5SetA-M · 到货日 2026-11-01 · 数量 100 · 美森 · IB106261006RS）。

用法：python scripts/goods/verify_arrival_popover.py
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
PORT = 9340
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
    prof = ROOT / "out" / "_chrome_arr"
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
        for _ in range(15):
            await asyncio.sleep(3)
            if await ev(cap, "document.querySelectorAll('.el-table__expand-icon').length"):
                break

        # 搜索 1026-5SetA，减少干扰
        await ev(cap, "(function(){var i=document.querySelectorAll('.goods-filter-input input')[0];"
                      "if(!i) return 'no input';"
                      "var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"
                      "'value').set; s.call(i,'1026-5SetA');"
                      "i.dispatchEvent(new Event('input',{bubbles:true}));return 'typed';})()", "search")
        await asyncio.sleep(3)
        print("筛选后行数:", await ev(cap, VIS_TABLE +
                                     "[0].querySelectorAll('tbody tr.el-table__row').length"))
        print("行文本:", await ev(cap, VIS_TABLE + "[0].querySelector('tbody tr.el-table__row')"
                                        ".innerText.replace(/\\s+/g,' ').slice(0,140)"))

        # 悬停到该行「最近到货」的日期上
        rect = await ev(cap, "(function(){var t=" + VIS_TABLE + "[0];"
                             "var e=t.querySelector('tbody tr .goods-arrival');"
                             "if(!e) return null;var r=e.getBoundingClientRect();"
                             "return [Math.round(r.x+r.width/2),Math.round(r.y+r.height/2)];"
                             "})()", "rect")
        print("日期坐标:", rect)
        if rect:
            x, y = rect
            await cap.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 5, "y": 5},
                           timeout=20)
            await asyncio.sleep(0.5)
            await cap.send("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": x, "y": y}, timeout=20)
            await asyncio.sleep(1)
            await cap.send("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": x + 2, "y": y + 2}, timeout=20)
            await asyncio.sleep(2)
            txt = await ev(cap, "(function(){var ps=Array.prototype.filter.call("
                                "document.querySelectorAll('.wh-pop'),function(p){"
                                "return p.offsetParent!==null});"
                                "return ps.length?ps[ps.length-1].innerText"
                                ".replace(/\\s+/g,' '):'none';})()", "text")
            link = await ev(cap, "(function(){var a=document.querySelector('.wh-pop a');"
                                 "return a?a.getAttribute('href'):'none';})()", "link")
            print("\n--- 最近到货 悬浮 ---")
            print(txt)
            print("飞书链接:", link)
    await cdp_net.close_tab(PORT, tid)
    p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
