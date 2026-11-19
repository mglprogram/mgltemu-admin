# -*- coding: utf-8 -*-
"""headmile_eta.py —— 开预售（头程）口径：到港日 → 送仓上架日。

★ 现行口径（用户 2026-11-19 定稿，覆盖之前所有版本）
=====================================================
「跟渠道没关系-送仓的时间，只跟周几到有关系；如果是周日到港，三个渠道都可能在下周六送仓上架」
「周三周四周五周六到 → 下周六」

  ① **送仓上架日只看到港是周几，跟渠道完全无关**，一律排到**到港日所在周的下一周**：
        周一到      → 下周二
        周二到      → 下周五
        周三/四/五/六/日到 → 下周六
     用户给的四个真值例子都对得上：
        11.15 周日 → 11.21 周六 ； 11.16 周一 → 11.24 下周二
        11.17 周二 → 11.27 下周五； 11.18 周三 → 11.28 下周六
     「最晚 X 号」= 上架日：再晚开预售，货到了却没有预售窗口（平台预售 7 天后自动关）。

  ② **只有「美国海关（国外）查验」才调整**（用户：「美国海关查验至少两个星期放行，
     如果这样上架时间要调整到两周后」）：正常上架日整体 **+14 天**；
     若已放行且放行日更晚，再按周往后顺延到不早于放行日的那一班（星期不变）。
     **出口查验 / 国内查验不算** —— 它们发生在国内、开船前，船期（ETA）已经吃掉了
     这段延误，再加两周会算到莫名其妙的两周后（关键词见 config
     export_inspection_keywords）。

配置在 config/headmile_eta.json（到港星期→送仓星期、查验天数、关键词都能改，代码不写死）。

历史沿革（别再翻回去用）
------------------------
  · 2026-11-18 第一版：按**渠道**给送仓星期（美森周六/以星周二/盐田周五）→ 2026-11-19 作废。
  · 2026-11-18 第二版：帕沃尔那批改用「到港 + 未查验实测中位天数」→ 同日作废。
  · 两个版本都保留了 delivery_eta()（现为 shelf_eta 别名）与 lead_stats()
    （仅作「历史偏差参考」，不再参与任何计算）。

为什么不再用「到港 + 渠道历史中位天数」：中位数把大量**查验卡住**的单混在一起
（以星 n=163 中位 10 天、p80 14 天），算出来的上架日系统性偏晚，
于是「7 天内能不能上架」这条判断整片判错。

用法
----
    from headmile_eta import shelf_eta, eta_from_row
    shelf_eta(date(2026, 11, 15))        # → 2026-11-21（周日到 → 下周六）
    shelf_eta(date(2026, 11, 16))        # → 2026-11-24（周一到 → 下周二）
    shelf_eta(date(2026, 11, 17))        # → 2026-11-27（周二到 → 下周五）
    eta_from_row(track_row)              # 直接给头程跟踪的一行
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
    # 到港星期(ISO 1=周一…7=周日) → 送仓上架星期。**跟渠道无关**（用户 2026-11-19）。
    cfg.setdefault("arrive_weekday_shelf_weekday",
                   {"1": 2, "2": 5, "3": 6, "4": 6, "5": 6, "6": 6, "7": 6})
    cfg.setdefault("unknown_lead_days", 10)
    cfg.setdefault("inspection_release_days", 14)
    cfg.setdefault("inspection_keywords", ["查验"])
    cfg.setdefault("release_keywords", ["放行"])
    # 帕沃尔「到港 → 送仓」实测样本的过滤与兜底（2026-11-18 第二次改口径）
    cfg.setdefault("lead_min_samples", 3)
    cfg.setdefault("lead_min_days", 0)
    cfg.setdefault("lead_max_days", 90)
    cfg.setdefault("pw_default_lead_days", 10)
    # 恒信（LM739）按**渠道**算，跟乐航那套「到港星期」不一样（用户 2026-11-19）
    cfg.setdefault("hengxin", {
        "美森": {"mode": "weekday", "weekday": 6, "latest_arrive_weekday": 3},
        "以星": {"mode": "days", "days": 13},
        "盐田": {"mode": "days", "days": 14},
        "": {"mode": "days", "days": 14},
    })
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

    ⇒ 送仓是**按周排班**：本周到港的赶不上本周的送仓，一律排到下周那一班。
    """
    monday = d - timedelta(days=d.isoweekday() - 1)
    return monday + timedelta(days=7 + (int(iso_weekday) - 1))


