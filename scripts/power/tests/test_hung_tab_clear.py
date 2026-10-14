# -*- coding: utf-8 -*-
"""test_hung_tab_clear.py — 「跑脚本前清无响应标签页」的离线回归（不连真浏览器）。

来历（2026-09-28 实测复现，非推断）：窗口里存在一个「渲染进程无响应」的标签页时，Playwright 的
browser-level CDP 附着会卡到超时（实测 8.24s）；同一时刻原生 websocket 发
`Browser.getVersion` 仍秒回（协议层健康）；**关掉那个标签页即恢复**（0.23s）。
已排除：扩展 offscreen 页（一直在，attach 照样通）、若干空白残留页（5 个 about:blank 时仍 0.72s 通）。

本文件钉死的口径：
  ① 只对 `type == "page"` 且**确定无响应**的 target 动手；
  ② 扩展页 / devtools 页 / 非 page 一律跳过（不是触因，且关扩展页可能破坏紫鸟扩展）；
  ③ **三态语义**：`False`（连上但不回）才关；`None`（缺库/连不上/探活抛错）＝无法判定，**绝不关**
     —— 风险不对称：漏关一个卡死页只是退回老的自愈路径，误关一个正常业务页是真实损失；
  ④ 安全阀 max_probe / max_close 生效，越界的只在 skipped 里留痕；
  ⑤ 任何异常不抛（自愈/自检不许把业务拖死）。
"""
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_POWER, "lib"))

import ziniao_store as zs  # noqa: E402

CFG = {"probe_timeout_sec": 0.01, "max_probe": 10, "max_close": 10,
       "skip_url_prefixes": ["chrome-extension://", "devtools://"]}


def _t(tid, url="https://seller/x/order", typ="page"):
    return {"id": tid, "type": typ, "url": url,
            "webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/%s/%s" % (typ, tid)}


def _run(targets, alive_map, **kw):
    """alive_map：target id → True/False/None；closer 记录被关的 id。"""
    closed = []
    r = zs.close_hung_tabs(
        1234, cfg=dict(CFG, **kw),
        targets=targets,
        responder=lambda ws, to: alive_map[ws.rsplit("/", 1)[-1]],
        closer=lambda port, tid: closed.append(tid) or True)
    return r, closed


# ------------------------------------------------------------------ ②③ 只关"确定无响应"的 page
def test_closes_only_definitely_unresponsive_pages():
    r, closed = _run([_t("a"), _t("b")], {"a": True, "b": False})
    assert r["checked"] == 2
    assert r["hung"] == ["b"] and r["closed"] == ["b"] and closed == ["b"]


def test_unknown_probe_must_not_close_anything():
    """`None`＝无法判定（缺库/连不上）→ 一个都不许关。"""
    r, closed = _run([_t("a"), _t("b")], {"a": None, "b": None})
    assert r["hung"] == [] and r["closed"] == [] and closed == []
    assert all(s["why"] == "探活无法判定" for s in r["skipped"])


def test_responder_exception_treated_as_unknown():
    closed = []
    r = zs.close_hung_tabs(1234, cfg=dict(CFG), targets=[_t("a")],
                           responder=lambda ws, to: (_ for _ in ()).throw(RuntimeError("boom")),
                           closer=lambda port, tid: closed.append(tid) or True)
    assert r["closed"] == [] and closed == [], "探活抛错不得被当成无响应"


# ------------------------------------------------------------------ ① 跳过面
def test_skips_extension_devtools_and_non_page():
    targets = [_t("ext", url="chrome-extension://abc/offscreen.html"),
               _t("dv", url="devtools://devtools/bundled/x.html"),
               _t("wk", typ="worker"),
               _t("ok", url="https://seller/x/order")]
    r, closed = _run(targets, {"ok": True})
    assert r["checked"] == 1, "只有普通业务 page 进入探活"
    assert r["closed"] == [] and closed == []
    whys = " ".join(s["why"] for s in r["skipped"])
    assert "扩展/devtools页" in whys and "非page/无ws" in whys


