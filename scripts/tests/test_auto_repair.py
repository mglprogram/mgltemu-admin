#!/usr/bin/env python3
"""钉住「看门狗发现失败 → 自动重跑一次」的规则与红线。

用户 2026-11-03 的要求：

> 你有一个 30 分钟刷新面板的看门狗任务，那你去刷新面板的时候，
> 当检测到又失败任务，你去自动修复失败的任务。

这个功能最大的风险不是"没效果"，而是**它自己变成新的故障源**。
所以规则全部钉在这里，任何一条被放松都会立刻红。

四条红线
--------
① **自激**：面板刷新/巡检自己失败时重跑它 → 再失败 → 再触发……无限循环。
② **无限重试**：任务真坏了（平台改版、账号停用）就每 30 分钟跑一次，一天 48 次，
   烧 Cloudflare 额度、占紫鸟窗口、把日志刷成噪声。
③ **越跑越糟**：内存不足时重载页面会把别人的页挤出去，实测 1 张卡死页变 3 张。
④ **没意义的重试**：rc=5/6 是"锁被占，按设计跳过"，下一班次自然补，重试只会再撞。

跑法：
    pytest scripts/tests/test_auto_repair.py -q
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))

import auto_repair as AR  # noqa: E402

REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
WATCH = PROJ / "scripts" / "data" / "task_watch.json"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))


def _entries() -> dict:
    return {e["name"]: e for e in _registry()["tasks"]}


CFG = {"auto_repair": True, "auto_repair_max_per_day": 3,
       "auto_repair_skip_if_next_within_min": 10}


def _decide(name, rc=1, *, st=None, cfg=None, mem_tight=False,
            minutes_to_next=None, last_run_key="", now=None):
    return AR.decide(name, rc, cfg or CFG, _entries().get(name), st or {},
                     now=now, mem_tight=mem_tight,
                     minutes_to_next=minutes_to_next,
                     last_run_key=last_run_key)


# ---------------------------------------------------------------- ① 自激
class TestSelfExcitationIsImpossible:
    def test_panel_refresh_never_retries(self):
        ok, why = _decide("AutomationPanel-Refresh")
        assert not ok
        assert "自激" in why

    def test_watchdog_never_retries_itself(self):
        ok, why = _decide("TEMU-TaskWatch-30min")
        assert not ok
        assert "自激" in why

    def test_hard_coded_even_if_registry_says_yes(self):
        """就算注册表被误改成 true，代码也必须拦住。"""
        assert "AutomationPanel-Refresh" in AR.NEVER_RETRY
        assert "TEMU-TaskWatch-30min" in AR.NEVER_RETRY
        assert AR.retry_allowed("AutomationPanel-Refresh", {"自动重试": True}) is False
        assert AR.retry_allowed("TEMU-TaskWatch-30min", {"自动重试": True}) is False


# ---------------------------------------------------------------- 授权
class TestRetryMustBeExplicitlyGranted:
    def test_default_is_no(self):
        """没写 `自动重试` 就不许动 —— 重跑等于再执行一次业务动作。"""
        assert AR.retry_allowed("TEMU-Goods-DailyList", {}) is False
        assert AR.retry_allowed("TEMU-Goods-DailyList", None) is False

    def test_registry_actually_opens_the_safe_ones(self):
        entries = _entries()
        for n in ("TEMU-Goods-DailyList", "TEMU-OMS-Orders-Daily",
                  "TEMU-Today-Sales", "TEMU限流监控"):
            assert entries[n].get("自动重试") is True, "%s 应该允许自动重试" % n
            assert entries[n].get("_自动重试说明"), "%s 缺少为什么可以重试的说明" % n

    def test_dangerous_ones_stay_closed(self):
        """发货/重启 DSH 这类动作重跑风险高于收益。"""
        entries = _entries()
        for n, why in (
            ("TEMU-Pending-Confirm-Daily", "提交发货"),
            ("WorkBuddy-Proxy-Sentinel", "重启 DSH"),
        ):
            assert entries[n].get("自动重试") is False, "%s 不该自动重试" % n
            assert why in (entries[n].get("_自动重试说明") or ""), (
                "%s 的理由里得说清为什么危险（找的是「%s」）" % (n, why))

    def test_every_task_makes_a_decision(self):
        """每个任务都要么开要么关，并写清理由 —— 不能有"没人想过"的任务。"""
        bad = [e["name"] for e in _registry()["tasks"]
               if e.get("自动重试") is None]
        assert not bad, "这些任务没明确决定是否自动重试：\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------- ② 无限重试
class TestBoundedRetries:
    def test_same_failure_only_once(self):
        st = {}
        ok, _ = _decide("TEMU-Goods-DailyList", st=st, last_run_key="2026-11-03T08:10:00")
        assert ok
        AR.record_attempt(st, "TEMU-Goods-DailyList", "2026-11-03T08:10:00")
        ok2, why = _decide("TEMU-Goods-DailyList", st=st, last_run_key="2026-11-03T08:10:00")
        assert not ok2, "同一次失败被重试了两次"
        assert "已经重试过" in why

    def test_new_failure_is_a_new_chance(self):
        st = {}
        AR.record_attempt(st, "TEMU-Goods-DailyList", "2026-11-03T08:10:00")
        ok, _ = _decide("TEMU-Goods-DailyList", st=st, last_run_key="2026-11-03T08:40:00")
        assert ok, "重试后仍然失败（新的 LastRunTime）应该还能再试"

    def test_daily_cap(self):
        st = {}
        for i in range(3):
            ok, _ = _decide("TEMU-Goods-DailyList", st=st, last_run_key="k%d" % i)
            assert ok, "第 %d 次应该允许" % (i + 1)
            AR.record_attempt(st, "TEMU-Goods-DailyList", "k%d" % i)
        ok, why = _decide("TEMU-Goods-DailyList", st=st, last_run_key="k3")
        assert not ok
        assert "上限" in why

    def test_counter_resets_next_day(self):
        st = {}
        day1 = datetime(2026, 11, 3, 10, 0)
        for i in range(3):
            AR.record_attempt(st, "TEMU-Goods-DailyList", "d1-%d" % i, now=day1)
        ok, why = _decide("TEMU-Goods-DailyList", st=st, last_run_key="d1-x", now=day1)
        assert not ok, "当天已经用满"
        day2 = day1 + timedelta(days=1)
        ok2, _ = _decide("TEMU-Goods-DailyList", st=st, last_run_key="d2-0", now=day2)
        assert ok2, "第二天应该重新计数"

    def test_cap_is_configurable_and_never_zero_division(self):
        st = {}
        for bad in (None, "x", 0, -5):
            cfg = {"auto_repair_max_per_day": bad}
            assert AR.max_per_day(cfg) >= 1
        cfg = {"auto_repair_max_per_day": 1}
        AR.record_attempt(st, "TEMU-Goods-DailyList", "k0")
        ok, _ = _decide("TEMU-Goods-DailyList", st=st, cfg=cfg, last_run_key="k1")
        assert not ok, "配成 1 次就只能 1 次"


# ---------------------------------------------------------------- ③ 内存
class TestMemoryPressureNoLongerBlocksRetry:
    """★ 2026-11-12 反转：内存紧张**不再**阻止自动重试。

    用户当天口径：「卡死这种情况直接重新刷新就可以了」。

    旧行为是硬闸门（mem_tight → 不重试），理由是 2026-11-03 一次实测：
    「10:36 1 页卡死 → 10:39 导航修复 → 10:42 3 页卡死」。
    但那条实测把因果搞反了 —— 3 页卡死是内存本来就在持续被吃掉，不是刷新造成的。
    而"不重试"的代价是任务当天彻底不跑（11-12 08:00 的在售款导出就是这样，
    告警里明写着"自动修复：未重试（本机内存紧张）"）。

    改法：卡死页由 `auth_guard.heal_own_hung_pages` 开跑时统一刷新（内存紧张也刷），
    重试不再因内存被拦，只在理由里带一句说明。
    """

    def test_tight_memory_still_retries(self):
        ok, why = _decide("TEMU-Goods-DailyList", mem_tight=True)
        assert ok, "内存紧张不该再阻止重试（卡死页会先被刷新）"
        assert "内存" in why, "理由里仍要写清内存状况，别让人以为一切正常"

    def test_roomy_memory_allows(self):
        ok, why = _decide("TEMU-Goods-DailyList", mem_tight=False)
        assert ok
        assert "内存" not in why

    def test_memory_is_only_a_note_not_a_gate(self):
        """同一条件下，紧张与充足都必须放行 —— 差别只在理由文字。"""
        tight_ok, tight_why = _decide("TEMU-Goods-DailyList", mem_tight=True)
        roomy_ok, roomy_why = _decide("TEMU-Goods-DailyList", mem_tight=False)
        assert tight_ok is True and roomy_ok is True
        assert tight_why != roomy_why, "紧张时必须在理由里留痕"

    def test_unknown_memory_is_treated_as_tight(self, monkeypatch):
        """读不到内存时保守判"紧张"（只影响日志口径，不再决定跑不跑）。"""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "auth_guard":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert AR.mem_is_tight() is True


# ---------------------------------------------------------------- ④ 跳过码
class TestSkipCodesAreNotFailures:
    @pytest.mark.parametrize("rc", [5, 6])
    def test_skip_codes_never_retry(self, rc):
        ok, why = _decide("TEMU-Goods-DailyList", rc=rc)
        assert not ok
        assert "按设计跳过" in why

    def test_real_failures_do_retry(self):
        for rc in (1, 2, 42, 267014):
            ok, _ = _decide("TEMU-Goods-DailyList", rc=rc)
            assert ok, "rc=%s 是真失败，应该重试" % rc

    def test_not_run_today_is_also_repaired(self):
        """漏跑比失败更该救：今天压根没执行过，补一次不可能重复。"""
        ok, why = _decide("TEMU-Goods-DailyList", rc=AR.NOT_RUN)
        assert ok
        assert "还没跑过" in why

    def test_not_run_still_obeys_every_other_gate(self):
        """但它不能绕过时机/次数/授权这几道闸门（内存从 2026-11-12 起不再是闸门）。"""
        assert _decide("TEMU-Goods-DailyList", rc=AR.NOT_RUN, mem_tight=True)[0] is True
        assert not _decide("TEMU-Goods-DailyList", rc=AR.NOT_RUN, minutes_to_next=2)[0]
        assert not _decide("TEMU-Pending-Confirm-Daily", rc=AR.NOT_RUN)[0]
        st = {}
        for i in range(3):
            AR.record_attempt(st, "TEMU-Goods-DailyList", "k%d" % i)
        assert not _decide("TEMU-Goods-DailyList", rc=AR.NOT_RUN, st=st,
                           last_run_key="k9")[0]


# ---------------------------------------------------------------- 时机
class TestTimingGuard:
    def test_too_close_to_next_run_waits(self):
        ok, why = _decide("TEMU-Goods-DailyList", minutes_to_next=3)
        assert not ok
        assert "分钟" in why

    def test_far_from_next_run_retries(self):
        ok, _ = _decide("TEMU-Goods-DailyList", minutes_to_next=90)
        assert ok

    def test_unknown_next_run_does_not_block(self):
        ok, _ = _decide("TEMU-Goods-DailyList", minutes_to_next=None)
        assert ok

    def test_minutes_until_next_parses_windows_iso(self):
        now = datetime(2026, 11, 3, 10, 0, 0)
        assert AR.minutes_until_next("2026-11-03T10:30:00", now) == 30.0
        assert AR.minutes_until_next("2026-11-03T10:30:00.0000000", now) == 30.0
        assert AR.minutes_until_next("0001-01-01T00:00:00", now) is None
        assert AR.minutes_until_next("", now) is None


# ---------------------------------------------------------------- 接线
class TestItIsActuallyWiredIntoTheWatchdog:
    """规则再好，没接进巡检就是空谈。"""

    def _src(self):
        return (PROJ / "scripts" / "watch_tasks.py").read_text(encoding="utf-8")

    def test_watchdog_calls_maybe_repair_on_failure(self):
        src = self._src()
        assert "maybe_repair(" in src, "巡检没调用自动重试"
        # 必须挂在「失败」分支里（result not in ok_codes）而不是别处
        fail_at = src.find("if result not in ok_codes and last_dt is not None:")
        call_at = src.find("maybe_repair(st, name, result")
        assert fail_at > 0 and call_at > fail_at, "不在失败分支里"

    def test_repair_happens_after_the_alert_is_recorded(self):
        """先记 seen 再重试：重试逻辑炸了也不能把告警本身弄丢。"""
        src = self._src()
        seen_at = src.find("seen[key] = now.isoformat", src.find("if result not in ok_codes"))
        call_at = src.find("maybe_repair(st, name, result")
        assert 0 < seen_at < call_at

    def test_attempt_is_persisted_before_returning(self):
        """重试后立刻落盘，否则下一轮会拿同一个 key 再试一次、白烧上限。"""
        src = self._src()
        seg = src[src.find("def maybe_repair"):]
        seg = seg[:seg.find("\ndef ", 1)]
        assert "record_attempt(" in seg
        assert seg.find("record_attempt(") < seg.find("save_state(st)")

    def test_can_be_switched_off(self):
        assert "auto_repair" in self._src()
        assert "--no-auto-repair" in self._src()


class TestMaybeRepairBehaviour:
    """`watch_tasks.maybe_repair` —— 把规则接到巡检上的那一层。"""

    def _mod(self):
        import watch_tasks as W
        return W

    def test_off_switch_returns_empty(self):
        W = self._mod()
        st = {}
        got = W.maybe_repair(st, "TEMU-Goods-DailyList", 1, {"auto_repair": False},
                             _entries(), "k", "", mem_tight=False)
        assert got == ""
        assert "autorepair" not in st

    def test_successful_trigger_is_recorded_and_persisted(self, monkeypatch, tmp_path):
        W = self._mod()
        st = {}
        seen = {}
        monkeypatch.setattr(W, "save_state", lambda s: seen.update(dict(s)))
        monkeypatch.setattr(AR, "trigger", lambda n, timeout=60.0: (True, "已触发"))
        got = W.maybe_repair(st, "TEMU-Goods-DailyList", 1, CFG,
                             _entries(), "2026-11-03T08:10:00", "",
                             mem_tight=False)
        assert "自动重试" in got and "已触发" in got
        assert st["autorepair_done"]["TEMU-Goods-DailyList"] == "2026-11-03T08:10:00"
        assert st["autorepair"]["TEMU-Goods-DailyList"]["count"] == 1
        assert seen, "重试之后必须立刻落盘"

    def test_failed_trigger_does_not_count_as_an_attempt(self, monkeypatch):
        """触发都没成功，就不能记成"试过了" —— 否则白烧一次上限。"""
        W = self._mod()
        st = {}
        monkeypatch.setattr(W, "save_state", lambda s: None)
        monkeypatch.setattr(AR, "trigger", lambda n, timeout=60.0: (False, "拒绝访问"))
        got = W.maybe_repair(st, "TEMU-Goods-DailyList", 1, CFG,
                             _entries(), "k", "", mem_tight=False)
        assert "触发失败" in got
        assert "autorepair_done" not in st

    def test_blocked_decision_reports_why_in_the_alert(self):
        """不重试也要在告警里说清为什么 —— 否则用户以为它坏了。"""
        W = self._mod()
        got = W.maybe_repair({}, "TEMU-Pending-Confirm-Daily", 1, CFG,
                             _entries(), "k", "", mem_tight=False)
        assert "未重试" in got
        assert "自动重试" in got


class TestConfigIsReal:
    def test_watch_config_enables_repair_with_sane_limits(self):
        cfg = json.loads(WATCH.read_text(encoding="utf-8-sig"))
        assert cfg.get("auto_repair") is True
        cap = int(cfg.get("auto_repair_max_per_day"))
        assert 1 <= cap <= 6, "每天重试 %d 次不合理（太多会刷屏烧额度）" % cap
        assert 0 <= int(cfg.get("auto_repair_skip_if_next_within_min")) <= 60

    def test_the_tasks_that_failed_today_are_now_watched(self):
        """截图里失败的 3 个任务原本不在巡检清单里 —— 看不见就谈不上救。"""
        names = {t["name"] for t in json.loads(WATCH.read_text(encoding="utf-8-sig"))["tasks"]}
        for n in ("TEMU-Presale-Notify", "TEMU-Today-Sales", "TEMU-Goods-DailyList"):
            assert n in names, "%s 不在巡检清单里，看门狗永远不会救它" % n


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
