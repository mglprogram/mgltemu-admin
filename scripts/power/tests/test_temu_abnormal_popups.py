# -*- coding: utf-8 -*-
"""test_temu_abnormal_popups.py — 合作仓异常单域的**弹窗登记表**离线回归（不起浏览器）。

来历（用户 2026-09-22）：「脚本能够走完完整的一条路，并且针对不同弹窗要能够检测并关闭」。
本域照下单域（`test_buylabel_popup_registry.py`）那套固化：判据与动作全在
`data/temu_abnormal_popups.json`，未知弹窗由 driver 自动登记进 `unknown[]`。

钉死六件事：
    ① 表结构合法（id 唯一、act 合法、when 非空、click 必须给 when.button、每条要有 _why）
    ② **流程弹层必须 keep** —— 两个第一层标题（「请选择重发方式」/「下单失败，是否重新下单」）
       与二次确认层，一旦被写成刷新/关闭，整批就白做（这是本域最容易踩死的坑）
    ③ **规则「遇到 TEMU 弹窗就刷新」必须记在数据里并接在代码上**（act=refresh + unknown_act + 刷新实现）
    ④ 空判据不许命中（防一条空规则吃掉所有弹窗）
    ⑤ 未知弹窗会被自动登记（去重 + 累加 hits）
    ⑥ driver 必须**在几个关键点真调** sweep_popups，且不把弹窗标题写死在代码里
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_POWER, "lib"))

import temu_abnormal_driver as D                 # noqa: E402

REG_FILE = os.path.join(_POWER, "data", "temu_abnormal_popups.json")
DRIVER_SRC = os.path.join(_POWER, "lib", "temu_abnormal_driver.py")

_ACTS = {"dismiss", "click", "keep", "stop", "refresh"}
#: 必须 keep 的流程弹层文案（出现即代表"这是流程的一部分，不许自动关"）
_MUST_KEEP = ["请选择重发方式", "下单失败，是否重新下单", "确认将该订单转为卖家履约订单吗"]


def _reg():
    with open(REG_FILE, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- ① 结构
def test_registry_file_is_valid():
    d = _reg()
    assert isinstance(d.get("popups"), list) and d["popups"], "登记表为空"
    assert isinstance(d.get("unknown"), list)
    ids = [e.get("id") for e in d["popups"]]
    assert len(ids) == len(set(ids)), "id 重复：%s" % ids
    for e in d["popups"]:
        assert e.get("id"), "缺 id"
        assert str(e.get("act")) in _ACTS, "%s act 非法：%s" % (e.get("id"), e.get("act"))
        w = e.get("when") or {}
        assert any(w.get(k) for k in ("title_contains", "text_regex", "buttons_have")), \
            "%s 判据全空（会吃掉所有弹窗）" % e.get("id")
        assert e.get("_why"), "%s 缺 _why（为什么这么处置）" % e.get("id")
        if e.get("act") == "click":
            assert w.get("button"), "%s act=click 必须给 when.button" % e.get("id")


# ---------------------------------------------------------------- ② 流程弹层必须 keep
def test_flow_dialogs_are_keep():
    d = _reg()
    for key in _MUST_KEEP:
        hit = D.match_popup(key + " 确认 取消", key, ["确认", "取消"], d)
        assert hit, "没认出来流程弹层：%s" % key
        assert hit.get("act") == "keep", \
            "流程弹层 %s 被写成 %s —— 自动关掉就等于取消本单操作" % (key, hit.get("act"))


def test_keep_entries_are_matched_before_dismiss_rules():
    """命中按表内顺序取第一条：keep 必须在 dismiss 之前，否则通知类规则可能先吃掉流程层。"""
    d = _reg()
    acts = [e.get("act") for e in d["popups"]]
    first_dismiss = acts.index("dismiss") if "dismiss" in acts else len(acts)
    for i, a in enumerate(acts):
        if a == "keep":
            assert i < first_dismiss, "keep 条目排在 dismiss 之后：%s" % d["popups"][i].get("id")


# ---------------------------------------------------------------- ③ 判据语义
def test_empty_criteria_never_match():
    d = {"popups": [{"id": "X", "act": "dismiss", "when": {}}], "unknown": []}
    assert D.match_popup("任意弹窗", "任意弹窗", ["取消"], d) is None


def test_notice_popup_acts_by_rule_refresh():
    """**规则（用户 2026-09-22）：遇到 TEMU 弹窗就刷新** —— 遮挡类弹窗的 act 应是 refresh。"""
    d = _reg()
    hit = D.match_popup("以下待办任务需要尽快处理 物流信息异常 去处理",
                        "以下待办任务需要尽快处理", ["去处理"], d)
    assert hit and hit.get("act") == "refresh", "遮挡类弹窗没按「刷新」规则处置：%s" % (hit or {})


def test_refresh_rule_is_recorded_and_wired():
    """规则要**记在数据里 + 接在代码上**，不能只是一句话。"""
    d = _reg()
    # ① 规则写在表头
    rule = " ".join(str(d.get(k) or "") for k in d if k.startswith("_规则"))
    assert "刷新" in rule and "TEMU" in rule, "登记表里没记下「遇到 TEMU 弹窗就刷新」这条规则"
    # ② 至少一条用 refresh，且 keep 之外不该再出现 dismiss（本域已统一到刷新）
    acts = [e.get("act") for e in d["popups"]]
    assert "refresh" in acts, "没有任何 refresh 条目"
    assert "dismiss" not in acts, "还留着 dismiss 条目（本域规则已统一为刷新）"
    # ③ 未登记弹窗的处置也是刷新
    import json as _json
    cfg = _json.load(open(os.path.join(_POWER, "data", "temu_abnormal.json"), encoding="utf-8"))
    assert (cfg.get("popups") or {}).get("unknown_act") == "refresh", "未知弹窗没按规则刷新"
    # ④ 代码里有这条动作（refresh 分支 + 刷新实现）
    src = open(DRIVER_SRC, encoding="utf-8").read()
    assert 'act == "refresh"' in src and "_refresh_page" in src and "refreshed(unknown)" in src
    # ⑤ 刷新后必须重设列表视图（否则回来是默认页签/默认每页，会漏单）
    assert "_apply_list_view" in src


# ---------------------------------------------------------------- ④ 未知自动登记
def test_unknown_popup_is_registered_and_deduped(tmp_path):
    p = str(tmp_path / "popups.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"popups": [], "unknown": []}, f, ensure_ascii=False)
    for _ in range(2):
        D.register_unknown_popup("某个没见过的弹窗 取消", "某个没见过的弹窗", ["取消"],
                                 url="https://x", step="before_convert", path=p)
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    assert len(d["unknown"]) == 1, "未知弹窗没去重"
    assert int(d["unknown"][0]["hits"]) == 2, "hits 没累加"


# ---------------------------------------------------------------- ⑤ driver 接线
def test_driver_sweeps_at_key_points_and_hardcodes_no_titles():
    src = open(DRIVER_SRC, encoding="utf-8").read()
    for stage in ("open", "before_convert", "list", "verify"):
        assert 'sweep_popups("%s")' % stage in src, "没在 %s 这一步清扫弹窗" % stage
    assert "load_popup_registry" in src and "register_unknown_popup" in src, "没复用登记表那套实现"
    # 弹窗标题只能活在数据里（文档串/注释里提到不算，故先剥掉再查）
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)
    code = "\n".join(ln.split("#")[0] for ln in code.splitlines())
    for key in _MUST_KEEP:
        assert key not in code, "弹窗标题被写死在代码里了：%s（应放 data/temu_abnormal_popups.json）" % key
