# -*- coding: utf-8 -*-
"""复现「展开 A → 收起 A → 点 B ⇒ A 又被连带展开」。

思路（2026-10-12 起）：
  · 状态用**一次成功的** `Runtime.evaluate`（IIFE 返回 JSON 字符串）读，不用封装层；
  · 点击用 CDP `Input.dispatchMouseEvent` 打**真实鼠标事件**，避开 DOM .click() 与事件冒泡差异；
  · 每步都打印「展开箭头态」与「是否出现 SKU 明细表」，二者必须一一对应。

用法：python scripts/goods/repro_expand2.py
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
PORT = 9342
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

STATE_JS = r"""
(function () {
  var tbls = Array.prototype.filter.call(document.querySelectorAll('.el-table'),
    function (x) { return x.getBoundingClientRect().width > 0; });
  var t = tbls[0];
  if (!t) return JSON.stringify({err: 'no visible table'});
  var icons = Array.prototype.slice.call(
    t.querySelectorAll('.el-table__body-wrapper .el-table__expand-icon'));
  var rows = icons.map(function (ic, i) {
    var tr = ic.closest('tr');
    var nx = tr.nextElementSibling;
    var det = !!(nx && (nx.innerText || '').indexOf('SKU 明细') >= 0);
    return {
      i: i,
      code: (tr.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 22),
      exp: ic.classList.contains('el-table__expand-icon--expanded'),
      det: det
    };
  });
  var rect = function (i) {
    var ic = icons[i];
    if (!ic) return null;
    var r = ic.getBoundingClientRect();
    return [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)];
  };
  return JSON.stringify({
    n: icons.length,
    rows: rows,
    rects: [rect(0), rect(1), rect(3)]
  });
})()
"""


async def ev(cap, expr, label=""):
    r = await cap.send("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True}, timeout=30)
    res = r.get("result") or {}
    v = res.get("value")
    if v is None:
        print("   [%s] raw=%s" % (label, json.dumps(r, ensure_ascii=False)[:260]))
    return v


async def state(cap, label):
    v = await ev(cap, STATE_JS, label)
    d = json.loads(v) if v else {}
    rows = d.get("rows") or []
    exp = [r["i"] for r in rows if r["exp"]]
    det = [r["i"] for r in rows if r["det"]]
    print("  [%s] 展开箭头=%s 有明细=%s （共 %s 行）" % (label, exp, det, d.get("n")))
    return {"exp": exp, "det": det, "rects": d.get("rects") or []}


async def click_at(cap, xy):
    x, y = xy
    for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
        await cap.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": x, "y": y, "button": "left", "clickCount": 1},
                       timeout=20)
        await asyncio.sleep(0.12)


async def main() -> int:
    import subprocess
    prof = ROOT / "out" / "_chrome_rep3"
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
            v = await ev(cap, "document.querySelectorAll('.el-table__expand-icon').length",
                         "icons")
            if v:
                break
        await asyncio.sleep(2)
        st = await state(cap, "初始")
        if not st["rects"] or None in st["rects"]:
            print("!! 取不到展开箭头坐标，放弃")
            return 1

        print("\n② 点箭头展开第 0 行（%s）" % st["rects"][0])
        await click_at(cap, st["rects"][0])
        await asyncio.sleep(1.5)
        await state(cap, "展开0")

        print("\n③ 再点第 0 行箭头收起")
        await click_at(cap, st["rects"][0])
        await asyncio.sleep(1.5)
        await state(cap, "收起0")

        print("\n④ 点第 3 行箭头展开")
        await click_at(cap, st["rects"][2])
        await asyncio.sleep(1.5)
        st4 = await state(cap, "展开3")
        print("    ⚠ 期望只展开 [3]；实际 %s" % st4["exp"])

        print("\n⑤ 再点第 0 行箭头")
        await click_at(cap, st["rects"][0])
        await asyncio.sleep(1.5)
        st5 = await state(cap, "再展开0")
        print("    期望 [0,3]（各自独立）；实际 %s" % st5["exp"])
    await cdp_net.close_tab(PORT, tid)
    p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
