# -*- coding: utf-8 -*-
"""test_selfheal_frozen.py — 开局七道自愈 + 脱离进程跑，**固化守门**（纯离线）。

来历：用户 2026-11-27 连续追问「能不能自愈」，逐条补上后要求「固化」。
这两类东西最容易退化：它们都在 `main()` 开头的几十行里，谁改顺序或删一段，
平时看不出问题，等哪天真断了窗口/掉了登录态才炸，而且症状会伪装成"授权失败"。

本文件守三件事：
  ① **七道自愈一道都不许少**，而且**顺序固定**（内存→端口→开窗→授权前清障→
     双域授权→存活复核→视图）；
  ② 几个"踩过就回不去"的细节：开窗读 `ziniao_window_store_id` 而不是
     `power_shop_id`；视图切换只对 `MULTI_VIEW_STORES` 生效；预检传参不能带 `log`；
  ③ pipeline json 里这几条口径**写进去了**（不然下次只剩代码没有说明书）。
"""
from __future__ import annotations

import io
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_PROJ = os.path.dirname(os.path.dirname(_POWER))
_SRC = os.path.join(_POWER, "run_temu_fulfill.py")
_PIPE = os.path.join(_POWER, "data", "temu_fulfill_pipeline.json")


def _src() -> str:
    return io.open(_SRC, encoding="utf-8").read()


def _main_head() -> str:
    """取 `def main(` 之后、第一个 `runs = []` 之前的**开局段**。"""
    s = _src()
    i = s.find("def main() -> int:")
    assert i > 0, "找不到 main()"
    j = s.find("runs = []", i)
    assert j > i, "找不到开局段结束标记 `runs = []`"
    return s[i:j]


# ---------------------------------------------------------------- ① 七道自愈齐全


def test_all_seven_self_heal_guards_present():
    """七道自愈一个都不能少（每个都有唯一的识别串）。"""
    head = _main_head()
    marks = {
        "① 内存回收": "reclaim_browser_memory",
        "② 钉端口": "TEMU_CDP_PORT",
        "③ 自动开窗": "open_store",
        "④ 授权前清障": "授权前",
        "⑤ 双域授权": "guard_store",
        "⑥ 存活复核": "[存活]",
        "⑦ 视图校验": "TEMU_FULFILL_VIEW_CHECK",
    }
    missing = [k for k, v in marks.items() if v not in head]
    assert not missing, "开局段缺了这些自愈：%s" % missing


def _code_only(text: str) -> str:
    """去掉整行注释，只留代码 —— 注释里会提到这些名字，不能拿它当证据。"""
    out = []
    for ln in text.splitlines():
        st = ln.strip()
        if st.startswith("#"):
            continue
        out.append(ln)
    return "\n".join(out)


def test_self_heal_order_is_fixed():
    """顺序必须固定：内存 → 端口 → 开窗 → 授权前清障 → 授权 → 存活 → 视图。

    顺序错了会互相打架：例如先授权后开窗（端口还没有）、
    或先视图后授权（登录态没恢复，探针读不到视图）。
    ⚠ 只看**代码行**：注释里到处提到这些名字，拿注释定顺序会误判。
    """
    head = _code_only(_main_head())
    order = [
        ("内存回收", "reclaim_browser_memory"),
        ("钉端口", "TEMU_CDP_PORT"),
        ("自动开窗", "open_store"),
        ("授权前清障", "授权前"),
        ("双域授权", "guard_store("),
        ("存活复核", "[存活]"),
        ("视图校验", "TEMU_FULFILL_VIEW_CHECK"),
    ]
    pos = []
    for name, mark in order:
        i = head.find(mark)
        assert i >= 0, "开局段（代码）找不到 %s" % name
        pos.append((name, i))
    for (a, ia), (b, ib) in zip(pos, pos[1:]):
        assert ia < ib, "顺序错了：%s 必须在 %s 之前（实得 %d vs %d）" % (a, b, ia, ib)


