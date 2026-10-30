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
import re
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


# ---------------------------------------------------------------- 缺陷 ①
class TestTypicalDurationIsHonest:
    """`实测耗时分钟` 不得显著小于 dsh-runner 里记录的实测最坏。

    用户看到的"典型 75 分"和实测 133 分差 77%，直接把「平时就重叠」算成了
    「平时不会撞」。
    """

    def test_no_task_understates_its_worst_run(self):
        bad = []
        for e in _registry()["tasks"]:
            name = e["name"]
            declared = e.get("实测耗时分钟")
            if declared is None:
                continue
            log = RUNNER / (name + ".log")
            if not log.is_file():
                continue
            secs = [float(x) for x in re.findall(
                r"elapsed=([0-9.]+)s", log.read_text(encoding="utf-8", errors="replace"))]
            if not secs:
                continue
            worst = max(secs) / 60.0
            if worst > float(declared) * 1.3 + 1:
                bad.append("%s: 登记 %s 分钟，实测最坏 %.1f 分钟" % (name, declared, worst))
        assert not bad, (
            "这些任务的「实测耗时分钟」严重偏小，重叠风险会被算低：\n  "
            + "\n  ".join(bad)
            + "\n⇒ 用 dsh-runner 日志的 elapsed 最大值重新填"
        )

    def test_pending_confirm_records_its_real_cost(self):
        """就是引发这次报告的那个任务。"""
        e = next(x for x in _registry()["tasks"]
                 if x["name"] == "TEMU-Pending-Confirm-Daily")
        assert int(e["实测耗时分钟"]) >= 90, (
            "pending-confirm 实测 93~133 分钟，登记值不该低于 90")

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
