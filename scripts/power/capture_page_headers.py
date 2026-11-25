# -*- coding: utf-8 -*-
"""用 `Page.addScriptToEvaluateOnNewDocument` 抓页面自己发的 recentOrderList header。

为什么换这个 API：普通 `Runtime.evaluate` 装 hook，一刷新就丢；
`addScriptToEvaluateOnNewDocument` 会在**每个新文档加载前**注入，刷新也在。

① 注册脚本 ② 刷新 ③ 等页面自己发请求 ④ 抄 header ⑤ 用同一份 header 复现对比
"""
import asyncio
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import websockets  # noqa: E402
import temu_buylabel_api as A  # noqa: E402

INJECT = r"""(() => {
  window.__cap = [];
  const grab = (h) => {
    const o = {};
    try {
      if (h && typeof h.forEach === 'function') h.forEach((v, k) => { o[k] = String(v); });
      else if (h) Object.keys(h).forEach((k) => { o[k] = String(h[k]); });
    } catch (e) {}
    return o;
  };
  const of = window.fetch;
  window.fetch = function (input, init) {
    try {
      const u = (typeof input === 'string') ? input : (input && input.url) || '';
      if (u.match(/\/(mms|kirogi)\//)) {
        window.__cap.push({url: u.slice(0, 220),
                           h: grab((init && init.headers) || {}),
                           b: (init && init.body) ? String(init.body).slice(0, 500) : '',
                           via: 'fetch'});
      }
    } catch (e) {}
    return of.apply(this, arguments);
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  const osh = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; this.__h = {}; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    try { this.__h[k] = String(v); } catch (e) {}
    return osh.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (b) {
    try {
      const u = String(this.__u || '');
      if (u.match(/\/(mms|kirogi)\//)) {
        window.__cap.push({url: u.slice(0, 220), h: this.__h || {},
                           b: b ? String(b).slice(0, 500) : '', via: 'xhr'});
      }
    } catch (e) {}
    return os.apply(this, arguments);
  };
})()"""


async def main():
    store = sys.argv[1] if len(sys.argv) > 1 else "BEL"
    ws_url, url = A._page_ws(store)
    print("页面:", url[:90])
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        mid = [0]

        async def call(method, params):
            mid[0] += 1
            i = mid[0]
            await ws.send(json.dumps({"id": i, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == i:
                    return m

        await call("Page.enable", {})
        r = await call("Page.addScriptToEvaluateOnNewDocument",
                       {"source": INJECT, "runImmediately": True})
        print("注入脚本 id:", (r.get("result") or {}).get("identifier"))
        print("刷新页面…")
        await call("Page.reload", {"ignoreCache": False})

        cap = []
        for _ in range(30):
            await asyncio.sleep(3)
            r = await call("Runtime.evaluate", {
                "expression": "(() => JSON.stringify((window.__cap||[]).slice(-40)))()",
                "returnByValue": True})
            try:
                cap = json.loads(((r.get("result") or {}).get("result") or {}).get("value") or "[]")
            except Exception:  # noqa: BLE001
                cap = []
            hit = [x for x in cap if "recentOrderList" in x.get("url", "")]
            if hit:
                one = hit[-1]
                print("\n✅ 页面自己发的 recentOrderList（via %s）：" % one.get("via"))
                for k, v in (one.get("h") or {}).items():
                    print("   %-26s %s" % (k, str(v)[:80]))
                hdr = one.get("h") or {}
                keep = {k: v for k, v in hdr.items()
                        if k.lower().startswith("x-") or k.lower() in
                        ("mallid", "web-version", "content-type")}
                body = {"fulfillmentMode": 0, "pageNumber": 1, "pageSize": 3,
                        "queryType": 2, "sortType": 1, "timeZone": "UTC+8",
                        "parentAfterSalesTag": 0, "needBuySignService": 0,
                        "sellerNoteLabelList": []}
                js = "(%s)(%s, %s, %s)" % (
                    A.JS_CALL, json.dumps("/kirogi/bg/mms/recentOrderList?is_back=1"),
                    json.dumps(body, ensure_ascii=False),
                    json.dumps(keep, ensure_ascii=False))
                out = json.loads(await A._ev(ws_url, js) or "{}")
                print("\n【复用同 header 复现】status:", out.get("status"))
                print("  body:", str(out.get("body"))[:160])
                return 0
        print("\n没抓到（共 %d 条 /mms|/kirogi 请求）" % len(cap))
        for x in cap[:8]:
            print("   ", x.get("via"), x.get("url")[:90])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
