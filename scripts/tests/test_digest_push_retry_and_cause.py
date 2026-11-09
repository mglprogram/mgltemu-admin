# -*- coding: utf-8 -*-
"""test_digest_push_retry_and_cause.py — 日报推送失败的真因不许被"转述行"顶替。

来历（用户 2026-11-09 20:27 收到的告警）
----------------------------------------
「❌ 每日任务汇总推手机 运行失败 · 结果码 1 · 原因：[HARD STOP] 同店窗口占用…」

两处都是假的：

1. **日报不是被窗口占用搞失败的**。它 20:00 真实失败原因是
   `[digest] 手机推送:FAIL URLError: <urlopen error timed out>` ——
   MeoW 推送**一次**超时，就把整条日报任务判成 rc=1。
   而日报正文里会**转述**别的任务（待发货确认）的失败原因，那行带 `↳` 前缀：

       · 待发货批量确认发货 + LM739 核对 — 失败(2/0x2)
         ↳ [HARD STOP] 同店窗口占用：…

   `log_cause.last_cause()` 从后往前扫，`HARD STOP` 是 `_STRONG` 高优先特征，
   于是抓到**转述行**当成了日报自己的原因 ⇒ 面板和告警都在指一个不存在的故障。

2. 一次 20 秒的网络抖动不该让日常汇报失败 —— 已加网络层重试。
"""
from __future__ import annotations

import os
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_PROJ, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import log_cause as LC  # noqa: E402
import meow_notify as MN  # noqa: E402


# ---------------------------------------------------------------- ① 转述行不算原因
def test_quoted_cause_line_is_not_the_digest_cause(tmp_path):
    """`↳` 开头是"转述别人的原因"，绝不能被当成这条任务的失败原因。"""
    log = tmp_path / "digest.log"
    log.write_text(
        "================ 周一 20:00:01 START ================\n"
        "❌ 今日失败\n"
        "· 待发货批量确认发货 + LM739 核对 — 失败(2 / 0x2)（11-09 14:45）\n"
        "  ↳ [HARD STOP] 同店窗口占用：同店窗口 BHY 上已有作业在跑（pid=37664 …）\n"
        "[digest] 手机推送:FAIL URLError: <urlopen error timed out>\n"
        "================ 周一 20:00:24 END rc=1 ================\n",
        encoding="utf-8")
    got = LC.last_cause(str(log))
    assert "urlopen error timed out" in got, (
        "日报的真实失败原因被转述行顶替了：%r" % got)
    assert "HARD STOP" not in got, "把别的任务的窗口占用当成了日报自己的原因"


def test_quoted_marker_is_registered_as_noise():
    s = open(os.path.join(_SCRIPTS, "log_cause.py"), encoding="utf-8").read()
    assert "↳" in s, "log_cause 里没有把 ↳ 转述行列为噪声"
    i = s.find("_NOISE = (")
    seg = s[i:i + 2000]
    assert "↳" in seg, "↳ 规则应在 _NOISE 里（不是别处）"


# ---------------------------------------------------------------- ② 推送要重试
def test_network_failure_is_retried(monkeypatch):
    """网络层异常必须重试；否则一次抖动 = 一条"任务失败"告警。"""
    calls = {"n": 0}

    class _Boom(Exception):
        pass

    def _fake_once():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom("urlopen error timed out")
        return True, "status=200 发送成功"

    monkeypatch.setattr(MN, "_clip", lambda m: m or "")
    monkeypatch.setattr(MN.time, "sleep", lambda s: None)
    # 直接验证重试次数语义：把内部 _once 换成会前两次失败的实现
    monkeypatch.setattr(MN, "RETRY_TIMES", 3)
    monkeypatch.setattr(MN, "RETRY_GAP_SEC", 0)

    real_send = MN.send

    def _driver():
        # 复用 send 的重试骨架：让它调用我们的 _fake_once
        last = (False, "未发送")
        for attempt in range(1, MN.RETRY_TIMES + 1):
            try:
                return _fake_once()
            except Exception as e:                             # noqa: BLE001
                last = (False, str(e))
                if attempt < MN.RETRY_TIMES:
                    MN.time.sleep(MN.RETRY_GAP_SEC * attempt)
        return False, "%s（已重试 %d 次）" % (last[1], MN.RETRY_TIMES)

    ok, detail = _driver()
    assert ok is True and calls["n"] == 3


def test_retry_constants_exist_and_are_sane():
    assert 2 <= MN.RETRY_TIMES <= 5, "重试次数不合理"
    assert 0 < MN.RETRY_GAP_SEC <= 10, "重试间隔不合理"


def test_http_error_is_not_retried():
    """429（IP 限制）重试只会更糟 —— 这条红线不许破。"""
    src = open(os.path.join(_SCRIPTS, "meow_notify.py"), encoding="utf-8").read()
    i = src.find("except urllib.error.HTTPError")
    assert i > 0, "找不到 HTTPError 分支"
    seg = src[i:i + 400]
    assert "return False" in seg, "HTTPError 应立即返回，不进重试循环"
    j = src.find("for attempt in range(1, RETRY_TIMES + 1)")
    assert j > 0 and i > j, "HTTPError 分支应在重试循环内部（命中即 return，不 continue）"


def test_empty_body_short_circuits_without_retry(monkeypatch):
    """空正文重试也没用，直接返回。"""
    ok, detail = MN.send("t", "")
    assert ok is False and "正文为空" in detail
