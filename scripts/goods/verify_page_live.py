# -*- coding: utf-8 -*-
"""验证线上「在售款明细」页：绕过密码门禁 → 打开 #/temu/on-sale → 读回渲染与搜索行为。

用法：python scripts/goods/verify_page_live.py BEL
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cdp_net  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

WINDOWS = {"BEL": "TM-BEL-MGL", "BHY": "TM-BHY-YJF"}
LIVE = "https://mgltemu.us.ci"
DIST_INDEX = Path(r"D:\AI-workplace\TEMU\temu-admin\dist\index.html")

JS_PROBE = r"""
(function () {
  try {
    var pins = document.querySelector('.goods-pinbar');
    var rows = document.querySelectorAll('.el-table__body tbody tr');
    return JSON.stringify({
      url: location.href,
      hasPinbar: !!pins,
      pinText: pins ? (pins.innerText || '').replace(/\s+/g, ' ').slice(0, 240) : '',
      placeholders: Array.prototype.slice.call(document.querySelectorAll('input'))
        .map(function (i) { return i.placeholder; }).filter(Boolean),
      rows: rows.length,
      firstRow: rows.length ? (rows[0].innerText || '').replace(/\s+/g, ' ').slice(0, 160) : '',
      head: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 200)
    });
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()
"""


def gate_password() -> str:
    html = DIST_INDEX.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'temu_admin_gate_ok",\s*want="((?:[^"\\]|\\.)*)"', html)
    if not m:
        return ""
    return json.loads('"' + m.group(1) + '"')


async def main_async(store: str):
    pw = gate_password()
    print("gate password:", pw or "(未找到)")
    port = probe_port(WINDOWS[store])
    print("port =", port)

    tid = await cdp_net.new_tab(port, LIVE + "/")
    t = None
    for _ in range(20):
        await asyncio.sleep(1)
        t = next((x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                  if (x.get("id") or x.get("targetId")) == tid), None)
        if t:
            break
    if not t:
        print("!! 新标签页未注册")
        return
    ws = t["webSocketDebuggerUrl"]

    async with cdp_net.NetCapture(ws) as cap:
        await asyncio.sleep(4)
        # 1) 过门禁（注入 sessionStorage 标记）
        if pw:
            v, exc = await cap.eval(
                "(function(){try{sessionStorage.setItem('temu_admin_gate_ok','1');"
                "return 'set';}catch(e){return String(e);}})()")
            print("gate:", v, "exc=", bool(exc))
        # 2) 直接进目标页
        v, exc = await cap.eval("(function(){location.hash='#/temu/on-sale';return location.href;})()")
        print("hash:", v)
        for i in range(12):
            await asyncio.sleep(3)
            v, exc = await cap.eval(JS_PROBE)
            d = json.loads(v) if v else {}
            print(f"  探测{i+1}: pinbar={d.get('hasPinbar')} rows={d.get('rows')}")
            if d.get("hasPinbar") and d.get("rows"):
                print("\n== 渲染结果 ==")
                print(json.dumps(d, ensure_ascii=False, indent=1)[:2000])
                break
        else:
            print("最后一次探测：", v)
    await cdp_net.close_tab(port, tid)


if __name__ == "__main__":
    asyncio.run(main_async((sys.argv[1] if len(sys.argv) > 1 else "BEL").upper()))
