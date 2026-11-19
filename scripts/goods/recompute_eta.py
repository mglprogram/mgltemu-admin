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


def samples_from_doc(doc: dict, cfg: dict) -> list[dict]:
    """从快照里挑「到港 → 送仓」的实测样本：真的到港 + 已入库 + 轨迹里没有查验。

    ⚠ 快照只留「在途/待催」，已上架满 3 天的早被 prune 清掉，所以这里样本通常很少；
    真正的样本量要靠 fetch_shipment_track.py 那次全量抓取（180 天窗口）。
    """
    out = []
    for r in doc.get("rows") or []:
        if str(r.get("source") or "") != "帕沃尔头程":
            continue
        arrive = _d(r.get("arriveDate"))
        if not arrive or "forecast" in str(r.get("arriveSource") or ""):
            continue
        wh = _d(r.get("shelfTime"))
        traces = ((r.get("logistics") or {}).get("traces")) or []
        days = HE.lead_days_of(arrive, wh, traces, cfg)
        if days is None:
            continue
        out.append({"channel": str(r.get("channel") or ""), "days": days})
    return out


def recompute(doc: dict, cfg: dict, today: date) -> tuple[list[dict], dict]:
    """就地改 rows，返回 ([{no, channel, arrive, old, new, basis, days}], 送仓样本统计)。

    ★ 2026-11-19 口径（用户第三次）：两个账号**同一套规则** ——
      上架日 = 到港日下一周的渠道送仓星期（美森周六 / 以星周二 / 盐田周五），
      美国海关查验的从放行日（或查验 + 14 天）起算再排班次。
      之前帕沃尔用「未查验实测中位天数」的那套作废，所以这里不再按 source 分支，
      统一走 HE.eta_from_row（内部就是 shelf_eta）。
      已上架（shelfTime 有值）→ 原值不动。
    """
    stats = HE.lead_stats(samples_from_doc(doc, cfg), cfg)   # 仅用于「偏差参考」，不参与计算
    pw_lead = stats
    changes = []
    for r in doc.get("rows") or []:
        # 已上架的票不重算：页面这一列显示的是 shelfTime（实际上架），
        # 把 predictedShelf 也改成同一天只会白刷一堆行。
        if _d(r.get("shelfTime")):
            continue
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
    return changes, pw_lead


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 public/data/shipment-track.json")
    ap.add_argument("--limit-print", type=int, default=12)
    a = ap.parse_args()

    doc = json.loads(PUBLIC_JSON.read_text(encoding="utf-8"))
    cfg = HE.load_config()
    today = date.today()
    ch_out, stats = recompute(doc, cfg, today)

    print("口径（只看到港星期，跟渠道无关）：%s"
          % " · ".join(f"周{k}到→下周{'一二三四五六日'[int(v)-1]}"
                       for k, v in cfg["arrive_weekday_shelf_weekday"].items()))
    print("查验：只有美国海关查验才推后，未放行按 +%d 天起算（出口/国内查验不算）"
          % cfg["inspection_release_days"])
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
        doc["pwLead"] = stats
        doc["etaRule"] = {
            "note": "上架日只看**周几到港**（与渠道无关）：周一到→下周二，周二到→下周五，"
                    "周三/四/五/六/日到→下周六；只有美国海关（国外）查验才推后两周，"
                    "已放行从放行日起算；出口/国内查验不算（船期已含）",
            "pwLead": stats,
            "arriveWeekdayShelfWeekday": cfg["arrive_weekday_shelf_weekday"],
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