def shelf_weekday_of(arrive: date, cfg: dict | None = None) -> int | None:
    """到港**星期** → 送仓上架**星期**（ISO，周一起算）。**与渠道无关**（乐航）。

    用户 2026-11-19 定稿（原话：「跟渠道没关系-送仓的时间，只跟周几到有关系；
    如果是周日到港，三个渠道都可能在下周六送仓上架」「周三周四周五周六到 → 下周六」）：

        周一到 → 下周二
        周二到 → 下周五
        周三/周四/周五/周六/周日到 → 下周六

    三个历史真值例子都对得上（见 config 的 `_user_examples`）。
    """
    cfg = cfg or load_config()
    m = cfg.get("arrive_weekday_shelf_weekday") or {}
    v = m.get(str(arrive.isoweekday()))
    return int(v) if v else None


def hengxin_shelf_date(arrive: date, channel: str, cfg: dict | None = None) -> tuple:
    """**恒信（LM739）**的到港 → 上架日。返回 (date|None, basis)。

    用户 2026-11-19 给的恒信口径（与乐航那套「到港星期」不同，按渠道算）：

      美森：「如果船期稳定，周一那天到港，正常当周会上架」
            ⇒ 周一~周三到港 → 当周周六上架；周四~周日到港 → 下周六。
              （「当周上架」具体是周几没细说，按周六算 —— 美森送仓/上架集中在周六，
                这样周一到的算 +5 天，与「当周能上架」吻合。）
      以星：「到港后得 3 个工作日提柜，预留拆柜和上架的时间预计得 5 个工作日，
            还要算上周末时间，合计到港后得 13 个自然日左右」⇒ 到港 + 13 天。
            实测样本印证：11-04 到港 → 11-16/11-17 上架（+12/+13）。
      盐田：「到港后预计 13-15 天左右」⇒ 取中位 + 14 天。
    """
    cfg = cfg or load_config()
    rule = (cfg.get("hengxin") or {}).get(str(channel or ""))
    if rule is None:
        rule = (cfg.get("hengxin") or {}).get("") or {"mode": "days", "days": 14}
    mode = str(rule.get("mode") or "days")
    if mode == "weekday":
        wd = int(rule.get("weekday") or 6)
        until = int(rule.get("latest_arrive_weekday") or 3)
        wd_name = WEEKDAY_CN[arrive.isoweekday() - 1]   # 已含「周」字前缀
        if arrive.isoweekday() <= until:
            # 当周：本周的星期 wd（wd 一定在该天之后，正常不会出现负数）
            monday = arrive - timedelta(days=arrive.isoweekday() - 1)
            eta = monday + timedelta(days=wd - 1)
            if eta < arrive:                      # 兜底，别给到港前的日期
                return next_week_weekday(arrive, wd), "%s到 → 下周六（当周已过）" % wd_name
            return eta, "%s到 → 当周周六" % wd_name
        return next_week_weekday(arrive, wd), "%s到 → 下周六" % wd_name
    days = int(rule.get("days") or 14)
    return arrive + timedelta(days=days), "到港 + %d 天" % days


