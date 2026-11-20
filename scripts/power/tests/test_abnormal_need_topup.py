# -*- coding: utf-8 -*-
"""test_abnormal_need_topup.py — 「平台欠费」不许被说成「脚本点不动」。

背景（2026-11-20 用户贴的告警）
--------------------------------------------------------------------
BEL 合作仓异常单只有 1 条 `PO-211-04726719109751683`，页面「异常原因」栏原文是：

    合作仓[订单下发]接口异常，服务商返回：下单成功0条,失败1条 …
    用户信用额度不足,待付金额为 -7094.27
    （操作指导）请先在海外仓系统内完成充值，再点击重发。

而任务报出来的原因却是 `点「转为卖家履约」失败：NO_POPOVER` —— 读起来像点击 bug：

· 巡检照着"脚本自报失败"自动重跑了 3 次（8:32 / 8:34 / 9:03 / 9:29 / 9:57），
  每次都在同一个点不动的弹窗上白跑；
· 告警也没告诉人「去充值」。

修法（三处，本文件逐一钉住）：
  ① 驱动：点之前先读行内原因，命中充值特征 → 直接 `NEED_TOPUP` 跳过点击、不再重试；
  ② `log_cause.cause_kind()`：把这类失败标成 `topup`；
  ③ `watch_tasks`：`topup` 类**不自动重跑**，告警写明「需先充值，不是脚本 bug」。

跑法: python -m pytest scripts/power/tests/test_abnormal_need_topup.py -q
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ = os.path.join(_HERE, "..", "..", "..")
sys.path.insert(0, os.path.join(_PROJ, "scripts"))
sys.path.insert(0, os.path.join(_PROJ, "scripts", "power", "lib"))

import log_cause as L  # noqa: E402

PAGE_REASON = ("合作仓[订单下发]接口异常，服务商返回：下单成功0条,失败1条 "
               "CW012611191065696287189258240更新失败-用户信用额度不足,待付金额为 -7094.27<br/>")
PAGE_GUIDE = "请先在海外仓系统内完成充值，再点击重发。"


class TestNeedsTopupDetection:
    def test_recognises_the_real_page_text(self):
        assert L.needs_topup(PAGE_REASON) is True
        assert L.needs_topup(PAGE_GUIDE) is True

    def test_plain_click_error_is_not_topup(self):
        """普通的点击失败不能被误判成欠费（否则真 bug 就没人查了）。"""
        assert L.needs_topup("点「转为卖家履约」失败：NO_POPOVER") is False
        assert L.needs_topup("点「重发」后浮层未出现") is False
        assert L.needs_topup("") is False

    def test_never_raises(self):
        assert L.needs_topup(None) is False


class TestDriverChecksRowBeforeClicking:
    def test_driver_has_topup_precheck(self):
        import io
        p = os.path.join(_PROJ, "scripts", "power", "lib", "temu_abnormal_driver.py")
        src = io.open(p, encoding="utf-8").read()
        assert "_JS_ROW_REASON" in src, "没有读行内原因列的 JS"
        assert "_row_topup_hint" in src
        # 预检必须在 `_convert_one` 的**最前面**（在点「重发」之前）
        i = src.find("def _convert_one")
        body = src[i:i + 900]
        assert "_row_topup_hint(page, po)" in body
        assert body.find("_row_topup_hint") < body.find("_clear_popovers"), (
            "预检必须在清浮层/点重发之前")
        assert "NEED_TOPUP" in body

    def test_topup_failure_is_not_retried(self):
        """`NEED_TOPUP` 只试一次：弹窗永远不会出现，重试纯属浪费窗口时间。"""
        import io
        p = os.path.join(_PROJ, "scripts", "power", "lib", "temu_abnormal_driver.py")
        src = io.open(p, encoding="utf-8").read()
        assert '"NEED_TOPUP" in err' in src
        i = src.find('"NEED_TOPUP" in err')
        assert "break" in src[i:i + 300], "命中欠费后没有跳出重试循环"


class TestWatcherDoesNotRetryTopup:
    def test_watcher_branches_on_cause_kind(self):
        import io
        p = os.path.join(_PROJ, "scripts", "watch_tasks.py")
        src = io.open(p, encoding="utf-8").read()
        assert "cause_kind(name)" in src
        assert 'kind == "topup"' in src
        # 不重跑（结构上仍调用 maybe_repair，但 topup 时丢弃它的返回值）
        assert 'kind == "topup"' in src and "fix = None" in src, "欠费任务仍会自动重跑"
        # 告警必须说人话
        assert "不是脚本 bug" in src and "充值" in src


class TestCauseKind:
    def test_cause_kind_returns_topup_for_this_log(self, tmp_path, monkeypatch):
        log = tmp_path / "abn.log"
        log.write_text(
            "[③] PO-211-04726719109751683 第 1 次未成（点「转为卖家履约」失败：NO_POPOVER）\n"
            "合作仓[订单下发]接口异常 用户信用额度不足,待付金额为 -7094.27\n"
            "请先在海外仓系统内完成充值，再点击重发。\n",
            encoding="utf-8")
        monkeypatch.setattr(L, "registry_log_path", lambda name: str(log))
        monkeypatch.setattr(L, "agent_verdict", lambda name, **kw: {})
        assert L.cause_kind("X") == "topup"

    def test_cause_kind_empty_for_generic_failure(self, tmp_path, monkeypatch):
        log = tmp_path / "abn.log"
        log.write_text("[③] PO-1 失败：点「重发」后浮层未出现\n", encoding="utf-8")
        monkeypatch.setattr(L, "registry_log_path", lambda name: str(log))
        monkeypatch.setattr(L, "agent_verdict", lambda name, **kw: {})
        assert L.cause_kind("X") == ""
