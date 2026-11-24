# -*- coding: utf-8 -*-
"""下单过程中的**自助诊断**：失败时立刻截图 + 抓关键上下文，不用等用户贴图。

用法（被 place_one / 批量下单调用）：
    capture_failure(store, po, why, extra={...}) -> dict

产物：`_logs/temu-buylabel-fail/<时间>_<单号>.png` + `.json`

为什么加：2026-11-24 用户批评「下单过程不能发现异常、不能自己截图去修复吗」——
  之前每次失败都靠用户贴图（预约弹窗、失败列表），效率太低。现在失败时自己留证。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-fail"
WINDOWS = {"BHY": "TM-BHY-YJF", "BEL": "TM-BEL-MGL", "KINGLAMANSPORT": "TM-KinglamanSport"}

#: 页面上值得一起记下来的文字（预约弹窗 / 失败列表 / 物流选项）
JS_SNAPSHOT = r"""(() => {
  const out = {url: location.href, title: document.title, texts: [], inputs: [], buttons: []};
  const t = document.body.innerText || '';
  for (const kw of ['预约', '失败', '物流服务', '收货地址', '订单号']) {
    const i = t.indexOf(kw);
    if (i >= 0) out.texts.push(kw + ' => ' + t.slice(Math.max(0, i - 60), i + 260)
                               .replace(/\s+/g, ' '));
  }
  document.querySelectorAll('input,select').forEach((e) => {
    const v = (e.value || '').trim();
    if (v && v !== 'on') out.inputs.push(v);
  });
  document.querySelectorAll('button').forEach((b) => {
    const s = (b.innerText || '').replace(/\s+/g, ' ').trim();
    if (s) out.buttons.push(s.slice(0, 16) + (b.disabled ? '[禁用]' : ''));
  });
  out.buttons = [...new Set(out.buttons)];
  return JSON.stringify(out);
})()"""


def capture_failure(store: str, po: str, why: str, extra: dict | None = None,
                    log=print) -> dict:
    """截图 + 抓页面快照 + 记失败原因。返回 `{png, json, snapshot}`（失败不抛）。"""
    import asyncio  # noqa: PLC0415
    import base64  # noqa: PLC0415

    import websockets  # noqa: PLC0415

    import temu_buylabel_api as A  # noqa: PLC0415

    os.makedirs(OUT, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = os.path.join(OUT, "%s_%s" % (stamp, po.replace("/", "_")))
    info = {"store": store, "po": po, "why": why, "at": stamp,
            "extra": extra or {}, "snapshot": {}}

    try:
        win = WINDOWS.get(store.upper(), store)
        import ziniao_store as zs  # noqa: PLC0415
        port = int(zs.probe_port_by_window(win) or 0)
        tabs = zs.list_page_targets(port) if port else []
        tab = next((t for t in tabs if "online-ship" in str(t.get("url") or "")), None) \
            or next((t for t in tabs if "orders.html" in str(t.get("url") or "")), None)
        if tab:
            async def _do():
                async with websockets.connect(tab.get("webSocketDebuggerUrl"),
                                              max_size=64 * 1024 * 1024) as ws:
                    # 页面快照
                    await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                              "params": {"expression": JS_SNAPSHOT,
                                                         "returnByValue": True}}))
                    snap = {}
                    while True:
                        m = json.loads(await ws.recv())
                        if m.get("id") == 1:
                            v = (m.get("result") or {}).get("result", {}).get("value")
                            snap = json.loads(v or "{}")
                            break
                    # 截图
                    await ws.send(json.dumps({"id": 2, "method": "Page.captureScreenshot",
                                              "params": {"format": "png"}}))
                    png = None
                    while True:
                        m = json.loads(await ws.recv())
                        if m.get("id") == 2:
                            png = (m.get("result") or {}).get("data")
                            break
                    return snap, png
            snap, png = asyncio.run(_do())
            info["snapshot"] = snap
            if png:
                png_path = stem + ".png"
                with open(png_path, "wb") as f:
                    f.write(base64.b64decode(png))
                info["png"] = png_path
    except Exception as ex:  # noqa: BLE001
        info["capture_error"] = str(ex)[:200]

    json_path = stem + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    info["json"] = json_path
    log("   📸 失败现场已留证：%s" % (info.get("png") or json_path))
    return info
