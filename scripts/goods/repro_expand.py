# -*- coding: utf-8 -*-
"""复现「展开一行却展开多行」：读每个展开箭头的状态 + 该行货号，点一个再看变化。

用法：python scripts/goods/repro_expand.py
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
PORT = 9335
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

#: 每个可展开行的「行内货号 + 展开图标状态」
#: ⚠ 别用 `tbody > tr.el-table__row` 计数：展开后详情本身也是 tr，会把行号算错。
#: 以**展开箭头**为锚点，取它所在的行与下一行（详情行）。
JS_ROWS = r"""
(function () {
  try {
    var tbl = document.querySelector('.el-table');
    var body = tbl.querySelector('.el-table__body-wrapper');
    var icons = Array.prototype.slice.call(body.querySelectorAll('.el-table__expand-icon'));
    var out = icons.map(function (icon, i) {
      var tr = icon.closest('tr');
      var nx = tr.nextElementSibling;
      var detail = false;
      if (nx && nx.classList.contains('el-table__row') === false &&
          (nx.innerText || '').indexOf('SKU 明细') >= 0) detail = true;
      return {
        i: i,
        code: (tr.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 34),
        spu: (function () {
          var t = (tr.innerText || '').replace(/\s+/g, ' ');
          var m = t.match(/\b(\d{9,12})\b/g);
          return m ? m.slice(0, 2).join('/') : '';
        })(),
        expanded: icon.classList.contains('el-table__expand-icon--expanded'),
        detailVisible: detail
      };
    });
    return JSON.stringify({n: icons.length, rows: out,
      tdTotal: body.querySelectorAll('td').length});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

JS_CLICK_NTH = r"""
(function (n) {
  var tbl = document.querySelector('.el-table');
  var body = tbl.querySelector('.el-table__body-wrapper');
  var icons = Array.prototype.slice.call(body.querySelectorAll('.el-table__expand-icon'));
  var icon = icons[n];
  if (!icon) return 'no-icon';
  var tr = icon.closest('tr');
  icon.click();
  return 'clicked ' + (tr.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 30);
})
"""


async def main() -> int:
    import subprocess
    prof = ROOT / "out" / "_chrome_repro"
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
        for _ in range(20):
            await asyncio.sleep(3)
            v, _ = await cap.eval(JS_ROWS)
            if v and json.loads(v).get("n"):
                break

        before = json.loads((await cap.eval(JS_ROWS))[0] or "{}")
        exp_before = [r["i"] for r in before.get("rows", []) if r.get("expanded")]
        print("可展开行数:", before.get("n"), "| 点击前已展开:", exp_before)
        print("前 8 行:", [(r["i"], r["code"][:24], r["expanded"], r["detailVisible"])
                           for r in before.get("rows", [])[:8]])

        # 逐次点开前 3 行，每次都核对「展开的行数」
        for n in (0, 1, 2, 5, 8):
            print("\n--- 点第", n, "行:", (await cap.eval(JS_CLICK_NTH, n))[0])
            await asyncio.sleep(1.5)
            after = json.loads((await cap.eval(JS_ROWS))[0] or "{}")
            exp = [r["i"] for r in after.get("rows", []) if r.get("expanded")]
            det = [r["i"] for r in after.get("rows", []) if r.get("detailVisible")]
            print("    已展开的行:", exp, "| 显示明细的行:", det,
                  "→", "OK" if len(exp) == len(set(exp)) == len(det) else "❌ 不一致")
            print("    各行:", [(r["i"], r["code"][:26], r["spu"]) for r in after.get("rows", [])[:12]])
    await cdp_net.close_tab(PORT, tid)
    p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
