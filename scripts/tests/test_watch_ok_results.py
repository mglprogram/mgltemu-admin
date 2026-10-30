#!/usr/bin/env python3
"""钉住「巡检的 ok_results 必须来自注册表」这条单一事实来源。

背景（2026-10-30 07:57 的真实误报）
-----------------------------------
用户 08:27 收到飞书/手机告警：

    ❌ TEMU 在售款明细日更 运行失败
       结果码：6
       原因：…goods.json 已更新，但发布阶段发现另一发布进程在跑，
             按设计不并发发布返回 rc=6，非故障…

`rc=6` 是**按设计的跳过**（"已并入那一轮，数据照样上线"），注册表里
`ok_results: [0, 6]`、面板 RC_MAP 也把它归到 `waiting` —— 只有巡检不知道。

根因：`ok_results` 被写在**两处** ——
  · `~/.agents/skills/scheduled-tasks/tasks.json`（manage.py / 面板用）
  · `D:\\AI-workplace\\TEMU\\scripts\\data\\task_watch.json`（巡检用）
两份靠人手同步，结果后者只填了 5/15 个任务，其余全走默认 `[0]`。

修法：注册表是单一事实来源，`watch_tasks.registry_ok_results()` 只读它；
`task_watch.json` 里的同名键降级为**显式覆盖**（保留兼容，但正常不该有）。

跑法：
    pytest scripts/tests/test_watch_ok_results.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))

import watch_tasks as W  # noqa: E402

REGISTRY = Path.home() / ".agents" / "skills" / "scheduled-tasks" / "tasks.json"
WATCH_CFG = PROJ / "scripts" / "data" / "task_watch.json"


def _registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8-sig"))


def _watch() -> dict:
    return json.loads(WATCH_CFG.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear_cache():
    """registry_ok_results() 有进程内缓存，每个用例都要清。"""
    W._REGISTRY_OK_CACHE = None
    yield
    W._REGISTRY_OK_CACHE = None


class TestRegistryIsTheSourceOfTruth:
    def test_reads_ok_results_from_the_registry(self):
        got = W.registry_ok_results()
        reg = {e["name"]: e.get("ok_results")
               for e in _registry()["tasks"] if e.get("ok_results")}
        assert got, "注册表里明明有 ok_results，却一个都没读到"
        for name, ok in reg.items():
            assert got.get(name) == [int(x) for x in ok], f"{name} 读错了"

    def test_skip_codes_six_and_five_are_present(self):
        """★ 2026-10-30 误报就是漏了 6。

        `TEMU-Goods-OnSale-Daily` 08:00 那轮 export/build/enrich 全 0，
        只因发布撞车返回 6，巡检却按 [0] 判成失败。
        """
        got = W.registry_ok_results()
        for name in ("TEMU-Goods-OnSale-Daily", "TEMU-Today-Sales",
                     "TEMU-OMS-Orders-Daily", "AutomationPanel-Refresh"):
            assert 6 in got.get(name, []), f"{name} 的 ok_results 少了 6"
        for name in ("TEMU-BEL-Abnormal-Daily", "TEMU-Presale-Notify",
                     "TEMU-Overdue-Confirm-Daily", "TEMU限流监控"):
            assert 5 in got.get(name, []), f"{name} 的 ok_results 少了 5"

    def test_every_task_keeps_zero(self):
        """`0` 必须在每个 ok_results 里，否则成功会被判成失败。"""
        bad = [n for n, ok in W.registry_ok_results().items() if 0 not in ok]
        assert not bad, "这些任务的 ok_results 不含 0（成功会被判失败）：" + ", ".join(bad)

    def test_missing_registry_degrades_to_empty_not_crash(self, monkeypatch, tmp_path):
        monkeypatch.setattr(W, "REGISTRY", str(tmp_path / "nope.json"))
        assert W.registry_ok_results() == {}


class TestNoDuplicatedCopy:
    """`task_watch.json` 不该再持有 ok_results 的副本。"""

    def test_watch_config_has_no_ok_results(self):
        stale = [t["name"] for t in _watch()["tasks"] if "ok_results" in t]
        assert not stale, (
            "task_watch.json 里还有 ok_results 副本，会**盖住**注册表：\n  "
            + "\n  ".join(stale)
            + "\n⇒ 删掉它，让巡检只读注册表"
        )

    def test_effective_codes_match_the_registry_for_all_watched_tasks(self):
        """巡检实际生效的码 == 注册表的码（这才是唯一要保证的事）。"""
        reg = W.registry_ok_results()
        bad = []
        for t in _watch()["tasks"]:
            name = t.get("name")
            override = t.get("ok_results")
            effective = sorted(int(x) for x in override) if override else sorted(reg.get(name, [0]))
            expected = sorted(reg.get(name, [0]))
            if effective != expected:
                bad.append(f"{name}: 生效 {effective} ≠ 注册表 {expected}")
        assert not bad, "巡检口径与注册表不一致：\n  " + "\n  ".join(bad)


class TestAlertDecision:
    """把真实的三条记录喂进判定逻辑，确认不会再误报。"""

    @staticmethod
    def _judge(result: int, name: str) -> bool:
        """→ True 表示「会告警」。复刻 main() 里的 ok_codes 分支。"""
        ok_codes = [int(x) for x in W.registry_ok_results().get(name, [0])]
        return result not in ok_codes

    def test_rc6_on_goods_onsale_is_not_a_failure(self):
        """2026-10-30 08:27 那条告警的确切输入。"""
        assert self._judge(6, "TEMU-Goods-OnSale-Daily") is False

    def test_rc5_on_bel_abnormal_is_not_a_failure(self):
        assert self._judge(5, "TEMU-BEL-Abnormal-Daily") is False

    def test_rc1_on_limited_watch_is_an_alert_not_a_failure(self):
        """rc=1 是「有重点款被限流」的业务告警，脚本本身正常。"""
        assert self._judge(1, "TEMU限流监控") is False

    def test_real_failures_still_alert(self):
        """别把该报的也一起放过。"""
        assert self._judge(1, "TEMU-Goods-OnSale-Daily") is True
        assert self._judge(2, "TEMU限流监控") is True
        assert self._judge(42, "TEMU-BEL-Abnormal-Daily") is True
        assert self._judge(3, "TEMU-Goods-DailyList") is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
