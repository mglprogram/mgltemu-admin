# -*- coding: utf-8 -*-
"""test_presale_stock_first.py — 预售单「有货就发货」的回归钉。

★ 2026-11-23 用户令（改口径）：
    「如果发货前抓到最新的领星总库存判断，开了预售的产品-尺码库存有货了，
      就可以把这些分类到恒信或是帕沃尔的 sheet 中」。

⇒ 预售单**不再无条件进 D 组**：
   · 当前库存（本批 refresh_stock 刚抓的 left_hx / left_pw）够整单 → 正常拆到 **HX / PW**；
   · 都不够 → 才进 D 组（PRESALE / PRESALE15）等补货。

`allocate()` 返回的是 `{组名: [行]}`（不是 counts），这里直接数行。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_POWER = os.path.dirname(_HERE)
for p in (_POWER, os.path.join(_POWER, "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import bel_split_by_stock as S  # noqa: E402

SKU = "BHY-YOGA-4SetA-XXL"          # 手工名单里确定算预售的款（仅 XXL）


def _ord(po, qty=1):
    return {"ord": po, "sku": SKU, "qty": qty}


def _n(g, key):
    return len(g.get(key) or [])


def _no_panel(monkeypatch):
    """把面板缓存置空 ⇒ 只按手工名单判预售（不依赖线上/本地面板，测试稳定）。"""
    monkeypatch.setattr(S, "PANEL_CACHE", {}, raising=True)
    monkeypatch.setattr(S, "PANEL_META", {}, raising=True)


def test_sku_is_presale():
    assert S.is_presale(SKU, "BHY"), "前提：这个 SKU 必须被判成预售"


def test_presale_with_hx_stock_goes_to_warehouse(monkeypatch):
    """预售尺码现在**恒信有货** ⇒ 进 HX，不进 PRESALE。"""
    _no_panel(monkeypatch)
    lx_idx = {S.brand_tail(S.norm_sku(SKU)): [{"avail": 5}]}
    g = S.allocate([_ord("PO-1")], {}, lx_idx, store="BHY")
    assert _n(g, "HX") == 1, g
    assert _n(g, "PRESALE") == 0, g


def test_presale_with_pw_stock_goes_to_pw(monkeypatch):
    """只有**帕沃尔**有货 ⇒ 进 PW，同样不该被扣在预售组。"""
    _no_panel(monkeypatch)
    pw_idx = {S.brand_tail(S.norm_sku(SKU)): [{"stock": 3, "wh": "加州3仓"}]}
    g = S.allocate([_ord("PO-3")], pw_idx, {}, store="BHY")
    assert _n(g, "PW") == 1, g
    assert _n(g, "PRESALE") == 0, g


def test_presale_without_stock_goes_to_presale(monkeypatch):
    """预售尺码仍然**没货** ⇒ 进 PRESALE（等补货）。"""
    _no_panel(monkeypatch)
    g = S.allocate([_ord("PO-2")], {}, {}, store="BHY")
    assert _n(g, "PRESALE") == 1, g
    assert _n(g, "HX") == 0 and _n(g, "PW") == 0, g


def test_presale_partial_stock_stays_in_presale(monkeypatch):
    """整单要 3 件、只有 1 件 ⇒ 仍进预售（整单口径：凑不齐不发）。"""
    _no_panel(monkeypatch)
    lx_idx = {S.brand_tail(S.norm_sku(SKU)): [{"avail": 1}]}
    g = S.allocate([_ord("PO-4", 3)], {}, lx_idx, store="BHY")
    assert _n(g, "PRESALE") == 1, g
