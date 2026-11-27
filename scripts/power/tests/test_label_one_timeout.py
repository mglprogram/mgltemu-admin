# -*- coding: utf-8 -*-
"""test_label_one_timeout.py — 买面单的**单笔超时**守门。

★★ 2026-11-27 实测事故：
    `label_pw` 跑到加州3仓段第 54 单后，日志 **8 分钟零输出**，进程 CPU 只用 5 秒
    （纯阻塞在网络等待），最后只能人工 kill 掉整条链。
    根因：`place_one` 内部是多段 HTTP（查渠道 → 算价 → 查揽收时段 → 提交），
    任一段 socket 无响应且没读超时，就会无限等。89 单里卡一单，整批全废。

修法：`_place_one_timeout()` 把单笔丢进守护线程，主线程 `join(timeout)`。
      超时返回 `{ok:False, timeout:True}`，**循环继续下一单**。
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for _p in (_POWER, os.path.join(_POWER, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_temu_fulfill as F  # noqa: E402


def _goods(po="PO-TEST"):
    return [{"order_sn": "x", "parent_order_sn": po,
             "goods_id": 1, "sku_id": "1", "quantity": 1}]


def test_timeout_wrapper_exists():
    assert hasattr(F, "_place_one_timeout"), "没有单笔超时包装"


def test_hanging_place_one_is_abandoned(monkeypatch):
    """卡死的 place_one 必须在超时后被放弃，不能拖死整批。"""
    import label_place as LP  # noqa: PLC0415

    def _hang(*a, **kw):
        time.sleep(120)
        return {"ok": True}

    monkeypatch.setattr(LP, "place_one", _hang)
    monkeypatch.setenv("TEMU_LABEL_ONE_TIMEOUT", "2")

    t0 = time.time()
    r = F._place_one_timeout("KINGLAMANSPORT", _goods("PO-HANG"), {}, "加州3仓",
                             apply=False, log=lambda m: None, timeout_s=2)
    el = time.time() - t0
    assert el < 10, "超时没生效，等了 %.1fs" % el
    assert r.get("ok") is False, "卡死的单不能报成功"
    assert r.get("timeout") is True, "要标记 timeout，便于统计"
    assert "超时" in str(r.get("why")), "why 要写清是超时"


def test_exception_inside_worker_does_not_crash_batch(monkeypatch):
    """单笔抛异常也要变成一条失败结果，不能把整批打崩。"""
    import label_place as LP  # noqa: PLC0415

    def _boom(*a, **kw):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(LP, "place_one", _boom)
    r = F._place_one_timeout("KINGLAMANSPORT", _goods("PO-ERR"), {}, "加州3仓",
                             apply=False, log=lambda m: None, timeout_s=10)
    assert r.get("ok") is False
    assert "HTTP 500" in str(r.get("why"))


def test_timeout_is_configurable_by_env():
    """允许按环境调（慢网络时放宽），并有默认值。"""
    src = open(os.path.join(_POWER, "run_temu_fulfill.py"), encoding="utf-8").read()
    i = src.find("def _place_one_timeout")
    body = src[i:i + 2000]
    assert "TEMU_LABEL_ONE_TIMEOUT" in body, "没给环境变量开关"
    assert "120" in body, "默认超时应在 120s 左右（实测单笔约 6~12s）"


def test_batch_calls_the_wrapper_not_place_one_directly():
    """`_label_batch` 的接口版必须走包装 —— 直接调 place_one 就没有超时保护了。"""
    src = open(os.path.join(_POWER, "run_temu_fulfill.py"), encoding="utf-8").read()
    i = src.find("def _label_batch(")
    j = src.find("def _label_batch_click(")
    body = src[i:j]
    assert "_place_one_timeout(" in body, "_label_batch 没走超时包装"
    assert "_LP.place_one(" not in body, "_label_batch 还在直接调 place_one（无超时）"
