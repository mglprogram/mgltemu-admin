# -*- coding: utf-8 -*-
"""扫「下单失败」单（`status=3`）+ 分类可修性，然后**只修能修的**。

★ 2026-11-25 探明：`status=3` 就是「下单失败」筛选（返回 50 条全是失败单）。

对每单：
  ① 抽平台SKU + 当前仓 + 错误里的 Fnsku
  ② 库存页查同款所有 Fnsku（`keywords`）
  ③ 分类：
       SAME_WH      有货且同仓  → 只换 Fnsku
       CROSS_WH     有货但异仓  → **换 Fnsku + 改仓**（用户 2026-11-25 追加口径）
       NEED_RESTOCK 整款无货    → 跳过（需补货）
  ④ `--apply` 时只对 SAME_WH / CROSS_WH 真修

用法：
    python pw_scan_and_fix.py                 # 只分类
    python pw_scan_and_fix.py --apply         # 真修 SAME_WH + CROSS_WH
    python pw_scan_and_fix.py --apply --only-cross   # 只修跨仓的
"""
import json
import re
import sys
import time

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_fix_by_fnsku as FX  # noqa: E402
import pw_fix_with_wh as W  # noqa: E402
import pw_session as SS  # noqa: E402

OUT = r"D:\AI-workplace\TEMU\_logs\pw_fail_plans.json"


def scan_failed(pw, pages: int = 4) -> list:
    """`status=3` 翻页收集下单失败单。"""
    out, seen = [], set()
    for page in range(1, pages + 1):
        body = {"act": "query_shop_order", "deliver_no": "", "shop_id": "",
                "ck_id": "", "status": "3", "start_date": "2026-11-20",
                "end_date": "2026-11-25", "country": "", "shop_types": "",
                "pb_str": "", "page": str(page)}
        for attempt in range(3):
            try:
                d = json.loads(pw.post("hwc_shop.php?is_ajax=1", body))
                break
            except Exception as ex:  # noqa: BLE001
                print("   第 %d 页重试（%s）" % (attempt + 1, str(ex)[:50]))
                time.sleep(5)
        else:
            break
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
                        "err": [x for x in txt.split("收件") if "下单" in x][:1]})
        print("  第 %d 页：%d 行（新 %d）｜累计失败 %d" % (page, len(rows), new, len(out)))
        if new == 0:
            break
    return out


def classify(pw, f: dict) -> dict:
    kw = re.sub(r"^[A-Z]{2,4}-", "", f.get("sku") or "")
    if not kw:
        return {**f, "plan": "UNKNOWN", "why": "无平台SKU"}
    try:
        cands = [c for c in W.stock_candidates(pw, kw) if c["stock_n"] > 0]
    except Exception as ex:  # noqa: BLE001
        return {**f, "plan": "UNKNOWN", "why": "库存查询失败 %s" % str(ex)[:40]}
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
    apply_ = "--apply" in sys.argv
    only_cross = "--only-cross" in sys.argv
    pw = SS.PW()
    pw.login()
    print("扫下单失败单（status=3）…")
    fails = scan_failed(pw)
    print("\n共 %d 单，逐个判可修性：\n" % len(fails))
    plans = []
    for f in fails:
        r = classify(pw, f)
        plans.append(r)
        print("  %-20s %-12s %-14s %s"
              % (r["po"], r["plan"], r.get("target_fnsku") or "-",
                 (r.get("why") or "")[:56]))
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(plans, fp, ensure_ascii=False, indent=1)

    print("\n=== 汇总 ===")
    for pl in ("SAME_WH", "CROSS_WH", "NEED_RESTOCK", "UNKNOWN"):
        n = len([x for x in plans if x["plan"] == pl])
        if n:
            print("  %-14s %d 单" % (pl, n))

    todo = [x for x in plans
            if x["plan"] in (("CROSS_WH",) if only_cross else ("SAME_WH", "CROSS_WH"))]
    if not apply_:
        print("\n[dry-run] 可修 %d 单；加 --apply 才真修" % len(todo))
        return 0
    if not todo:
        print("\n没有可修的单")
        return 0

    print("\n=== 开始修 %d 单 ===" % len(todo))
    ok, bad = [], []
    for x in todo:
        print("\n▶ %s [%s] %s" % (x["po"], x["plan"], x.get("why", "")))
        try:
            r = W.pick_and_fix(pw, x["po"], re.sub(r"^[A-Z]{2,4}-", "",
                                                   x.get("sku") or ""),
                               apply=True, log=print)
        except Exception as ex:  # noqa: BLE001
            bad.append([x["po"], "异常 %s" % str(ex)[:80]])
            continue
        if r.get("ok"):
            ok.append(x["po"])
        else:
            bad.append([x["po"], r.get("why") or r.get("err") or "失败"])
    print("\n=== 结果 ===")
    print("  成功 %d / 失败 %d" % (len(ok), len(bad)))
    for p, why in bad:
        print("    ✗ %-20s %s" % (p, str(why)[:80]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
