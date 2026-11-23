# -*- coding: utf-8 -*-
"""test_hx_ship_warehouse_route.py — 恒信仓「分配仓库&物流」按店分流的回归钉。

★ 2026-11-21 用户令：用户贴出领星「分配仓库&物流」弹窗截图（发货仓库＝
  `ZOO WAREHOUSE ⑥美西 USW01`、渠道＝`上传物流面单 Upload_Shipping_Label`）并说：

      「今天 BS 发货为什么使用 temu 的发货仓（temu自动分仓）；**shein 就是之前那种方式去选择**」

  11-20 那次改成了**全局** `Temu Selected Warehouse`（`hx_ship_gw.js` 里两行常量），
  于是 SHEIN 店（BS/BD/BAN）全被打成 Temu 自动分仓 —— 领星/帕沃尔那边根本没有这个仓。

  现在口径写死成按平台分流，钉在 `scripts/oms/hx_ship_gw.js` 的 `whOf(store)`：
    · TEMU 店（BHY / BEL / KinglamanSport）→ `Temu Selected Warehouse` + `Temu自动分配渠道`
    · SHEIN 店（BS / BD / BAN / BE）      → `ZOO WAREHOUSE ⑥美西` + `Upload_Shipping_Label`
  这个测试读源码做静态断言（不连浏览器、不跑 node），因为那两个常量就是唯一真相。
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
_WS = os.path.dirname(os.path.dirname(_POWER))
GW = os.path.join(_WS, "scripts", "oms", "hx_ship_gw.js")


def _src() -> str:
    assert os.path.exists(GW), "找不到 hx_ship_gw.js：%s" % GW
    return open(GW, encoding="utf-8").read()


def _const(src: str, name: str) -> str:
    m = re.search(r"const\s+%s\s*=[^\n]*?['\"]([^'\"]+)['\"]" % re.escape(name), src)
    assert m, "源码里找不到常量 %s" % name
    return m.group(1)


def test_shein_warehouse_is_the_old_one():
    """SHEIN 店必须是老口径：**仓编码 USW01** + 上传物流面单。

    ★ 2026-11-23 真机：填仓库显示名 `ZOO WAREHOUSE ⑥美西` 会被领星审核拒
      （「当前仓库[...]不可用，请修改;仓库不可使用选定物流渠道[Upload_Shipping_Label]」），
      必须填编码 `USW01`（最初跑通那版就是它）。
    """
    src = _src()
    assert _const(src, "WH_SHEIN") == "USW01", _const(src, "WH_SHEIN")
    assert _const(src, "CH_SHEIN") == "Upload_Shipping_Label", _const(src, "CH_SHEIN")
    assert _const(src, "CH_SHEIN_NAME") == "上传物流面单", _const(src, "CH_SHEIN_NAME")


def test_temu_warehouse_is_auto_split():
    """TEMU 店仍是 Temu 自动分仓（2026-11-20 定的，用户 11-21 只要求把 SHEIN 改回去）。"""
    src = _src()
    assert _const(src, "WH_TEMU") == "Temu Selected Warehouse", _const(src, "WH_TEMU")
    assert _const(src, "CH_TEMU") == "Temu Selected Warehouse", _const(src, "CH_TEMU")
    assert _const(src, "CH_TEMU_NAME") == "Temu自动分配渠道", _const(src, "CH_TEMU_NAME")


def test_temu_store_list():
    """哪些店走 Temu 分仓：只许这三家。"""
    src = _src()
    m = re.search(r"const\s+TEMU_STORES\s*=\s*\[([^\]]*)\]", src)
    assert m, "找不到 TEMU_STORES"
    stores = {x.strip().strip("'\"").upper() for x in m.group(1).split(",") if x.strip()}
    assert stores == {"BHY", "BEL", "KINGLAMANSPORT"}, stores


def test_whOf_dispatches_by_store():
    """`whOf()` 必须按店返回不同仓：TEMU 三店 → TEMU 那套；其余（SHEIN）→ 老那套。"""
    src = _src()
    assert re.search(r"function\s+whOf\s*\(", src), "缺少 whOf()"
    body = re.search(r"function\s+whOf\s*\(store\)\s*\{([\s\S]*?)\n\}", src).group(1)
    assert "TEMU_STORES" in body, body
    assert "WH_TEMU" in body and "WH_SHEIN" in body, body


def test_payloads_no_longer_use_global_constants():
    """★ 事故根因：payload 直接用全局常量 `WH` ⇒ 所有店同一个仓。

    现在每处都必须先 `whOf(<单的店>)` 再赋 `sendWhCode`。
    """
    src = _src()
    assert "payload.sendWhCode = WH;" not in src, "还有 payload 在用全局 WH"
    assert "payload0.sendWhCode = WH;" not in src, "还有 payload 在用全局 WH"
    # 每次赋 sendWhCode 都要有对应的 whOf(...) 取仓
    n_wh = len(re.findall(r"sendWhCode\s*=\s*w[01f]\.wh", src))
    n_local = len(re.findall(r"const\s+w[01f]\s*=\s*whOf\(", src))
    assert n_wh >= 3 and n_wh == n_local, (n_wh, n_local)
