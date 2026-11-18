# -*- coding: utf-8 -*-
"""recompute_eta.py —— 用**新的头程口径**重算 shipment-track.json 的「预测/上架」，不重新抓数。

为什么要这个脚本
----------------
`fetch_shipment_track.py` 要连领星 OMS + 恒信/乐航查件，跑一次几分钟且依赖网络。
改了「到港 → 送仓上架」口径之后，只想把**已有快照**按新口径重算一遍，
不重抓（也免得抓数过程中接口抖动导致整份文件缺行）。

口径（用户 2026-11-18，见 config/headmile_eta.json + headmile_eta.py）
--------------------------------------------------------------------
    上架日 = 到港日所在周的**下一周**、该渠道固定送仓星期那一天
             美森 → 周六 · 以星 → 周二 · 盐田 → 周五
    被查验（海关/国内）→ 至少 14 天放行，从查验日（或放行日）起算再排送仓
    没有到港节点 → 保持原值（OMS 预计到货 / 帕沃尔下单 + 渠道天数）
    已上架（shelfTime 有值）→ 保持原值

用法
----
    python scripts/goods/recompute_eta.py            # 只看会变多少（dry-run）
    python scripts/goods/recompute_eta.py --apply    # 写回 JSON
    python scripts/goods/recompute_eta.py --apply --limit-print 20
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_JSON = ROOT / "temu-admin" / "public" / "data" / "shipment-track.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import headmile_eta as HE  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ON_SHELF = (3, 4, 5)          # 已收货 / 已上架 / 已取消：不再算超期


def _d(s) -> date | None:
    t = str(s or "").strip()[:10]
    if not t:
        return None
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except ValueError:
        return None


def recompute(doc: dict, cfg: dict, today: date) -> list[dict]:
    """就地改 rows，返回 [{no, channel, arrive, old, new, basis, days}]。"""
    changes = []
    for r in doc.get("rows") or []:
        e = HE.eta_from_row(r, cfg)
        new = e.get("eta")
        if not new:
            continue
        old = _d(r.get("predictedShelf"))
        old_basis = r.get("predictedBasis") or ""
        arrive = _d(r.get("arriveDate"))
        # 日期或口径变了都要写（口径文字改了也得刷新，否则页面还是老说明）
        if old == new and old_basis == (e.get("basis") or ""):
            continue
        r["predictedShelf"] = new.isoformat()
        r["predictedBasis"] = e.get("basis") or old_basis
        if arrive:
            r["leadDays"] = (new - arrive).days
        # 超期 / 已到港未上架：按新日期重算（口径与 fetch 一致）
        st = r.get("status")
        issues = [x for x in (r.get("issues") or [])
                  if not (x.startswith("超期") or x == "已到港未上架")]
        overdue = (today - new).days if (arrive and st not in ON_SHELF) else 0
        if arrive and st in (1, 2):
            issues.append("超期 +%d天" % overdue if overdue > 0 else "已到港未上架")
        r["issues"] = issues
        r["overdueDays"] = overdue if overdue > 0 else 0
        changes.append({"no": r.get("inboundNo"), "channel": r.get("channel"),
                        "arrive": r.get("arriveDate") or "",
                        "old": old.isoformat() if old else "",
                        "new": new.isoformat(), "basis": r["predictedBasis"],
                        "days": r.get("leadDays")})

    # summary 的「超期」计数跟着重算
    sm = doc.get("summary")
    if isinstance(sm, dict):
        waiting = [x for x in (doc.get("rows") or []) if x.get("status") in (1, 2)]
        sm["overdue"] = sum(1 for x in waiting if int(x.get("overdueDays") or 0) > 0)
        sm["issues"] = sum(1 for x in (doc.get("rows") or []) if x.get("issues"))
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 public/data/shipment-track.json")
    ap.add_argument("--limit-print", type=int, default=12)
    a = ap.parse_args()

    doc = json.loads(PUBLIC_JSON.read_text(encoding="utf-8"))
    cfg = HE.load_config()
    today = date.today()
    ch_out = recompute(doc, cfg, today)

    print("口径：%s" % " · ".join(f"{k}→周{v}" for k, v in cfg["channel_shelf_weekday"].items()))
    print("查验：未放行 +%d 天起算（查验 → 放行 → 送仓）" % cfg["inspection_release_days"])
    print("行数 %d，会变 %d 行" % (len(doc.get("rows") or []), len(ch_out)))
    for c in ch_out[: a.limit_print]:
        print("  %-16s %-4s 到港 %-11s %-11s → %-11s  %s"
              % (c["no"], c["channel"] or "—", c["arrive"][:10], c["old"] or "—",
                 c["new"], c["basis"]))
    if len(ch_out) > a.limit_print:
        print("  … 另有 %d 行" % (len(ch_out) - a.limit_print))

    if a.apply:
        if not ch_out:
            print("没有变化，不写文件")
            return 0
        doc["etaRule"] = {
            "note": "到港 → 送仓上架：按渠道固定送仓星期（乐航按周排班），查验至少 14 天放行",
            "channelShelfWeekday": cfg["channel_shelf_weekday"],
            "inspectionReleaseDays": cfg["inspection_release_days"],
            "recomputedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = PUBLIC_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PUBLIC_JSON)
        print("已写回 %s" % PUBLIC_JSON)
    else:
        print("（dry-run，加 --apply 写回）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
