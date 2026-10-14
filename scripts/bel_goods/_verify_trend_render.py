# -*- coding: utf-8 -*-
"""临时：用 6 天模拟数据验证趋势区块（折线/排行/状态序列）能正确渲染。

用法：python _verify_trend_render.py
会先备份真实 trend_data.json，验证完自动恢复。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"D:\AI-workplace\TEMU")
TREND = ROOT / "data" / "trend_data.json"
HTML = ROOT / "data" / "temu_dashboard.html"
BAK = ROOT / "data" / "_trend_verify_bak.json"

DAYS = ["2026-10-04", "2026-10-05", "2026-10-06",
        "2026-10-07", "2026-10-08", "2026-10-09"]
VALS = [41, 38, 44, 39, 35, 33]
HITS = [1, 2, 0, 2, 3, 2]


def make_fake(src):
    tl = []
    for store, vals, hits in (
            ("BEL", [41, 38, 44, 39, 35, 33], [1, 2, 0, 2, 3, 2]),
            ("BHY", [34, 36, 30, 33, 31, 34], [1, 1, 2, 2, 1, 2])):
        for d, v, h in zip(DAYS, vals, hits):
            tl.append({"checked_at": f"{d} 08:00:00", "day": d, "store": store,
                       "limited_total": v, "hit_count": h,
                       "flow": {"流量预计100%+": v // 2, "流量预计40%+": v // 3,
                                "流量预计60%+": 2}})
    rec = [{"sku_code": f"BEL-TEST-{i:02d}", "spu_id": f"900000000{i}",
            "appear": 6 - i, "days": 6 - i,
            "first_seen": DAYS[0], "last_seen": DAYS[-1],
            "flow_status": "流量预计100%+", "sales7": i,
            "declare_price": 20 + i, "target_price": 12 + i}
           for i in range(1, 13)]
    seq = [{"at": f"{d} 08:00:00", "limited": (i % 3 == 0)}
           for i, d in enumerate(DAYS)]
    ft = [{"store": st, "spu": s, "checks": 6, "hit_times": 3, "first": DAYS[0],
           "last": DAYS[-1], "current": True, "timeline": seq}
          for st, s in (("BEL", "1249844154"), ("BEL", "5745761504"),
                        ("BHY", "1289576127"), ("BHY", "7235257044"))]
    return {"generated_at": src["generated_at"], "snapshot_count": len(tl),
            "distinct_days": 6, "days": DAYS,
            "first_check": DAYS[0] + " 08:00:00", "last_check": DAYS[-1] + " 08:00:00",
            "timeline": tl, "recurring": rec, "focus_track": ft,
            "resolved": [{"store": "BEL", "sku_code": "BEL-FIXED-01",
                          "spu_id": "888", "flow_status": "流量预计60%+",
                          "cleared_at": DAYS[-1] + " 08:00:00"}]}


def render():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "bel_goods" / "build_dashboard.py")],
                   check=True, capture_output=True)
    h = HTML.read_text(encoding="utf-8")
    return {
        "chart_divs": h.count('class="chart"'),
        "opt_defs": h.count("window.__OPT_"),
        "chart_opt_matched": h.count('class="chart"') > 0
                             and h.count('class="chart"') == h.count("window.__OPT_"),
        "echarts_inlined": "echarts" in h and len(h) > 500_000,
        "degraded_msg_gone": "折线趋势需至少 2 天" not in h,
        "resolved_block": "已解除限流" in h,
        "div_balanced": h.count("<div") - h.count("</div>") == 0,
        "table_balanced": h.count("<table") - h.count("</table>") == 0,
        "both_stores_charted": ("BEL 店 · 每日限流商品总数走势" in h
                                and "BHY 店 · 每日限流商品总数走势" in h),
        "init_script": "echarts.init" in h,
    }


def main():
    src = json.loads(TREND.read_text(encoding="utf-8"))
    shutil.copy2(TREND, BAK)
    try:
        TREND.write_text(json.dumps(make_fake(src), ensure_ascii=False), encoding="utf-8")
        r = render()
        print("=== 模拟 6 天趋势渲染检查 ===")
        for k, v in r.items():
            print(f"  {k}: {v}")
        ok = (r["chart_opt_matched"] and r["echarts_inlined"] and r["init_script"]
              and r["degraded_msg_gone"] and r["div_balanced"] and r["table_balanced"]
              and r["both_stores_charted"])
        print("\n结论:", "通过" if ok else "有问题")
        return 0 if ok else 1
    finally:
        shutil.copy2(BAK, TREND)
        BAK.unlink(missing_ok=True)
        render()
        print("已恢复真实趋势数据并重建面板")


if __name__ == "__main__":
    raise SystemExit(main())