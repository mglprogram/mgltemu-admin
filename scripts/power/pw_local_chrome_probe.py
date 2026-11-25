# -*- coding: utf-8 -*-
"""用**本地谷歌浏览器**（有帕沃尔登录态）抓帕沃尔发货全流程的后端请求。

用户：紫鸟窗口里帕沃尔没登录态，但本地 Chrome 是登录着的。

做法：
  ① 从 `http://127.0.0.1:<port>/json/list` 找本地 Chrome 的调试端口
     （常见 9222；也扫 9223~9230）
  ② 找/新开帕沃尔 `hwc_shop.php?act=shop_order&pb=1` 页
  ③ 浏览器级 Network 旁听，记录所有 powerwarehouse 域的非静态请求
  ④ 用户在页面上点「下单代发」等动作时，请求就会被记下

如果本地 Chrome 没开调试端口，脚本会提示用 `--remote-debugging-port=9222` 重启。
"""
import asyncio
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\pw_local_probe.json"
URL = "https://www.powerwarehouse.net/hwc_shop.php?act=shop_order&pb=1"
PORTS = [9222, 9223, 9224, 9225, 9226, 9227, 9228, 9229, 9230]


def find_chrome_port() -> int:
    for p in PORTS:
        try:
            v = json.loads(urllib.request.urlopen(
                "http://127.0.0.1:%d/json/version" % p, timeout=3).read())
            if v.get("Browser"):
                print("找到本地 Chrome 调试端口 %d：%s" % (p, v.get("Browser")))
                return p
        except Exception:  # noqa: BLE001
            continue
    return 0


async def main():
    port = find_chrome_port()
    if not port:
        print("❌ 本地 Chrome 没开调试端口。")
        print("   请用带调试端口的快捷方式启动（会新开一个 Chrome）：")
        print('   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
              '--remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\\Google\\Chrome\\User Data"')
        return 1
    ver = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/version" % port, timeout=10).read())
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 240

    recs, pending, seen = {}, {}, set()
    async with websockets.connect(ver["webSocketDebuggerUrl"],
                                  max_size=200 * 1024 * 1024) as ws:
        mid = [0]

        async def send(method, params=None, sid=None):
            mid[0] += 1
            msg = {"id": mid[0], "method": method, "params": params or {}}
            if sid:
                msg["sessionId"] = sid
            await ws.send(json.dumps(msg))
            return mid[0]

        await send("Target.setDiscoverTargets", {"discover": True})
        await asyncio.sleep(2)

        # 找已有帕沃尔页；没有就开一个
        tabs = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/json/list" % port, timeout=10).read())
        pw_tabs = [t for t in tabs if "powerwarehouse" in str(t.get("url") or "")]
        if pw_tabs:
            print("已有帕沃尔页：", str(pw_tabs[0].get("url"))[:90])
            await send("Target.activateTarget", {"targetId": pw_tabs[0]["id"]})
        else:
            print("新开帕沃尔发货页…")
            await send("Target.createTarget", {"url": URL})

        print("\n👉 请在浏览器里操作帕沃尔：搜索单号 → 勾选 → 下单代发（最多 %ds）" % secs,
              flush=True)
        end = time.time() + secs
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=4)
            except asyncio.TimeoutError:
                continue
            except Exception as ex:  # noqa: BLE001
                print("ws:", str(ex)[:60])
                break
            m = json.loads(raw)
            me, p = m.get("method"), (m.get("params") or {})
            if me == "Target.attachedToTarget":
                if p.get("sessionId"):
                    await send("Network.enable", {"maxTotalBufferSize": 80000000},
                               p["sessionId"])
            elif me == "Target.targetCreated":
                ti = p.get("targetInfo") or {}
                if ti.get("type") == "page" and ti.get("targetId") not in seen:
                    seen.add(ti["targetId"])
                    await send("Target.attachToTarget",
                               {"targetId": ti["targetId"], "flatten": True})
            elif me == "Network.requestWillBeSent":
                r = p.get("request") or {}
                u = r.get("url", "")
                if "powerwarehouse" not in u:
                    continue
                if any(u.split("?")[0].endswith(x) for x in
                       (".js", ".css", ".png", ".jpg", ".gif", ".woff", ".woff2",
                        ".ico", ".svg")):
                    continue
                rid = p.get("requestId")
                rec = {"url": u, "method": r.get("method"),
                       "postData": (r.get("postData") or "")[:800]}
                pending[rid] = rec
                if r.get("method") == "POST" and r.get("postData"):
                    print("\n★ POST %s" % u.replace(
                        "https://www.powerwarehouse.net", "")[:80], flush=True)
                    print("   %s" % rec["postData"][:300], flush=True)
            elif me == "Network.responseReceived":
                rid = p.get("requestId")
                if rid in pending:
                    rec = pending.pop(rid)
                    rec["status"] = (p.get("response") or {}).get("status")
                    rec["mime"] = (p.get("response") or {}).get("mimeType")
                    recs[rid] = rec

    print("\n=== 共 %d 条业务请求 ===" % len(recs))
    for rec in recs.values():
        u = rec["url"].replace("https://www.powerwarehouse.net", "")
        print("  %-4s %-6s %-56s %s" % (rec.get("status"), rec.get("method"),
                                        u[:56], str(rec.get("mime") or "")[:18]))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(list(recs.values()), open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n已存 → %s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
