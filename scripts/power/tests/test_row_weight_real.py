# -*- coding: utf-8 -*-
"""`set_row_weight_real()` 的离线测试（2026-11-05 用户真机教学）。

来历（用户原话）：「点击输入框 → 输入 → 点空白 就可以了」＋「这个红字字样的数字，跟我们的
默认重量差太多，这里提示我们填多少你就去修改…修改完后，再在空白处点击一次，因为这个订单会重置」。

为什么不能再用 `_JS_SET_ROW_WEIGHT`：
  它只写 `el.value` 再派发 input/change，**React 受控组件不认** ⇒ 回读能读到新值，
  但一失焦/页面重算就被平台**重置**回原来的数。必须走真实输入路径：
  真实点击 → Ctrl+A（全选，否则会在原值后追加）→ `Input.insertText` → 点空白触发重算。

纯离线：不连浏览器。把 `_js` / `cdp` / `cdp_click` / `shot` / `log` 全部替掉。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))

import temu_buylabel_driver as tbd  # noqa: E402


class FakeCdp:
    """记下派发的 CDP 命令（要能证明 Ctrl+A 与 insertText 都发了）。"""

    def __init__(self):
        self.sent = []

    def send(self, method, params=None):
        self.sent.append((method, params or {}))
        return {"id": len(self.sent), "result": {}}


class FakeDrv(tbd.BuylabelDriver):
    def __init__(self, box, after=None, box_second=None):
        self.cfg = {"weight_per_unit_lb": 1.1, "weight_decimals": 2}
        self._box = box                 # 第一次 _JS_ROW_WEIGHT_BOX 的回包
        self._box2 = box_second if box_second is not None else box
        self._after = after             # 点空白后回读（None＝沿用 _box2）
        self._calls = 0
        self.clicks = []
        self.logs = []
        self._cdp = FakeCdp()
        self._blank = {"x": 1700, "y": 620}

    def _js(self, page, js, *a, **kw):
        if js is tbd._JS_ROW_WEIGHT_BOX:
            self._calls += 1
            v = self._box if self._calls == 1 else (
                self._after if self._after is not None else self._box2)
            return json.dumps(v) if isinstance(v, dict) else v
        if js is tbd._JS_FIND_BLANK_POINT:
            return json.dumps(self._blank)
        return None

    def cdp(self, page):
        return self._cdp

    def cdp_click(self, page, x, y):
        self.clicks.append((x, y))

    def log(self, m):
        self.logs.append(str(m))

    def shot(self, page, name):
        return name


def box(value="1.10", x=624.0, y=586.0, in_view=True):
    """重量框定位回包。`in_view` = 该框是否在视口内（2026-11-05 新加，见下方 ⑨）。"""
    return {"x": x, "y": y, "value": value, "inView": in_view, "y0": int(y), "vh": 1012}


def test_real_input_hits_box_then_blank():
    """① 顺序铁律：先点输入框，再发键，最后点空白（三次真实点击/派发都要发生）。"""
    d = FakeDrv(box(), after={"x": 624.0, "y": 586.0, "value": "1.94"})
    r = d.set_row_weight_real(object(), "PO-1", "1.94")
    assert r == "ok:1.94", r
    assert d.clicks[0] == (624.0, 586.0), "第一步必须点输入框"
    assert len(d.clicks) == 2 and d.clicks[1] == (1700.0, 620.0), "最后必须点空白"
    methods = [m for m, _ in d._cdp.sent]
    assert "Input.insertText" in methods, "必须用真实输入而不是写 value"
    assert "Input.dispatchKeyEvent" in methods, "必须先全选，否则会在原值后追加"


def test_ctrl_a_uses_modifier_2():
    """② Ctrl+A 必须带 `modifiers=2`，否则平台收到的是普通 a，选择区不会全选。"""
    d = FakeDrv(box(), after={"x": 1, "y": 2, "value": "2.02"})
    d.set_row_weight_real(object(), "PO-2", "2.02")
    keys = [p for m, p in d._cdp.sent if m == "Input.dispatchKeyEvent"]
    assert keys and all(p.get("modifiers") == 2 for p in keys), keys
    assert all(p.get("windowsVirtualKeyCode") == 65 for p in keys), keys


def test_value_lands_and_is_read_back():
    """③ 回读等于目标值 → `ok:`；不等 → `mismatch:`（绝不假通过）。"""
    d = FakeDrv(box(), after={"x": 1, "y": 2, "value": "4.00"})
    assert d.set_row_weight_real(object(), "PO-3", "4.00") == "ok:4.00"

    d2 = FakeDrv(box(), after={"x": 1, "y": 2, "value": "1.10"})
    r = d2.set_row_weight_real(object(), "PO-4", "4.00")
    assert r == "mismatch:1.10", "被平台重置后必须如实报 mismatch，不能报 ok"


def test_reset_detected_as_mismatch():
    """④ ★ 本条就是用户说的「这个订单会重置」：JS 写进去的值失焦后回退，必须被抓到。"""
    d = FakeDrv(box("1.10"), after={"x": 1, "y": 2, "value": "1.10"})
    r = d.set_row_weight_real(object(), "PO-5", "1.94")
    assert r.startswith("mismatch:"), r


def test_no_weight_box_returns_no_box():
    """⑤ 定位不到重量框 → `no-box:`（不崩、不乱点）。"""
    d = FakeDrv("no-weight-box")
    r = d.set_row_weight_real(object(), "PO-6", "1.50")
    assert r.startswith("no-box:"), r
    assert d.clicks == [], "定位失败时一次都不该点"


def test_no_blank_point_still_blurs():
    """⑥ 找不到空白点时退化为 blur/Escape，但仍要回读（不能因此跳过校验）。"""
    d = FakeDrv(box(), after={"x": 1, "y": 2, "value": "1.60"})
    d._blank = None
    r = d.set_row_weight_real(object(), "PO-7", "1.60")
    assert r == "ok:1.60", r
    assert len(d.clicks) == 1, "只点输入框；空白点找不到就不瞎点"


def test_click_blank_can_be_disabled():
    """⑦ click_blank=False 时不点空白（只给排障/单测用，正常链路必须点）。"""
    d = FakeDrv(box(), after={"x": 1, "y": 2, "value": "1.74"})
    d.set_row_weight_real(object(), "PO-8", "1.74", click_blank=False)
    assert len(d.clicks) == 1


def test_fill_and_weight_fix_both_use_real_input():
    """⑧ 源码级防回潮：写重量的三处（②-b / ③ / 整段 fill）**不许**再调 `_JS_SET_ROW_WEIGHT`。"""
    import inspect
    srcs = {
        "fill": inspect.getsource(tbd.BuylabelDriver.fill),
        "_fill_batch": inspect.getsource(tbd.BuylabelDriver._fill_batch),
        "weight_fix": inspect.getsource(tbd.BuylabelDriver.weight_fix),
    }
    for name, src in srcs.items():
        assert "_JS_SET_ROW_WEIGHT" not in src, "%s 仍在用 JS 写 value（会被重置）" % name
    assert "set_row_weight_real" in srcs["_fill_batch"], "②-b 逐单修正必须走真实输入"
    assert "set_row_weight_real" in srcs["weight_fix"], "③ 重量核对必须走真实输入"


# ---- ★ 2026-11-05 第二个真机事故：视口外的重量框，点了等于没点 -------------------
# 现场：7 单批量处理，第 5/6/7 单重量框 y=1109/1338，视口高只有 1012。
# `getBoundingClientRect()` 给的是**视口坐标**，CDP Input.dispatchMouseEvent 点视口外坐标
# **完全无效**（不报错、也不会点到别处）⇒ 输入框没聚焦、输入没进去、红字一直挂着。
# 前 3 单恰好在视口内，所以只有"后面几单"失败 —— 这就是用户问的"为什么后面四单没消红字"。
# 修法：定位 JS 里先 `scrollIntoView({block:'center'})` 再取坐标，并回报 `inView`。

def test_find_box_js_scrolls_into_view_first():
    """⑨ 定位 JS 必须先滚动到视口中央再取坐标，否则视口外的行永远点不到。"""
    src = tbd._JS_ROW_WEIGHT_BOX
    assert "scrollIntoView" in src, "定位重量框的 JS 没有 scrollIntoView（视口外的行会静默失败）"
    assert "inView" in src, "必须回报 inView，供上层判断坐标是否可信"
    # 顺序也重要：先滚动，再取 rect（反了拿到的还是滚动前的旧坐标）。
    # ⚠ 只在**去掉注释的代码**里比顺序 —— 注释里也会提到这两个词（踩过，假红）。
    code = "\n".join(ln.split("//")[0] for ln in src.splitlines())
    assert code.index("scrollIntoView") < code.index("getBoundingClientRect"), \
        "必须先 scrollIntoView 再 getBoundingClientRect"


def test_offscreen_box_is_refused_not_clicked():
    """⑩ 两次取坐标都报 inView=False → 返回 `no-box:`，**一次都不许点**（点了也是白点）。"""
    d = FakeDrv(box(in_view=False))
    r = d.set_row_weight_real(object(), "PO-OFF", "1.94")
    assert r.startswith("no-box:"), r
    assert "视口外" in r, r
    assert d.clicks == [], "视口外的坐标绝不允许派发点击"


def test_offscreen_then_onscreen_after_reprobe_is_clicked():
    """⑪ 第一次在视口外、重取后进视口 → 用新坐标点（不因第一次失败就放弃）。"""
    d = FakeDrv(box(in_view=False))
    d._after = box("1.10", x=700.0, y=400.0, in_view=True)   # 第二次 _js 回包（回读也用它）
    d._calls = 0
    # 让第 1 次=视口外、第 2 次=视口内、第 3 次（回读）=视口内且值是目标
    seq = [box(in_view=False), box("1.10", x=700.0, y=400.0, in_view=True),
           box("1.94", x=700.0, y=400.0, in_view=True)]
    d._js = lambda page, js, *a, **kw: (
        json.dumps(seq.pop(0)) if js is tbd._JS_ROW_WEIGHT_BOX and seq
        else (json.dumps(d._blank) if js is tbd._JS_FIND_BLANK_POINT else None))
    r = d.set_row_weight_real(object(), "PO-RE", "1.94")
    assert r == "ok:1.94", r
    assert d.clicks[0] == (700.0, 400.0), "必须用重取后的视口内坐标"


def test_blank_point_prefers_clickable_area_not_input():
    """⑫ 空白点候选坐标必须避开 input/button/select/label（点到它们等于又聚焦回控件）。"""
    src = tbd._JS_FIND_BLANK_POINT
    for tag in ("INPUT", "BUTTON", "SELECT", "LABEL"):
        assert tag in src, "空白点判据没排除 %s" % tag
    assert "elementFromPoint" in src


# ---- ⑬ 2026-11-05 第三个真机坑：日期下拉是 fixed 浮层，offsetParent 恒 null ----------------
# 现场：取件日期填不上 → 平台报「预约取件时间不能为空」→ 点「已确认，继续下单」**不提交**，
# 表现成"点了没下单"（7 单 BHY 实测）。根因是 `_JS_PICK_EARLIEST_DATE` 用
# `offsetParent !== null` 判可见，而**日期下拉面板是 position:fixed 的浮层**，
# fixed 元素的 offsetParent 恒为 null ⇒ 整个下拉被滤掉 ⇒ 返回 no-option。
# 与 TB-039 / PV-004 是同一个坑。改成 rect 判可见后立刻读到 2026-11-06/11-09/11-10 并点中。

def test_pick_earliest_date_uses_rect_visibility():
    """⑬ 日期下拉的可选日期必须用 **rect 判**可见（fixed 浮层不能用 offsetParent）。"""
    js = tbd._JS_PICK_EARLIEST_DATE
    code = "\n".join(ln.split("//")[0] for ln in js.splitlines())
    assert "offsetParent" not in code, \
        "日期下拉是 fixed 浮层，offsetParent 恒为 null ⇒ 会假阴性、日期永远填不上"
    assert "getBoundingClientRect" in code, "必须用 rect 判可见"
    assert "getClientRects" in code
    # 「非营业日」必须排除，否则会点到不可预约的日期
    assert "非营业日" in code


def test_service_modal_accepts_both_titles():
    """⑭ 物流弹窗标题改版后必须两个都认，否则弹窗明明在也判「没出现」、④ 步被假拦死。"""
    assert "物流服务一键填充策略" in tbd._SERVICE_MODAL_TITLES, "新版标题没登记"
    assert "批量填充物流服务" in tbd._SERVICE_MODAL_TITLES, "旧版标题要留着兼容灰度回滚"
    import inspect
    src = inspect.getsource(tbd.BuylabelDriver._wait_service_modal)
    assert "_SERVICE_MODAL_TITLES" in src, "等待弹窗必须遍历全部标题"
    # ★ 2026-11-05 pipeline 化后逻辑搬到了 `_fill_logistics`（fill 只是兼容壳）。
    src_logi = inspect.getsource(tbd.BuylabelDriver._fill_logistics)
    assert '_JS_MODAL_PRESENT, "物流服务一键填充策略"' not in src_logi, \
        "④ 段不许用单一标题探测弹窗，统一走 _wait_service_modal"
    assert '_JS_MODAL_PRESENT, "批量填充物流服务"' not in src_logi
    assert "_wait_service_modal" in src_logi


# ---- ⑮ 2026-11-05 用户令「固化买面单这个 skill 并 pipeline 化」---------------------------
# 原 `fill()` 一口气干完 ②顶部四维 ③批量填充+改重量 ④物流服务，中途任一失败就整段重跑。
# 拆成三段可选单跑：fill_dims / weight_fix / fill_logistics，pipeline 才能只重跑错的那段。

def test_fill_is_split_into_rerunnable_stages():
    """⑮ `fill_dims` 与 `fill_logistics` 必须是**独立方法**，且不复用整段 `fill()`。"""
    import inspect
    for name in ("fill_dims", "fill_logistics", "weight_fix"):
        assert callable(getattr(tbd.BuylabelDriver, name, None)), "缺独立阶段方法 %s" % name
    src_dims = inspect.getsource(tbd.BuylabelDriver.fill_dims)
    src_logi = inspect.getsource(tbd.BuylabelDriver.fill_logistics)
    # 各自只调自己的子步，绝不反过来调整段 fill（那等于没拆）
    assert "self.fill(" not in src_dims and "self.fill(" not in src_logi
    # fill_dims 不许触碰物流服务；fill_logistics 不许重填顶部四维
    assert "_fill_logistics" not in src_dims, "fill_dims 越界做了 ④ 物流服务"
    assert "_fill_top" not in src_logi, "fill_logistics 越界重填了 ② 顶部四维"
    assert "_fill_batch" not in src_logi, "fill_logistics 越界重做了批量填充"


def test_split_actions_registered_and_not_money_gated():
    """⑯ 两个新 action 必须进入口白名单，且**不进**花钱闸门（它们不下单）。"""
    import importlib.util
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "run_temu_buylabel.py")
    src = open(p, encoding="utf-8").read()
    assert '"fill_dims", "fill_logistics"' in src, "入口 choices 没登记新 action"
    assert "SPLIT_FILL_ACTIONS" in src
    # 它们确实是写操作（改页面值），但不花钱 ⇒ 不放进 WRITE_ACTIONS。
    # ⚠ 只比**元组本体**（到第一个 `)` 为止），注释里提到这两个名字是正常的（踩过，假红）。
    seg = src[src.index("WRITE_ACTIONS = ("):src.index("SPLIT_FILL_ACTIONS")]
    body = seg[:seg.index(")")]
    assert "fill_dims" not in body and "fill_logistics" not in body, \
        "fill_dims/fill_logistics 不提交下单，不该要求 --confirm"
