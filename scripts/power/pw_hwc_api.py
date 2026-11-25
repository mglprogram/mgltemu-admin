# -*- coding: utf-8 -*-
"""把帕沃尔「查单 / 改仓库渠道 / 下单代发」三个动作统一成 **HTTP 接口版**。

★ 2026-11-25 真机挖到（调试 Chrome 里读页面 live JS）：
   搜索：POST /hwc_shop.php?is_ajax=1
         act=query_shop_order&deliver_no=<单号>&shop_id=&ck_id=&status=
         &start_date=..&end_date=..&country=&shop_types=&pb_str=&page=1
   下单：POST /hwc_shop.php?act=xiadan_df&id=<订单id>&is_ajax=1
         无 body（processData:false, contentType:false）；返回 {error,msg,msg2,html}
   改仓：已有 `pw_hwc_flow.http_set_wh`（deliver.php?act=get_shop_paisongx + batch）

调用链（页面 JS）：
   edit_order('xiadan_df') → confirm → xiadan_df()
     → auto_update_ship_news(ids_list) → auto_update_ship_newsx(ids,0,n)
       → 逐个 POST act=xiadan_df
"""
from __future__ import annotations

import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_session as SS  # noqa: E402

DATE_FMT = "%Y-%m-%d"


def _pw():
    p = SS.PW()
    p.login()
    return p


def query_orders(pw, deliver_no: str = "", start: str = "", end: str = "",
                 page: int = 1, pb: str = "1") -> dict:
    """按参考单号查订单（JSON）。返回 `{rows: [...], raw: str}`。

    页面里同一接口的 form 参数（2026-11-25 抓包）：
      act=query_shop_order / deliver_no / shop_id / ck_id / status /
      start_date / end_date / country / shop_types / pb_str / page
    """
    body = {
        "act": "query_shop_order", "deliver_no": deliver_no, "shop_id": "",
        "ck_id": "", "status": "", "start_date": start or time.strftime(DATE_FMT),
        "end_date": end or time.strftime(DATE_FMT), "country": "",
        "shop_types": "", "pb_str": "", "page": str(page),
    }
    raw = pw.post("hwc_shop.php?is_ajax=1", body)
    txt = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    # 返回是 `{"error":0,"message":"","content":"<html>..."}` ⇒ 先解出 content 再解析
    try:
        html = (json.loads(txt) or {}).get("content") or txt
    except Exception:  # noqa: BLE001
        html = txt
    return {"raw": txt, "html": html, "rows": _parse_rows(html, deliver_no)}


def _parse_rows(html: str, po: str = "") -> list:
    """从返回的 html 片段里抠出每行的 `订单id`（`ids[]` 的 value）与参考单号。

    真实页面每行有 `<input type="checkbox" name="ids[]" value="123456" ...>`
    以及参考单号文本。这里尽量宽松匹配，找不到就返回空。
    """
    rows = []
    # 按 <tr ...> ... </tr> 切
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        # 真实 HTML（2026-11-25 实测）：
        #   <input type="checkbox" name="checkboxes[]" value="3013745" class="checkbox" />
        m = re.search(r'name=["\']?checkboxes\[\]?["\']?[^>]*value=["\']?(\d+)', tr)
        if not m:
            m = re.search(r'value=["\']?(\d+)["\']?[^>]*name=["\']?checkboxes', tr)
        if not m:
            m = re.search(r'name=["\']?ids\[\]?["\']?[^>]*value=["\']?(\d+)', tr)
        if not m:
            continue
        oid = m.group(1)
        pom = re.search(r"(PO-\d+-\d+|GSU[0-9A-Z]+|GU\d+)", tr)
        rows.append({"id": oid, "po": pom.group(1) if pom else ""})
    if po and not rows:
        # 退化：整页只找一个 id（单号精确查时常见）
        m = re.search(r'name=["\']?checkboxes\[\]?["\']?[^>]*value=["\']?(\d+)', html)
        if m:
            rows.append({"id": m.group(1), "po": po})
    return rows


def xiadan_df(pw, order_id: str) -> dict:
    """下单代发（单笔）。返回 `{ok, error, msg, msg2, html}`。"""
    r = pw.post("hwc_shop.php?act=xiadan_df&id=%s&is_ajax=1" % order_id, {})
    try:
        d = json.loads(r) if isinstance(r, str) else r
    except Exception:  # noqa: BLE001
        d = {"error": -1, "msg": str(r)[:200]}
    d["ok"] = int(d.get("error") or 0) == 0
    return d


def ship_by_po(pw, po: str, start: str = "", end: str = "",
               apply: bool = False, log=print) -> dict:
    """按参考单号：查 → 下单代发。`apply=False` 只查不动。"""
    q = query_orders(pw, deliver_no=po, start=start, end=end)
    rows = q["rows"]
    log("   [查单] %s → %d 行" % (po, len(rows)))
    if not rows:
        return {"ok": False, "why": "查不到订单", "raw": q["raw"][:200]}
    hit = next((x for x in rows if not x["po"] or x["po"] == po), rows[0])
    if not apply:
        return {"ok": True, "dry": True, "id": hit["id"], "po": hit["po"] or po}
    d = xiadan_df(pw, hit["id"])
    log("   [下单] %s id=%s → error=%s msg=%s"
        % (po, hit["id"], d.get("error"), str(d.get("msg"))[:60]))
    return d


if __name__ == "__main__":
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    do_apply = "--apply" in sys.argv
    p = _pw()
    print(ship_by_po(p, po, apply=do_apply))
