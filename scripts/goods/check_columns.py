# -*- coding: utf-8 -*-
"""复核「最近到货」是否无需横向滚动即可看到：读表头顺序 + 各列宽度 + 表格总宽 vs 视口宽。

用法：python scripts/goods/check_columns.py
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
PORT = 9336
URL = "https://mgltemu.us.ci/#/temu/on-sale?view=low"
WINDOW = "1280,900"

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

JS_COLS = r"""
(function () {
  try {
    var tbl = document.querySelector('.el-table');
    var hw = tbl.querySelector('.el-table__header-wrapper');
    var ths = Array.prototype.slice.call(hw.querySelectorAll('thead th'));
    var cols = ths.map(function (th, i) {
      return {i: i, label: (th.innerText || '').trim(),
              w: Math.round(th.getBoundingClientRect().width),
              x: Math.round(th.getBoundingClientRect().left)};
    });
    var body = tbl.querySelector('.el-table__body-wrapper');
    var inner = body.querySelector('.el-scrollbar__wrap');
    var innerW = inner ? Math.round(inner.getBoundingClientRect().width) : 0;
    var tableW = 0;
    var tr = tbl.querySelector('.el-table__header colgroup');
    if (tr) tableW = Array.prototype.slice.call(tr.children)
      .reduce(function (a, c) { return a + (parseInt(c.style.width, 10) || 0); }, 0);
    return JSON.stringify({
      cols: cols.filter(function (c) { return c.label; }),
      innerWidth: innerW,
      viewportWidth: window.innerWidth,
      tableWidth: tableW,
      scrollWidth: inner ? inner.scrollWidth : null,
      needScroll: inner ? inner.scrollWidth > inner.clientWidth : null
    });
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""


async def main() -> int:
    import subprocess
    prof = ROOT / "out" / "_chrome_cols"
    p = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
                          f"--user-data-dir={prof}", "--no-first-run", "--disable-gpu",
                          f"--window-size={WINDOW}", "about:blank"],
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
        data = {}
        for _ in range(15):
            await asyncio.sleep(3)
            v, _ = await cap.eval(JS_COLS)
            data = json.loads(v) if v else {}
            if data.get("cols"):
                break
        cols = data.get("cols") or []
        print("窗口宽 %s | 表体可视宽 %s | 表格总宽 %s | 需要横向滚动: %s"
              % (data.get("viewportWidth"), data.get("innerWidth"),
                 data.get("tableWidth"), data.get("needScroll")))
        print()
        print("%-4s %-16s %6s %8s" % ("#", "列名", "宽度", "左边界"))
        for c in cols:
            print("%-4s %-16s %6s %8s" % (c["i"], c["label"], c["w"], c["x"]))
        arrived = next((c for c in cols if "最近到货" in c["label"]), None)
        if arrived:
            right = arrived["x"] + arrived["w"]
            print()
            print("「最近到货」右边界 = %s，表体可视右边界 ≈ %s ⇒ %s"
                  % (right,
                     (data.get("innerWidth") or 0) + (cols[0]["x"] if cols else 0),
                     "无需横向滚动即可见 ✅" if right <= (data.get("innerWidth") or 0)
                     + (cols[0]["x"] if cols else 0) else "仍需滚动 ❌"))
    await cdp_net.close_tab(PORT, tid)
    p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
