# -*- coding: utf-8 -*-
"""查面单任务表（`query_label_task_list`）—— 正确字段是 `label_task_info_list`。

字段结构（2026-11-26 实测）：
    {"total_task_num": 376,
     "label_task_info_list": [{
        label_task_id, task_create_time_str, label_ship_type,
        total_number, success_number, failure_number,
        task_status,
        success_package_sn_list: [...], failure_package_sn_list: [...] }]}

用途：
  · 买面单前判断「这单是否已有任务」（避免「发货订单重复」）
  · 买完核对任务结果

用法：
    python pw_label_tasks.py                    # 最近 50 个任务
    python pw_label_tasks.py --find PO-211-xxx  # 找特定单
"""
import argparse
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import asyncio  # noqa: E402

import temu_buylabel_api as A  # noqa: E402

PATH = "/mms/eagle/package/online/query_label_task_list?is_back=1"


def fetch(store: str = "KINGLAMANSPORT", page_size: int = 100) -> dict:
    ws_url, _u = A._page_ws(store)
    hdr = A.load_headers(store) or {}
    keep = {k: v for k, v in hdr.items()
            if k.lower().startswith("x-") or k.lower() in
            ("mallid", "web-version", "content-type")}
    keep["mallid"] = A.mallid_of(store, ws_url)
    body = {"page_number": 1, "page_size": page_size}
    js = "(%s)(%s, %s, %s)" % (A.JS_CALL, json.dumps(PATH),
                               json.dumps(body, ensure_ascii=False),
                               json.dumps(keep, ensure_ascii=False))
    out = json.loads(asyncio.run(A._ev(ws_url, js)) or "{}")
    try:
        return json.loads(out.get("body") or "{}")
    except Exception:  # noqa: BLE001
        return {"_raw": str(out.get("body"))[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="KINGLAMANSPORT")
    ap.add_argument("--find", default="")
    a = ap.parse_args()

    d = fetch(a.store)
    res = d.get("result") or {}
    tasks = res.get("label_task_info_list") or []
    print("任务总数:", res.get("total_task_num"), "| 本页", len(tasks))
    if not tasks:
        print(d)
        return 1

    if a.find:
        hit = [t for t in tasks
               if a.find in json.dumps(t, ensure_ascii=False)]
        print("\n含 %s 的任务 %d 条：" % (a.find, len(hit)))
        for t in hit:
            print(json.dumps(t, ensure_ascii=False)[:500])
        return 0

    for t in tasks[:15]:
        print("  %-24s %-22s status=%s total=%s ok=%s fail=%s"
              % (t.get("label_task_id"), t.get("task_create_time_str"),
                 t.get("task_status"), t.get("total_number"),
                 t.get("success_number"), t.get("failure_number")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
