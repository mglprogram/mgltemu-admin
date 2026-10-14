# -*- coding: utf-8 -*-
"""复核展开行「仓内现货」悬浮里的算式与新鲜度（日均来源 / 需求 / 快照时间）。

用法：python scripts/goods/verify_wh_popover2.py
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
PORT = 9339
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

#: 展开行里第 n 个「仓内现货」的位置（n 默认 1，避开表头那一行）
RECT_JS = ("(function(){var e=document.querySelectorAll("
           "'.el-table__expanded-cell .goods-wh')[%d];"
           "if(!e) return null;var r=e.getBoundingClientRect();"
           "return [Math.round(r.x+r.width/2),Math.round(r.y+r.height/2)];})()")


async def ev(cap, expr, label=""):
    v, exc = await cap.eval(expr)
    if exc:
        print(f"   [{label}] 异常: {json.dumps(exc, ensure_ascii=False)[:140]}")
    return v


async def hover(cap, x, y):
    await cap.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y},
                   timeout=20)
    await asyncio.sleep(1)
    await cap.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x + 2, "y": y + 2},
                   timeout=20)
    await asyncio.sleep(2)


async def main() -> int:
    import subprocess
    prof = ROOT / "out" / "_chrome_pop3"
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
            if await ev(cap, "document.querySelectorAll('.el-table__expand-icon').length"):
                break

        await ev(cap, "(function(){var t=" + VIS_TABLE + "[0];"
                      "t.querySelector('.el-table__expand-icon').click();return 1;})()")
        await asyncio.sleep(2)
        codes = await ev(cap, "Array.prototype.map.call(document.querySelectorAll("
                              "'.el-table__expanded-cell tr'),function(tr){"
                              "return (tr.innerText||'').replace(/\\s+/g,' ').slice(0,60)})"
                              ".slice(0,6).join(' || ')")
        print("展开的款：" + str(codes)[:200])
        print("行内可悬浮点:", await ev(
            cap, "document.querySelectorAll('.el-table__expanded-cell .goods-wh').length"))

        for n in (0, 2):
            rect = await ev(cap, RECT_JS % n, f"rect{n}")
            print(f"\n=== 悬浮第 {n} 个仓内现货 @ {rect}")
            if not rect:
                continue
            # 先移开，确保重新触发
            await cap.send("Input.dispatchMouseEvent",
                           {"type": "mouseMoved", "x": 5, "y": 5}, timeout=20)
            await asyncio.sleep(0.5)
            await hover(cap, rect[0], rect[1])
            txt = await ev(cap, "(function(){var ps=Array.prototype.filter.call("
                                "document.querySelectorAll('.wh-pop'),function(p){"
                                "return p.offsetParent!==null});"
                                "return ps.length? ps[ps.length-1].innerText"
                                ".replace(/\\s+/g,' '):'none';})()", "text")
            print(txt)
    await cdp_net.close_tab(PORT, tid)
    p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
