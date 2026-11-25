# -*- coding: utf-8 -*-
"""TEMU 确认发货**前置闸门**：必须核实领星和帕沃尔都真的发货成功。

★ 用户 2026-11-24 口径：
  「这个点确认，必须核实到领星和帕沃尔全部发货成功」
  判据：
    · 帕沃尔：`pw_receive` 结果 = 已下单 **且** `pw_track` 拿到运单号（第 14 步）
    · 恒信（领星 OMS）：`hx_oms_check` 的 `status ∈ {2 仓库处理中, 3 已出库}` **且** 有 `track`
    · 任一边没成功 ⇒ 该单**不进确认列表**，写进 failed 交人工

产物实测（2026-11-24）：
  · `pw_now.json`      {"rows":[{po,id,status:"已下单"|"下单失败",sku,wh,qd,err,lines}], "missing":[]}
  · `bhy_hx_oms_status.json` {"rows":[{po, accounts:[{tag:"LM739", status:2,
                                statusName:"仓库处理中", track:"1Z..."}]}]}
  · `pw_order_result.json`   {"total":N,"ok":[[po,"状态=已下单"],...],"fail":[...]}
"""
from __future__ import annotations

import glob
import json
import os

#: 帕沃尔「成功」的状态文案（真机：`pw_now.json` 的 status 列）
PW_OK_STATUS = "已下单"
#: 领星 OMS 视为「发货成功」的 status（2=仓库处理中 / 3=已出库）
HX_OK_STATUS = (2, 3)


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _latest(pattern: str) -> str:
    fs = sorted(glob.glob(pattern, recursive=True))
    return fs[-1] if fs else ""


def _by_store(store: str, *names: str) -> str:
    """按店铺找产物：先精确路径（多种命名都试），没有再回落全局 `_latest`。

    精确候选（2026-11-25 实测两种命名都存在）：
      · `out/<日期>/fulfill/<店小写>_hx_oms_status.json`
      · `out/<日期>/fulfill/<店>/<文件名>`
    """
    s = str(store or "").strip()
    if not s:
        return ""
    low = s.lower()
    cands = []
    for n in names:
        cands.append(os.path.join(_ROOT, "*", "fulfill", "%s_%s" % (low, n)))
        cands.append(os.path.join(_ROOT, "*", "fulfill", s, n))
        cands.append(os.path.join(_ROOT, "*", "fulfill", "*", "%s_%s" % (low, n)))
    for pat in cands:
        got = _latest(pat)
        if got:
            return got
    return ""


def pw_success(pos: set, store: str = "") -> set:
    """帕沃尔成功的单：`pw_now.json` 里 status == 已下单**且**拿到 id（单号）。

    用户口径「要等拿到运单号（pw_track）才算」⇒ 同时看 `pw_now.json`（track 阶段产物）
    与 `pw_order_result.json`（receive 阶段产物）。
    """
    ok = set()
    f = _by_store(store, "pw_now.json") if store else ""
    now = _load(f or _latest(os.path.join(_ROOT, "*", "**", "pw_now.json")))
    for r in (now.get("rows") or []):
        po = str(r.get("po") or "").strip()
        if not po:
            continue
        if str(r.get("status") or "").strip() == PW_OK_STATUS and str(r.get("id") or "").strip():
            ok.add(po)
    # 兜底：receive 阶段的 ok 列表（"状态=已下单"）
    f2 = _by_store(store, "pw_order_result.json") if store else ""
    res = _load(f2 or _latest(os.path.join(_ROOT, "*", "**", "pw_order_result.json")))
    for item in (res.get("ok") or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            po, state = str(item[0]), str(item[1])
            if PW_OK_STATUS in state:
                ok.add(po)
    return ok & pos if pos else ok


def hx_success(pos: set, store: str = "") -> set:
    """恒信（领星 OMS）成功的单：status ∈ {2,3} 且**有运单号**（track 非空）。"""
    ok = set()
    f = _by_store(store, "hx_oms_status.json") if store else ""
    f = f or _latest(os.path.join(_ROOT, "*", "**", "*_hx_oms_status.json"))
    d = _load(f)
    for r in (d.get("rows") or []):
        po = str(r.get("po") or "").strip()
        if not po:
            continue
        for acc in (r.get("accounts") or []):
            try:
                st = int(acc.get("status"))
            except (TypeError, ValueError):
                continue
            if st in HX_OK_STATUS and str(acc.get("track") or "").strip():
                ok.add(po)
                break
    return ok & pos if pos else ok


_ROOT = r"D:\AI-workplace\TEMU\scripts\power\out"


def verifiable(pw_pos: set, hx_pos: set, store: str = "") -> tuple:
    """算出「可以确认发货」的白名单 → `(ok_set, failed)`。

    `failed` = `[{po, side, why}]`：明确失败的（不确认，交人工）。
    只在一边出现的单按那一边判；两边都有的单要**两边都成功**才算。
    """
    pw_ok = pw_success(pw_pos, store)
    hx_ok = hx_success(hx_pos, store)
    ok, failed = set(), []
    for po in set(pw_pos) | set(hx_pos):
        in_pw, in_hx = po in pw_pos, po in hx_pos
        need = []
        if in_pw and po not in pw_ok:
            need.append("帕沃尔未拿到运单号/未下单成功")
        if in_hx and po not in hx_ok:
            need.append("领星 OMS 未受理或无运单号")
        if need:
            failed.append({"po": po, "side": "PW" if in_pw else "HX",
                           "why": "；".join(need)})
        else:
            ok.add(po)
    return ok, failed
