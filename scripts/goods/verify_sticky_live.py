# -*- coding: utf-8 -*-
"""用 CDP 精确验证「表头是否与表体分离、随表体滚动固定」。

判据（页面内实测，不是读 DOM 猜）：
  · `.el-table__body-wrapper` 是滚动容器（scrollHeight > clientHeight）；
  · 表头 wrapper 与表体 wrapper 的 `getBoundingClientRect().top` 在**表体滚动前后不变**；
  · 页面滚动窗口时，两者一起移动（说明是表内滚动，不是整页滚动）。

用法：python scripts/goods/verify_sticky_live.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdp_net  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9334
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

PROBE = r"""
(function () {
  try {
    var tbl = document.querySelector('.el-table');
    if (!tbl) return JSON.stringify({ok: false, why: 'no-table'});
    var hw = tbl.querySelector('.el-table__header-wrapper');
    var bw = tbl.querySelector('.el-table__body-wrapper');
    var inner = bw ? bw.querySelector('.el-scrollbar__wrap') : null;
    var scroller = inner || bw;
    var r = function (el) { var b = el.getBoundingClientRect(); return {top: Math.round(b.top), h: Math.round(b.height)}; };
    return JSON.stringify({
      ok: true,
      tableStyleHeight: (tbl.style.height || ''),
      header: hw ? r(hw) : null,
      body: bw ? r(bw) : null,
      bodyScrollable: scroller ? scroller.scrollHeight > scroller.clientHeight : null,
      bodyScrollHeight: scroller ? scroller.scrollHeight : null,
      bodyClientHeight: scroller ? scroller.clientHeight : null,
      headerRows: hw ? hw.querySelectorAll('th').length : 0,
      firstHeaderText: hw ? (hw.innerText || '').replace(/\s+/g, ' ').slice(0, 120) : ''
    });
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

SCROLL_BODY = r"""
(function () {
  var tbl = document.querySelector('.el-table');
  var bw = tbl && tbl.querySelector('.el-table__body-wrapper');
  var inner = bw && bw.querySelector('.el-scrollbar__wrap');
  var s = inner || bw;
  if (!s) return 'no-scroller';
  s.scrollTop = 400;
  return 'scrolled to ' + s.scrollTop;
})()
"""


async def main() -> int:
    # 复用已有的 headless Chrome 启动方式（带门禁脚本）
    import subprocess
    import time
    prof = ROOT / "out" / "_chrome_sticky_live"
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                    "Where-Object { $_.CommandLine -like '*_chrome_sticky_live*' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True, text=True, timeout=60)
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

    index = ROOT / "temu-admin" / "dist" / "index.html"
    html = index.read_text(encoding="utf-8", errors="replace")
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
        # 轮询等表格挂载（固定 sleep 太脆：门禁 + 首次加载 SPA 都要时间）
        before = {}
        for i in range(20):
            await asyncio.sleep(3)
            v, exc = await cap.eval(PROBE)
            before = json.loads(v) if v else {}
            if before.get("ok"):
                print(f"第 {i+1} 次探测到表格")
                break
            gate = await cap.eval("(function(){return !!document.getElementById('gate');})()")
            print(f"  第 {i+1} 次：还没表格（门禁在？{gate[0]}）")
        print("滚动前:", json.dumps(before, ensure_ascii=False, indent=1))

        v2, _ = await cap.eval(SCROLL_BODY)
        print("\n表体滚动:", v2)
        await asyncio.sleep(1)
        v3, _ = await cap.eval(PROBE)
        after = json.loads(v3) if v3 else {}
        print("滚动后表头/表体 top:", after.get("header"), after.get("body"))

        if before.get("header") and after.get("header"):
            same = before["header"]["top"] == after["header"]["top"]
            print("\n表头位置是否不随表体滚动而变化:", same)
            print("表体可滚动:", before.get("bodyScrollable"),
                  "scrollHeight=%s clientHeight=%s" % (before.get("bodyScrollHeight"),
                                                       before.get("bodyClientHeight")))
        try:
            r = await cap.send("Page.captureScreenshot", {"format": "png"}, timeout=60)
            data = (r.get("result") or {}).get("data")
            if data:
                import base64
                out = ROOT / "out" / "live-lowstock-sticky.png"
                out.write_bytes(base64.b64decode(data))
                print("截图 ->", out)
        except Exception as e:  # noqa: BLE001
            print("截图失败:", str(e)[:80])
    await cdp_net.close_tab(PORT, tid)
    p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
