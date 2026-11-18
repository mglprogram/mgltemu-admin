# -*- coding: utf-8 -*-
"""headmile_eta.py —— 开预售（头程）口径：到港日 → 送仓上架日。

用户 2026-11-18 给的新逻辑（原话）
----------------------------------
「开预售（头程）的新逻辑：到港时间-送仓上架-帕沃尔运单号（乐航）：
  11.15（周天）→ 最晚 21 号（周六）
  11.16（周一）→ 最晚 24（下周二）
  11.17（周二）→ 最晚 27（下周五）；
  每个乐航的美森、以星、盐田渠道送仓时间跟周几到港有关，如果看到到港时间，
  但是也要看有没有被查验，如果被查验（美国海关/国内海关查验至少两个星期放行，
  放行才能开预售）」

⇒ 落地成两条规则：

  ① **送仓上架日 = 到港日之后、该渠道固定的送仓星期那一天**
       美森 → 周六   以星 → 周二   盐田 → 周五
     三个例子正好对上：11.15 周日到（美森）→ 11.21 周六 (+6)；
                       11.16 周一到（以星）→ 11.24 下周二 (+8)；
                       11.17 周二到（盐田）→ 11.27 下周五 (+10)。
     「最晚 X 号」= 上架日：再晚开预售，货到了却没有预售窗口（平台预售 7 天后自动关）。

  ② **被查验 → 至少两周放行，放行之后才可能上架**
       轨迹里出现【查验】而没有【放行】：从查验日 + 14 天起算，再取该渠道的送仓日；
       已经【放行】：从放行日起算，取该渠道的送仓日（放行当天正好是送仓日则当天）。

配置在 `config/headmile_eta.json`（渠道→星期、查验天数、关键词都能改，代码不写死）。

为什么不再用「到港 + 渠道历史中位天数」：中位数把大量**查验卡住**的单混在一起
（以星 n=163 中位 10 天、p80 14 天），算出来的上架日系统性偏晚，
于是「7 天内能不能上架」这条判断整片判错。

用法
----
    from headmile_eta import shelf_eta, eta_from_row
    shelf_eta(date(2026, 11, 15), "美森")                       # → 2026-11-21
    shelf_eta(date(2026, 11, 17), "盐田")                       # → 2026-11-27
    eta_from_row(track_row)                                     # 直接给头程跟踪的一行
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "headmile_eta.json"

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]  # ISO: 周一=1 … 周日=7


def load_config(path: Path | None = None) -> dict:
    p = Path(path) if path else CONFIG
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cfg = {}
    cfg.setdefault("channel_shelf_weekday", {"美森": 6, "以星": 2, "盐田": 5})
    cfg.setdefault("unknown_lead_days", 10)
    cfg.setdefault("inspection_release_days", 14)
    cfg.setdefault("inspection_keywords", ["查验"])
    cfg.setdefault("release_keywords", ["放行"])
    return cfg


def _d(s) -> date | None:
    t = str(s or "").strip()[:10]
    if not t:
        return None
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except ValueError:
        return None


def next_week_weekday(d: date, iso_weekday: int) -> date:
    """`d` 所在自然周（周一起算）的**下一周**、星期 `iso_weekday` 的那一天。

    用户给的三条都对得上（见模块 docstring）：
        11.15 周日（第 46 周）→ 11.21 周六（第 47 周）  = 美森 +6
        11.16 周一（第 47 周）→ 11.24 下周二（第 48 周）= 以星 +8
        11.17 周二（第 47 周）→ 11.27 下周五（第 48 周）= 盐田 +10
    ⇒ 乐航的送仓是**按周排班**：本周到港的赶不上本周的送仓，一律排到下周的那一班。
    """
    monday = d - timedelta(days=d.isoweekday() - 1)
    return monday + timedelta(days=7 + (int(iso_weekday) - 1))


def inspection_of(traces: list, cfg: dict | None = None) -> tuple[bool, date | None, date | None]:
    """从轨迹里读查验状态。返回 (被查验, 查验日, 放行日)。

    · 只看**最新一段**：查验之后又放行了，就算已放行。
    · 「已于11/16报关放行」这种**报关**放行不算海关查验放行（报关是出货前的正常动作），
      所以只有出现过「查验」关键词才进入查验分支。
    """
    cfg = cfg or load_config()
    kw_insp = tuple(cfg["inspection_keywords"])
    kw_rel = tuple(cfg["release_keywords"])
    inspected = False
    insp_at: date | None = None
    rel_at: date | None = None
    for t in traces or []:
        info = str(t.get("info") or "")
        day = _d(t.get("time"))
        if any(k in info for k in kw_insp):
            if not inspected or (day and (not insp_at or day < insp_at)):
                inspected = True
                insp_at = insp_at or day
            # 查验节点里带「放行」也算放行（「查验后已放行」）
        if inspected and any(k in info for k in kw_rel):
            if day and (not rel_at or day > rel_at):
                rel_at = day
    return inspected, insp_at, rel_at


def shelf_eta(arrive: date | None, channel: str = "", traces: list | None = None,
              cfg: dict | None = None) -> dict:
    """到港日 → 送仓上架日。返回 dict（见下）。

    eta      上架日（date）；没有到港日 → None
    days     eta − arrive（天）
    basis    口径说明（页面「预测/上架」的副标题直接用）
    inspected / inspectAt / releaseAt   查验三件套
    """
    cfg = cfg or load_config()
    out = {"eta": None, "days": None, "basis": "", "inspected": False,
           "inspectAt": None, "releaseAt": None}
    if not arrive:
        return out

    ch = str(channel or "").strip()
    weekday = cfg["channel_shelf_weekday"].get(ch)
    inspected, insp_at, rel_at = inspection_of(traces or [], cfg)
    out.update({"inspected": inspected, "inspectAt": insp_at, "releaseAt": rel_at})

    if not weekday:
        # 渠道没落上（「未知」）：不猜星期，退回粗口径「到港 + 未知渠道天数」
        eta = arrive + timedelta(days=int(cfg["unknown_lead_days"]))
        out.update({"eta": eta, "days": (eta - arrive).days,
                    "basis": "到港·渠道未知 +%d 天" % cfg["unknown_lead_days"]})
        return out

    if inspected:
        extra = int(cfg["inspection_release_days"])
        if rel_at:
            base = rel_at
            why = "%s 海关放行" % rel_at.isoformat()
        else:
            base = (insp_at or arrive) + timedelta(days=extra)
            why = "查验未放行：%s +%d 天" % ((insp_at or arrive).isoformat(), extra)
        eta = next_week_weekday(base, weekday)
        out.update({"eta": eta, "days": (eta - arrive).days,
                    "basis": "到港查验 → %s → %s送仓上架"
                             % (why, WEEKDAY_CN[int(weekday) - 1])})
        return out

    eta = next_week_weekday(arrive, weekday)
    out.update({"eta": eta, "days": (eta - arrive).days,
                "basis": "到港 + %d 天（%s送仓上架）"
                         % ((eta - arrive).days, WEEKDAY_CN[int(weekday) - 1])})
    return out


def eta_from_row(row: dict, cfg: dict | None = None) -> dict:
    """头程跟踪（shipment-track.json）的一行 → shelf_eta 结果。

    已上架（shelfTime 有值）直接返回真实上架日；没有到港日返回 eta=None（调用方自己兜底）。
    """
    cfg = cfg or load_config()
    if not row:
        return shelf_eta(None, "", None, cfg)
    done = _d(row.get("shelfTime"))
    if done:
        return {"eta": done, "days": None, "basis": "已上架", "inspected": False,
                "inspectAt": None, "releaseAt": None}
    traces = ((row.get("logistics") or {}).get("traces")) or []
    return shelf_eta(_d(row.get("arriveDate")), str(row.get("channel") or ""), traces, cfg)
