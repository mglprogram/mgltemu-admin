# -*- coding: utf-8 -*-
"""scripts/notify_push.py 的离线单测（不真的发消息）。

跑：python -m pytest scripts/tests -q
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # scripts/

import notify_push                                 # noqa: E402


# ------------------------------------------------------------------ 飞书
class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_feishu_ok(monkeypatch):
    monkeypatch.setattr(notify_push, "feishu_webhook", lambda: "https://hook/x")
    seen = {}

    def fake_open(req, timeout=None):
        seen["body"] = json.loads(req.data.decode())
        return _FakeResp({"code": 0, "msg": "success"})

    monkeypatch.setattr(notify_push.urllib.request, "urlopen", fake_open)
    ok, detail = notify_push.push_feishu("TEMU 标题", "正文", url="https://u")
    assert ok and detail == "OK"
    text = seen["body"]["content"]["text"]
    assert "TEMU 标题" in text and "正文" in text and "https://u" in text


def test_feishu_keyword_rejection_is_explained(monkeypatch):
    """19024 = 群机器人关键词校验没过。报错必须说清「标题要含 TEMU」，
    否则下次还是照着改文案瞎试。"""
    monkeypatch.setattr(notify_push, "feishu_webhook", lambda: "https://hook/x")
    monkeypatch.setattr(notify_push.urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp({"code": 19024, "msg": "x"}))
    ok, detail = notify_push.push_feishu("标题", "正文")
    assert not ok
    assert "19024" in detail and "TEMU" in detail


def test_feishu_without_webhook_is_a_quiet_skip(monkeypatch):
    monkeypatch.setattr(notify_push, "feishu_webhook", lambda: "")
    ok, detail = notify_push.push_feishu("t", "m")
    assert not ok and "FEISHU_WEBHOOK" in detail


def test_feishu_never_raises(monkeypatch):
    monkeypatch.setattr(notify_push, "feishu_webhook", lambda: "https://hook/x")

    def boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(notify_push.urllib.request, "urlopen", boom)
    ok, detail = notify_push.push_feishu("t", "m")
    assert not ok and "network down" in detail


# ------------------------------------------------------------------ 手机
def test_meow_never_raises(monkeypatch):
    """手机通道坏了只能少一条消息，不能把调用方搞崩。"""
    class Boom:
        @staticmethod
        def send(*a, **kw):
            raise RuntimeError("429 IP限制")

    monkeypatch.setattr(notify_push, "meow_notify", Boom)
    ok, detail = notify_push.push_meow("t", "m")
    assert not ok and "429" in detail


def test_meow_missing_module_degrades(monkeypatch):
    monkeypatch.setattr(notify_push, "meow_notify", None)
    ok, detail = notify_push.push_meow("t", "m")
    assert not ok and "meow_notify" in detail


# ------------------------------------------------------------------ 组合
def test_push_isolates_channels(monkeypatch):
    """一个通道失败不能影响另一个 —— 飞书挂了手机还得发出去。"""
    monkeypatch.setattr(notify_push, "push_feishu", lambda *a, **kw: (False, "boom"))
    monkeypatch.setattr(notify_push, "push_meow", lambda *a, **kw: (True, "OK"))
    res = notify_push.push("t", "m")
    assert res["feishu"] == (False, "boom")
    assert res["meow"] == (True, "OK")


def test_push_can_select_channels(monkeypatch):
    called = []
    monkeypatch.setattr(notify_push, "push_feishu",
                        lambda *a, **kw: called.append("feishu") or (True, "OK"))
    monkeypatch.setattr(notify_push, "push_meow",
                        lambda *a, **kw: called.append("meow") or (True, "OK"))
    notify_push.push("t", "m", channels=("meow",))
    assert called == ["meow"]


def test_brief_is_one_line():
    line = notify_push.brief({"feishu": (True, "OK"), "meow": (False, "429 限流")})
    assert "\n" not in line
    assert "feishu:OK" in line and "meow:FAIL" in line and "429" in line


def test_keyword_constant_is_temu():
    """本机所有已成功的飞书推送都含 TEMU；改了这里等于悄悄让告警发不出去。"""
    assert notify_push.FEISHU_KEYWORD == "TEMU"
