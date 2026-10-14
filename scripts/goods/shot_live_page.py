# -*- coding: utf-8 -*-
r"""用本机 Chrome（headless）打开线上面板，验证**渲染**并截图。

为什么需要它：紫鸟浏览器把 `mgltemu.us.ci` 拦在它自己的访问策略页（本机实测 2026-10-12），
所以「页面是否真的渲染」不能用紫鸟窗口验 —— 用本机 Chrome + CDP 独立验。

用法：
    python scripts/goods/shot_live_page.py            # 默认验 #/temu/on-sale（含低库存视图）
    python scripts/goods/shot_live_page.py dashboard  # 验首页卡片
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdp_net  # noqa: E402

ROOT = Path(r"D:\AI-workplace\TEMU")
OUT_DIR = ROOT / "out"
INDEX = ROOT / "temu-admin" / "dist" / "index.html"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PORT = 9333
LIVE = "https://mgltemu.us.ci"

def gate_password() -> str:
    html = INDEX.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'temu_admin_gate_ok",\s*want="((?:[^"\\]|\\.)*)"', html)
    return json.loads('"' + m.group(1) + '"') if m else ""


def gate_script() -> str:
    """过密码门禁：直接填密码 + 触发「进入」。

    ⚠ 别只写 `sessionStorage` 标记：新标签页里 `about:blank` → 目标页是**跨源导航**，
    新 origin 下 sessionStorage 本来就是空的，而插入的初始化脚本跑在门禁脚本**之前**，
    标记会被门禁自己的 `if(sessionStorage.getItem(KEY)==='1')` 判成"没有" ⇒ 仍弹门禁。
    直接 DOM 填密码最稳（门禁就是纯前端比对，密码在页面里就有）。
    """
    pw = gate_password()
    if not pw:
        return ""
    return """
