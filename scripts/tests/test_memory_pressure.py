#!/usr/bin/env python3
"""钉住「内存不足必须被巡检直接报出来」。

2026-11-03 用户追问「卡死页反复出现，值得单独检查」。
查下来根因**不是页面有 bug**，而是物理内存耗尽：

    物理内存 16,069 MB，可用 **133 MB（99% 占用）**
    提交量 44,122 / 48,381 MB
    ziniaobrowser 8,054MB、chrome 4,683MB、WorkBuddy 2,836MB、ima 2,228MB

而它**表现出来全是别的东西**，这是它藏了这么久的原因：

| 表面症状 | 实际原因 |
|---|---|
| 「执行去订单列表 一直获取不到登录态」 | 卡死页上读一次 `.url` 要 60 秒，烧光 300 秒兜底预算 |
| `server rejected WebSocket connection: HTTP 500` | 页面被换页出去，唤醒超时 |
| 「页面已死 → 换页后重试」然后那单失败 | 同上 |
| 卡死页反复出现、修好又犯 | 内存没释放，重载只是重新抢内存 |

反直觉的一处恰恰是证据：卡死页所在窗口的内存**更低**
（BEL 375MB / 2 页卡死 vs BHY 999MB / 0 页卡死）—— 紧张时页面被换页出去，
常驻内存变小，但唤醒要重新换入。

⇒ 本文件钉住三件事：
  ① 巡检能报出内存不足（否则永远是打地鼠）
  ② 紧张时**不许导航修复**（实测：10:36 一页 → 10:39 导航 → 10:42 三页）
  ③ 探活必须复核（忙页面 5.3 秒才答，4 秒预算会误判成"死"）

跑法：
    pytest scripts/tests/test_memory_pressure.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

PROJ = Path(r"D:\AI-workplace\TEMU")
sys.path.insert(0, str(PROJ / "scripts"))
sys.path.insert(0, str(PROJ / "scripts" / "power" / "lib"))

import auth_guard


class TestWatcherReportsMemoryPressure:
    """巡检必须把「内存不足」当成一条**独立告警**报出来。"""

    def test_watcher_has_a_memory_alert(self):
        import watch_tasks as W
        assert hasattr(W, "_memory_alert"), "看护脚本没有内存告警"
        assert callable(W._memory_alert)

    def test_alert_threshold_is_shared_with_the_guard(self):
        """两个阈值要一致，否则「看护说紧张、兜底说充足」自相矛盾。"""
        import watch_tasks as W
        assert int(W.MEM_ALERT_MB) == int(auth_guard.MEM_TIGHT_MB), (
            "watch_tasks.MEM_ALERT_MB(%s) ≠ auth_guard.MEM_TIGHT_MB(%s)"
            % (W.MEM_ALERT_MB, auth_guard.MEM_TIGHT_MB))

    def test_memory_alert_never_raises(self):
        """它跑在巡检主流程里，坏了只能少一条告警，不能把整轮巡检搞挂。"""
        import watch_tasks as W
        assert isinstance(W._memory_alert(), str)

    def test_memory_alert_mentions_the_real_symptoms(self):
        """正文必须说明「这不是脚本问题」，否则收到告警的人还是会去查脚本。"""
        import watch_tasks as W
        src = inspect.getsource(W._memory_alert)
        assert "卡死" in src and ("登录态" in src or "HTTP 500" in src), (
            "告警没说清它在表面上的症状 —— 收到的人会去查错方向")
        assert "关" in src, "告警没给出可操作建议（关掉不用的程序）"

    def test_memory_alert_is_NOT_wired_into_main(self):
        """★ 2026-11-09 反转：内存告警**不许**再接进 main() 的推送列表。

        用户当天发来截图（一条每 30 分钟就来一次的「本机内存不足」）并明确说
        「把这个定时任务删除，无意义的推送通知」。

        为什么它确实是噪音：这台 16 GB 机器常驻紫鸟多店窗口 + Chrome + WPS + DSH，
        可用内存长期 300~700 MB，而阈值 800 MB ⇒ 几乎每轮命中、一天几十条。
        收件人既不能关那些程序（正是干活要用的），也无法据此做决定。

        ⚠ 关掉的只是**推送**：`_memory_alert()` 函数保留（`--memory` 手工可查），
        `auto_repair.mem_is_tight()` / `auth_guard` 照旧用它决定
        「紧张时不要重载页面 / 不要自动重跑」—— 那部分行为一个字没改。
        """
        import watch_tasks as W
        src = inspect.getsource(W.main)
        code = [ln for ln in src.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
        offenders = [ln for ln in code if "_memory_alert()" in ln]
        assert not offenders, (
            "内存告警又被接回 main() 了（用户已明确否掉这条推送）：%s" % offenders)

    def test_alert_is_suppressed_when_memory_is_fine(self, monkeypatch):
        """内存充足时必须返回空串，否则每 30 分钟一条假告警。"""
        import watch_tasks as W
        monkeypatch.setattr(W, "MEM_ALERT_MB", 0)
        assert W._memory_alert() == "", "阈值设成 0 时仍报警"


class TestGuardRefusesToHealUnderPressure:
    """内存紧张时，导航修复是**加害**（实测把 1 页卡死变成 3 页）。"""

    def test_threshold_exists_and_is_sane(self):
        assert hasattr(auth_guard, "MEM_TIGHT_MB")
        assert 200 <= int(auth_guard.MEM_TIGHT_MB) <= 4000

    def test_probe_reports_available_memory(self):
        v = auth_guard.avail_phys_mb()
        assert isinstance(v, int)
        assert v == -1 or 0 <= v <= 1024 * 1024

    def test_tight_branch_still_refreshes_hung_pages(self):
        """★ 2026-11-12 反转：内存紧张**也要**刷新卡死页。

        用户原话：「卡死这种情况直接重新刷新就可以了」。

        旧断言是「紧张分支里不许出现 navigate_hung_page」，依据是 2026-11-03 那次
        「1 页卡死 → 导航修复 → 3 页卡死」。但那是把因果搞反了：3 页卡死是因为内存
        本来就在被持续吃掉。而"紧张就不修"的代价很硬 —— 11-12 08:00 的在售款导出
        在死页上跑了 8.5/10.8 分钟、零输出、零产出 xlsx，最后被强杀。

        ⇒ 现在内存只写进日志，不再是拦截条件；`navigate_hung_page` 必须留在主路径上。
        """
        src = inspect.getsource(auth_guard.heal_own_hung_pages)
        # 只看**代码行**（注释里会提到旧写法，不能拿它当"还有拦截"的证据）
        code = [ln for ln in src.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
        after_loop = None
        for i, ln in enumerate(code):
            if "for t in cdp.list_pages" in ln:
                after_loop = i
                break
        assert after_loop is not None, "找不到逐页循环"
        buf = []
        for ln in code[after_loop:]:
            if "navigate_hung_page" in ln:
                break
            buf.append(ln)
        else:
            raise AssertionError("循环里没有 navigate_hung_page —— 卡死页不会被刷新了")
        assert "if tight" not in "\n".join(buf), (
            "navigate_hung_page 之前又加了 tight 拦截：内存紧张时卡死页会没人修")

    def test_own_pages_still_closed(self):
        """自有页在紧张时**更要关**（关掉＝释放内存），不能被阈值挡住。"""
        src = inspect.getsource(auth_guard.heal_own_hung_pages)
        own = src.split("if tid in mine")[1].split("continue")[0]
        assert "recover_hung_page" in own
        assert "tight" not in own, "自有页的关闭不该受内存阈值影响"

    def test_skipped_is_reported_not_swallowed(self):
        """跳过的用户页要**说出来**，否则「没修」和「不用修」看起来一样。"""
        src = inspect.getsource(auth_guard.heal_own_hung_pages)
        assert "skipped" in src
        assert "未处理" in src or "跳过" in src


class TestProbeDoesNotCryWolf:
    """`probe_page_alive` 的假阳性会直接导致误伤用户的页。"""

    def test_default_confirms_before_hung(self):
        sig = inspect.signature(__import__("cdp_transport").probe_page_alive)
        assert "confirm" in sig.parameters
        assert sig.parameters["confirm"].default is True, (
            "默认必须复核 —— 否则每一处调用都在冒险误判忙页面")

    def test_confirm_timeout_is_longer_than_first_probe(self):
        import cdp_transport as cdp
        assert cdp._CONFIRM_HUNG_TIMEOUT >= 10, (
            "复核预算太小起不到作用（实测忙页面 5.3 秒才答）")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
