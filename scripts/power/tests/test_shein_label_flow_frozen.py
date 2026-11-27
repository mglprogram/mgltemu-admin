# -*- coding: utf-8 -*-
"""test_shein_label_flow_frozen.py — SHEIN 每店必须「买面单 + 复核运单号」，且重量=件数×单件。

★★ 用户 2026-11-27 明确口径：
    「每个 SHEIN 店铺去买完面单，然后去检查有没有出运单号，也就是下单失败可能出现」
    「以后的发货要有这几步」

为什么复核不是多余动作：
    `gsp/place-express-order` 返回 `code=0` 只代表**平台受理**。承运商事后拒单时，
    页面显示「下单失败」，而 OpenAPI **只表现为 `packageWaybillList[].waybillNo` 为空**。
    2026-11-18 BS 就有 1 单（SwiftX 拒单）要人工去页面才发现。

重量口径（用户 2026-11-27）：
    「默认件数 × 1000g」或「件数 × 1.1lb」⇒ 实现是**按店取单件克数再乘件数**：
      BS / BE = 1000 g/件；BD / BAN = 1.1 lb ≈ 499 g/件
"""
from __future__ import annotations

import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_PROJ = os.path.dirname(os.path.dirname(_POWER))
for _p in (_POWER, os.path.join(_POWER, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import run_temu_fulfill as F  # noqa: E402

_SHEIN_STORES = ("BS", "BD", "BAN", "BE")
_PIPE = os.path.join(_POWER, "data", "temu_fulfill_pipeline.json")


# ---------------------------------------------------------------- pipeline 配置


def test_both_steps_present_and_ordered():
    """买面单必须在复核之前，且两步都在整链里。"""
    d = json.load(io.open(_PIPE, encoding="utf-8"))
    names = [s["name"] for s in d["steps"]]
    assert "label_shein" in names, "整链缺 label_shein"
    assert "label_shein_verify" in names, "整链缺 label_shein_verify"
    assert names.index("label_shein") < names.index("label_shein_verify"), \
        "必须先买面单再复核"


def test_verify_enabled_for_all_four_shein_stores():
    """复核步骤必须对 BS/BD/BAN/BE 四店都开。"""
    d = json.load(io.open(_PIPE, encoding="utf-8"))
    s = next(x for x in d["steps"] if x["name"] == "label_shein_verify")
    stores = {str(x).upper() for x in (s.get("enabled_stores") or [])}
    assert stores == set(_SHEIN_STORES), "复核没覆盖四店：%s" % stores
    assert s.get("delegate") is not True, "复核是本店内的轻量动作，不必强制派子代理"


def test_verify_params_are_sane():
    """买完等 5 分钟再核、最多 2 轮（等太久没意义，轮次太多会重复花钱）。"""
    d = json.load(io.open(_PIPE, encoding="utf-8"))
    s = next(x for x in d["steps"] if x["name"] == "label_shein_verify")
    p = s.get("params") or {}
    assert int(p.get("wait_min") or 0) >= 1
    assert int(p.get("rounds") or 0) >= 1


def test_pipeline_json_documents_shein_flow():
    """口径必须写进 json（不然只剩代码没有说明书）。"""
    d = json.load(io.open(_PIPE, encoding="utf-8"))
    blob = " ".join("%s %s" % (k, d[k]) for k in d if k.startswith("_"))
    assert "复核运单号" in blob or "检查有没有出运单号" in blob, "没写复核要求"
    assert "1000" in blob and ("1.1" in blob or "499" in blob), "没写重量口径"
    assert "v13" in str(d.get("_版本"))


# ---------------------------------------------------------------- 代码行为


def test_non_shein_store_skips_both_steps():
    """TEMU 店（BHY/BEL/KLS）两步都要 skip —— 它们没有 SHEIN 账号。"""
    for fn in (F.step_label_shein, F.step_label_shein_verify):
        out = fn({"store": "KinglamanSport", "out_dir": "/tmp",
                  "split_json": "/tmp/x.json", "params": {}},
                 log=lambda m: None)
        assert "skipped" in str(out.get("label_shein")
                                or out.get("label_shein_verify") or ""), out


def test_verify_writes_apply_only_when_confirmed():
    """复核会**重下单＝花钱**，所以 `--apply` 只能在 `--confirm` 时加。"""
    src = io.open(os.path.join(_POWER, "run_temu_fulfill.py"),
                  encoding="utf-8").read()
    i = src.find("def step_label_shein_verify")
    body = src[i:src.find("\ndef ", i + 10)]
    assert 'ctx.get("confirm")' in body, "复核没看 --confirm 闸门"
    assert '"--apply"' in body, "复核没传 --apply（那它只会干看）"
    # --apply 必须在 confirm 分支里
    j = body.find('ctx.get("confirm")')
    k = body.find('"--apply"')
    assert j < k, "--apply 应写在 confirm 判断之后"


def test_verify_is_soft_failure_not_hard():
    """复核没全出号不应把整链打断（后面的仓内动作照跑，report 收尾）。"""
    assert "label_shein_verify" not in F.STRUCTURAL_STEPS


# ---------------------------------------------------------------- 重量口径


def _shein_script():
    return os.path.join(os.path.expanduser("~/.agents/skills"),
                        "shein-buylabel", "scripts", "shein_buy_label_split.py")


def test_weight_is_per_item_times_quantity():
    """★ 用户口径：默认 **件数 × 单件重量**（BS/BE=1000g，BD/BAN=1.1lb≈499g）。"""
    p = _shein_script()
    if not os.path.exists(p):
        import pytest  # noqa: PLC0415
        pytest.skip("本机没有 shein-buylabel skill")
    src = io.open(p, encoding="utf-8").read()
    # DEFAULT_WT 表存在且分店
    assert "DEFAULT_WT" in src, "没有按店的单件重量表"
    assert '"BS"' in src and '"BD"' in src, "DEFAULT_WT 没分 BS/BD"
    # 乘法：per_item * qty
    assert "per_item * " in src or "* max(1, qty)" in src, "不是按件数相乘算的"
    # 固定重量可覆盖
    assert "weight_g" in src, "没有固定包裹重量的覆盖入口"


def test_weight_values_match_user_spec():
    """BS/BE = 1000 g/件；BD/BAN = 1.1 lb ≈ 499 g/件。"""
    p = _shein_script()
    if not os.path.exists(p):
        import pytest  # noqa: PLC0415
        pytest.skip("本机没有 shein-buylabel skill")
    spec_dir = os.path.dirname(p)
    if spec_dir not in sys.path:
        sys.path.insert(0, spec_dir)
    import shein_buy_label_split as S  # noqa: PLC0415
    wt = S.DEFAULT_WT
    assert wt["BS"] == 1000, "BS 应是 1000 g/件，实得 %s" % wt.get("BS")
    assert wt["BE"] == 1000, "BE 应是 1000 g/件，实得 %s" % wt.get("BE")
    assert 490 <= wt["BD"] <= 510, "BD 应约为 1.1lb≈499g，实得 %s" % wt.get("BD")
    assert 490 <= wt["BAN"] <= 510, "BAN 应约为 1.1lb≈499g，实得 %s" % wt.get("BAN")
