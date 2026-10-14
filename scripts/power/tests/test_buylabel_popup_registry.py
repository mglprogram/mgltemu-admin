# -*- coding: utf-8 -*-
"""test_buylabel_popup_registry.py — **弹窗登记表**的离线回归（不起浏览器）。

来历（2026-09-21 用户）：「现在路线总是没那么确定，因为 TEMU 总是有弹窗，
但之前的经验总是没办法固化」。病根两条，本文件把它们钉死：
    ① 弹窗知识曾**写死在代码里**（`_POPUP_DISMISSABLE` 一个常量元组）⇒ 多认识一个弹窗＝改代码；
    ② 未知弹窗只 HARD STOP + 截图、**不登记** ⇒ 下次遇到同一个还要人肉翻日志、再改代码。

现在：识人判据与动作全在 `data/temu_buylabel_popups.json`；未登记的弹窗由 driver
**自动登记**进 `unknown[]`（按标题去重、累加 hits、留首次/最近时间与证据）。
「固化经验」＝改一行数据。

本文件钉死六件事：
    ① 登记表结构合法（id 唯一、act 合法、when 非空、click 必须给 button、每条必须有 _why）
    ② `classify_popup` 旧签名/旧行为不回归（已知提示 → dismiss，其余 unknown，None/"" → unknown）
    ③ 判据语义：title_contains / buttons_have / 空判据**不许**命中
    ④ 未登记弹窗会被**自动登记**（去重 + hits 累加）——这是"经验固化"的入口
    ⑤ 已登记弹窗每命中一次累加 hits（哪些弹窗真在发生，看数据就知道）
    ⑥ 源码级：不许再回到"代码里写死弹窗标题"的写法

运行：python -m pytest scripts/power/tests/test_buylabel_popup_registry.py -q
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_POWER, "lib"))

import temu_buylabel_driver as D                  # noqa: E402

REG_FILE = os.path.join(_POWER, "data", "temu_buylabel_popups.json")
DRIVER_SRC = os.path.join(_POWER, "lib", "temu_buylabel_driver.py")

_ACTS = {"dismiss", "click", "stop"}


def _reg():
    with open(REG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _tmp_reg(tmp_path):
    """给一个临时登记表 + cfg（driver 全程按 cfg["popups_file"] 读写，不碰真表）。"""
    p = os.path.join(str(tmp_path), "popups.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"popups": [
            {"id": "PU-T1", "act": "dismiss", "when": {"title_contains": ["提示通知"]},
             "_why": "测试用：提示类"},
            {"id": "PU-T2", "act": "click",
             "when": {"title_contains": ["无法发货"], "buttons_have": ["剔除并继续"], "button": "剔除并继续"},
             "_why": "测试用：要决策的确认框"},
        ], "unknown": []}, f, ensure_ascii=False)
    return p, {"popups_file": p}


# ---------------------------------------------------------------- ① 结构
def test_registry_file_is_valid():
    d = _reg()
    ps = d.get("popups") or []
    assert ps, "登记表里一条都没有：%s" % REG_FILE
    ids = [p.get("id") for p in ps]
    assert all(ids), "有弹窗缺 id"
    assert len(ids) == len(set(ids)), "弹窗 id 重复：%s" % ids
    for p in ps:
        assert p.get("act") in _ACTS, "%s 的 act 不合法：%r" % (p.get("id"), p.get("act"))
        assert p.get("when"), "%s 缺 when（怎么认这个弹窗）" % p.get("id")
        assert str(p.get("_why") or "").strip(), "%s 缺 _why（为什么这么处理，丢了就等于没固化）" % p.get("id")
        if p.get("act") == "click":
            btn = (p.get("when") or {}).get("button") or p.get("button")
            assert str(btn or "").strip(), "%s 是 click 却没给 button" % p.get("id")


# ---------------------------------------------------------------- ② 旧行为不回归
def test_classify_popup_legacy_contract():
    """旧签名（只给标题）与旧行为必须原样成立——driver 别处与旧测试都按它判。"""
    assert D.classify_popup("以下待办任务需要尽快处理") == "dismiss"
    assert D.classify_popup("请完成安全验证") == "unknown"
    assert D.classify_popup("") == "unknown"
    assert D.classify_popup(None) == "unknown"


def test_classify_popup_is_data_driven(tmp_path):
    """新增一个弹窗＝只改数据：临时表里放一条 click，匹配立刻认识它（代码一行都没动）。"""
    _, cfg = _tmp_reg(tmp_path)
    reg = D.load_popup_registry(cfg=cfg)
    assert D.classify_popup("无法发货", "无法发货", ["剔除并继续"], reg) == "click"
    e = D.match_popup("无法发货", "无法发货", ["剔除并继续"], reg)
    assert e and e["id"] == "PU-T2" and e["act"] == "click"


# ---------------------------------------------------------------- ③ 判据语义
def test_match_popup_button_and_empty_when(tmp_path):
    _, cfg = _tmp_reg(tmp_path)
    reg = D.load_popup_registry(cfg=cfg)
    # buttons_have 不满足 → 不命中
    assert D.match_popup("无法发货", "无法发货", ["取消"], reg) is None
    # 文本不含 → 不命中
    assert D.match_popup("别的什么弹窗", "别的什么弹窗", ["剔除并继续"], reg) is None
    # 空判据一条都不许命中（否则一条空规则会吃掉所有弹窗）
    assert D.match_popup("随便", "随便", [], {"popups": [{"id": "PU-X", "act": "stop", "when": {}}]}) is None


def test_money_path_popup_click_is_per_user_decision():
    """PU-002（「无法发货…剔除并继续」确认框）：**2026-09-21 用户已拍板**改成自动点。

    这条钉两件事，防回退也防"悄悄改"：
      ① 花钱路径上的自动确认只能由用户授权——授权写在这条数据里（`_用户已拍板`），
         没有这句就不许把 act 从 stop 改成 click；
      ② 既然是 click，就必须给出 button（否则代码点不动，会退化成"点不到就放过"）。
    """
    d = _reg()
    pu2 = [p for p in d["popups"] if p.get("id") == "PU-002"]
    assert pu2, "PU-002（「无法发货…剔除并继续」确认框）应该在表里"
    e = pu2[0]
    assert e.get("act") == "click", "用户已拍板自动点「剔除并继续」：act 应为 click"
    assert str((e.get("when") or {}).get("button") or "").strip() == "剔除并继续", e
    assert "拍板" in str(e.get("_用户已拍板") or ""), "花钱路径的自动确认必须留下用户授权记录"


# ---------------------------------------------------------------- ④ 自动登记（固化入口）
def test_unknown_popup_is_auto_registered_and_deduped(tmp_path):
    p, cfg = _tmp_reg(tmp_path)
    r1 = D.register_unknown_popup("新弹窗A", "新弹窗A 的正文", ["知道了", "取消"],
                                  url="https://x/orders", step="点按钮前:全选", shot="/tmp/a.png", cfg=cfg)
    r2 = D.register_unknown_popup("新弹窗A", "新弹窗A 的正文", ["知道了"], cfg=cfg)
    d = json.load(open(p, encoding="utf-8"))
    unk = d.get("unknown") or []
    assert len(unk) == 1, "同一个弹窗被登记了两遍：%s" % unk
    assert unk[0]["hits"] == 2, unk[0]
    assert unk[0]["first_seen"] and unk[0]["last_seen"]
    assert unk[0]["buttons"] == ["知道了", "取消"], unk[0]
    assert r1["title"] == r2["title"] == "新弹窗A"


def test_bump_hits_records_known_popup(tmp_path):
    p, cfg = _tmp_reg(tmp_path)
    reg = D.load_popup_registry(cfg=cfg)
    e = D.match_popup("提示通知到了", "提示通知到了", [], reg)
    assert e and e["id"] == "PU-T1"
    D.bump_popup_hits(e, reg, cfg=cfg)
    D.bump_popup_hits(e, reg, cfg=cfg)
    d = json.load(open(p, encoding="utf-8"))
    got = [x for x in d["popups"] if x["id"] == "PU-T1"][0]
    assert got["hits"] == 2 and got.get("last_seen"), got


# ---------------------------------------------------------------- driver 行为（假 self，不起浏览器）
class _FakePage:
    url = "https://agentseller-us.temu.com/mmsos/orders.html"


class _Fake:
    """最小替身：只提供 dismiss_popups 用到的几个东西，并记录它点了什么。"""

    def __init__(self, cfg, probe):
        self.cfg = cfg
        self._probe = dict(probe)
        self.clicks = []
        self.logs = []
        self.shots = []
        self.evidence_dir = ""
        self.dismissed = False          # 被点掉之后真实页面上弹窗就没了 → 探测要跟着变

    def _js(self, page, js, arg=None, tries=1):
        if js is D._JS_POPUP_PROBE:
            if self.dismissed:
                return json.dumps({"present": False})
            return json.dumps(self._probe, ensure_ascii=False)
        if js is D._JS_INNER_APPROX_BY_TEXT:
            return None                     # 兜底通道：默认"找不到"，需要时由用例显式给
        self.clicks.append(js)          # 其余记为"点了一次"
        self.dismissed = True
        return "ok"

    def shot(self, page, name):
        self.shots.append(name)
        return "/tmp/%s.png" % name

    def log(self, msg):
        self.logs.append(str(msg))

    def wait(self, key, factor=1.0):
        pass

    def cdp_click(self, page, x, y):
        self.clicks.append("cdp:%s,%s" % (x, y))
        self.dismissed = True


def test_dismiss_popups_registers_unknown(tmp_path):
    """**核心行为**：遇到未登记弹窗 → 自动进登记表（下次它就在表里），且不阻断非严格模式。"""
    p, cfg = _tmp_reg(tmp_path)
    f = _Fake(cfg, {"present": True, "text": "没见过的新弹窗", "buttons": ["知道了"], "hasClose": True,
                    "closeAt": [10, 10], "maskOnly": False})
    out = D.BuylabelDriver.dismiss_popups(f, _FakePage(), "点按钮前:全选", strict=False)
    assert out == ["没见过的新弹窗"], out
    d = json.load(open(p, encoding="utf-8"))
    assert len(d["unknown"]) == 1 and d["unknown"][0]["hits"] == 1, d["unknown"]
    assert d["unknown"][0]["step"] == "点按钮前:全选"
    assert any("未登记" in x for x in f.logs), f.logs


def test_dismiss_popups_strict_unknown_raises(tmp_path):
    _, cfg = _tmp_reg(tmp_path)
    f = _Fake(cfg, {"present": True, "text": "没见过的新弹窗", "buttons": [], "hasClose": False,
                    "closeAt": None, "maskOnly": False})
    try:
        D.BuylabelDriver.dismiss_popups(f, _FakePage(), "订单页就绪", strict=True)
        raise AssertionError("strict 模式下未登记弹窗必须 HARD STOP")
    except RuntimeError as e:
        assert "未知弹窗" in str(e) and "未登记" in str(e), str(e)


def test_dismiss_popups_clicks_registered_button(tmp_path):
    """act=click 的弹窗要按表点它指定的按钮（且不能递归调用 dismiss_popups）。"""
    _, cfg = _tmp_reg(tmp_path)
    f = _Fake(cfg, {"present": True, "text": "无法发货，是否剔除并继续？", "buttons": ["取消", "剔除并继续"],
                    "hasClose": True, "closeAt": [1, 2], "maskOnly": False})
    out = D.BuylabelDriver.dismiss_popups(f, _FakePage(), "下单前", strict=False)
    assert out == ["无法发货，是否剔除并继续？"], out
    assert D._JS_CLICK_BUTTON in f.clicks, "没有点表里指定的按钮"
    assert not any(str(c).startswith("cdp:") for c in f.clicks), "act=click 时不该去点关闭键"
    assert not (json.load(open(D.popups_path(cfg=cfg), encoding="utf-8")).get("unknown")), "已登记的不该进 unknown"


def test_dismiss_popups_dismiss_uses_close_key(tmp_path):
    """已登记 dismiss：照旧点关闭键（旧行为不能变）。"""
    _, cfg = _tmp_reg(tmp_path)
    f = _Fake(cfg, {"present": True, "text": "提示通知到了", "buttons": [], "hasClose": True,
                    "closeAt": [7, 8], "maskOnly": False})
    out = D.BuylabelDriver.dismiss_popups(f, _FakePage(), "筛选前", strict=False)
    assert out == ["提示通知到了"], out
    assert "cdp:7,8" in f.clicks, f.clicks


def test_pu003_notice_is_registry_driven_and_uses_approx_close():
    """PU-003「物流细则」：**下单链通知弹窗**，知识在表里（代码里那份 _PLACE_NOTICES 已删）。

    它没有 `beast-core-icon-close`，所以要 `close: approx` 才允许用近似右上角点；
    `on_place: true` 让 place() 的弹窗步骤从表里取它。
    """
    d = _reg()
    pu3 = [p for p in d["popups"] if p.get("id") == "PU-003"]
    assert pu3, "PU-003（物流细则通知）应该在表里"
    e = pu3[0]
    assert e.get("act") == "dismiss" and e.get("close") == "approx", e
    assert e.get("on_place") is True, "下单链上的通知弹窗要标 on_place（place() 按它取）"
    assert "物流细则" in ((e.get("when") or {}).get("title_contains") or []), e


def test_approx_close_only_for_entries_that_declare_it(tmp_path):
    """**闸门**：近似关闭点是"盲点"，未登记/未声明的弹窗绝不许用它（别改成自动生效）。"""
    p = os.path.join(str(tmp_path), "popups.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"popups": [
            {"id": "PU-N1", "act": "dismiss", "when": {"title_contains": ["没声明近似点的通知"]},
             "_why": "测试：不带 close=approx"},
        ], "unknown": []}, f, ensure_ascii=False)
    cfg = {"popups_file": p}
    probe = {"present": True, "text": "没声明近似点的通知", "buttons": [],
             "hasClose": False, "closeAt": None, "approxAt": [900, 40], "maskOnly": False}
    f = _Fake(cfg, probe)
    out = D.BuylabelDriver.dismiss_popups(f, _FakePage(), "点按钮前", strict=False)
    # 找不到关闭点 = 没处理（旧行为原样：非严格模式返回空、什么都不点）——关键是**没点近似点**
    assert out == [], out
    assert not any(str(c).startswith("cdp:") for c in f.clicks), "没声明 close=approx 却点了近似点：%s" % f.clicks


def test_approx_close_used_when_declared(tmp_path):
    p = os.path.join(str(tmp_path), "popups.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"popups": [
            {"id": "PU-N2", "act": "dismiss", "close": "approx",
             "when": {"title_contains": ["物流细则"]}, "_why": "测试：声明了近似点"},
        ], "unknown": []}, f, ensure_ascii=False)
    cfg = {"popups_file": p}
    probe = {"present": True, "text": "物流细则 一堆政策文字", "buttons": [],
             "hasClose": False, "closeAt": None, "approxAt": [900, 40], "maskOnly": False}
    f = _Fake(cfg, probe)
    out = D.BuylabelDriver.dismiss_popups(f, _FakePage(), "下单前", strict=False)
    assert out and out[0].startswith("物流细则"), out
    assert "cdp:900,40" in f.clicks, "声明了 close=approx 就该点近似点：%s" % f.clicks


def test_approx_close_falls_back_when_probe_cannot_see_the_modal(tmp_path):
    """**不回归的保险**：老口径是直接在 `MDL_inner` 里按文本找右上角（不依赖外壳）。

    万一通知弹窗不在探测认的那层外壳下（探测报"没有弹窗"），声明了 `close: approx` 的条目
    仍要能关掉它——否则就是从"能关"退化成"关不掉"。
    """
    p = os.path.join(str(tmp_path), "popups.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"popups": [
            {"id": "PU-N4", "act": "dismiss", "close": "approx", "on_place": True,
             "when": {"title_contains": ["物流细则"]}, "_why": "测试：仅兜底通道能找到"},
        ], "unknown": []}, f, ensure_ascii=False)
    cfg = {"popups_file": p}

    class _F:
        def __init__(self):
            self.clicks = []

        def _js(self, page, js, arg=None, tries=1):
            if js is D._JS_POPUP_PROBE:
                return json.dumps({"present": False})          # 探测看不见
            if js is D._JS_INNER_APPROX_BY_TEXT:
                return [880, 40] if arg == "物流细则" else None  # 兜底按关键词给点
            self.clicks.append(js)
            return "ok"

        def log(self, msg):
            pass

        def cdp_click(self, page, x, y):
            self.clicks.append("cdp:%s,%s" % (x, y))

    f = _F()
    pos = D.BuylabelDriver._popup_close_point(f, _FakePage(), {"close": "approx",
                                                               "when": {"title_contains": ["物流细则"]}})
    assert pos == [880, 40], pos


def test_place_notice_entries_read_from_registry(tmp_path):
    """place() 的"通知弹窗"清单必须来自登记表（on_place 条目），不是代码里的元组。"""
    _, cfg = _tmp_reg(tmp_path)
    with open(D.popups_path(cfg=cfg), encoding="utf-8") as f:
        d = json.load(f)
    d["popups"].append({"id": "PU-N3", "act": "dismiss", "close": "approx", "on_place": True,
                        "when": {"title_contains": ["某通知"]}, "_why": "测试"})
    with open(D.popups_path(cfg=cfg), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    fake = _Fake(cfg, {"present": False})
    got = D.BuylabelDriver._place_notice_entries(fake)
    assert [e["id"] for e in got] == ["PU-N3"], got


# ---------------------------------------------------------------- ⑥ 源码级闸门
def test_no_hardcoded_popup_title_in_code():
    """不许回到"弹窗标题写死在代码里"：知识必须在数据里（否则又固化不了）。

    只查**非注释代码**（历史沿革的注释里可以留现场原文，那不是判据）。
    """
    src = open(DRIVER_SRC, encoding="utf-8").read()
    assert "_POPUP_DISMISSABLE" not in src, "弹窗白名单又回到代码里了（应放 data/temu_buylabel_popups.json）"
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "_PLACE_NOTICES" not in code, "下单链的通知清单又写死在代码里了（应放登记表 on_place 条目）"
    assert "以下待办任务需要尽快处理" not in code, "弹窗标题不该作为**判据**出现在代码里"
    assert "物流细则" not in code, "通知弹窗标题不该作为**判据**出现在代码里（放登记表）"
    assert "temu_buylabel_popups.json" in src, "登记表路径没接进 driver"