def test_each_guard_has_an_env_switch():
    """四个主动作都要能一键关掉（排障用），且默认开。"""
    s = _src()
    for k in ("TEMU_FULFILL_MEM_RECLAIM", "TEMU_FULFILL_OPEN_STORE",
              "TEMU_FULFILL_AUTH_GUARD", "TEMU_FULFILL_VIEW_CHECK"):
        assert k in s, "缺开关 %s" % k
        i = s.find(k)
        assert '"1"' in s[i:i + 120], "%s 默认值不是 1（应变默认开）" % k


# ---------------------------------------------------------------- ② 踩过的细节


def test_open_store_uses_window_store_id_not_power_shop_id():
    """开窗要 `ziniao_window_store_id`；`power_shop_id` 是 TEMU mallid，填错会开错店。

    ⚠ 只看代码行：注释里专门写了「不是 power_shop_id」，拿注释判会误报。
    """
    s = _src()
    assert s.find("def _lookup_store_map") > 0, "没有 _lookup_store_map"
    head = _code_only(_main_head())
    assert "ziniao_window_store_id" in head, "开窗没用 ziniao_window_store_id"
    assert "power_shop_id" not in head, \
        "开窗段代码里出现了 power_shop_id（那是 TEMU mallid，不是紫鸟 storeId）"


def test_view_switch_only_for_multi_view_stores():
    """BHY/BEL 单视图，绝不许进切换分支（否则把 KLS mallid 写进别家窗口）。"""
    s = _src()
    assert "MULTI_VIEW_STORES" in s
    i = s.find("def _switch_store_view")
    assert i > 0, "没有 _switch_store_view"
    # 取到下一个顶层 def 为止（docstring 很长，固定切片会切不到）
    j = s.find("\ndef ", i + 10)
    body = s[i:j if j > 0 else i + 6000]
    assert "MULTI_VIEW_STORES" in body, "_switch_store_view 内部没有拦单视图店"
    assert "拒绝切换" in body, "拦下时要明确打日志"
    head = _main_head()
    assert "_is_multi_view" in head, "main 的视图段没区分单/多视图店"


def test_label_precheck_called_without_log_kwarg():
    """`bought_set` 签名里没有 `log`，传了会抛异常被吞掉 ⇒ 预检静默失效。

    2026-11-27 实测踩到：
        [预检] 跳过（bought_set() got an unexpected keyword argument 'log'）
    """
    s = _src()
    i = s.find("bought_set(")
    assert i > 0, "主流程没调 bought_set"
    call = s[i:s.find(")", i) + 1]
    assert "log=" not in call, "bought_set 又被传了 log=：%s" % call
    assert "pos=" in call, "必须只按本批单号查（pos=）"


# ---------------------------------------------------------------- ③ json 写进去了


def _pipe() -> dict:
    return json.load(io.open(_PIPE, encoding="utf-8"))


def _pipe_blob() -> str:
    """顶层说明文字：key 与 value 都要拼（只拼 value 会漏掉 key 里的标题）。"""
    d = _pipe()
    return " ".join("%s %s" % (k, d[k]) for k in d if k.startswith("_"))


def test_pipeline_json_documents_the_seven_guards():
    blob = _pipe_blob()
    for kw in ("内存自动回收", "自动开窗", "授权前先清障", "双域授权",
               "存活复核", "店铺视图校验"):
        assert kw in blob, "pipeline json 没写「%s」的口径" % kw
    assert "v11" in str(_pipe().get("_版本")), "版本没升到 v11"


def test_pipeline_json_documents_detached_run_requirement():
    """必须写清「不要交互式长跑」这条 —— 这是四次断链的真因。"""
    blob = _pipe_blob()
    assert "脱离进程" in blob or "WindowStyle Hidden" in blob, "没写脱离式启动要求"
    assert "run_temu_fulfill_one.cmd" in blob, "没指向启动器"


def test_pipeline_json_records_known_gap():
    """已知未修的要留档，避免下次又当成"新 bug"重查一遍。"""
    blob = _pipe_blob()
    assert "已知未修" in blob, "没留「已知未修」小节"
    assert "pw_index_deliver" in blob or "deliver.php" in blob, "没写清 report 运单号那条缺口"