(function () {
  var PW = %s;
  function fill() {
    var i = document.getElementById('gp'), b = document.getElementById('gb');
    if (!i || !b) return false;
    i.value = PW;
    b.click();
    return !document.getElementById('gate');
  }
  var tries = 0;
  var t = setInterval(function () {
    tries++;
    if (fill() || tries > 60) clearInterval(t);
  }, 250);
  document.addEventListener('DOMContentLoaded', fill);
  return 'gate-armed';
})();
""" % json.dumps(pw)


GATE = gate_script()


def start_chrome() -> subprocess.Popen:
    profile = Path(r"D:\AI-workplace\TEMU\out\_chrome_profile")
    profile.mkdir(parents=True, exist_ok=True)
    # 清掉上一次留下的 headless Chrome（否则端口被占，navigate 会一直不返回）
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                        "Where-Object { $_.CommandLine -like '*_chrome_profile*' } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                       capture_output=True, text=True, timeout=60)
        time.sleep(1)
    except Exception:  # noqa: BLE001
        pass
    args = [str(CHROME), "--headless=new", f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--disable-gpu", "--hide-scrollbars", "--window-size=1600,1200",
            "about:blank"]
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        try:
            cdp_net.http_json(f"http://127.0.0.1:{PORT}/json/version", timeout=2)
            return p
        except Exception:  # noqa: BLE001
            continue
    raise RuntimeError("Chrome 调试端口没起来")


JS_STATE = r"""
(function () {
  try {
    var q = function (s) { return document.querySelectorAll(s).length; };
    var pins = document.querySelector('.goods-pinbar');
    var cards = Array.prototype.slice.call(document.querySelectorAll('.quick-card'))
      .map(function (c) { return (c.innerText || '').replace(/\s+/g, ' ').slice(0, 90); });
    return JSON.stringify({
      url: location.href,
      title: document.title,
      pinbar: pins ? (pins.innerText || '').replace(/\s+/g, ' ').slice(0, 200) : null,
      inputs: Array.prototype.slice.call(document.querySelectorAll('input'))
        .map(function (i) { return i.placeholder; }).filter(Boolean),
      tables: q('.el-table'),
      rows: q('.el-table__body tbody tr'),
      lowSection: !!document.querySelector('#low-stock'),
      lowStats: (function () {
        var b = document.querySelector('.goods-low-stats');
        return b ? (b.innerText || '').replace(/\s+/g, ' ').slice(0, 160) : null;
      })(),
      quickCards: cards.slice(0, 6),
      bodyHead: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 220)
    });
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""


JS_EXPAND_FIRST = r"""
(function () {
  try {
    var ic = document.querySelector('.el-table__expand-icon');
    if (!ic) return JSON.stringify({ok: false, why: 'no-expand-icon'});
    ic.click();
    return JSON.stringify({ok: true});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""

JS_EXPANDED = r"""
(function () {
  try {
    var boxes = Array.prototype.slice.call(document.querySelectorAll('.goods-sku-box'));
    var out = boxes.map(function (b) {
      var rows = Array.prototype.slice.call(b.querySelectorAll('tbody tr'));
      return {
        title: (b.querySelector('.goods-sku-box__title') || {}).innerText || '',
        skuRows: rows.length,
        lowRows: b.querySelectorAll('tr.goods-low-row').length,
        firstRows: rows.slice(0, 6).map(function (tr) {
          return (tr.innerText || '').replace(/\s+/g, ' ').slice(0, 80);
        })
      };
    });
    return JSON.stringify({expanded: out});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""


async def capture(route: str, name: str, wait_s: float = 6.0, expand: bool = False):
    port = PORT
    tid = await cdp_net.new_tab(port, "about:blank")
    t = None
    for _ in range(20):
        await asyncio.sleep(0.5)
        t = next((x for x in cdp_net.http_json(f"http://127.0.0.1:{port}/json/list")
                  if (x.get("id") or x.get("targetId")) == tid), None)
        if t:
            break
    if not t:
        print("!! 新标签页未注册")
        return
    ws = t["webSocketDebuggerUrl"]

    async with cdp_net.NetCapture(ws) as cap:
        # 先装门禁放行脚本（每次导航都会执行），再导航
        await cap.send("Page.addScriptToEvaluateOnNewDocument", {"source": GATE})
        # 关掉浏览器缓存：Cloudflare Pages 的自定义域可能仍回旧 HTML，
        # 不关掉会拿旧 bundle 渲染 → 明明发布了却看到上一版（2026-10-12 踩过）
        try:
            await cap.send("Network.setCacheDisabled", {"cacheDisabled": True})
        except Exception:  # noqa: BLE001
            pass
        try:
            await cap.send("Page.navigate", {"url": f"{LIVE}/#{route}"}, timeout=120)
        except Exception as e:  # noqa: BLE001
            print("[warn] navigate 未在超时内返回（继续等渲染）:", str(e)[:80])
        await asyncio.sleep(wait_s)

        state = None
        for i in range(10):
            v, exc = await cap.eval(JS_STATE)
            state = json.loads(v) if v else {}
            if state.get("rows") or state.get("quickCards"):
                break
            await asyncio.sleep(4)

        print(f"=== {name} ({route}) ===")
        print(json.dumps(state, ensure_ascii=False, indent=1)[:2200])

        if expand:
            v, exc = await cap.eval(JS_EXPAND_FIRST)
            print("展开首行:", v, "exc=", bool(exc))
            await asyncio.sleep(2)
            v2, exc2 = await cap.eval(JS_EXPANDED)
            print("展开内容:", str(v2)[:1200], "exc=", bool(exc2))

        # 只截视口：`captureBeyondViewport` 在超长页上会静默返回空 data
        # （2026-10-12 实测：低库存页整页高度过大 → 截图为空，日志连"截图失败"都不打）
        for extra, label in (({}, "viewport"), ({"captureBeyondViewport": True}, "full")):
            try:
                r = await cap.send("Page.captureScreenshot",
                                   {"format": "png", "fromSurface": True, **extra},
                                   timeout=60)
                data = (r.get("result") or {}).get("data")
                if data:
                    OUT_DIR.mkdir(parents=True, exist_ok=True)
                    p = OUT_DIR / f"live-{name}.png"
                    p.write_bytes(base64.b64decode(data))
                    print(f"截图({label}) ->", p, f"{p.stat().st_size/1024:.0f} KB")
                    break
                print(f"截图({label}) 返回空 data")
            except Exception as e:  # noqa: BLE001
                print(f"截图({label})失败:", str(e)[:100])
    await cdp_net.close_tab(port, tid)


async def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "onsale").lower()
    proc = start_chrome()
    try:
        if mode == "dashboard":
            await capture("/dashboard", "dashboard")
        else:
            await capture("/temu/on-sale", "onsale")
            await capture("/temu/on-sale?view=low", "onsale-low", expand=True)
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    asyncio.run(main())
