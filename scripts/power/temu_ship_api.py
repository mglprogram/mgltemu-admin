# -*- coding: utf-8 -*-
"""temu_ship_api.py — TEMU 买面单**只读**接口层（2026-11-23 抓包实测）。

已实测的链路（都在 BHY 真机上抓到、返回 200）：
  POST /mms/eagle/package/batch_check_request                       校验待下单（商品/规格/多包裹）
  POST /mms/eagle/package/reservation/query_avail_reserve_period    查仓 + 渠道 + 报价 + 可选揽收日

鉴权（关键）：**不需要算签名**，页面自己带的这几个 header 就够：
  mallid: <店铺 mallId>            ← BHY=634418228526171
  x-phan-data: <页面生成的串>       ← 必须从页面上下文里取（见 _page_headers）
  x-document-referer / web-version / Content-Type

调用方式：**在页面上下文里 fetch**（CDP Runtime.evaluate + awaitPromise），
这样 `x-phan-data`、cookie、mallid 都由页面自己提供，不重算、不伪造。

用法（只读，不下单）：
    python scripts/power/temu_ship_api.py --store BHY --check  PO-211-xxx
    python scripts/power/temu_ship_api.py --store BHY --quote  PO-211-xxx --company GOFO --fee 188000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = os.path.join(_HERE, "lib")
for p in (_HERE, _LIB):
    if p not in sys.path:
        sys.path.insert(0, p)

#: 各店 mallid（Temu 商家中心订单页的 `mallid` 请求头）
MALLID = {
    "BHY": "634418228526171",
    "BEL": "634418228477419",
    "KINGLAMANSPORT": "27364108847582",
}

WINDOWS = {"BHY": "TM-BHY-YJF", "BEL": "TM-BEL-MGL",
           "KinglamanSport": "TM-KinglamanSport"}

#: 页面里取 header 的 JS：优先读已保存的，没有就让页面自己发一次只读请求再从 request 里拿。
JS_CALL = r"""(async (url, body, hdr) => {
  try {
    const h = Object.assign({'Content-Type': 'application/json;charset=UTF-8'}, hdr || {});
    const r = await fetch(url, {
      method: 'POST',
      headers: h,
      credentials: 'include',
      body: JSON.stringify(body)
    });
    const t = await r.text();
    return JSON.stringify({status: r.status, body: t.slice(0, 20000)});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})"""


def _ws_url(store: str):
    import ziniao_store as zs  # noqa: PLC0415
    win = WINDOWS.get(store, store)
    port = zs.probe_port_by_window(win)
    if not port:
        raise RuntimeError("窗口 %s 未开" % win)
    tabs = zs.list_page_targets(int(port))
    for key in ("online-ship", "orders.html"):
        for t in tabs:
            if key in str(t.get("url") or ""):
                return t.get("webSocketDebuggerUrl"), str(t.get("url"))
    raise RuntimeError("窗口 %s 里没有订单页/在线下单页" % win)


#: ★ 2026-11-23 真机（复现失败 → 403 400020037 得到的结论）：
#:   `/mms/*` 网关**不是看 cookie**，而是看页面自己挂的那几个 header —— 少了就 403：
#:     mallid            店铺 ID（BHY=634418228526171）
#:     x-phan-data       页面 JS 插进 axios 的签名串（**每次请求都不同**，必须实时取）
#:     x-document-referer/web-version 来源与前端版本
#:   ⇒ 所以「在页面里裸 fetch」不够，要**按原样的 header 发**。
#:   拿到 x-phan-data 的办法见 `grab_page_headers()`：从页面自己发出的一次请求里抄。
JS_GRAB = r"""(async (probe) => {
  try {
    // 拦截一次页面自己的请求，把 header 抄下来（只读探测请求）
    const orig = window.fetch;
    let captured = null;
    window.fetch = function (input, init) {
      try {
        const u = (typeof input === 'string') ? input : (input && input.url) || '';
        if (u.indexOf('/mms/') >= 0 && !captured) {
          const h = {};
          const src = (init && init.headers) || {};
          try {
            if (typeof src.forEach === 'function') src.forEach((v, k) => { h[k] = v; });
            else Object.keys(src).forEach((k) => { h[k] = src[k]; });
          } catch (e) {}
          captured = h;
        }
      } catch (e) {}
      return orig.apply(this, arguments);
    };
    try { await probe(); } catch (e) {}
    await new Promise((r) => setTimeout(r, 1200));
    window.fetch = orig;
    return JSON.stringify(captured || {});
  } catch (e) { return JSON.stringify({error: String(e)}); }
})()"""


async def _eval(ws_url: str, expression: str):
    import websockets  # noqa: PLC0415
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
            "userGesture": True}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == 1:
                res = m.get("result") or {}
                if res.get("exceptionDetails"):
                    raise RuntimeError(str(res["exceptionDetails"])[:300])
                return (res.get("result") or {}).get("value")


def api_call(store: str, path: str, body: dict, headers: dict | None = None) -> dict:
    """在页面上下文里 POST 一次 `/mms/...`。`headers` 必须含 mallid/x-phan-data 等。"""
    ws_url, _url = _ws_url(store)
    hdr = headers or {}
    if not hdr.get("mallid"):
        hdr = dict(hdr, mallid=MALLID.get(store.upper(), ""))
    js = "(%s)(%s, %s, %s)" % (JS_CALL, json.dumps(path),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(hdr, ensure_ascii=False))
    raw = asyncio.run(_eval(ws_url, js))
    return json.loads(raw or "{}")


def grab_page_headers(store: str) -> dict:
    """从页面自己发出的一次 `/mms/` 请求里抄下 header（主要是 `x-phan-data`）。

    做法：临时包一层 fetch → 触发一次页面自己的只读请求（点一下仓下拉/或直接调一个
    已知的只读接口）→ 抄 header。抄不到返回 {}。
    """
    ws_url, _u = _ws_url(store)
    probe = ("() => { try { return fetch('/mms/otter/warehouse/query_by_region', "
             "{method:'POST',credentials:'include',"
             "headers:{'Content-Type':'application/json;charset=UTF-8'},"
             "body:JSON.stringify({region:'US'})}); } catch (e) { return null; } }")
    js = "(%s)(%s)" % (JS_GRAB, probe)
    raw = asyncio.run(_eval(ws_url, js))
    try:
        return json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {}


def check_request(store: str, po: str, goods: list) -> dict:
    """① 校验待下单：goods=[{"order_sn","parent_order_sn","goods_id","sku_id","quantity"}]"""
    body = {"check_info_type_list": [8, 11],
            "send_request_list": [{"row_index": 0, "order_send_info_list": goods,
                                   "width": "9.30", "height": "3.00", "length": "12.30",
                                   "weight": "1.50", "dimension_unit": "in",
                                   "weight_unit": "lb"}]}
    return api_call(store, "/mms/eagle/package/batch_check_request", body)


def quote(store: str, info: dict) -> dict:
    """② 查仓/渠道/报价/可选揽收日。`info` 见 capture 里的 reserve_info_list[0]。"""
    body = {"reserve_info_list": [info]}
    return api_call(store, "/mms/eagle/package/reservation/query_avail_reserve_period", body)


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 买面单只读接口（查仓/查价）")
    ap.add_argument("--store", required=True, choices=sorted(MALLID))
    ap.add_argument("--check", default="", help="只做校验：给 parent 订单号")
    ap.add_argument("--quote", default="", help="查报价：给 parent 订单号")
    ap.add_argument("--body", default="", help="直接给 reserve_info JSON 文件（高级）")
    a = ap.parse_args()

    if a.check:
        print("⚠ 需要 goods 明细才能校验；本命令先打印 mallid/页面信息")
        ws_url, url = _ws_url(a.store)
        print("store=%s mallid=%s" % (a.store, MALLID.get(a.store.upper(), "?")))
        print("page =%s" % url[:120])
        return 0
    if a.quote and a.body:
        info = json.load(open(a.body, encoding="utf-8"))
        print(json.dumps(quote(a.store, info), ensure_ascii=False, indent=1)[:4000])
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
