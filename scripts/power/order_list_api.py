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


def fetch_page(store: str, page: int = 1, size: int = 100,
               status: str = "", pos: list | None = None) -> dict:
    """拉一页。返回 `{ok, total, items, raw}`。"""
    body = dict(BASE_BODY, pageNumber=page, pageSize=size)
    if status and status in STATUS:
        body["parentOrderStatus"] = STATUS[status]
    if pos:
        body["parentOrderSnList"] = list(pos)
    ws_url, _u = A._page_ws(store)
    # ★ 2026-11-24 实测：`/kirogi/bg/mms/*` **只需要 `mallid`** 就能返回 200
    #   （不带任何 header 会 403 400020037；缓存里有 x-phan-data 时一并带上也无害）。
    hdr = A.load_headers(store) or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID.get(store.upper(), "") or keep.get("mallid", "")
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(PATH),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
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
