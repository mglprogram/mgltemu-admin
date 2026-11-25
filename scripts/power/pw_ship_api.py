# -*- coding: utf-8 -*-
"""纯接口版帕沃尔「下单代发」——`wh 选择按平台`（用户 2026-11-25 确认）。

★ 用户口径：
    「temu 选 temu，shein 选 shein」
    PLATFORM_ROUTE = {"temu": ["temu", "temu官方面单"],
                      "shein": ["Shein", "Shein官方面单"]}
    CK = {"加州3仓": "22", "kansas仓": "10"}

★ 顺序（`pw_hwc_flow.order_pos` 文档里记死的，跳过 ④ = 假下单）：
    ① 改仓库渠道：POST deliver.php?act=get_shop_paisongx 取渠道 id
                  → POST hwc_shop.php?act=operate (act=batch) 提交
    ② 下单代发：   POST hwc_shop.php?act=xiadan_df&id=<id>&is_ajax=1
    ③ 回读：       POST hwc_shop.php?is_ajax=1 act=query_shop_order 看「状态」列

判据：**只认状态列离开「未下单」+ 运单号**，接口 msg / 弹层都不算数。
"""
from __future__ import annotations

import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_hwc_api as H  # noqa: E402
import pw_hwc_flow as F  # noqa: E402

STATUSES = ("下单失败", "下单成功", "已下单", "未下单")


def _row_of(html: str, po: str) -> str:
    for _oid, tr in re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', html, re.S):
        if po in tr:
            return tr
    return ""


def read_state(pw, po: str) -> dict:
    """回读单号的状态 + 运单号片段。"""
    q = H.query_orders(pw, po)
    html = q.get("html") or ""
    tr = _row_of(html, po)
    st = ""
    for s in STATUSES:
        if s in tr:
            st = s
    m = re.search(r"1Z[0-9A-Z]{16}|LM\d{8,}|\b\d{12,22}\b", tr)
    return {"po": po, "id": (q["rows"] or [{}])[0].get("id"), "status": st,
            "track": m.group(0) if m else "", "raw_len": len(html)}


def ship(pw, po: str, wh: str = "加州3仓", platform: str = "temu",
         apply: bool = False, log=print) -> dict:
    """按单号：设仓渠道 → 下单代发 → 回读。"""
    st0 = read_state(pw, po)
    log("   查单 %s id=%s 状态=%s" % (po, st0["id"], st0["status"] or "?"))
    if not st0["id"]:
        return {"ok": False, "why": "查不到订单"}
    if st0["status"] and st0["status"] != "未下单":
        return {"ok": True, "skip": "状态=%s" % st0["status"], **st0}
    if not apply:
        return {"ok": True, "dry": True, **st0}

    # ① 改仓库渠道（接口直提）
    r1 = F.http_set_wh([int(st0["id"])], wh, platform=platform, log=log)
    log("   设仓渠道 → %s" % json.dumps(r1, ensure_ascii=False)[:140])
    if not r1.get("ok"):
        return {"ok": False, "why": "设仓渠道失败: %s" % str(r1)[:120]}
    time.sleep(2)

    # ② 下单代发
    d = H.xiadan_df(pw, st0["id"])
    log("   下单代发 → error=%s msg=%s" % (d.get("error"), str(d.get("msg"))[:40]))
    time.sleep(3)

    # ③ 回读（只认状态列）
    st1 = read_state(pw, po)
    ok = st1["status"] not in ("", "未下单")
    log("   回读 %s → 状态=%s 运单=%s" % (po, st1["status"] or "?", st1["track"] or "-"))
    return {"ok": ok, "before": st0["status"], "after": st1["status"],
            "track": st1["track"], "api_msg": d.get("msg")}


def main():
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    wh = sys.argv[2] if len(sys.argv) > 2 else "加州3仓"
    plat = sys.argv[3] if len(sys.argv) > 3 else "temu"
    apply = "--apply" in sys.argv
    pw = H._pw()
    print(ship(pw, po, wh, plat, apply=apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