def inspection_of(traces: list, cfg: dict | None = None) -> tuple[bool, date | None, date | None]:
    """从轨迹里读**美国海关查验**状态。返回 (被查验, 查验日, 放行日)。

    ⚠ 三条真机踩过的坑：

    1. **「报关放行」不是海关查验放行**。轨迹里 `【报关】已于11/16报关放行` 是出货前的
       正常动作（还在国内、船都没开），把它当放行会把「查验」直接判成已放行。
       所以匹配放行前先排除带「报关 / 清关」字样的节点。
    2. **放行必须晚于查验**。轨迹是「新的在前」，查验节点可能比报关放行更新，
       原来的写法会取到比查验更早的放行日（实测 到港 11-13、查验 11-xx、放行却写 10-21）。
    3. **出口查验 / 国内查验 ≠ 美国海关查验**（2026-11-19 用户口径）。
       出口查验在国内、开船前发生，船期（ETA）里已经吃掉这段延误；
       再按「查验 + 两周」算，会把 12-01 到港的单排到 12 月下旬去。
       命中 `export_inspection_keywords` 的节点直接跳过，不当查验。
    """
    cfg = cfg or load_config()
    kw_insp = tuple(cfg["inspection_keywords"])
    kw_rel = tuple(cfg["release_keywords"])
    skip_rel = tuple(cfg.get("release_ignore_keywords") or ["报关", "清关"])
    kw_export = tuple(cfg.get("export_inspection_keywords") or [])
    inspected = False
    insp_at: date | None = None
    rel_at: date | None = None
    for t in traces or []:
        info = str(t.get("info") or "")
        day = _d(t.get("time"))
        # 出口/国内查验：发生在国内、开船前，不算美国海关查验
        if kw_export and any(k in info for k in kw_export):
            continue
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
              cfg: dict | None = None, account: str = "") -> dict:
    """到港日 → 送仓上架日。返回 dict（见下）。

    eta      上架日（date）；没有到港日 → None
    days     eta − arrive（天）
    basis    口径说明（页面「预测/上架」的副标题直接用）
    inspected / inspectAt / releaseAt   查验三件套

    ★ 两个账号**口径不同**（用户 2026-11-19 分别给的）：
      · 乐航 / 帕沃尔（account 为 14450 或空）：**只看到港星期，跟渠道无关**
        周一到 → 下周二 · 周二到 → 下周五 · 周三~周日到 → 下周六。
      · 恒信（account == "LM739"）：**按渠道**
        美森 当周周六（周一~周三到）/ 下周六（周四~周日到）；以星 +13 天；盐田 +14 天。
      两套规则都见 `config/headmile_eta.json`。
      被**美国海关**查验的（用户原话「至少两个星期放行，上架时间要调整到两周后」）：
      在上面的正常上架日上**整体后移两周**；若已放行且放行日更晚，
      再按周往后顺延到不早于放行日的那一班（星期不变）。
    """
    cfg = cfg or load_config()
    out = {"eta": None, "days": None, "basis": "", "inspected": False,
           "inspectAt": None, "releaseAt": None}
    if not arrive:
        return out

    inspected, insp_at, rel_at = inspection_of(traces or [], cfg)
    out.update({"inspected": inspected, "inspectAt": insp_at, "releaseAt": rel_at})

    if str(account or "") == "LM739":
        eta, basis = hengxin_shelf_date(arrive, channel, cfg)
        if not eta:
            eta = arrive + timedelta(days=int(cfg["unknown_lead_days"]))
            basis = "到港·恒信送仓规则未配置"
    else:
        weekday = shelf_weekday_of(arrive, cfg)
        if not weekday:
            # 兜底（理论上配置齐全不会走到）：粗口径「到港 + 未知天数」
            eta = arrive + timedelta(days=int(cfg["unknown_lead_days"]))
            basis = "到港·送仓星期未配置"
        else:
            eta = next_week_weekday(arrive, weekday)
            basis = "%s到港 → 下%s" % (WEEKDAY_CN[arrive.isoweekday() - 1],
                                      WEEKDAY_CN[int(weekday) - 1])

    if inspected:
        extra = int(cfg["inspection_release_days"])
        eta = eta + timedelta(days=extra)
        if rel_at:
            # 放行更晚 → 顺着同样的星期往后挪，直到不早于放行日
            while eta < rel_at:
                eta = eta + timedelta(days=7)
            basis += " · 查验 %s 放行 → 顺延" % rel_at.isoformat()
        else:
            basis += " · 查验 %s 未放行 → 后移两周" % (insp_at or arrive).isoformat()

    out.update({"eta": eta, "days": (eta - arrive).days, "basis": basis})
    return out


def eta_from_row(row: dict, cfg: dict | None = None) -> dict:
    """头程跟踪（shipment-track.json）的一行 → shelf_eta 结果。

    已上架（shelfTime 有值）直接返回真实上架日；没有到港日返回 eta=None（调用方自己兜底）。

    ★ 2026-11-19 起「页面显示 / recompute / 预售判定」三处永远一致 —— 都调 shelf_eta。
    账号口径之分在 shelf_eta 里处理（恒信按渠道，乐航按到港星期）。
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
    return shelf_eta(arrive, str(row.get("channel") or ""), traces, cfg,
                     account=str(row.get("account") or ""))


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
    """到港日 → **大概送仓日**。

    ⚠ 2026-11-19 用户第三次给口径后，帕沃尔（乐航）**不再用实测中位天数**，
    统一按「到港日所在周的下一周、该渠道固定送仓星期」算 —— 跟恒信同一套。
    这个函数保留只为兼容旧调用点（`stats` 参数已无意义，留着不报错）。
    """
    return shelf_eta(arrive, channel, traces, cfg)


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

