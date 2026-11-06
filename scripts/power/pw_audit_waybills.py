# -*- coding: utf-8 -*-
"""pw_audit_waybills.py — 逐单核对帕沃尔运单号是否**真的存在**（只读，判据=页面命中）。

为什么要这个脚本（2026-11-06 用户令「逐单核对」）：
    子代理交了一份 124 行「全部有运单号」的 CSV，不能只信交付清单，必须回平台页面核。

★ 2026-11-06 我自己在这里翻过一次车，必须记住：
  第一版按 `deliver.php?act=list&pb=2&deliver_no=<单号>` 逐单搜，
  124 单里 74 单判 `not_found`，我据此说「出库单没生成」——**是错的**。
  两个真因：
    ① `deliver_no` 这个 **GET 参数不生效**（它只在页面表单 POST `searchOrder()` 里有用），
       请求返回的是默认第 1 页；
    ② pb=2 有 1079 条、每页几十条，**不翻页**就只看到第一屏。
  验证方式：`pb=2&page_size=300` 一次扫全量 → 74/74 全在，状态=待打包。
  正解：用 `pw_index_deliver.py` 建全量索引，再拿索引对号（快、不漏）。

判据（查索引，每单独立判）：
    ① 在 pb=2（待打包）且页面运单号 == 清单运单号 → ok_pending
    ② 在 pb=2 但运单号对不上                    → mismatch（记页面实际号）
    ③ 在 pb=2 但页面还没有运单号                 → pending_no_wb
    ④ 只在 pb=1（待确认）                        → still_pending_confirm
    ⑤ 两个页签都查不到                           → not_found（**极可能真没出库单**）

用法：
    python scripts/power/pw_audit_waybills.py --csv <清单.csv> --report
    python scripts/power/pw_audit_waybills.py --csv <清单.csv> --reindex   # 强制重建索引
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "pw_orders", "lib"))
sys.path.insert(0, _HERE)

OUT = os.path.join(_HERE, "out", time.strftime("%Y-%m-%d"))
WB_PAT = (r"(1Z[0-9A-Z]{10,}|GFUS[0-9A-Z]{6,}|SPX[0-9A-Z]{6,}|SWX[0-9A-Z]{6,}"
          r"|YWE[0-9A-Z]{6,}|\d{12,22})")


def judge(rec: dict, want: str) -> dict:
    """纯函数：按索引里的一条记录 + 清单运单号定档。"""
    if not rec:
        return {"verdict": "not_found", "page_wb": ""}
    pb = str(rec.get("pb") or "")
    wb = (rec.get("wb") or "").strip()
    want = (want or "").strip()
    if pb == "1":
        return {"verdict": "still_pending_confirm", "page_wb": wb}
    if not wb:
        return {"verdict": "pending_no_wb", "page_wb": ""}
    if want and re.search(WB_PAT, want) and wb != want:
        return {"verdict": "mismatch", "page_wb": wb}
    return {"verdict": "ok_pending", "page_wb": wb}


def main() -> int:
    ap = argparse.ArgumentParser(description="逐单核对帕沃尔运单号（只读）")
    ap.add_argument("--csv", required=True, help="待核对清单 csv（需含 参考单号/运单号 列）")
    ap.add_argument("--index", default="", help="索引 json（默认 out/<日期>/pw_deliver_index.json）")
    ap.add_argument("--reindex", action="store_true", help="先重建索引再核对")
    ap.add_argument("--report", action="store_true", help="落 csv + 打印汇总")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    with open(a.csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    print("待核对 %d 单（来源 %s）" % (len(rows), os.path.basename(a.csv)))

    ipath = a.index or os.path.join(OUT, "pw_deliver_index.json")
    if a.reindex or not os.path.exists(ipath):
        import pw_session as S  # noqa: PLC0415
        import pw_index_deliver as I  # noqa: PLC0415
        pw = S.PW()
        pw.login()
        idx = I.build(pw)
        os.makedirs(os.path.dirname(os.path.abspath(ipath)), exist_ok=True)
        with open(ipath, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
        print("索引已重建：%d 单 → %s" % (len(idx), ipath))
    else:
        with open(ipath, encoding="utf-8") as f:
            idx = json.load(f)
        print("用索引 %s（%d 单）" % (os.path.basename(ipath), len(idx)))

    out = []
    for r in rows:
        po = (r.get("参考单号") or "").strip()
        if not po:
            continue
        j = judge(idx.get(po) or {}, r.get("运单号") or "")
        out.append(dict(r, 核对结果=j["verdict"], 页面运单号=j["page_wb"]))

    from collections import Counter
    c = Counter(x["核对结果"] for x in out)
    print("\n=== 汇总 ===")
    for k in ("ok_pending", "mismatch", "pending_no_wb",
              "still_pending_confirm", "not_found"):
        if c.get(k):
            print("  %-24s %d" % (k, c[k]))
    for k in ("mismatch", "pending_no_wb", "still_pending_confirm", "not_found"):
        for x in [y for y in out if y["核对结果"] == k][:8]:
            print("  [%s] %-26s 清单=%s 页面=%s"
                  % (k, x["参考单号"], x.get("运单号") or "-", x["页面运单号"] or "-"))

    if a.report:
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, "逐单核对结果_%s.csv" % time.strftime("%H%M%S"))
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        print("\n已写:", p)
        bad = [x for x in out if x["核对结果"] != "ok_pending"]
        if bad:
            pb = os.path.join(OUT, "核对不通过_%s.csv" % time.strftime("%H%M%S"))
            with open(pb, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(bad[0].keys()))
                w.writeheader()
                w.writerows(bad)
            print("不通过 %d 单 →" % len(bad), pb)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
