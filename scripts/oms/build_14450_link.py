# -*- coding: utf-8 -*-
"""生成 14450 的「帕沃尔参考单号 ↔ 领星入库单号」对照表（一次性，结果入库）。

为什么需要
----------
2026-11-02 起 14450 在货件跟踪里换成帕沃尔参考单号（144503080）当主键，
因为 14450 的领星 OpenAPI 停更了。但用户之前抓过 14450 的领星入库单号
（IB003…），要求把这批号**回溯**到帕沃尔那一列。

好消息：不需要猜。领星 14450 入库单详情里有 `trackingNo` 字段，
装的就是**帕沃尔参考单号**（实测 67 张里 59 张有值且 100% 能在帕沃尔侧找到，
0 个错配）。所以这是一条真实的外键，不是模糊匹配。

残余的 8 张（IB003261024R*，2026-10-24 创建）`trackingNo` 为空 ——
那批是领星停更前最后一批，帕沃尔侧还没回填，只能留空。

产物：scripts/oms/14450_link.json（只有单号，无凭证，可入库）
      { "generated": "...", "byRef": {"144503058": {"omsNo":"IB003261023T3", ...}} }

用法：
    python scripts/oms/build_14450_link.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "oms"))

OUT = ROOT / "scripts" / "oms" / "14450_link.json"


def main() -> int:
    from match_14450 import oms_inbound  # noqa: E402

    rows = oms_inbound()
    by_ref: dict[str, dict] = {}
    no_track = []
    for d in rows:
        ref = str(d.get("trackingNo") or "").strip()
        no = str(d.get("inboundOrderNo") or "")
        if not ref:
            no_track.append(no)
            continue
        by_ref[ref] = {
            "omsNo": no,
            "status": d.get("status"),
            "createTime": str(d.get("orderCreateTime") or ""),
            "receivedTime": str(d.get("receivedEndTime") or ""),
            "shelfTime": str(d.get("shelfEndTime") or ""),
            "qty": sum(int(s.get("quantity") or 0) for s in (d.get("inboundSkuVOList") or [])),
        }

    print(f"① 领星 14450 入库单 {len(rows)} 张")
    print(f"   其中 trackingNo（= 帕沃尔参考单号）有值：{len(by_ref)}")
    print(f"   trackingNo 为空（停更前最后一批，无法回溯）：{len(no_track)}"
          + (f" → {', '.join(no_track[:8])}" if no_track else ""))

    OUT.write_text(json.dumps({
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "_说明": "帕沃尔参考单号 → 领星 14450 入库单。键来自领星 detail.trackingNo，是真实外键。",
        "byRef": by_ref,
        "noTrackingNo": no_track,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"   → {OUT.relative_to(ROOT)}（{len(by_ref)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
