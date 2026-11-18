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

  ③ **2026-11-18 当天第二次改口径（只针对帕沃尔那批）**，用户原话：
    「预测上架的值根据我今天给的帕沃尔单号到港后，**未被查验送仓时间**去预测，
      而不是写预测上架时间下写文字描述，而是写大概送仓时间；
      恒信单号的到港送仓上架时间明天给你」
    ⇒ 帕沃尔（`source == "帕沃尔头程"`）不再按送仓星期排，改用**实测样本**：
      取近 180 天帕沃尔单里「真的到港 + 已有海外仓入库时间 + 轨迹里没有查验」的票，
      算 (海外仓入库日 − 到港日)，按渠道取中位天数（样本 < lead_min_samples 退回全体中位，
      再不够用 pw_default_lead_days）。页面这一列只写「大概送仓 YYYY-MM-DD」。
      恒信（LM739）暂时仍走上面的送仓星期口径，等用户给到港→送仓→上架的实际时间。

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
    # 帕沃尔「到港 → 送仓」实测样本的过滤与兜底（2026-11-18 第二次改口径）
    cfg.setdefault("lead_min_samples", 3)
    cfg.setdefault("lead_min_days", 0)
    cfg.setdefault("lead_max_days", 90)
    cfg.setdefault("pw_default_lead_days", 10)
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

    ⚠ 两条真机踩过的坑（2026-11-18 用户指出「预测上架不准」时查出来的）：

    1. **「报关放行」不是海关查验放行**。轨迹里 `【报关】已于11/16报关放行` 是出货前的
       正常动作（还在国内、船都没开），把它当放行会把「查验」直接判成已放行。
       所以匹配放行前先排除带「报关 / 清关」字样的节点。
    2. **放行必须晚于查验**。轨迹是「新的在前」，查验节点可能比报关放行更新，
       原来的写法会取到比查验更早的放行日（实测 到港 11-13、查验 11-xx、放行却写 10-21）。
    """
    cfg = cfg or load_config()
    kw_insp = tuple(cfg["inspection_keywords"])
    kw_rel = tuple(cfg["release_keywords"])
    skip_rel = tuple(cfg.get("release_ignore_keywords") or ["报关", "清关"])
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
        # 同一个节点可能同时写「查验放行」，所以这里不 continue
        if inspected and any(k in info for k in kw_rel) and not any(k in info for k in skip_rel):
            if day and (not insp_at or day >= insp_at) and (not rel_at or day > rel_at):
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
                    "basis": "到港·渠道未知"})
        return out

    if inspected:
        extra = int(cfg["inspection_release_days"])
        if rel_at:
            base = rel_at
            why = "%s 海关放行" % rel_at.isoformat()
        else:
            base = (insp_at or arrive) + timedelta(days=extra)
            why = "查验 %s 起算两周" % (insp_at or arrive).isoformat()
        eta = next_week_weekday(base, weekday)
        out.update({"eta": eta, "days": (eta - arrive).days,
                    "basis": "到港·查验 → %s → 周%s送仓"
                             % (why, WEEKDAY_CN[int(weekday) - 1][-1])})
        return out

    eta = next_week_weekday(arrive, weekday)
    out.update({"eta": eta, "days": (eta - arrive).days,
                "basis": "到港·周%s送仓" % WEEKDAY_CN[int(weekday) - 1][-1]})
    return out


def eta_from_row(row: dict, cfg: dict | None = None) -> dict:
    """头程跟踪（shipment-track.json）的一行 → shelf_eta 结果。

    已上架（shelfTime 有值）直接返回真实上架日；没有到港日返回 eta=None（调用方自己兜底）。

    ★ 2026-11-18 当天第二次改口径：帕沃尔头程那批（`source == "帕沃尔头程"`）的
    「大概送仓日」是**用近 180 天未查验样本的实测中位天数**算出来的，不是按送仓星期，
    所以这里直接读快照里 `predictedShelf`，别再用 shelf_eta 重算一遍（会跟页面不一致）。
    """
    cfg = cfg or load_config()
    if not row:
        return shelf_eta(None, "", None, cfg)
    done = _d(row.get("shelfTime"))
    if done:
        return {"eta": done, "days": None, "basis": "已上架", "inspected": False,
                "inspectAt": None, "releaseAt": None}
    traces = ((row.get("logistics") or {}).get("traces")) or []
    arrive = _d(row.get("arriveDate"))
    if str(row.get("source") or "") == "帕沃尔头程":
        eta = _d(row.get("predictedShelf"))
        if eta:
            inspected, insp_at, rel_at = inspection_of(traces, cfg)
            return {"eta": eta,
                    "days": ((eta - arrive).days if arrive else None),
                    "basis": str(row.get("predictedBasis") or "送仓"),
                    "inspected": inspected, "inspectAt": insp_at, "releaseAt": rel_at}
    return shelf_eta(arrive, str(row.get("channel") or ""), traces, cfg)


# --------------------------------------------------------------------------- 帕沃尔：实测送仓天数
def lead_days_of(arrive: date | None, delivered: date | None,
                 traces: list | None, cfg: dict | None = None) -> int | None:
    """**实测**的「到港 → 送仓」天数；被查验的票不算样本。

    用户 2026-11-18 原话：「预测上架的值根据我今天给的帕沃尔单号到港后，
    **未被查验**送仓时间去预测」⇒ 只统计「真的到港 + 已有海外仓入库时间 +
    轨迹里没有查验节点」的票。
    """
    cfg = cfg or load_config()
    if not arrive or not delivered:
        return None
    inspected, _, _ = inspection_of(traces or [], cfg)
    if inspected:
        return None
    d = (delivered - arrive).days
    if d < int(cfg.get("lead_min_days", 0)) or d > int(cfg.get("lead_max_days", 90)):
        return None
    return d


def lead_stats(samples: list | None, cfg: dict | None = None) -> dict:
    """样本 → 送仓天数统计（按渠道 + 全体）。样本形如 `[{"channel": "以星", "days": 7}]`。"""
    cfg = cfg or load_config()
    min_n = int(cfg.get("lead_min_samples", 3))
    by_ch: dict[str, list[int]] = {}
    allv: list[int] = []
    for s in samples or []:
        d = s.get("days")
        if d is None:
            continue
        v = int(d)
        allv.append(v)
        by_ch.setdefault(str(s.get("channel") or ""), []).append(v)

    def stat(vals: list[int]) -> dict:
        vals = sorted(vals)
        n = len(vals)
        if not n:
            return {"n": 0, "median": None, "p80": None, "min": None, "max": None}
        med = float(statistics_median(vals))
        p80 = vals[min(n - 1, int(round(0.8 * (n - 1))))]
        return {"n": n, "median": round(med, 1), "p80": p80, "min": vals[0], "max": vals[-1]}

    return {
        "overall": stat(allv),
        "channels": {k: stat(v) for k, v in sorted(by_ch.items())},
        "minN": min_n,
        "note": "到港→送仓 的实测中位天数（只算未查验的票；样本取自帕沃尔头程近 180 天单）",
    }


def statistics_median(vals: list[int]) -> float:
    v = sorted(vals)
    n = len(v)
    if not n:
        return 0.0
    m = n // 2
    return float(v[m]) if n % 2 else (v[m - 1] + v[m]) / 2.0


def lead_days_for(channel: str, stats: dict | None, cfg: dict | None = None) -> tuple[int, str]:
    """某渠道该用几天：渠道样本够 → 渠道中位；否则全体中位；再否则配置默认。"""
    cfg = cfg or load_config()
    ch = str(channel or "")
    st = ((stats or {}).get("channels") or {}).get(ch) or {}
    min_n = int((stats or {}).get("minN") or cfg.get("lead_min_samples", 3))
    if st.get("median") is not None and int(st.get("n") or 0) >= min_n:
        return int(round(float(st["median"]))), "%s中位%s天(n=%s)" % (ch, st["median"], st["n"])
    ov = (stats or {}).get("overall") or {}
    if ov.get("median") is not None and int(ov.get("n") or 0) >= min_n:
        return int(round(float(ov["median"]))), "全体中位%s天(n=%s)" % (ov["median"], ov["n"])
    d = int(cfg.get("pw_default_lead_days", cfg.get("unknown_lead_days", 10)))
    return d, "样本不足，按默认 %d 天" % d


def delivery_eta(arrive: date | None, channel: str = "", traces: list | None = None,
                 stats: dict | None = None, cfg: dict | None = None) -> dict:
    """到港日 → **大概送仓日**（用户 2026-11-18 改口径）。

    未被查验：到港 + 实测中位天数（渠道优先，样本不够退全体）
    被查验  ：放行日（没放行就 查验日+14；都没就到港日）起算 + 同样的天数
    页面只显示「大概送仓 YYYY-MM-DD」，口径文字放 basis 里（悬停看）。
    """
    cfg = cfg or load_config()
    out = {"eta": None, "days": None, "basis": "", "lead": None, "leadWhy": "",
           "inspected": False, "inspectAt": None, "releaseAt": None}
    if not arrive:
        return out
    lead, why = lead_days_for(channel, stats, cfg)
    inspected, insp_at, rel_at = inspection_of(traces or [], cfg)
    out.update({"lead": lead, "leadWhy": why, "inspected": inspected,
                "inspectAt": insp_at, "releaseAt": rel_at})
    if not inspected:
        eta = arrive + timedelta(days=lead)
        out.update({"eta": eta, "days": (eta - arrive).days,
                    "basis": "到港+%d天（%s）" % (lead, why)})
        return out
    extra = int(cfg["inspection_release_days"])
    if rel_at:
        base = max(rel_at, arrive)
        why2 = "查验后 %s 放行" % rel_at.isoformat()
    else:
        base = max((insp_at or arrive) + timedelta(days=extra), arrive)
        why2 = "查验（未放行）%s 起算两周" % (insp_at or arrive).isoformat()
    eta = base + timedelta(days=lead)
    out.update({"eta": eta, "days": (eta - arrive).days,
                "basis": "%s +%d天（%s）" % (why2, lead, why)})
    return out


def eta_usable(row: dict, today: date, cfg: dict | None = None) -> tuple:
    """这条在途的 ETA **能不能拿来判「7 天内能上架 / 该开预售」**。返回 (bool, 原因)。

    用户 2026-11-18 的硬要求：「不能只看预测上架，要看到港时间有没有值，再看有没有被查验，
    预测上架不准」。所以按顺序卡四道：

      ① 没有**真的到港日** → 预测上架只是「OMS 预计到货 + 中位天数」，不准 ⇒ 不可用
      ② 被查验但**没放行** → 至少两周放行，放行日未知 ⇒ 不可用（这类开了预售就是超卖）
      ③ 到港已经很久（> cfg.stale_arrive_days，默认 21 天）还没上架 ⇒ 这票卡住了，
         再拿它的预测日当"能上架"是自欺 ⇒ 不可用
      ④ 预测上架日已经过去 ⇒ 已逾期，不算"能上架" ⇒ 不可用
    """
    cfg = cfg or load_config()
    if not row:
        return False, "无头程行"
    if _d(row.get("shelfTime")):
        return False, "已上架（现货已在仓，不需要靠预售）"
    arrive = _d(row.get("arriveDate"))
    if not arrive:
        return False, "没有到港时间（预测上架不可信）"
    e = eta_from_row(row, cfg)
    if e.get("inspected") and not e.get("releaseAt"):
        return False, "查验未放行（至少两周）"
    stale = int(cfg.get("stale_arrive_days", 21))
    if (today - arrive).days > stale:
        return False, "到港 %d 天仍未上架" % (today - arrive).days
    if e.get("eta") and e["eta"] < today:
        return False, "预测上架日已过（%s）" % e["eta"].isoformat()
    if not e.get("eta"):
        return False, "算不出上架日"
    src = str(row.get("arriveSource") or "")
    tag = "预计到港" if "forecast" in src else "已到港"
    return True, "%s·%s" % (tag, e.get("basis") or "")

