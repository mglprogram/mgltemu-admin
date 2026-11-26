# -*- coding: utf-8 -*-
"""诊断：KLS 订单页为什么读不到内容（`document.body.innerText` 为空）。

可能：
  · 内容在 iframe 里（`mmsos` 用微前端）
  · 页面还在加载
  · `_page_ws` 拿到的是空白页/别的 tab
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402
import ziniao_store as zs  # noqa: E402

JS = r"""(() => {
  const out = {
    url: location.href,
    ready: document.readyState,
    bodyLen: document.body ? (document.body.innerText || '').length : -1,
    head: document.body ? (document.body.innerText || '').slice(0, 200).replace(/\s+/g, ' ') : '',
    iframes: [...document.querySelectorAll('iframe')].map((f) => ({
      src: (f.src || '').slice(0, 140),
      w: f.clientWidth, h: f.clientHeight,
    })),
    divs: document.querySelectorAll('div').length,
  };
  return JSON.stringify(out);
})()"""


async def main():
    port = int(zs.probe_port_by_window("TM-KinglamanSport") or 0)
    tabs = zs.list_page_targets(port) if port else []
    print("KLS 端口 %s，标签页 %d 个：" % (port, len(tabs)))
    for t in tabs:
        u = str(t.get("url") or "")
        if "temu" not in u:
            continue
        print("\n--- %s ---" % u[:110])
        try:
            raw = await asyncio.wait_for(
                A._ev(t["webSocketDebuggerUrl"], JS), timeout=30)
            d = json.loads(raw or "{}")
            print("  ready:", d.get("ready"), "| bodyLen:", d.get("bodyLen"),
                  "| divs:", d.get("divs"))
            print("  head:", (d.get("head") or "")[:150])
            for f in (d.get("iframes") or []):
                print("  iframe:", f.get("w"), "x", f.get("h"), f.get("src"))
        except Exception as ex:  # noqa: BLE001
            print("  读失败:", str(ex)[:100])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
