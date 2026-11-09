# -*- coding: utf-8 -*-
"""test_weight_fix_waits_for_hints.py — 钉住「重量核对必须等红字渲染完再判」。

2026-11-09 真机事故（BHY 47 单买面单全部失败）
------------------------------------------------
`fill_dims` 点完「批量填充」后**立刻**进 `weight_fix`。那一刻页面刚把 1.50
写进每个重量框，**平台的校验红字还没渲染出来**（React 异步比「包裹内 SKU 总重量」）。
旧实现 `_scan()` **读一次就判**：

    weights=["1.50",...], mismatchHint=false, hintWeight=""

⇒ `bad=[]` ⇒ 报「已匹配」⇒ 一路带到 `place_only` ⇒ 平台这才报
「差异过大（3.07 磅）」⇒ 下单被拒、47 单全部退回待发货。

铁证：`03c_weight_fix_ok.png` 与 `01_batch_page.png` **同一秒**落盘
（11:11:20），说明它压根没等。

修法
----
改成轮询等待「红字数量不再变化」：
  · `wait_min_s` 内一直没红字 ⇒ 真·已匹配（不是"还没渲染"）
  · 红字数量连续 `wait_stable_rounds` 次相同 ⇒ 渲染完成、开始修
  · `wait_max_s` 硬上限（超时按最后一次结果继续，绝不卡死）

本文件只做**纯逻辑**校验：用假 page/假扫描喂给 `weight_fix`，不起浏览器。
"""
from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # scripts/power/tests
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # 仓库根
sys.path.insert(0, os.path.join(_PROJ, "scripts", "power", "lib"))

DRIVER = os.path.join(_PROJ, "scripts", "power", "lib", "temu_buylabel_driver.py")
CFG = os.path.join(_PROJ, "scripts", "power", "data", "temu_buylabel.json")


