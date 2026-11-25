# -*- coding: utf-8 -*-
"""order_list_api.py — TEMU「订单列表」内部接口（2026-11-23 实测）。

接口：`POST /kirogi/bg/mms/recentOrderList?is_back=1`
  ✅ **不需要 x-phan-data**（实测不带签名也返回 200；比 `/mms/eagle/*` 宽松）
  ✅ 一次返回 `totalItemNum` + `pageItems[]`，每项 `parentOrderMap` 是父单，
     商品行（含 `orderSn / goodsId / skuId / quantity`）可在其中递归取到。

用途：**批量买面单的取数入口** —— 不再需要人工点「在线下单」页抓 goods。

用法：
    python scripts/power/order_list_api.py --store BHY --status 待发货 --limit 100
    python scripts/power/order_list_api.py --store BHY --pos PO-211-xxx,PO-211-yyy
    python scripts/power/order_list_api.py --store BHY --dump out.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_HERE, os.path.join(_HERE, "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import temu_buylabel_api as A  # noqa: E402

PATH = "/kirogi/bg/mms/recentOrderList?is_back=1"

#: queryType=2（按订单时间）/ sortType=1（新→旧）/ fulfillmentMode=0（卖家履约）
BASE_BODY = {"fulfillmentMode": 0, "queryType": 2, "sortType": 1,
             "timeZone": "UTC+8", "parentAfterSalesTag": 0,
             "needBuySignService": 0, "sellerNoteLabelList": []}

#: 订单状态码（实测：页面「待发货」= parentOrderStatus 2）
STATUS = {"待处理": 1, "待发货": 2, "已发货": 3, "已签收": 4, "已取消": 5}


def _find_goods(obj, out, depth=0):
    """递归收集带 skuId/goodsId 的商品行。"""
    if depth > 8 or not isinstance(obj, (dict, list)):
        return
    if isinstance(obj, list):
        for x in obj[:80]:
            _find_goods(x, out, depth + 1)
        return
    if "skuId" in obj or "goodsId" in obj:
        rec = {k: obj.get(k) for k in
               ("orderSn", "goodsId", "skuId", "quantity", "skuCode",
                "warehouseId", "warehouseName") if k in obj}
        # ★ 2026-11-24 真机：SKU 货号在 `extCodeList[0]`（如 BHY-H103903-2B-XL）
        ec = obj.get("extCodeList")
        if isinstance(ec, list) and ec:
            rec["skuCode"] = str(ec[0]).strip()
        out.append(rec)
    for v in obj.values():
        _find_goods(v, out, depth + 1)


def parse_items(res: dict) -> list:
    """`result` → `[{parentOrderSn, regionId1, status, goods:[...]}]`（纯函数）。"""
    out = []
    for it in (res or {}).get("pageItems") or []:
        pm = it.get("parentOrderMap") or {}
        goods = []
        _find_goods(it, goods)
        # ★ 2026-11-24：抽取预售标记（商品行 `isExtendedPreSaleOrder` / `preSaleEndTimeStr`）
        import re as _re  # noqa: PLC0415
        _s = json.dumps(it, ensure_ascii=False)
        _pre_ship = bool(_re.search(r'"isExtendedPreSaleShipRestricted"\s*:\s*true', _s))
        _pre = bool(_re.search(r'"isExtendedPreSaleOrder"\s*:\s*true', _s)) or _pre_ship
        _end = ""
        _m = _re.search(r'"preSaleEndTimeStr"\s*:\s*"([^"]+)"', _s)
        if _m:
            _end = _m.group(1)
        out.append({"parentOrderSn": pm.get("parentOrderSn"),
                    "regionId1": pm.get("regionId1"),
                    "status": pm.get("parentOrderStatus"),
                    "isExtendedPreSale": _pre,
                    "preSaleEndTimeStr": _end,
                    "goods": goods})
    return out


#: 抄页面签名的注入脚本（addScriptToEvaluateOnNewDocument —— 刷新也在）
JS_INJECT_CAP = r"""(() => {
  window.__hdrCap = [];
  const keep = (h) => {
    const o = {};
    try {
      if (h && typeof h.forEach === 'function') h.forEach((v, k) => { o[k] = String(v); });
      else if (h) Object.keys(h).forEach((k) => { o[k] = String(h[k]); });
    } catch (e) {}
    if (o['x-phan-data']) window.__hdrCap.push(o);
  };
  const of = window.fetch;
  window.fetch = function (input, init) {
    try {
      const u = (typeof input === 'string') ? input : (input && input.url) || '';
      if (u.match(/\/(mms|kirogi)\//)) keep((init && init.headers) || (input && input.headers));
    } catch (e) {}
    return of.apply(this, arguments);
  };
  const osh = XMLHttpRequest.prototype.setRequestHeader, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    try { (this.__h = this.__h || {})[k] = String(v); } catch (e) {}
    return osh.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    try { if (this.__h && this.__h['x-phan-data']) window.__hdrCap.push(this.__h); } catch (e) {}
    return os.apply(this, arguments);
  };
  return 'ok';
})()"""

_HDR_CACHE: dict = {}
_HDR_TTL = 60.0


def fresh_headers(store: str, force: bool = False) -> dict:
    """从页面抄一份**新鲜的** `x-phan-data` 头；拿不到返回 {}（调用方用缓存兜底）。

    为什么必须现取：它是页面按「时间戳+请求内容」现算的签名，**用过即失效**。
    2026-11-25 BEL 实测：带它 200（totalItemNum=159），不带 403 400020037。
    """
    import time as _t  # noqa: PLC0415

    c = _HDR_CACHE.get(store)
    if c and not force and (_t.time() - c[1]) < _HDR_TTL:
        return c[0]
    try:
        ws_url, _u = A._page_ws(store)
    except Exception:  # noqa: BLE001
        return {}

    async def _grab():
        import websockets as _ws  # noqa: PLC0415

        async with _ws.connect(ws_url, max_size=64 * 1024 * 1024) as sock:
            n = [0]

            async def call(method, params):
                n[0] += 1
                i = n[0]
                await sock.send(json.dumps({"id": i, "method": method, "params": params}))
                while True:
                    m = json.loads(await sock.recv())
                    if m.get("id") == i:
                        return m

            await call("Page.enable", {})
            await call("Page.addScriptToEvaluateOnNewDocument",
                       {"source": JS_INJECT_CAP, "runImmediately": True})
            await call("Page.reload", {"ignoreCache": False})
            for _ in range(20):
                await asyncio.sleep(2)
                r = await call("Runtime.evaluate", {
                    "expression": "(() => JSON.stringify((window.__hdrCap||[]).slice(-1)[0]||null))()",
                    "returnByValue": True})
                v = ((r.get("result") or {}).get("result") or {}).get("value")
                if v and v != "null":
                    try:
                        h = json.loads(v)
                        if h.get("x-phan-data"):
                            return h
                    except Exception:  # noqa: BLE001
                        pass
            return {}

    try:
        h = asyncio.run(_grab())
    except Exception as ex:  # noqa: BLE001
        print("   [签名] 抓取失败：%s" % str(ex)[:100])
        h = {}
    if h:
        _HDR_CACHE[store] = (h, _t.time())
        return h
    return {}


JS_CLICK_QUERY = r"""(() => {
  const vis = (e) => { const r = e.getBoundingClientRect();
    return e.getClientRects().length > 0 && r.width > 0 && r.height > 0; };
  let b = null;
  document.querySelectorAll('button').forEach((x) => {
    if (b || !vis(x)) return;
    if ((x.innerText || '').replace(/\s+/g, '') === '查询') b = x;
  });
  if (!b) return 'no-btn';
  b.click();
  return 'clicked';
})()"""


def _click_query(ws_url: str) -> str:
    """在页面里点「查询」，让服务端给窗口发业务票据（BEL 403 自愈用）。"""
    try:
        return str(asyncio.run(A._ev(ws_url, JS_CLICK_QUERY)))
    except Exception as ex:  # noqa: BLE001
        return "ERR " + str(ex)[:60]


def fetch_page(store: str, page: int = 1, size: int = 100,
               status: str = "", pos: list | None = None) -> dict:
    """拉一页。返回 `{ok, total, items, raw}`。"""
    body = dict(BASE_BODY, pageNumber=page, pageSize=size)
    if status and status in STATUS:
        body["parentOrderStatus"] = STATUS[status]
    if pos:
        body["parentOrderSnList"] = list(pos)
    ws_url, _u = A._page_ws(store)
    # ★★ 2026-11-25 真机证明（BEL）：缺新鲜的 `x-phan-data` ⇒ **403 400020037**；
    #    带页面现算的那份 ⇒ 200（抓包对比：同一请求，唯一差别就是这个头）。
    #    它是页面按「时间戳+请求内容」现算的签名，用过即失效 ⇒ 每次现抄一份。
    hdr = A.load_headers(store) or {}
    fresh = fresh_headers(store)
    if fresh:
        hdr = dict(hdr, **fresh)
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k.lower() in
            ("mallid", "web-version", "content-type")}
    keep["mallid"] = A.MALLID.get(store.upper(), "") or keep.get("mallid", "")
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(PATH),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    # ★ 2026-11-25 真机（BEL）：窗口刚授权完发请求 403，页面「用一次」后就 200
    #   ⇒ 403 时自动激活会话再重试（刷新页面 / 点查询）
    if out.get("status") == 403:
        # ★★ 2026-11-25：403 的根因是**签名过期**（页面现算、用过即失效）。
        #   ⇒ 必须先清缓存再 force 重抄，光刷新页面不够（会继续用旧签名）。
        for attempt in range(1, 3):
            print("   [403] 第 %d 次自愈：清缓存 + 重抄签名" % attempt)
            try:
                _HDR_CACHE.pop(store, None)
                fresh = fresh_headers(store, force=True)
                if not fresh:
                    print("   [403] 仍抄不到签名")
                    continue
                ws_url, _u2 = A._page_ws(store)
                keep2 = {k: v for k, v in fresh.items()
                         if k.lower().startswith("x-") or k.lower() in
                         ("mallid", "web-version", "content-type")}
                keep2["mallid"] = A.MALLID.get(store.upper(), "") or keep2.get("mallid", "")
                js2 = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(PATH),
                                            json.dumps(body, ensure_ascii=False),
                                            json.dumps(keep2, ensure_ascii=False))
                out = json.loads(asyncio.run(A._ev(ws_url, js2)) or "{}")
                print("   [403] 自愈后 status=%s" % out.get("status"))
                if out.get("status") == 200:
                    break
            except Exception as ex:  # noqa: BLE001
                print("   [403] 自愈异常：%s" % str(ex)[:80])

    txt = out.get("body") or ""
    try:
        j = json.loads(txt)
    except Exception:  # noqa: BLE001
        return {"ok": False, "total": 0, "items": [], "raw": txt[:400]}
    res = j.get("result") or {}
    return {"ok": bool(j.get("success")), "total": res.get("totalItemNum") or 0,
            "items": parse_items(res), "raw": ""}


def fetch_all(store: str, status: str = "", limit: int = 0, page_size: int = 100) -> list:
    """翻页拿全（`limit` > 0 时最多拿这么多单）。

    ⚠ 循环变量不能叫 `page` —— 模块顶部 `import argparse` 之后这里若遮蔽了别的名字
      不会有问题，但早期版本把 `page` 当成函数名误用，导致第一次就 break 出 0 条。
      现在显式用 `page_no`。
    """
    got = []
    page_no = 1
    while True:
        r = fetch_page(store, page=page_no, size=page_size, status=status)
        if not r.get("ok"):
            break
        items = r.get("items") or []
        got.extend(items)
        if limit and len(got) >= limit:
            return got[:limit]
        total = r.get("total") or 0
        if not items or len(got) >= total:
            break
        page_no += 1
        if page_no > 50:
            break
    return got


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 订单列表内部接口")
    ap.add_argument("--store", required=True, choices=sorted(A.MALLID))
    ap.add_argument("--status", default="", help="待处理/待发货/已发货/已签收/已取消")
    ap.add_argument("--pos", default="", help="只查这些参考单号（逗号分隔）")
    ap.add_argument("--limit", type=int, default=0, help="最多拿多少单（0=全量）")
    ap.add_argument("--dump", default="", help="结果存 json")
    a = ap.parse_args()

    pos = [x.strip() for x in a.pos.split(",") if x.strip()]
    if pos:
        r = fetch_page(a.store, size=100, pos=pos)
        items, total = r.get("items") or [], r.get("total") or 0
    else:
        items = fetch_all(a.store, status=a.status, limit=a.limit)
        total = len(items)
    n_goods = sum(len(i.get("goods") or []) for i in items)
    print("接口: %s" % PATH)
    print("单数 %d（totalItemNum=%s）｜商品行 %d" % (len(items), total, n_goods))
    for it in items[:5]:
        g = (it.get("goods") or [{}])[0]
        print("  %-24s status=%s goods=%d 例:%s/%s"
              % (it.get("parentOrderSn"), it.get("status"), len(it.get("goods") or []),
                 g.get("goodsId"), g.get("skuId")))
    missing = [i.get("parentOrderSn") for i in items if not i.get("goods")]
    if missing:
        print("⚠ 没有商品行的单 %d 个：%s" % (len(missing), missing[:5]))
    if a.dump:
        json.dump(items, open(a.dump, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("已存 →", a.dump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
