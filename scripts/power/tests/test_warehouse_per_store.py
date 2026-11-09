# -*- coding: utf-8 -*-
"""test_warehouse_per_store.py — 买面单「三仓」口径（2026-11-08 用户令）回归测试。

锁死三件事（纯函数，不开浏览器）：
  A) 每店允许的三仓清单 = 用户给的三个名字（BEL 带「仓」/ BHY 中文三且不带「仓」）；
  B) `map_warehouse` 能把库存源名字（加州3仓 / kansas仓 / 恒信加州3仓）转成该店下拉真值；
  C) `assert_warehouse` 对不在清单里的仓名**本地就拦**（不必等 no-option）。
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
if _POWER not in sys.path:
    sys.path.insert(0, _POWER)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_temu_fulfill", os.path.join(_POWER, "run_temu_fulfill.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

CFG = json.load(open(os.path.join(_POWER, "data", "temu_buylabel.json"), encoding="utf-8"))


def test_allow_lists():
    assert M.allowed_warehouses("BEL", CFG) == ["加州3仓", "kansas", "恒信加州3仓"]
    assert M.allowed_warehouses("BHY", CFG) == ["加州三仓", "kansas", "恒信加州3"]


def test_map_bel():
    assert M.map_warehouse("BEL", "加州3仓", CFG) == "加州3仓"
    assert M.map_warehouse("BEL", "kansas仓", CFG) == "kansas"
    assert M.map_warehouse("BEL", "恒信加州3仓", CFG) == "恒信加州3仓"
    assert M.map_warehouse("BEL", "恒信加州3", CFG) == "恒信加州3仓"


def test_map_bhy():
    assert M.map_warehouse("BHY", "加州3仓", CFG) == "加州三仓"
    assert M.map_warehouse("BHY", "kansas仓", CFG) == "kansas"
    assert M.map_warehouse("BHY", "恒信加州3仓", CFG) == "恒信加州3"


def test_gate_rejects_cross_store_name():
    for store, bad in (("BEL", "加州三仓"), ("BEL", "恒信加州3"), ("BHY", "加州3仓"), ("BHY", "恒信加州3仓")):
        try:
            M.assert_warehouse(store, bad, CFG, "t")
        except RuntimeError as ex:
            assert "允许清单" in str(ex), str(ex)
        else:
            raise AssertionError("%s 用了 %r 竟然没被拦" % (store, bad))


def test_gate_accepts_own_names():
    for store in ("BEL", "BHY"):
        for wh in M.allowed_warehouses(store, CFG):
            M.assert_warehouse(store, wh, CFG, "t")


def test_hx_warehouse_per_store():
    bel = ((CFG.get("stores") or {}).get("BEL") or {}).get("hx_warehouse")
    bhy = ((CFG.get("stores") or {}).get("BHY") or {}).get("hx_warehouse")
    assert bel == "恒信加州3仓" and bhy == "恒信加州3", (bel, bhy)


# ---------------------------------------------------------------- 2026-11-08 更新订单前置步骤
def test_pipeline_has_update_before_hwc_order():
    """下单代发(hwc_order) 前必须紧跟 pw_update_orders（不同步就「复制订单找不到订单」）。"""
    import json as _json
    import os as _os
    p = _os.path.join(_POWER, "data", "temu_fulfill_pipeline.json")
    steps = _json.load(open(p, encoding="utf-8"))["steps"]
    names = [s["name"] for s in steps]
    assert "pw_update_orders" in names, names
    assert names[names.index("hwc_order") - 1] == "pw_update_orders", names
    assert "pw_update_orders" in M.STEPS, "步骤没注册到 STEPS"
    assert "pw_update_orders" in M.WRITE_STEPS, "写步骤没进 --confirm 闸门"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn()
            print("[OK  ]", fn.__name__)
        except Exception as ex:  # noqa: BLE001
            bad += 1
            print("[FAIL]", fn.__name__, "→", ex)
    print("\n%d 过 / %d 败" % (len(fns) - bad, bad))
    sys.exit(0 if not bad else 1)