def _src() -> str:
    with open(DRIVER, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- 源码层

def test_weight_fix_uses_settle_helper():
    """重量核对必须走「等红字稳定」的辅助函数，不能直接 `_scan()`。"""
    src = _src()
    assert "def _scan_when_hints_settle" in src, "等待红字的辅助函数不见了"
    i = src.find("def weight_fix")
    seg = src[i:i + 12000]
    assert "_scan_when_hints_settle()" in seg, (
        "weight_fix 又直接 _scan() 读一次就判了 → 红字没渲染就会误判「已匹配」")
    # 首次扫描必须来自等待函数，而不是裸 _scan()
    m = re.search(r"rows = (_scan_when_hints_settle\(\)|_scan\(\))", seg)
    assert m and m.group(1).startswith("_scan_when_hints_settle"), (
        "首次扫描还是裸 _scan()：%r" % (m.group(1) if m else None))


def test_settle_helper_polls_until_stable():
    """必须「红字数量稳定」才返回，而不是第一次读到红字就返回。"""
    src = _src()
    i = src.find("def _scan_when_hints_settle")
    assert i > 0
    seg = src[i:i + 3200]
    assert "stable" in seg, "没有稳定性判断"
    assert "stable_need" in seg or "wait_stable_rounds" in seg, "没有连续次数阈值"
    assert "time.sleep" in seg, "没有轮询，等于还是读一次"
    assert "time.time()" in seg, "没有超时控制"


def test_settle_helper_has_hard_cap():
    """必须有硬上限：超时就按最后结果继续，绝不能卡死。"""
    src = _src()
    i = src.find("def _scan_when_hints_settle")
    seg = src[i:i + 3200]
    assert "max_wait" in seg, "没有最大等待上限"
    assert "超时" in seg, "超时没有走「按最后结果继续」的分支"


def test_no_hint_within_min_wait_means_matched():
    """min_wait 内没红字 = 真·已匹配（这是唯一允许「跳过修复」的路径）。"""
    src = _src()
    i = src.find("def _scan_when_hints_settle")
    seg = src[i:i + 3200]
    assert "min_wait" in seg, "没有最短等待，会重演「读一次就判」"
    assert "无红字" in seg or "均已匹配" in seg, "没有「没红字即视为匹配」的显式分支"


# ---------------------------------------------------------------- 配置层

def test_config_carries_wait_params():
    import json
    with open(CFG, encoding="utf-8-sig") as fh:
        wm = json.load(fh)["weight_mismatch"]
    for k in ("wait_min_s", "wait_max_s", "wait_poll_s", "wait_stable_rounds"):
        assert k in wm, "配置缺 %s（代码会退回默认值，但口径不可见）" % k
    assert wm["wait_min_s"] >= 2, "最短等待太短，红字来不及渲染"
    assert wm["wait_max_s"] > wm["wait_min_s"], "上限必须大于下限"
    assert wm["wait_poll_s"] > 0
    assert wm["wait_stable_rounds"] >= 2, "至少要连续两次相同才叫稳定"


def test_config_mentions_the_incident():
    """口径文件要留下「为什么加这个等待」，否则下次有人会把它当无用参数删掉。"""
    import json
    with open(CFG, encoding="utf-8-sig") as fh:
        wm = json.load(fh)["weight_mismatch"]
    blob = json.dumps(wm, ensure_ascii=False)
    assert "2026-11-09" in blob, "没记事故日期"
    assert "同一秒" in blob or "13.07" in blob or "3.07" in blob or "差异过大" in blob, (
        "没写清事故现象（红字没渲染就判已匹配）")


def test_production_config_never_disables_wait():
    """`wait_min_s <= 0` 会让代码退化成「读一次就判」= 今天这个 bug 原样复活。

    单测里为了不真等秒数可以设 0，但**生产配置文件里绝对不许**。
    """
    import json
    with open(CFG, encoding="utf-8-sig") as fh:
        wm = json.load(fh)["weight_mismatch"]
    assert wm.get("wait_min_s", 0) > 0, (
        "temu_buylabel.json 里 wait_min_s <= 0 ⇒ 重量核对又会读一次就判，"
        "2026-11-09 那 47 单事故会重演")


# ---------------------------------------------------------------- 行为层（假 page）

class _FakePage:
    """按预置脚本依次返回扫描结果。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def evaluate(self, js, *a, **kw):
        self.calls += 1
        if not self.script:
            return self.script_hold
        return self.script.pop(0)


def _make_driver(wm_over: dict | None = None):
    import temu_buylabel_driver as D
    cfg = {
        "weight_per_unit_lb": 1.5,
        "weight_decimals": 2,
        "weight_mismatch": {
            "enabled": True,
            "hint_wins": True,
            "wait_min_s": 0.01,
            "wait_max_s": 0.2,
            "wait_poll_s": 0.005,
            "wait_stable_rounds": 2,
        },
    }
    if wm_over:
        cfg["weight_mismatch"].update(wm_over)
    cls = getattr(D, "BuylabelDriver", None) or getattr(D, "TemuBuyLabelDriver")
    drv = object.__new__(cls)
    drv.cfg = cfg
    drv.log = lambda *a, **k: None
    drv.shot = lambda *a, **k: None
    return D, drv


def _rows(hint: bool, w: str = "1.50", n: str = ""):
    return [{"ord": "PO-211-00000000000000001", "qty": 1,
             "weights": [w], "mismatchHint": hint, "hintWeight": n}]


def test_waits_until_hint_appears(monkeypatch):
    """先读不到红字、后读到 ⇒ 必须等到红字，并按红字的 N 修。"""
    D, drv = _make_driver()
    seq = [
        json_dumps(_rows(False)),          # 第 1 次：红字还没渲染
        json_dumps(_rows(True, n="3.07")), # 第 2 次：红字出现
        json_dumps(_rows(True, n="3.07")), # 第 3 次：稳定
        json_dumps(_rows(True, n="3.07")),
        json_dumps(_rows(True, n="3.07")),
    ]
    page = _FakePage(seq)
    page.script_hold = json_dumps(_rows(True, n="3.07"))
    out = drv.weight_fix(page, plan=[{"order_no": "PO-211-00000000000000001", "qty": "1"}])
    assert page.calls >= 3, "没轮询（只读了 %d 次）" % page.calls
    assert out["checked"] >= 1


def test_hint_value_beats_base_formula():
    """红字里的 N（3.07）必须**覆盖** 基准×件数（1.50）—— 否则等到了也白等。"""
    D, drv = _make_driver()
    seen = {}

    def _fake_fix(page, ordno, want, *a, **kw):
        seen[ordno] = want
        return True

    drv._set_weight_one = _fake_fix
    drv._fix_one_weight = _fake_fix
    page = _FakePage([json_dumps(_rows(True, w="1.50", n="3.07"))] * 6)
    page.script_hold = json_dumps(_rows(True, w="1.50", n="3.07"))
    drv.weight_fix(page, plan=[{"order_no": "PO-211-00000000000000001", "qty": "1"}])
    if seen:
        assert any(abs(float(v) - 3.07) < 0.01 for v in seen.values()), (
            "填进去的不是红字里的 3.07，而是 %r" % seen)


def test_no_hint_stays_untouched():
    """一直没红字 ⇒ 不能乱改重量（这是唯一允许跳过的路径）。"""
    D, drv = _make_driver()
    touched = []

    def _spy(*a, **kw):
        touched.append(a)
        return True

    for name in ("_set_weight_one", "_fix_one_weight"):
        if hasattr(drv, name):
            setattr(drv, name, _spy)
    page = _FakePage([json_dumps(_rows(False))] * 20)
    page.script_hold = json_dumps(_rows(False))
    out = drv.weight_fix(page, plan=[{"order_no": "PO-211-00000000000000001", "qty": "1"}])
    assert not touched, "没红字却改了重量：%r" % touched[:3]
    assert out.get("fixed", 0) == 0


def json_dumps(o):
    import json
    return json.dumps(o, ensure_ascii=False)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
