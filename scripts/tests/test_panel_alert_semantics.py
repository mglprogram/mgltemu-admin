#!/usr/bin/env python3
"""钉住「面板的失败/告警口径必须与现实一致」。

2026-11-03 用户截图里的三处误报，都是**同一类问题**：面板自己另立了一套口径。

  ① `TEMU-Pending-Confirm-Daily` 显示「失败」。
     它的 rc=1 = 「有单在领星 LM739 里查不到，结论不可下」，**注册表 ok_results=[0,1]、
     巡检 watch_tasks 都不告警** —— 只有面板不知道，因为它只看 RC_MAP 的「非零即失败」。
     用户看到的是一整片红，而系统其他地方都认为没事。

  ② `TEMU-Presale-Notify` 的原因一栏写着 `Traceback (most recent call last):`。
     那句话一个信息量都没有，真因（`TimeoutExpired: ... timed out`）在它下面二十行。
     `_STRONG` 命中 "Traceback"，然后把这一行原样返回了。

  ③ `TEMU-Pending-Confirm-Daily` 的原因一栏写着
     `[守卫] 占用检测不可用… → 跳过，不阻断`。
     这行恰恰在说"不影响结果"，报它等于把读者引向一个不存在的问题，
     并且把真正的失败（端口读不到）挤掉了。

跑法：
    pytest scripts/tests/test_panel_alert_semantics.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))
sys.path.insert(0, str(PROJ / "scripts" / "bel_goods"))

import log_cause  # noqa: E402


# ---------------------------------------------------------------- ① ok_results 口径
class TestOkResultsDrivesThePanel:
    """注册表的 `ok_results` 是"哪些码算正常"的唯一事实来源。

    巡检（watch_tasks.registry_ok_results）早就按它放过 rc=1/5/6；
    面板若还按「非零即失败」判，用户就会看到一片红，而系统认为一切正常。
    """

    def _mod(self):
        import build_automation_data as B
        return B

    def test_registry_ok_is_loaded(self):
        B = self._mod()
        assert hasattr(B, "REGISTRY_OK"), "面板没读注册表的 ok_results"
        assert B.REGISTRY_OK, "读到的是空表（路径或字段名错了？）"

    def test_pending_confirm_rc1_is_an_alert_not_a_failure(self):
        B = self._mod()
        sem = B.TASK_RC_SEMANTICS.get("TEMU-Pending-Confirm-Daily") or {}
        assert 1 in sem, "pending-confirm 的 rc=1 没有语义覆盖，面板会显示「失败」"
        status, why = sem[1]
        assert status == "alert", "rc=1 是业务告警（查不到就是结论不可下），不是故障"
        assert "LM739" in why or "领星" in why, "告警文案没说清 rc=1 是什么意思"

    def test_ok_results_codes_never_render_as_fail(self):
        """凡是注册表放行的非 0 码，面板都不能显示成 fail（除非有更精确的语义说它是失败）。"""
        B = self._mod()
        bad = []
        for name, codes in B.REGISTRY_OK.items():
            sem = B.TASK_RC_SEMANTICS.get(name) or {}
            for c in codes:
                try:
                    c = int(c)
                except (TypeError, ValueError):
                    continue
                if c == 0:
                    continue
                if c in sem:
                    st = sem[c][0]
                    if st == "fail":
                        bad.append("%s rc=%s：注册表放行，语义表却说失败" % (name, c))
        assert not bad, "口径自相矛盾：\n  " + "\n  ".join(bad)


class TestHumanReasonWording:
    """「原因」一栏要说人话，而且不能把"不阻断"当失败原因。"""

    def _human(self, **kw):
        import build_automation_data as B
        return B.human_reason(**kw)

    def test_alert_says_explicitly_it_is_not_a_fault(self):
        got = self._human(status="alert", rc=1, state="Ready", last="2026-11-03T14:45:00",
                          act="", name="TEMU-Pending-Confirm-Daily")
        assert "不是故障" in got
        assert "Traceback (most recent call last)" not in got

    def test_posting_says_not_a_fault(self):
        """rc=5/6 是"按设计跳过"，不能出现在 problem 里。"""
        import build_automation_data as B
        assert B.RC_MAP[5][0] == "waiting"
        assert B.RC_MAP[6][0] == "waiting"


# ---------------------------------------------------------------- ② Traceback 行
class TestTracebackResolution:
    """`Traceback (most recent call last):` 本身没有信息，真因在它下面。"""

    def _cause(self, text, tmp_path, name="T"):
        p = tmp_path / "x.log"
        p.write_text(text, encoding="utf-8")
        return log_cause.last_cause(str(p))

    def test_returns_the_exception_line_not_the_marker(self, tmp_path):
        got = self._cause(
            "Traceback (most recent call last):\n"
            '  File "a.py", line 1, in <module>\n'
            "    raise SystemExit(main())\n"
            "subprocess.TimeoutExpired: Command 'x' timed out after 300 seconds\n",
            tmp_path)
        assert "Traceback (most recent call last)" not in got, (
            "还是那句没有信息量的标记行")
        assert "TimeoutExpired" in got or "timed out" in got

    def test_the_last_exception_wins(self, tmp_path):
        """traceback 里最后一个异常才是真正抛出的。"""
        got = self._cause(
            "Traceback (most recent call last):\n"
            "ValueError: inner\n"
            "RuntimeError: outer is the real one\n",
            tmp_path)
        assert "outer" in got

    def test_keyboard_interrupt_alone_is_not_a_cause(self, tmp_path):
        """只有 KeyboardInterrupt ⇒ 那是被打断，不是失败原因。"""
        got = self._cause(
            "Traceback (most recent call last):\n"
            "  File \"b.py\", line 1, in <module>\n"
            "KeyboardInterrupt\n",
            tmp_path)
        assert "KeyboardInterrupt" not in got

    def test_does_not_bleed_into_the_next_traceback(self, tmp_path):
        """两段 traceback 相邻时，不能把下一段的异常算到这一段头上。"""
        got = self._cause(
            "Traceback (most recent call last):\n"
            "KeyboardInterrupt\n"
            "Traceback (most recent call last):\n"
            "OSError: disk full\n",
            tmp_path)
        assert "disk full" in got


# ---------------------------------------------------------------- ③ "不阻断"噪音
class TestNotBlockingIsNotACause:
    """脚本自己说"不阻断"的行，不可能是失败原因。"""

    def test_guard_noise_is_skipped(self, tmp_path):
        p = tmp_path / "x.log"
        p.write_text(
            "[守卫] 占用检测不可用（缺 node/raw_cdp.js 或探测失败）→ 跳过，不阻断\n"
            "[HARD STOP] 未探测到窗口 TM-BHY-YJF 的 CDP 端口\n",
            encoding="utf-8")
        got = log_cause.last_cause(str(p))
        assert "占用检测" not in got, "把「不阻断」当成了失败原因"
        assert "CDP 端口" in got or "HARD STOP" in got

    def test_nothing_but_noise_returns_empty(self, tmp_path):
        p = tmp_path / "x.log"
        p.write_text("[授权兜底] ⚠ 超时（300s）→ 不阻断，交业务自行报错\n",
                     encoding="utf-8")
        assert log_cause.last_cause(str(p)) == "", (
            "整份日志只有噪音时应该返回空，而不是把噪音报成原因")


class TestRealLogsRightNow:
    """拿当前真实日志跑一遍：不允许出现那两种"等于没解释"的输出。"""

    def _cause(self, task):
        return log_cause.cause_for(task)

    @pytest.mark.parametrize("task", [
        "TEMU-Pending-Confirm-Daily",
        "TEMU-Presale-Notify",
        "TEMU-Today-Sales",
        "TEMU限流监控",
    ])
    def test_cause_is_never_a_bare_traceback_marker(self, task):
        got = self._cause(task)
        assert "Traceback (most recent call last)" not in got

    @pytest.mark.parametrize("task", [
        "TEMU-Pending-Confirm-Daily",
        "TEMU-Presale-Notify",
    ])
    def test_cause_is_never_pure_noise(self, task):
        """「不阻断」只有在它**没有带任何真实报错**时才算噪音。

        `[待确认] 复核读回失败（不阻断）：server rejected WebSocket ... HTTP 500`
        是真报错，必须留着；`→ 跳过，不阻断` 才是纯噪音。
        """
        got = self._cause(task)
        if not got:
            return
        assert "占用检测" not in got or "HTTP" in got, (
            "把守卫的「不可用 → 跳过，不阻断」报成了失败原因")
        if "不阻断" in got:
            assert any(t in got for t in ("HTTP", "Error", "Traceback", "timed out",
                                          "失败：", "refused")), (
                "这一行只在说「不阻断」，不是失败原因：%s" % got)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