def test_target_without_ws_url_is_skipped():
    t = _t("nows")
    t.pop("webSocketDebuggerUrl")
    r, _ = _run([t], {})
    assert r["checked"] == 0 and r["skipped"][0]["id"] == "nows"


# ------------------------------------------------------------------ ④ 安全阀
def test_max_probe_caps_probing_and_max_close_caps_closing():
    targets = [_t("t%d" % i) for i in range(5)]
    r, closed = _run(targets, {t["id"]: False for t in targets}, max_probe=3, max_close=2)
    assert r["checked"] == 3, "max_probe 生效"
    assert r["hung"] == ["t0", "t1", "t2"] and r["closed"] == ["t0", "t1"], "max_close 生效"
    assert any(s["why"] == "超 max_close 未关" for s in r["skipped"])


def test_close_failure_is_recorded_not_raised():
    r = zs.close_hung_tabs(1234, cfg=dict(CFG), targets=[_t("a")],
                           responder=lambda ws, to: False,
                           closer=lambda port, tid: (_ for _ in ()).throw(RuntimeError("被占用")))
    assert r["hung"] == ["a"] and r["closed"] == []
    assert any("关闭失败" in s["why"] for s in r["skipped"])


# ------------------------------------------------------------------ ⑤ 降级
def test_target_listing_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(zs, "list_all_targets",
                        lambda p: (_ for _ in ()).throw(RuntimeError("bridge down")))
    r = zs.close_hung_tabs(1)
    assert r["closed"] == [] and r["error"] and "bridge down" in r["error"]


# ------------------------------------------------------------------ 三态签名（tab_responds）
class _FakeWs:
    def __init__(self, mode):
        self.mode = mode

    def send(self, _s):
        return None

    def recv(self, timeout=None):
        if self.mode == "timeout":
            raise TimeoutError("no reply")
        if self.mode == "reply":
            return '{"id":1,"result":{"result":{"type":"number","value":1}}}'
        return "not-json"

    def close(self):
        return None


def _patch_ws(monkeypatch, mode=None, connect_raises=False):
    mod = types.ModuleType("websockets.sync.client")

    def _connect(uri, **kw):
        if connect_raises:
            raise OSError("connection refused")
        return _FakeWs(mode)

    mod.connect = _connect
    monkeypatch.setitem(sys.modules, "websockets.sync.client", mod)


def test_tab_responds_true_on_reply(monkeypatch):
    _patch_ws(monkeypatch, mode="reply")
    assert zs.tab_responds("ws://x", timeout=1) is True


def test_tab_responds_false_when_connected_but_silent(monkeypatch):
    """连上了但 `timeout` 内不回 ＝ **确定无响应**（实测复现的那条签名，才会触发关闭）。"""
    _patch_ws(monkeypatch, mode="timeout")
    assert zs.tab_responds("ws://x", timeout=1) is False


def test_tab_responds_none_when_cannot_connect(monkeypatch):
    _patch_ws(monkeypatch, connect_raises=True)
    assert zs.tab_responds("ws://x", timeout=1) is None


def test_tab_responds_none_on_garbage_reply(monkeypatch):
    _patch_ws(monkeypatch, mode="garbage")
    assert zs.tab_responds("ws://x", timeout=1) is None


# ------------------------------------------------------------------ 口径外置
def test_hung_tab_cfg_defaults_and_json_override(tmp_path):
    d = zs.load_hung_tab_cfg()
    assert d["probe_timeout_sec"] == 3.0 and "chrome-extension://" in d["skip_url_prefixes"]
    # 口径可被 data/hung_tabs.json 覆盖（AI 只改 json，不改代码）；缺文件回落到缺省
    p = tmp_path / "hung_tabs.json"
    p.write_text('{"probe_timeout_sec": 9, "_说明": "x"}', encoding="utf-8")
    assert zs.load_hung_tab_cfg(str(p))["probe_timeout_sec"] == 9
    assert zs.load_hung_tab_cfg(str(tmp_path / "missing.json"))["probe_timeout_sec"] == 3.0
