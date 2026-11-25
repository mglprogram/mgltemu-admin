# -*- coding: utf-8 -*-
"""扫今天所有「下单失败」单，找出**跨仓可修**的（换 Fnsku + 改仓 就能下的）。

判定：
  ① 订单状态 = 下单失败，错误里能抽到 `sku:<Fnsku>仓库没有库存`
  ② 用平台SKU 去库存页查同款所有 Fnsku
  ③ 若存在「有货」的 Fnsku：
       仓库 == 订单当前仓  → SAME_WH  （只换 Fnsku）
       仓库 != 订单当前仓  → CROSS_WH （换 Fnsku + 改仓）  ← 用户 2026-11-25 说的那种
     都不满足           → NEED_RESTOCK
只读，不改任何单。
"""
import json
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_fix_by_fnsku as FX  # noqa: E402
import pw_fix_with_wh as W  # noqa: E402
import pw_session as SS  # noqa: E402

STATUSES = ("下单失败", "已下单", "未下单", "已取消")


def list_failed(pw, pages: int = 6) -> list:
    """翻页收集所有「下单失败」单。"""
    out, seen = [], set()
    for page in range(1, pages + 1):
        body = {"act": "query_shop_order", "deliver_no": "", "shop_id": "",
                "ck_id": "", "status": "", "start_date": "2026-11-20",
                "end_date": "2026-11-25", "country": "", "shop_types": "",
                "pb_str": "", "page": str(page)}
        d = json.loads(pw.post("hwc_shop.php?is_ajax=1", body))
        c = d.get("content") or ""
        rows = re.findall(r'<tr class="tr_html_(\d+)[^"]*">(.*?)</tr>', c, re.S)
        if not rows:
            break
        new = 0
        for oid, tr in rows:
            if oid in seen:
                continue
            seen.add(oid)
            new += 1
            if "下单失败" not in tr:
                continue
            txt = FX.clean(tr)
            po = re.search(r"(PO-\d+-\d+|[A-Z]{2,3}\d[A-Z0-9]{6,})", txt)
            sku = re.search(r"平台SKU\s*[:：]\s*(\S+)", txt)
            wh = re.search(r"发货仓库\s*[:：]\s*(\S*)", txt)
            fn = re.search(r"sku:([A-Z0-9]+)仓库没有库存", txt)
            out.append({"id": oid, "po": po.group(1) if po else "",
                        "sku": sku.group(1) if sku else "",
                        "wh": (wh.group(1) if wh else "").strip(),
                        "cur_fnsku": fn.group(1) if fn else "",
                        "err": txt[:100]})
        print("  第 %d 页：%d 行（新 %d）｜累计失败单 %d"
              % (page, len(rows), new, len(out)))
        if new == 0:
            break
    return out


def classify(pw, f: dict) -> dict:
    kw = re.sub(r"^[A-Z]{2,4}-", "", f.get("sku") or "")
    if not kw:
        return {**f, "plan": "UNKNOWN", "why": "无平台SKU"}
    cands = [c for c in W.stock_candidates(pw, kw) if c["stock_n"] > 0]
    if not cands:
        return {**f, "plan": "NEED_RESTOCK", "why": "整款无货"}
    best = max(cands, key=lambda x: (x["stock_n"], x["wh"] == f.get("wh")))
    plan = "SAME_WH" if best["wh"] == f.get("wh") else "CROSS_WH"
    return {**f, "plan": plan, "target_fnsku": best["fnsku"],
            "target_seller": best["seller"], "target_wh": best["wh"],
            "stock": best["stock"],
            "why": "%s → %s（%s，库存 %s）"
                   % (f.get("wh") or "?", best["wh"], best["seller"], best["stock"])}


def main():
    pw = SS.PW()
    pw.login()
    print("扫今天失败单…")
    fails = list_failed(pw)
    print("\n失败单 %d 个，逐个判可修性：" % len(fails))
    out = []
    for f in fails:
        r = classify(pw, f)
        out.append(r)
        print("  %-26s %-5s %-22s %s"
              % (r["po"], r["plan"], r.get("target_fnsku") or "-", r.get("why", "")[:60]))

    print("\n=== 汇总 ===")
    for plan in ("SAME_WH", "CROSS_WH", "NEED_RESTOCK", "UNKNOWN"):
        n = len([x for x in out if x["plan"] == plan])
        if n:
            print("  %-14s %d 单" % (plan, n))
    with open(r"D:\AI-workplace\TEMU\_logs\pw_fail_plans.json", "w",
              encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1)
    print("\n明细 → D:\\AI-workplace\\TEMU\\_logs\\pw_fail_plans.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
