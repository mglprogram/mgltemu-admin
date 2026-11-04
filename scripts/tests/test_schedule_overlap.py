#!/usr/bin/env python3
"""钉住「面板的重叠风险必须可信」。

2026-10-30 用户在面板上看到：

    时段重叠风险 · 平时就重叠
    16:30  待发货已买面单订单手动确认 + 双账号核对   超时 180 分 / 典型 75 分
      会压到 17:40 TEMU 预售/加库存提醒
      会压到 18:30 TEMU 热销款限流监控
      会压到 18:45 TEMU 今日销量刷新

两个独立缺陷叠在一起，才让这条一直没人发现：

  ① **典型耗时登记失真**。`实测耗时分钟` 是手工填的，`pending-confirm` 写 75，
     实测是 93 / 133 分钟（10-30 16:30→18:03、10-29 16:30→18:43）。
     面板据此算出"典型只到 17:45"，掩盖了真相。
  ② **排班本身就撞**。16:30 + 165 分钟上限 = 19:15，而 17:40 / 18:30 / 18:45
     三个任务全在同一扇 BHY+BEL 窗口上 ⇒ 不管典型多少都会互相挡。

改法：
  · `实测耗时分钟` 一律取 dsh-runner 日志里的**实测最坏**（agent 真实墙钟），
    而不是业务日志里 `[START]→[END]` 的差（那不含 agent 的启停开销）。
  · 用最坏耗时重新排班，而不是用"平时够用"的典型值。

跑法：
    pytest scripts/tests/test_schedule_overlap.py -q
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
RUNNER = PROJ / "_logs" / "dsh-runner"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))


def _stores(window: str) -> set[str]:
    w = (window or "").upper()
    out = set()
    if "BHY" in w:
        out.add("BHY")
    if "BEL" in w:
        out.add("BEL")
    return out


def _browser_tasks() -> list[dict]:
    """会占紫鸟窗口的任务 → 每次触发的 (起始分钟, 典型结束, 最坏结束)。"""
    rows = []
    for e in _registry()["tasks"]:
        if not e.get("enabled", True):
            continue
        stores = _stores(e.get("紫鸟窗口"))
        if not stores:
            continue
        sched = e.get("schedule") or {}
        times = sched.get("daily") or []
        if not times:
            times = [t for t in ((sched.get("weekly") or {}).get("times") or [])]
        typ = int(e.get("实测耗时分钟") or 0)
        worst = int(e.get("timeout_minutes") or 30)
        for hh in times:
            h, m = hh.split(":")
            start = int(h) * 60 + int(m)
            rows.append({
                "name": e["name"], "time": hh, "start": start,
                "typicalEnd": start + typ, "worstEnd": start + worst,
                "stores": stores, "typical": typ, "worst": worst,
            })
    rows.sort(key=lambda r: r["start"])
    return rows


def _pairs() -> list[dict]:
    rows = _browser_tasks()
    out = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if b["start"] >= a["worstEnd"]:
                break                       # 最坏都够不着，后面的更够不着
            if not (a["stores"] & b["stores"]):
                continue                    # 不同店铺各用各的窗口
            typ_ov = max(0, min(a["typicalEnd"], b["typicalEnd"]) - max(a["start"], b["start"]))
            worst_ov = max(0, min(a["worstEnd"], b["worstEnd"]) - max(a["start"], b["start"]))
            if worst_ov > 0:
                out.append({
                    "a": a["name"], "aTime": a["time"],
                    "b": b["name"], "bTime": b["time"],
                    "typicalOverlap": typ_ov, "worstOverlap": worst_ov,
                    "stores": sorted(a["stores"] & b["stores"]),
                })
    return out


def _elapsed_minutes(name: str) -> list[float]:
    """该任务在 dsh-runner 里的全部实测耗时（分钟）。"""
    log = RUNNER / (name + ".log")
    if not log.is_file():
        return []
    secs = [float(x) for x in re.findall(
        r"elapsed=([0-9.]+)s", log.read_text(encoding="utf-8", errors="replace"))]
    return [s / 60.0 for s in secs]


def _p90(vals: list[float]) -> float:
    """90 分位（向上取整到下标）：代表"平常能遇到的慢"，不含一次性事故。"""
    if not vals:
        return 0.0
    xs = sorted(vals)
    idx = min(len(xs) - 1, int(len(xs) * 0.9))
    return xs[idx]


#: pending-confirm 的**业务**日志（它不走 dsh，dsh.enabled=false，没有 dsh-runner 日志）
PENDING_LOG = Path.home() / ".workbuddy" / "skills" / "temu-pending-confirm" / \
    "_logs" / "pending-confirm" / "pending.log"

#: `================ 周二 2026/11/03 14:45:00.12 START ================`
_P_START = re.compile(r"=+\s+\S+\s+(\d{4})/(\d{2})/(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})\.\d+\s+START")
_P_END = re.compile(r"=+\s+\S+\s+(\d{4})/(\d{2})/(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})\.\d+\s+END rc=(-?\d+)")


def _pending_runs_since(since: date) -> list[float]:
    """pending-confirm 在 `since` 之后的实测耗时（分钟）。

    为什么不复用 `_elapsed_minutes`：这个任务**没迁到 DSH**（注册表 `dsh.enabled=false`，
    要长时间独占两扇紫鸟窗口逐单操作），所以 `_logs/dsh-runner/` 下根本没有它的日志，
    只有业务自己的 `[START]…[END rc=N]`。要按模式（逐单 / 批量）分段取样本，
    只能读业务日志。
    """
    if not PENDING_LOG.is_file():
        return []
    txt = PENDING_LOG.read_text(encoding="utf-8", errors="replace")
    out, start = [], None
    for ln in txt.splitlines():
        m = _P_START.search(ln)
        if m:
            start = datetime(*map(int, m.groups()))
            continue
        m = _P_END.search(ln)
        if m and start:
            end = datetime(*map(int, m.groups()[:6]))
            if start.date() >= since:
                out.append((end - start).total_seconds() / 60.0)
            start = None
    return out



# ---------------------------------------------------------------- 缺陷 ①
class TestTypicalDurationIsHonest:
    """`实测耗时分钟` 不得显著小于**平常的慢**（p90），也不得超过超时上限。

    ⚠ 2026-11-03 改（原来比的是「全历史最大值」，会误伤）：
      一次性的异常跑（被 Ctrl+C 打断后重跑、卡死页拖慢）会把 max 顶得很高，
      而那正是要被修掉的故障本身。用 max 会逼着人按**故障态**去排班 ——
      结果是每天多留一堆空档，却对真正的系统性偏小（pending-confirm 75 vs 133）不敏感。
      ⇒ 改成 p90：既抓得住「整体偏小」，又不会被一次事故绑架。
      另外单独钉一条：任何一次跑都不得超过自己的超时上限（那是真事故，必须解释）。
    """

    def test_no_task_understates_its_typical_worst(self):
        bad = []
        for e in _registry()["tasks"]:
            name = e["name"]
            declared = e.get("实测耗时分钟")
            if declared is None:
                continue
            vals = _elapsed_minutes(name)
            if len(vals) < 5:               # 样本太少，p90 没意义
                continue
            p90 = _p90(vals)
            if p90 > float(declared) * 1.3 + 1:
                bad.append("%s: 登记 %s 分钟，实测 p90 %.1f 分钟（%d 次）"
                           % (name, declared, p90, len(vals)))
        assert not bad, (
            "这些任务的「实测耗时分钟」严重偏小，重叠风险会被算低：\n  "
            + "\n  ".join(bad)
            + "\n⇒ 用 dsh-runner 日志重新填（p90，别用全历史最大值）"
        )

    def test_no_run_exceeds_its_own_timeout(self):
        """单次跑超过自己的超时上限 = 被调度器强杀过，必须当成事故查。

        2026-11-03 的两条超标都是同一个故障（卡死页拖住授权/抓取）：
            TEMU-Today-Sales   22.0 分钟（08:55→09:16，两次 Ctrl+C + 重跑）
            TEMU-Presale-Notify 11.0 分钟（三次业务跑全被 Ctrl+C 打断）
        """
        bad = []
        for e in _registry()["tasks"]:
            worst_to = e.get("timeout_minutes")
            if not worst_to:
                continue
            vals = _elapsed_minutes(e["name"])
            if not vals:
                continue
            m = max(vals)
            if m > float(worst_to):
                bad.append("%s: 单次 %.1f 分钟 > 上限 %s 分钟"
                           % (e["name"], m, worst_to))
        assert not bad, (
            "这些任务跑到过自己的超时上限之外（会被调度器强杀）：\n  "
            + "\n  ".join(bad))

    def test_pending_confirm_records_its_real_cost(self):
        """就是引发这次报告的那个任务 —— 但判据必须跟着**当前模式**走。

        ⚠ 2026-11-03 改：原来写死「≥ 90 分钟」，那是**逐单「去手动确认」**时代的实测
        （10-29 133 分钟 / 10-30 93 分钟）。当天改成「批量确认发货」抽屉后实测
        **2.3 分钟**（BEL 4 单），登记值随之从 133 降到 10。
        写死 90 会把一个正确的校准判成回归 —— 于是测试反而逼着人保留一个
        谎报的 90 分钟，把 BHY+BEL 窗口整段占掉。

        现在的判据：**只用当前模式下跑出来的样本**，登记值必须罩得住它们。
        模式再变回去（样本重新变长）时，这条会自动重新报警。

        ⚠ 2026-11-04 再改：必须留**取整余量**。实测是浮点（10.02 分钟），
        而 `实测耗时分钟` 是整数分钟 —— 差 1 秒就红，那是录入精度问题，
        不是"登记偏小到有重叠风险"。真正要抓的是「登记 10、实测 25」这个量级。
        """
        e = next(x for x in _registry()["tasks"]
                 if x["name"] == "TEMU-Pending-Confirm-Daily")
        declared = int(e["实测耗时分钟"])
        runs = _pending_runs_since(date(2026, 11, 3))   # 批量模式上线日
        if runs:
            worst = max(runs)
            assert declared >= math.floor(worst), (
                "批量模式下实测最坏 %.1f 分钟，登记值 %d 太小，重叠风险会被算低"
                % (worst, declared))
        assert declared >= 1, (
            "登记值 %d 分钟比任何一次实测都小，等于宣称这任务不用时间" % declared)

    def test_typical_never_exceeds_the_timeout(self):
        """典型 > 上限 本身就是矛盾配置。"""
        bad = [e["name"] for e in _registry()["tasks"]
               if e.get("实测耗时分钟") and e.get("timeout_minutes")
               and int(e["实测耗时分钟"]) > int(e["timeout_minutes"])]
        assert not bad, "典型耗时大于超时上限：\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------- 缺陷 ②
class TestNoTypicalOverlap:
    """同一扇紫鸟窗口上，**典型耗时**不准重叠。

    只有最坏才撞（= 卡住时才发生）可以接受；典型就撞说明排班本来就不够错开，
    2026-10-30 之前的 16:30 pending-confirm 就是这种。
    """

    def test_no_pair_overlaps_on_typical_duration(self):
        bad = ["%s %s vs %s %s（典型重叠 %d 分）"
               % (p["a"], p["aTime"], p["b"], p["bTime"], p["typicalOverlap"])
               for p in _pairs() if p["typicalOverlap"] > 0]
        assert not bad, (
            "这些任务按典型耗时就会抢同一扇窗口（%s）：\n  "
            % ", ".join(sorted({s for p in _pairs() for s in p["stores"]}))
            + "\n  ".join(bad)
            + "\n⇒ 错开触发时间，或把「实测耗时分钟」改成真实值后重排"
        )

    def test_full_overlap_matrix_is_reported(self):
        """把当前所有（含仅最坏才撞的）配对打印出来，便于人工复核。"""
        pairs = _pairs()
        for p in pairs:
            print("%-28s %s vs %-28s %s  典型%3d 最坏%3d"
                  % (p["a"], p["aTime"], p["b"], p["bTime"],
                     p["typicalOverlap"], p["worstOverlap"]))
        assert all(p["typicalOverlap"] == 0 for p in pairs)


class TestTheSpecificRegression:
    """2026-10-30 那三对必须不再存在。"""

    def test_pending_confirm_no_longer_blocks_the_evening_block(self):
        pairs = _pairs()
        pc = [p for p in pairs
              if "TEMU-Pending-Confirm-Daily" in (p["a"], p["b"])]
        assert not pc, (
            "pending-confirm 仍会压到其它同窗口任务：\n  "
            + "\n  ".join("%s %s vs %s %s 典型%d 最坏%d"
                          % (p["a"], p["aTime"], p["b"], p["bTime"],
                             p["typicalOverlap"], p["worstOverlap"]) for p in pc)
        )

    def test_pending_confirm_is_scheduled_early_enough(self):
        e = next(x for x in _registry()["tasks"]
                 if x["name"] == "TEMU-Pending-Confirm-Daily")
        hh = (e["schedule"]["daily"])[0]
        h, m = hh.split(":")
        start = int(h) * 60 + int(m)
        end = start + int(e["timeout_minutes"])
        assert end <= 17 * 60 + 40, (
            "pending-confirm 最坏结束 %02d:%02d 会压到 17:40 的 Presale" % (end // 60, end % 60))

    def test_today_sales_first_run_clears_the_0800_export(self):
        """08:00 的 Goods-OnSale 实测最坏 25 分钟，08:15 起跑必重叠。"""
        e = next(x for x in _registry()["tasks"] if x["name"] == "TEMU-Today-Sales")
        first = sorted(e["schedule"]["daily"])[0]
        h, m = first.split(":")
        assert int(h) * 60 + int(m) >= 8 * 60 + 25, (
            "Today-Sales 首班 %s 早于 Goods-OnSale 的最坏结束时刻 08:25" % first)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
