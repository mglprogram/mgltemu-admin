# -*- coding: utf-8 -*-
"""test_ziniao_login_gate.py — 「跑前先自检紫鸟登录态」的离线回归（不碰网络、不起浏览器）。

来历（2026-09-28 事故写回）：紫鸟客户端 09-27 12:27 自行登出（`user logout, trigger_reason:5`），
此后所有依赖店窗口的自动化全部停在「未探测到窗口 X 的 CDP 端口」——**报的是窗口，真因是没登录**，
从 12:27 到次日 09:35 无人发现，白丢一整天。于是把「登录态」做成跑前自检。

本文件钉死判定（唯一源见 `lib/ziniao_store.py` 的 LOGIN_* 段）：
  ① 只有「**明确未登录**」（`ok:false` + `error.type=auth` / message 含 not logged in / 未登录）才拦；
  ② Boss 权限类错误（同为 `type=auth`，但不是"没登录"）**不拦**；
  ③ 超时 / 桥故障 / 输出无法解析 / 空输出 → **不拦**（宁可放行走老路失败，也不误杀正常作业）；
  ④ 四个入口（发货更新 / 库存快照 / TEMU 导出 / TK 导出）在**动手前真的调了**它（静态守卫）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_POWER, "lib"))

import ziniao_store as zs  # noqa: E402

OK_JSON = ('{"ok":true,"data":{"items":[{"storeId":"1"},{"storeId":"2"}]},'
           '"_notice":{"update":{"current":"1.1.3"}}}')
NO_JSON = '{"ok":false,"error":{"type":"auth","message":"Ziniao user is not logged in"}}'
NO_JSON_CN = '{"ok":false,"error":{"type":"auth","message":"紫鸟用户未登录"}}'
BOSS_JSON = '{"ok":false,"error":{"type":"auth","message":"此操作需要 Boss 权限，当前为成员账号"}}'

#: 会被自检的四个入口（相对 scripts/power/）
ENTRIES = ["run_fahuo_update.py", "run_stock_agg.py", "run_temu_sales.py", "run_daily_sales.py"]


# ------------------------------------------------------------------ ① 判定（纯函数）

def test_ok_passes_and_reports_store_count():
    state, detail = zs._login_verdict(OK_JSON)
    assert state == zs.LOGIN_OK, detail
    assert "2" in detail


def test_explicit_not_logged_in_is_blocked():
    assert zs._login_verdict(NO_JSON)[0] == zs.LOGIN_NO


def test_explicit_not_logged_in_cn_is_blocked():
    assert zs._login_verdict(NO_JSON_CN)[0] == zs.LOGIN_NO


def test_err_wrapper_shape_is_still_parsed():
    """`ziniao()` 会把 rc!=0 的输出包成 "ERR rc=N {json}" —— 未登录真实形态就是它。"""
    assert zs._login_verdict("ERR rc=1 " + NO_JSON)[0] == zs.LOGIN_NO


def test_boss_permission_error_must_not_block():
    """同是 type=auth，但"需要 Boss 权限"不是没登录 —— 拦了会误杀。"""
    assert zs._login_verdict(BOSS_JSON)[0] == zs.LOGIN_UNKNOWN


def test_garbage_and_empty_are_unknown_not_blocked():
    for text in ("", "   ", "错误：连接超时", "ERR [WinError 2] 系统找不到指定的文件。", "not json at all"):
        assert zs._login_verdict(text)[0] == zs.LOGIN_UNKNOWN, text


# ------------------------------------------------------------------ ② require_login 语义

def test_require_login_blocks_only_on_not_logged_in(monkeypatch):
    monkeypatch.setattr(zs, "probe_login", lambda timeout=60.0: (zs.LOGIN_NO, "Ziniao user is not logged in"))
    ok, why = zs.require_login()
    assert ok is False
    assert "未登录" in why


def test_require_login_passes_on_unknown(monkeypatch):
    monkeypatch.setattr(zs, "probe_login", lambda timeout=60.0: (zs.LOGIN_UNKNOWN, "桥超时"))
    ok, why = zs.require_login()
    assert ok is True
    assert why == "桥超时"


def test_require_login_passes_on_ok_and_logs(monkeypatch):
    monkeypatch.setattr(zs, "probe_login", lambda timeout=60.0: (zs.LOGIN_OK, "登录态正常（可见 10 家店铺）"))
    seen = []
    ok, _ = zs.require_login(log=seen.append)
    assert ok is True
    assert seen and "登录态" in seen[0]


def test_require_login_survives_broken_log_callback(monkeypatch):
    """log 回调抛异常不得影响判定（自检不能把业务拖死）。"""
    monkeypatch.setattr(zs, "probe_login", lambda timeout=60.0: (zs.LOGIN_OK, "ok"))

    def _boom(_msg):
        raise RuntimeError("boom")

    assert zs.require_login(log=_boom)[0] is True


# ------------------------------------------------------------------ ③ 入口接线守卫（静态）

def test_entries_call_the_login_gate_before_touching_windows():
    for name in ENTRIES:
        src = open(os.path.join(_POWER, name), encoding="utf-8").read()
        assert "require_login(" in src, "%s 未接跑前登录自检" % name
        # 只在 main() 体内比顺序（文件头的说明/help 文本里也提到探端口函数，全局搜索会误判）
        body = src[src.find("def main("):]
        assert body, "%s 找不到 main()" % name
        gate = body.find("require_login(")
        probes = [p for p in (body.find("_find_store_port("), body.find("window_ready(")) if p != -1]
        assert probes, "%s main() 里找不到探端口/判窗口调用（测试已过时，请更新）" % name
        assert gate < min(probes), "%s 的登录自检必须在探端口/判窗口之前" % name
