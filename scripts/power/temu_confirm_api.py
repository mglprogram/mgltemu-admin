# -*- coding: utf-8 -*-
"""temu_confirm_confirm_ship_api → 批量确认发货（**接口版**，2026-11-24 真机抓到）。

链路（全部实测）：
  ① POST /kirogi/bg/mms/confirmShippingList?is_back=1
       body {"pageNumber":1,"pageSize":100,"parentOrderSnList":[],"onlyY2Package":false}
       → result.pageItems[] = [{packageSn, trackingNumber, parentOrderList, ...}]
          result.totalItemNum
  ② POST /mms/eagle/package/online/batch_confirm_send_package?is_back=1
       body {"confirm_package_request_list":[{package_sn, tracking_number,
                                             order_send_info_list:[{order_sn,parent_order_sn,quantity}]}]}
  ③ 回读 confirmShippingList 的 totalItemNum 是否减少（替代「等 30s reload」）

用法：
    python temu_confirm_api.py --store BHY                 # 只列出待确认（dry-run）
    python temu_confirm_api.py --store BHY --pos PO-1,PO-2 # 只看这些单的包裹
    python temu_confirm_api.py --store BHY --apply         # 真确认（⚠ 业务后果）
    python temu_confirm_api.py --store BHY --apply --pos PO-1   # 只确认这些单
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import temu_buylabel_api as A  # noqa: E402

LIST_PATH = "/kirogi/bg/mms/confirmShippingList?is_back=1"
CONFIRM_PATH = "/mms/eagle/package/online/batch_confirm_send_package?is_back=1"


def _call(store: str, path: str, body: dict) -> dict:
    hdr = A.load_headers(store) or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k in ("mallid", "web-version")}
    keep["mallid"] = A.MALLID.get(store.upper(), "") or keep.get("mallid", "")
    ws_url, _ = A._page_ws(store)
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(path),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    try:
        return json.loads(out.get("body") or "{}")
    except Exception:  # noqa: BLE001
        return {"_raw": str(out.get("body"))[:300], "_status": out.get("status")}


def list_pending(store: str, pos: list | None = None, page_size: int = 100) -> dict:
    """拉待确认包裹。返回 `{total, items}`；`pos` 给了就只保留这些单的包裹。"""
    body = {"pageNumber": 1, "pageSize": page_size,
            "parentOrderSnList": list(pos or []), "onlyY2Package": False}
    j = _call(store, LIST_PATH, body)
    res = j.get("result") or {}
    items = res.get("pageItems") or []
    if pos:
        want = set(pos)
        items = [it for it in items
                 if any((po.get("parentOrderSn") in want)
                        for po in _po_list(it))]
    return {"total": res.get("totalItemNum") or 0, "items": items,
            "ok": bool(j.get("success"))}


def _po_list(item: dict) -> list:
    """摊平父单：`parentOrderList[].parentOrderMap.parentOrderSn`。"""
    out = []
    for po in item.get("parentOrderList") or []:
        if not isinstance(po, dict):
            continue
        pm = po.get("parentOrderMap") or {}
        sn = pm.get("parentOrderSn") or po.get("parentOrderSn")
        if sn:
            out.append({"parentOrderSn": sn, "raw": po})
    if item.get("parentOrderSn"):
        out.append({"parentOrderSn": item["parentOrderSn"], "raw": item})
    return out


def _order_send_info(item: dict) -> list:
    """从 `parentOrderList[].orderList[]` 摊出 `order_send_info_list`。

    真机：`parentOrderList[i].orderList[j] = {orderSn, goodsId, skuId, quantity, ...}`
    """
    out = []
    seen = set()
    for po in item.get("parentOrderList") or []:
        if not isinstance(po, dict):
            continue
        pm = po.get("parentOrderMap") or {}
        pon = pm.get("parentOrderSn") or po.get("parentOrderSn")
        for sub in (po.get("orderList") or []):
            if not isinstance(sub, dict):
                continue
            osn = sub.get("orderSn") or sub.get("order_sn")
            if not osn or (osn, pon) in seen:
                continue
            seen.add((osn, pon))
            out.append({"order_sn": osn, "parent_order_sn": pon,
                        "quantity": sub.get("quantity") or 1})
    # 兜底：别的形态
    if not out:
        for k in ("orderSendInfoList", "order_send_info_list"):
            v = item.get(k)
            if isinstance(v, list) and v:
                for x in v:
                    if isinstance(x, dict):
                        osn = x.get("orderSn") or x.get("order_sn")
                        pon = x.get("parentOrderSn") or x.get("parent_order_sn")
                        if osn and pon:
                            out.append({"order_sn": osn, "parent_order_sn": pon,
                                        "quantity": x.get("quantity") or 1})
                if out:
                    break
    return out


def confirm(store: str, items: list, log=print) -> dict:
    """调 `batch_confirm_send_package` 真确认。返回 `{ok, code, msg, count}`。"""
    req = []
    for it in items:
        pkg = it.get("packageSn") or it.get("package_sn")
        tn = it.get("trackingNumber") or it.get("tracking_number")
        if not pkg:
            continue
        req.append({"package_sn": pkg, "tracking_number": tn,
                    "order_send_info_list": _order_send_info(it)})
    if not req:
        return {"ok": False, "code": "no_package", "msg": "没有可确认的包裹", "count": 0}
    j = _call(store, CONFIRM_PATH, {"confirm_package_request_list": req})
    return {"ok": bool(j.get("success")),
            "code": j.get("error_code") or j.get("errorCode"),
            "msg": str(j.get("error_msg") or j.get("errorMsg") or "")[:200],
            "count": len(req), "raw": j}


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 批量确认发货（接口版）")
    ap.add_argument("--store", required=True)
    ap.add_argument("--pos", default="", help="只处理这些参考单号（逗号分隔）")
    ap.add_argument("--apply", action="store_true", help="真确认（业务后果！）")
    ap.add_argument("--page-size", type=int, default=100)
    a = ap.parse_args()
    pos = [x.strip() for x in a.pos.split(",") if x.strip()]

    r0 = list_pending(a.store, pos or None, a.page_size)
    print("待确认总数: %s｜选中包裹: %d｜接口 ok=%s"
          % (r0["total"], len(r0["items"]), r0["ok"]))
    for it in r0["items"][:10]:
        pos_list = [p.get("parentOrderSn") for p in _po_list(it)]
        n_goods = len(_order_send_info(it))
        print("  %-24s %-28s 子单 %d"
              % (it.get("packageSn"), ",".join(pos_list)[:28], n_goods))
    if len(r0["items"]) > 10:
        print("  …共 %d 个包裹" % len(r0["items"]))

    if not a.apply:
        print("\n（dry-run，未确认。加 --apply 才真提交）")
        return 0

    out = confirm(a.store, r0["items"])
    print("\n确认结果: ok=%s code=%s msg=%s count=%s"
          % (out["ok"], out["code"], out["msg"], out["count"]))
    if not out["ok"]:
        return 1

    r1 = list_pending(a.store, pos or None, a.page_size)
    print("回读：待确认总数 %s → %s（减少 %s）"
          % (r0["total"], r1["total"], (r0["total"] or 0) - (r1["total"] or 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
