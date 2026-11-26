# -*- coding: utf-8 -*-
"""深挖单号在面单任务表里的记录 —— 判断「发货订单重复」的真因。

★ 2026-11-26 真机：`PO-211-07248603720310091`
    订单列表 status=2（待发货）、preSale=False
    confirmShippingList 查无包裹
    买面单却回「发货订单重复」
  ⇒ 可能面单任务表里有半成品/失败记录，需要看 `query_label_task_list`。

接口：
    POST /mms/eagle/package/online/query_label_task_list?is_back=1
    body {"page_number":1,"page_size":50}
    → 任务列表（TK-xxx / 状态 / 包裹号 / 订单号）
"""
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402

import temu_buylabel_api as A  # noqa: E402

PO = sys.argv[1] if len(sys.argv) > 1 else "PO-211-07248603720310091"


def main():
    ws_url, _u = A._page_ws("KINGLAMANSPORT")
    hdr = A.load_headers("KINGLAMANSPORT") or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k.lower() in ("mallid", "web-version",
                                                           "content-type")}
    keep["mallid"] = A.mallid_of("KINGLAMANSPORT", ws_url)
    path = "/mms/eagle/package/online/query_label_task_list?is_back=1"
    body = {"page_number": 1, "page_size": 50}
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(path),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    print("status:", out.get("status"))
    raw = out.get("body") or ""
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        print("body:", raw[:400])
        return 1

    res = d.get("result") or {}
    items = res.get("pageItems") or res.get("taskList") or []
    print("任务数:", len(items), "| total:", res.get("totalItemNum"))
    hit = []
    for it in items:
        s = json.dumps(it, ensure_ascii=False)
        if PO in s:
            hit.append(it)
    print("\n含 %s 的任务 %d 条：" % (PO, len(hit)))
    for it in hit:
        print(json.dumps(it, ensure_ascii=False)[:600])
    if not hit:
        print("（任务列表里也没有这单）")
        # 打前 3 条看结构
        for it in items[:3]:
            print("样例:", json.dumps(it, ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
