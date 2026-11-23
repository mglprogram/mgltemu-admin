# -*- coding: utf-8 -*-
"""test_hx_approve_verdict.py — 「审核成败只认 successQuantity」的回归钉。

★ 2026-11-23 真机（BS 103 / BD 52 / BAN 2 单全卡「待处理」）：
  `batchApprove` 返回 `code=200`，但 `data.successQuantity = 0`、
  `data.failList[0].errorMsg = "当前仓库[...]不可用…"`。
  旧代码只看顶层 `code` ⇒ 判成「已审核」⇒ 一路到 gw_verify 回读才发现全是待处理。

这里不跑浏览器，直接静态断言 `hx_ship_gw.js` 的判定写法，防止被改回只看 code。
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_WS = os.path.dirname(os.path.dirname(_POWER))
GW = os.path.join(_WS, "scripts", "oms", "hx_ship_gw.js")


def _src() -> str:
    assert os.path.exists(GW), GW
    return open(GW, encoding="utf-8").read()


def test_approve_uses_success_quantity():
    src = _src()
    assert "successQuantity" in src, "审核判定必须看 successQuantity"
    # 首轮：okQty = code==200 && successQuantity>0
    assert re.search(r"const\s+okQty\s*=\s*j\.code\s*===\s*200\s*&&", src), \
        "首轮审核必须同时要求 code==200 与 successQuantity>0"


def test_approve_records_error_msg():
    """失败必须把 failList 的 errorMsg 带进 why，日志里直接能看到原因。"""
    src = _src()
    assert "failList" in src and "errorMsg" in src, "必须读取 failList[].errorMsg"
    assert "approve 未通过" in src, "失败原因要写进 why"


def test_retry_also_checks_success_quantity():
    """重试那一轮以前只看 aj.code === 200，同样要改。"""
    src = _src()
    assert "const aok = aj.code === 200 && Number.isFinite(asucc) && asucc > 0;" in src, \
        "重试轮也必须按 successQuantity 判定"


def test_bare_code_check_is_gone():
    """不许再出现「只看 code 就判 ok」的老写法。"""
    src = _src()
    assert "ok: j.code === 200, data:" not in src, "还有老写法：ok 只看 code"
    assert "f.retryCode = aj.code; f.ok = aj.code === 200;" not in src, "重试轮还有老写法"
