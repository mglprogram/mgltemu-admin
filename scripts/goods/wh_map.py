# -*- coding: utf-8 -*-
"""各店真实仓名/仓 id 映射（2026-11-24 实测采集，用 scan_wh_names.py 可重采）。

⚠ 三店仓名**不一样**，而且互相会撞子串匹配：
    BHY：帕沃尔=「加州三仓」      恒信=「恒信加州3」      kansas=「kansas」
    BEL：帕沃尔=「加州3仓」        恒信=「恒信加州3仓」    kansas=「kansas仓」
    注意 BEL 的「恒信加州3仓」同时含「恒信」和「加州3仓」⇒ 拿子串匹配必然把
    恒信仓认成加州三仓（2026-11-24 踩过：BEL-202504-5SetA 5 个尺码写入成功却校验成 qty=0）。

所以一律**按店查表**，不要靠子串猜。表里没有的仓名归 unknown，单独列出来人工看。
"""
from __future__ import annotations

#: 店 → {仓类别: 仓 id}；类别固定为 hx(恒信) / ca3(帕沃尔加州3) / ks(Kansas)
WH_ID = {
    "BHY": {
        "ca3": "WH-09107063234693163",
        "hx": "WH-04940925214853163",
        "ks": "WH-02922306109573163",
    },
    "BEL": {
        "ca3": "WH-05667065528452987",
        "hx": "WH-10953873940612987",
        "ks": "WH-09790186209412987",
    },
}

#: 店 → {仓类别: 仓名原文}（出错时给人看）
WH_NAME = {
    "BHY": {"ca3": "加州三仓", "hx": "恒信加州3", "ks": "kansas"},
    "BEL": {"ca3": "加州3仓", "hx": "恒信加州3仓", "ks": "kansas仓"},
}

#: 店 → {仓 id: 仓类别}（回读时反查，避免用仓名做子串匹配）
ID2SIDE = {st: {v: k for k, v in m.items()} for st, m in WH_ID.items()}

CANON = {"KLS": "KinglamanSport", "BH": "BHY", "KU": "KinglamanSport"}


def side_by_id(store: str, whid: str) -> str:
    """仓 id → hx/ca3/ks；认不出返回空串。"""
    return ID2SIDE.get(CANON.get(store.upper(), store), {}).get(str(whid or ""), "")


def side_by_name(store: str, name: str) -> str:
    """仓名 → hx/ca3/ks。**精确比对**（先查本店表，再退回通用兜底）。"""
    n = (name or "").strip()
    m = WH_NAME.get(CANON.get(store.upper(), store), {})
    for side, nm in m.items():
        if n == nm:
            return side
    # 兜底：只用于表里没登记的新店/新仓，别用于 BEL（会撞「恒信加州3仓」）
    if n.startswith("恒信"):
        return "hx"
    if n in ("加州三仓", "加州3仓", "加州三仓"):
        return "ca3"
    if ("kansas" in n.lower()) or ("堪萨斯" in n):
        return "ks"
    return ""
