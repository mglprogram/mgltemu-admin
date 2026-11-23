# -*- coding: utf-8 -*-
"""temu_label_prep.py — 从**订单导出表**取买面单需要的 goods 明细。

TEMU 买面单接口（`temu_buylabel_api.py`）要的不只是订单号，还要每一行的：
  `order_sn`（子订单号）/ `parent_order_sn`（参考单号）/ `goods_id` / `sku_id` / `quantity`

这些字段在**订单列表页**才全（参考单号 + 子订单 + SKU ID）；
`订单导出·美国`那类 CSV/xlsx 只有订单号与 SKU货号，**没有 goods_id / sku_id**，
所以这个模块的用途是：**给定一批参考单号，输出它们在接口里要用的 goods 结构**——
字段从调用方提供的「行字典」里取（行字典由订单页解析或导出表 + SKU 映射补齐）。

纯函数，不碰浏览器；供 `temu_label_dryrun.py --from-json` 与发货总链调用。
"""
from __future__ import annotations

import json

#: 行字典里可能的键名 → 标准键（不同来源的表列名不一样）
_ALIASES = {
    "parent_order_sn": ("parent_order_sn", "参考单号", "订单号", "parentOrderSn",
                        "order_no", "ord"),
    "order_sn": ("order_sn", "子订单号", "sub_order_sn", "orderSn"),
    "goods_id": ("goods_id", "goodsId", "商品ID", "goodsSn"),
    "sku_id": ("sku_id", "skuId", "SKU ID", "skuIdStr"),
    "quantity": ("quantity", "数量", "件数", "qty", "应履约件数"),
}


def _pick(row: dict, key: str, default=None):
    for k in _ALIASES[key]:
        if k in row and str(row[k]).strip() not in ("", "None"):
            return row[k]
    return default


def to_goods_row(row: dict) -> dict | None:
    """一行 → 接口要的 goods 结构；缺关键字段返回 None（不猜）。"""
    po = _pick(row, "parent_order_sn")
    osn = _pick(row, "order_sn")
    gid = _pick(row, "goods_id")
    sid = _pick(row, "sku_id")
    if not (po and osn and gid and sid):
        return None
    try:
        qty = int(float(_pick(row, "quantity", 1) or 1))
    except (TypeError, ValueError):
        qty = 1
    return {"order_sn": str(osn), "parent_order_sn": str(po),
            "goods_id": gid, "sku_id": str(sid), "quantity": max(1, qty)}


def build_goods(rows: list, pos: list | None = None) -> tuple:
    """多行 → `(按 parent 分组的 {po: [goods...]}, 缺字段的行)`。

    `pos` 给了就只保留这些参考单号。
    """
    want = {str(x).strip() for x in (pos or []) if str(x).strip()}
    groups: dict = {}
    bad = []
    for r in rows or []:
        g = to_goods_row(r)
        if not g:
            bad.append(r)
            continue
        if want and g["parent_order_sn"] not in want:
            continue
        groups.setdefault(g["parent_order_sn"], []).append(g)
    return groups, bad


def load_rows(path: str) -> list:
    """从 json 读行字典列表（`[{"参考单号":..,"子订单号":..,...}]`）。"""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict):
        return d.get("rows") or []
    return d or []


def describe(groups: dict) -> str:
    parts = []
    for po, gs in groups.items():
        parts.append("%s(%d 行/%d 件)" % (po, len(gs), sum(x["quantity"] for x in gs)))
    return "；".join(parts) or "-"
