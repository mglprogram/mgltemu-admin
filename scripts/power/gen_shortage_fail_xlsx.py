# -*- coding: utf-8 -*-
"""gen_shortage_fail_xlsx.py — 把某店当天的「缺货清单」和「失败订单」整理成一份 Excel。

用户口径（2026-11-06）：
    「把今天 bel 和 BHY 的缺货清单和失败订单整理到一个 excel 表分成两个 sheet」
    追问确认后：「分两个表，一个 BHY，一个 BEL，**一个表分为一个缺货 sheet 和失败订单 sheet**」
    ⇒ 每个店一份工作簿，每份**恰好两个 sheet**：`缺货` + `失败订单`。

两个 sheet 的定义（别混，混了人工会重复处理）：
    缺货    —— 拆仓阶段两边都没有可用库存的（`split.json → groups.MISS`），**不许发货**
    失败订单 —— 已经派到仓库、但下单/生成出库单失败的（`hwc_shop.php` 状态=下单失败/未下单）
               前者是「没货可发」，后者是「有分配但没成」，处理动作完全不同。

数据来源（都是只读产物，本脚本不碰浏览器）：
    split.json          拆仓结果（含每单的 why / variant_missing）
    <店>_hwc_now.json   帕沃尔 hwc_shop 当前状态（status=下单失败/未下单 + err）
    pw_deliver_index.json  帕沃尔 deliver 索引（判断该单是否其实已有运单号）

用法：
    python scripts/power/gen_shortage_fail_xlsx.py --store BHY \
        --split out/2026-11-06/fulfill/split.json \
        --hwc out/2026-11-06/bhy_hwc_now.json \
        --out out/2026-11-06/BHY_缺货与失败订单_20261106.xlsx
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

#: 缺货原因里的「变体不存在」提示前缀（`split.py` 里拼的）
_VAR_HINT = re.compile(r"\*\*变体不存在\*\*：(.+?)，疑似上链接命名有误")


def _load(p: str, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def classify_miss(rows: list, idx: dict) -> tuple[list, list]:
    """把 MISS 拆成「真缺货」和「其实已下单（有运单号）」两摞（纯函数）。

    ★ 2026-11-06 真机：BHY 308 单 MISS 里有 6 单在帕沃尔**已经出单且有运单号**
      （`BS-202504-5SetE-XL`，add 16:04），它们不是缺货，是早就发掉了。
      不区分就会让人工去"补货"，实际要做的只是回 TEMU 点确认发货。
    """
    real, already = [], []
    for r in rows or []:
        po = r.get("ord") or ""
        hit = (idx or {}).get(po) or {}
        wb = (hit.get("wb") or "").strip()
        if wb:
            already.append(dict(r, waybill=wb))
        else:
            real.append(r)
    return real, already


def miss_reason(r: dict) -> tuple[str, str]:
    """缺货行 → (人读的原因, 变体名提示)。"""
    why = str(r.get("why") or "")
    m = _VAR_HINT.search(why)
    hint = m.group(1) if m else ""
    if hint:
        return "两边都无可用库存；**变体在库存里不存在**（疑似上链接命名有误）", hint
    if "分完" in why:
        return "两边可用库存已被本批前面的单分完（不是没这个 SKU）", ""
    if "无此 SKU" in why:
        return "两边都无此 SKU（库存里查不到）", ""
    return why or "两边都无可用库存", ""


def fail_reason(status: str, err: str) -> tuple[str, str]:
    """帕沃尔失败行 → (失败环节, 人读原因)。"""
    err = (err or "").strip()
    if status == "未下单":
        return "帕沃尔下单（未提交）", "仓库里没有这个单的发货记录（下了但没成，需重下）"
    m = re.search(r"sku:(\S+?)(库存不足发货|仓库没有库存)", err)
    if m:
        good = "库存不足" if "不足" in m.group(2) else "该仓无此库存"
        return "帕沃尔下单", "Fnsku %s %s（该仓现货 0）" % (m.group(1), good)
    if "状态异常" in err:
        return "帕沃尔下单", "SKU 状态异常，无法下单"
    if "网络异常" in err:
        return "帕沃尔下单", "网络异常（可原样重试）"
    return "帕沃尔下单", err or "未知"


#: 领星 OMS 状态号 → 人话（与 `scripts/oms/check_po_status.py` 的 `STATUS_TEXT` 同源）
OMS_STATUS = {0: "草稿", 1: "待处理", 2: "仓库处理中", 3: "已出库", 4: "已取消", 5: "异常"}

#: 这些状态算「已受理」，不进失败单
OMS_OK = (2, 3)


def hx_fail(rec: dict, po: str, shipped: bool = True) -> dict | None:
    """恒信那份在领星 OMS 的终态判定（纯函数）：不通过就返回一行失败，通过返回 None。

    ★ 2026-11-06 用户追问「恒信的失败订单你有一起汇总到表吗」—— 之前 `build()` 里
      写了一句 `if sd == "恒信": continue`，把整个恒信组**无条件**排除在失败 sheet 外，
      恒信真失败也会被静默吞掉。这里补上：恒信只看领星 OMS 的终态，不看帕沃尔页面的
      「未下单」（那份本来就走 OMS，帕沃尔那边必然显示未下单，不是失败）。

    ★ shipped：**这一单**是否真的被 `hx_ship` 带进过 OMS（2026-11-07 改成按单号判）。
      之前是整个步骤一个开关：跑过就算全进过 ⇒ 没进 OMS 的单被报「查无此单」，
      BEL 19 单就是这么被误报成失败、还顺带把 pw_track / temu_confirm 带停的。
      没带进 OMS 的单是「还没发」，交给 `hx_count_check` 的「数量对不上」归类。
      `shipped=None` = **没核对**（旧调用没传单号清单）：此时既不报「查无此单」，
      也不用空记录去套状态判据（`{}` 会被误当成"查了但没有"）。

    判据（都不许当成功）：
      · OMS 里查不到该单            → 失败「OMS 里查不到」（仅 shipped=True）
      · status 不在 (2 仓库处理中, 3 已出库) → 失败，按状态名说清（待处理/草稿/异常/已取消）
      · status 已受理但**没有运单号** → 失败「已受理但未出运单号」
    """
    if shipped is None:
        return None      # 没核对进没进过 OMS ⇒ 不判（由数量台账兜底）
    if not rec:
        if not shipped:
            return None      # 还没发过 ⇒ 不是失败
        return {"失败环节": "领星 OMS", "状态": "查无此单",
                "失败原因": "领星 OMS 里搜不到该平台单号（可能没导单/店铺对不上）",
                "发货仓": "恒信加州3"}
    accs = rec.get("accounts") or []
    a = accs[0] if accs else {}
    st = a.get("status")
    sname = a.get("statusName") or OMS_STATUS.get(st, "未知")
    track = str(a.get("track") or "").strip()
    out_t = str(a.get("outboundTime") or "").strip()
    # ★ 2026-11-13 修（BD 40 单被判「草稿」、BHY/BEL 83+36 单被判「未知」）：
    #   ① 领星**公开 OpenAPI** 的 `status` 与内部网关不是一套口径 —— 用上传物流面单发出去的单，
    #      公开接口可能仍读 `0=草稿`，但**运单号已经有了**。草稿不可能有运单号 ⇒ 有单号＝已发。
    #   ② `status=None` 表示**公开接口没返回这单**（不是"查了但状态异常"）。没送进 OMS 的是
    #      「还没发」，交数量台账；送过却查不到才是「查无此单」。
    if track:
        return None
    if st is None:
        if not shipped:
            return None
        return {"失败环节": "领星 OMS", "状态": "查无此单", "发货仓": "恒信加州3",
                "失败原因": "本轮送进了 OMS，但公开接口查不到该单（可能还没同步）",
                "处理动作": "稍后重跑 verify 再核；仍查不到就人工确认"}
    if st not in OMS_OK:
        return {"失败环节": "领星 OMS", "状态": sname, "发货仓": "恒信加州3",
                "失败原因": {
                    0: "订单还是草稿（未提交）",
                    1: "仍是「待处理」⇒ 没点「确定并审核」，未发货",
                    4: "已被取消",
                    5: "OMS 标记异常，要看异常原因",
                }.get(st, "OMS 状态异常（%s）" % sname),
                "处理动作": "回 OMS 重新分配仓库&物流 → 确定并审核"}
    if not track:
        return {"失败环节": "领星 OMS", "状态": sname, "发货仓": "恒信加州3",
                "失败原因": "已受理但没有物流单号（面单没生成）",
                "处理动作": "检查面单上传，必要时重新分配物流"}
    if out_t:
        return None  # 已真正出库
    return None  # 仓库处理中 + 有运单号 ⇒ 正常在途，不是失败


def _avail_from_why(why: str, which: str) -> str:
    """从 split 的 `why` 里抠出「恒信可分 N → 分配后余 R」/「帕沃尔可分 N …（加州3仓=a/kansas仓=b）」。

    ★ 2026-11-10：report 两个 sheet 都要标「拆分的恒信仓 / 帕沃尔仓」，而 split.json 里
      只有 `why` 这一句自然语言记着两边的可发数 —— 解析它，而不是另起一份口径。
      解析不出就返回 ""（宁缺勿猜）。
    """
    why = str(why or "")
    m = re.search(r"恒信可分 ([\d.]+) → 分配后余 ([\d.]+)", why)
    hx = "可分 %s，分配后余 %s" % (m.group(1), m.group(2)) if m else ""
    m = re.search(r"帕沃尔可分 (\d+) → 分配后余 (\d+)（([^）]*)）", why)
    pw = ""
    if m:
        pw = "可分 %s，分配后余 %s" % (m.group(1), m.group(2))
        if m.group(3).strip():
            pw += "（%s）" % m.group(3).strip()
    elif "可用已被前面的单分完" in why:
        m2 = re.search(r"本批共需 (\d+)，恒信 ([\d.]+) \+ 帕沃尔 (\d+)", why)
        if m2:
            hx = hx or "本批已被分完（需 %s）" % m2.group(1)
            pw = pw or "本批已被分完（需 %s）" % m2.group(1)
    elif "两边都无此 SKU" in why:
        # 缺货单的 why 是「两边都无此 SKU（恒信 0 / 帕沃尔 0）」——两边都明确为 0
        hx = hx or "0（库存里无此 SKU）"
        pw = pw or "0（库存里无此 SKU）"
    if which == "hx":
        return hx
    return pw


def hx_avail_text(why: str) -> str:
    return _avail_from_why(why, "hx")


def pw_avail_text(why: str) -> str:
    return _avail_from_why(why, "pw")


def split_reason(why: str) -> str:
    """split 的 `why` → 一句人读的「为什么落这个仓」（纯函数）。

    ★ 2026-11-10：report 要给「拆分的恒信仓 / 帕沃尔仓」两列，「拆仓原因」说的是
      为什么落在这里 —— 恒信优先、帕沃尔兜底、还是被前面的单分完 / 根本没这个 SKU。
    """
    w = str(why or "")
    if not w:
        return ""
    # ★ 2026-11-12：整单定档（一单只进一个仓）后，缺货里出现「本行有货但整单装不进同一个仓」，
    #   这类必须以「整单」为原因，不能被下面「恒信可分」那句抢走结论（那会写成"落恒信仓"）。
    if "无法全部落在同一个仓" in w:
        return "整单不能装进同一个仓（一单只有一张面单）→ 整单不自动发，交人工"
    if "恒信可分" in w:
        return "恒信有可用库存 → 按口径优先落恒信仓"
    if "帕沃尔可分" in w:
        return "恒信可用为 0（或已被前面的单分完）→ 兜底落帕沃尔仓"
    if "分完" in w:
        return "两边可用库存已被本批前面的单分完"
    if "变体不存在" in w or _VAR_HINT.search(w):
        return "两边都无库存，且变体名在库存里不存在（疑似上链接命名有误）"
    if "无此 SKU" in w:
        return "两边都无此 SKU（库存里查不到）"
    return w[:80]


def split_side(r: dict, side: dict) -> str:
    """★ 2026-11-10 用户令：「在这一步各表中加上拆分的恒信仓和帕沃尔仓」。

    给每行补「拆仓归属」：优先用单号在 split 里的落档（HX / PW / MISS），
    查不到再退回行里自带的 `拆仓归属`，最后才由调用处给默认值。
    """
    sd = side.get(str(r.get("参考单号") or "")) or str(r.get("拆仓归属") or "")
    return {"HX": "恒信", "PW": "帕沃尔", "MISS": "缺货"}.get(sd, sd)


def build(store: str, split: dict, hwc: list, idx: dict,
          hx_oms: dict | None = None, hx_shipped: bool = False,
          hx_shipped_ords=None, hx_count: dict | None = None) -> dict:
    """组装两个 sheet 的行（纯函数）。

    hx_oms: 恒信那份在领星 OMS 的状态 `{平台单号: {"accounts":[{status,statusName,track,outboundTime}]}}`
            （`scripts/oms/check_po_status.py --json` 的产物）。
            ★ 2026-11-06 补：`None` = **没查**（打印里会明说），`{}` = 查了但单子不在 OMS。
              前者绝不等于"恒信全成功"。
    hx_shipped: 旧口径的**全体**开关（`hx_ship` 跑没跑过）。保留只为兼容老调用，
                `hx_shipped_ords` 给了就以按单号的判定为准。
    hx_shipped_ords: **这次真的被送进 OMS 的单号集合**（`hx_ship` 的产物）。
    hx_count: 领星数量核对台账（`hx_count_check.json`）。
              用户 2026-11-07 口径：「去领星发货数量对不上直接归类到失败订单」。
    """
    miss_rows = ((split.get("groups") or {}).get("MISS")) or []
    real_miss, already = classify_miss(miss_rows, idx)

    # ★ 2026-11-10：单号 → 拆仓归属（HX/PW/MISS），两个 sheet 都要用它标「恒信 / 帕沃尔」。
    #   必须在 short 之前建好（缺货 sheet 也要标）。
    side = {r.get("ord"): "缺货" for r in miss_rows}
    for sd in ("PW", "HX"):
        for r in ((split.get("groups") or {}).get(sd) or []):
            side[r.get("ord")] = "帕沃尔" if sd == "PW" else "恒信"

    short = []
    for r in real_miss:
        why, hint = miss_reason(r)
        short.append({
            "参考单号": r.get("ord") or "",
            "平台SKU": r.get("sku") or "",
            "件数": int(r.get("qty") or 1),
            "拆仓归属": split_side({"参考单号": r.get("ord")}, side) or "缺货",
            "状态": "真缺货（不可发）",
            "原因": why,
            "库存里同款同码有的变体": hint,
            "处理动作": ("核对上链接命名（改回 5SetA/5SetB 等真实变体）" if hint
                         else "补货 / 从别处调货，本批不发"),
        })
    for r in already:
        short.append({
            "参考单号": r.get("ord") or "",
            "平台SKU": r.get("sku") or "",
            "件数": int(r.get("qty") or 1),
            "拆仓归属": split_side({"参考单号": r.get("ord")}, side) or "缺货",
            "状态": "已实际下单（有运单号）",
            "原因": "帕沃尔已出单，运单号 %s，不属于缺货" % r.get("waybill"),
            "库存里同款同码有的变体": "",
            "处理动作": "回 TEMU「待发货」勾选确认发货即可",
        })

    fails = []
    for x in hwc or []:
        st = str(x.get("status") or "")
        po = str(x.get("po") or "")
        if not po or st not in ("下单失败", "未下单"):
            continue
        sd = side.get(po, "")
        # ★ 2026-11-06 过滤（会让失败 sheet 混进根本不是失败的单）：
        #   ① 恒信那份在帕沃尔页面上必然显示「未下单」（它走领星 OMS）—— 这里跳过，
        #      **但不是不查它**：恒信改成查 OMS 终态（见下面 `hx_oms` 那段）；
        #   ② 缺货单本来就没派仓库，「未下单」是预期——也不算失败。
        if sd == "恒信":
            continue
        if sd == "缺货" and st == "未下单":
            continue
        step, why = fail_reason(st, str(x.get("err") or ""))
        fails.append({
            "参考单号": po,
            "平台SKU": x.get("sku") or "",
            "发货仓": x.get("wh") or "",
            "拆仓归属": sd,
            "状态": st,
            "失败环节": step,
            "失败原因": why,
            "处理动作": "改仓/改 Fnsku 后重新下单；换不了的挂起人工",
        })

    # ★ 2026-11-06 用户口径：「汇总**两个仓**的失败订单」。
    #   帕沃尔这边还有一类失败前面漏掉了：**帕沃尔侧显示「已下单」但没有运单号**
    #   （= 出库单成了、面单/取号没成，货发不出去）。它既不是"下单失败"也不是"缺货"，
    #   不看运单号就永远查不出来。判据用 deliver 索引里的 `wb`。
    already_failed = {r["参考单号"] for r in fails}   # 防同一单进表两次
    hit_po = {str(x.get("po") or "") for x in (hwc or [])}
    stat_by_po = {str(x.get("po") or ""): str(x.get("status") or "") for x in (hwc or [])}
    for r in ((split.get("groups") or {}).get("PW") or []):
        po = str(r.get("ord") or "")
        if not po or po in already_failed:
            continue
        rec = (idx or {}).get(po) or {}
        wb = (rec.get("wb") or "").strip()
        if wb:
            continue                      # 有运单号 ⇒ 正常，不是失败
        if not rec:
            # ★ 2026-11-06 真机：帕沃尔**已下单**，但 deliver 索引里根本没有该单
            #   （索引过期，或出库单真没生成）。这时「有没有运单号」**没法判** ——
            #   不许当它正常放过，也不许直接说"没运单号"，如实标「未核对」。
            fails.append({
                "参考单号": po, "平台SKU": r.get("sku") or "",
                "发货仓": r.get("wh") or "", "拆仓归属": "帕沃尔",
                "状态": "已下单（deliver 无记录）",
                "失败环节": "帕沃尔取号",
                "失败原因": "帕沃尔已下单，但 deliver.php 里查不到该单"
                            "（索引过期或出库单没生成）⇒ 运单号无法核对",
                "处理动作": "重跑 pw_index_deliver.py 再核；仍查不到就重新下单/挂人工",
            })
        elif str(rec.get("status") or "") not in ("", "待打包", "待出库"):
            # 已出库/已发货 ⇒ 运单号在别的环节，不算失败
            continue
        else:
            fails.append({
                "参考单号": po, "平台SKU": r.get("sku") or "",
                "发货仓": r.get("wh") or "", "拆仓归属": "帕沃尔", "状态": "已下单无运单号",
                "失败环节": "帕沃尔取号",
                "失败原因": "出库单已建但没有运单号（面单/取号没成功，货发不出去）",
                "处理动作": "到 deliver.php pb=2 批量获取单号；取不到就看面单",
            })

    # ★ 2026-11-06 补（用户追问「恒信的失败订单你有一起汇总到表吗」）：
    #   恒信那份的失败**只能看领星 OMS**，帕沃尔页面对它们必然显示「未下单」，
    #   所以之前那句 `if sd == "恒信": continue` 会把恒信真失败静默吞掉。
    #   ⚠ `hx_oms is None` = **根本没查**（不是"查了没有"）：这时一行都不写，
    #     由调用方的 `_hx_checked` 明说未核对；传 `{}` 才是"查了、单子不在 OMS 里"。
    hx_rows = ((split.get("groups") or {}).get("HX")) or []
    unshipped = set()          # 本次没被送进 OMS 的单号（交给数量台账报）
    if hx_oms is not None:
        # ★ 2026-11-07：按**单号**判「这一单进过 OMS 没有」，不再用整步开关。
        shipped_set = None if hx_shipped_ords is None else {
            str(x).strip() for x in hx_shipped_ords if str(x).strip()}
        for r in hx_rows:
            po = str(r.get("ord") or "")
            if not po:
                continue
            # ★ 2026-11-07 这一分支才是"按单号判"的落地：
            #   给了清单就以清单为准（_MANIFEST_SET），否则退回旧的整步开关。
            #   没进清单的单**不是**「查无此单」，是「本次没送进 OMS」——
            #   它由下面的数量台账（hx_count）负责报，不许在这里先报成一次。
            if shipped_set is None:
                shp = bool(hx_shipped)
            else:
                if po not in shipped_set:
                    unshipped.add(po)
                    continue
                shp = True
            bad = hx_fail((hx_oms or {}).get(po) or {}, po, shipped=shp)
            if not bad:
                continue
            fails.append({
                "参考单号": po,
                "平台SKU": r.get("sku") or "",
                "发货仓": bad.get("发货仓") or "恒信加州3",
                "拆仓归属": "恒信",
                "状态": bad.get("状态") or "",
                "失败环节": bad.get("失败环节") or "领星 OMS",
                "失败原因": bad.get("失败原因") or "",
                "处理动作": bad.get("处理动作") or "回 OMS 重新分配仓库&物流 → 确定并审核",
            })

    # ★ 2026-11-07 用户口径：「去领星发货数量对不上，直接归类到失败订单」。
    #   这一步**不看单号层面能不能对上**，只看**数量台账**：
    #     拆给恒信 N / 送进 OMS M / OMS 里查到 K —— N≠M 或 M≠K 就是「对不上」。
    #   差额单号（拆了但没进 OMS、或进了 OMS 却查不到）逐单落进失败 sheet；
    #   已经因为状态被判过失败的单不重复写。数量对得上就什么都不加。
    if hx_count or unshipped:
        done = {r["参考单号"] for r in fails if r["拆仓归属"] == "恒信"}
        sku_of = {str(r.get("ord") or ""): (r.get("sku") or "",
                                            r.get("wh") or "") for r in hx_rows}
        cnt = hx_count or {}
        total_n = cnt.get("split_hx") or len(hx_rows)
        # ① 进了 OMS 却查不到
        for po in (cnt.get("missing_in_oms") or []):
            po = str(po)
            if not po or po in done:
                continue
            sku, wh = sku_of.get(po, ("", ""))
            fails.append({
                "参考单号": po, "平台SKU": sku, "发货仓": wh or "恒信加州3",
                "拆仓归属": "恒信", "状态": "OMS 查无此单",
                "失败环节": "领星数量核对",
                "失败原因": "拆给恒信 %s 单，但这单在领星 OMS 里查不到（发货数量对不上）"
                            % total_n,
                "处理动作": "回领星 OMS 确认是否导入/审核 → 重新分配仓库&物流",
            })
            done.add(po)
        # ② 没被送进 OMS 的（清单直接给，或由台账 sent_to_oms 反推）
        # ★ 2026-11-07：只给了台账、`sent_to_oms` 又是空 = **本次没跑 hx_ship**
        #   （比如只重出表）——这时不许把整批报成「未送进 OMS」。
        if cnt.get("sent_to_oms") is not None and (
                cnt["sent_to_oms"] or cnt.get("checked_send")):
            sent = {str(x) for x in (cnt.get("sent_to_oms") or [])}
            unsent = {str(r.get("ord") or "") for r in hx_rows
                      if str(r.get("ord") or "") and str(r.get("ord")) not in sent}
        else:
            unsent = set(unshipped)
            sent = {str(r.get("ord") or "") for r in hx_rows} - unsent
        for po in sorted(unsent):
            if not po or po in done:
                continue
            sku, wh = sku_of.get(po, ("", ""))
            fails.append({
                "参考单号": po, "平台SKU": sku, "发货仓": wh or "恒信加州3",
                "拆仓归属": "恒信", "状态": "未送进 OMS",
                "失败环节": "领星发货",
                "失败原因": "本次 oms_ship 没有把这单送进领星 OMS（发货数量对不上："
                            "拆仓 %s 单 / 实际送出 %s 单）" % (total_n, len(sent)),
                "处理动作": "重跑 hx_ship 补发这单，或手动在 OMS 分配仓库&物流",
            })
            done.add(po)

    # 排序：真缺货排前面（要人工补货），「其实已下单」压到最后（只需回 TEMU 确认）。
    short.sort(key=lambda r: (0 if r["状态"].startswith("真缺货") else 1,
                              r["平台SKU"], r["参考单号"]))
    fails.sort(key=lambda r: (r["拆仓归属"] != "恒信", r["平台SKU"], r["参考单号"]))

    # ★ 2026-11-10：两个 sheet 都补「拆仓归属 / 恒信仓 / 帕沃尔仓」。
    #   可发件数取 split 每行的 `why`（那里记着「恒信可分 N / 帕沃尔可分 M」），
    #   按单号回查，避免行里没带 wh 时留空。
    why_of = {}
    for sd in ("PW", "HX", "MISS"):
        for r in ((split.get("groups") or {}).get(sd) or []):
            why_of[str(r.get("ord") or "")] = str(r.get("why") or "")
    for r in short + fails:
        po = str(r.get("参考单号") or "")
        w = why_of.get(po, "")
        r["拆仓归属"] = split_side(r, side) or r.get("拆仓归属") or ""
        r["恒信仓"] = hx_avail_text(w)
        r["帕沃尔仓"] = pw_avail_text(w)
        r["拆仓原因"] = split_reason(w)
        if not r["拆仓归属"]:
            r["拆仓归属"] = "恒信" if r["恒信仓"] else ("帕沃尔" if r["帕沃尔仓"] else "")
    return {"缺货": short, "失败订单": fails,
            "_hx_checked": hx_oms is not None, "_hx_total": len(hx_rows),
            "_hx_count_ok": (None if not hx_count else bool(hx_count.get("ok")))}


#: sheet 名后缀 → 归属（★ 2026-11-10 用户口径：每个店一张表，sheet 名带店名前缀：
#:   `<店> 恒信仓` / `<店> 帕沃尔仓` / `<店> 缺货` / `失败订单`）
#: ★ 2026-11-12 加 D 组：`<店> 预售`（只有 BHY / BEL 会有行，见 bel_split_by_stock.is_presale）
SHEET_SUFFIX = {"恒信仓": "HX", "帕沃尔仓": "PW", "缺货": "MISS", "预售": "PRESALE"}

#: 只有这两家店的表才出「预售」sheet + 帕沃尔仓的「仓库」列（用户 2026-11-12 口径）
STORE_PRESALE = ("BHY", "BEL")


def _pct_cols():
    """「占拆分比」列需要的两个分母（本店送进帕沃尔/恒信的总件数）。"""
    return {"帕沃尔仓": {"qty": 0, "orders": 0}, "恒信仓": {"qty": 0, "orders": 0}}


def group_sheets(rows: list, split: dict, store: str) -> dict:
    """★ 2026-11-10 用户令（贴图定稿）：每店一张表 → sheet 名 = `<店> 恒信仓` / `<店> 帕沃尔仓`
    / `<店> 缺货` / `失败订单`。

    三个仓 sheet 直接来自 split 的三组（HX / PW / MISS），每单一行；
    末尾再加一行「合计」，并按「占拆分比」给出该单件数在本店该仓总量里的占比
    —— 这是用户表头上要看的两个数（`件数` + `占拆分比`）。

    行结构：`参考单号 / 平台SKU / 规格 / 渠道SKU / 件数 / 占拆分比 / 另一仓可用 / 拆仓原因 / 可发总数`
    """
    g = {k: list(v or []) for k, v in (split.get("groups") or {}).items()}
    # ★ 2026-11-12 用户令：「把恒信仓、帕沃尔仓、缺货三个 sheet 去重，
    #   不能一个订单重复出现多个 sheet」。split 那边已整单定档 + split_dedupe 闸门，
    #   这里再兜一道：同一参考单号只留在**最保守**的那一组（缺货 > 恒信 > 帕沃尔）。
    #   为什么还要兜：report 也可能读到昨天的 session split.json；宁可在表上少一行，
    #   也不能让同一单在两个仓 sheet 里各出现一次（人工会照表重复买面单/重复下单）。
    _kept_ord = {}
    for _sd in ("PRESALE", "MISS", "HX", "PW"):
        _keep = []
        for _r in (g.get(_sd) or []):
            _o = str(_r.get("ord") or "").strip()
            if _o and _o in _kept_ord and _kept_ord[_o] != _sd:
                continue          # 该单已在更保守的组里 ⇒ 本组丢掉（同组多行是正常的）
            if _o:
                _kept_ord.setdefault(_o, _sd)
            _keep.append(_r)
        g[_sd] = _keep
    why_by_side = {s: {str(r.get("ord") or ""): str(r.get("why") or "") for r in (g.get(s) or [])}
                   for s in ("HX", "PW", "MISS", "PRESALE")}
    # 规格的补充来源：split 行里没有 `spec`（那是导出表的列），
    # 从缺货/失败行里按平台单号回查，能补上就不留空。
    spec_of = {}
    for r in (rows or []):
        po = str(r.get("参考单号") or "")
        if po and r.get("规格"):
            spec_of.setdefault(po, r.get("规格") or "")
    out = {}
    for suffix, sd in SHEET_SUFFIX.items():
        if sd == "PRESALE" and str(store or "").upper() not in STORE_PRESALE:
            continue          # 只有 BHY / BEL 有 D 组（预售）
        src = g.get(sd) or []
        tot_qty = sum(int(r.get("qty") or 1) for r in src)
        tot_ord = len([r for r in src if str(r.get("ord") or "")])
        rows_out = []
        for r in src:
            po = str(r.get("ord") or "")
            w = why_by_side[sd].get(po, "")
            q = int(r.get("qty") or 1)
            other = pw_avail_text(w) if sd == "HX" else hx_avail_text(w)
            rows_out.append({
                "参考单号": po,
                "平台SKU": r.get("sku") or "",
                "规格": r.get("spec") or spec_of.get(po) or "",
                "渠道SKU": r.get("key") or "",
                "件数": q,
                "占拆分比": ("%.1f%%" % (q * 100.0 / tot_qty)) if tot_qty else "",
                "另一仓": other or "0（无可用）" if sd != "MISS" else "",
                "拆仓原因": split_reason(w),
                "本仓可发货数": (hx_avail_text(w) if sd == "HX"
                                 else pw_avail_text(w) if sd == "PW" else ""),
                # ★ 2026-11-12 用户令：「BHY 和 BEL 的表格中帕沃尔仓加一列仓库列」
                #   —— 帕沃尔两个发货仓（加州3仓 / kansas仓）肉眼必须能分辨，按 split 行里的
                #   `wh` 落列（那是 `_take_pw` 按实际有货的仓写的，不是猜的）。
                "仓库": (r.get("wh") or "") if sd == "PW" else "",
            })
        rows_out.sort(key=lambda x: (x["平台SKU"], x["参考单号"]))
        out["%s %s" % (store, suffix)] = {
            "rows": rows_out,
            "total": {"参考单号": "合计", "平台SKU": "%d 单" % tot_ord, "件数": tot_qty,
                      "占拆分比": "100.0%" if tot_qty else ""},
            "side": sd,
        }
    return out


def write_xlsx(store: str, sheets: dict, out: str) -> str:
    """落 xlsx（每店一张表：`<店> 恒信仓` / `<店> 帕沃尔仓` / `<店> 缺货` / `失败订单`）。

    ★ 2026-11-06 真机：目标文件**正被 Excel 打开**时 openpyxl 抛
      `PermissionError: [Errno 13]`，整条 pipeline 的 report 步就挂了。
      这不是逻辑错误 ⇒ 换名另存（`_v2`、`_v3`…）并**返回实际路径**，让人能看到结果。
    """
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415
    from openpyxl.utils import get_column_letter  # noqa: PLC0415

    # ★ 2026-11-10 用户令：「在这一步各表中加上拆分的恒信仓和帕沃尔仓」。
    #   「拆仓归属」＝这一单该发给哪个仓（恒信 / 帕沃尔 / 缺货）；
    #   「恒信仓」「帕沃尔仓」两列 = 两组各自的可发件数（split 里记的 `why` 原话），
    #   便于人工一眼看出「为什么没落在另一个仓」。
    COLS = {
        "缺货": ["参考单号", "平台SKU", "件数", "拆仓归属", "拆仓原因", "恒信仓", "帕沃尔仓",
                 "状态", "原因", "库存里同款同码有的变体", "处理动作"],
        "失败订单": ["参考单号", "平台SKU", "发货仓", "拆仓归属", "拆仓原因", "恒信仓", "帕沃尔仓",
                     "状态", "失败环节", "失败原因", "处理动作"],
    }
    WIDTH = {
        "缺货": [28, 26, 7, 11, 40, 26, 26, 20, 44, 34, 44],
        "失败订单": [28, 26, 12, 11, 40, 26, 26, 11, 20, 44, 44],
    }
    # ★ 2026-11-10 用户令（贴图定稿）：「BEL 店铺这一步输出的表格里面含有多个 sheet：
    #   BEL 恒信仓、BEL 帕沃尔仓、BEL 缺货、失败订单」⇒ sheet 名带店名前缀，顺序也照这个来。
    WH_COLS = ["参考单号", "平台SKU", "规格", "渠道SKU", "件数", "占拆分比",
               "另一仓", "本仓可发货数", "拆仓原因"]
    WH_WIDTH = [28, 26, 30, 26, 7, 11, 26, 26, 46]
    WH_FILL = {"HX": "DDEBF7", "PW": "E2EFDA", "MISS": "FCE4E4", "PRESALE": "FFF2CC"}

    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    hdr_font = Font(color="FFFFFF", bold=True, size=11)

    wb = Workbook()
    wb.remove(wb.active)

    order = ["%s 恒信仓" % store, "%s 帕沃尔仓" % store, "%s 缺货" % store]
    if str(store or "").upper() in STORE_PRESALE:
        order.append("%s 预售" % store)      # D 组（2026-11-12 用户令）
    order.append("失败订单")
    for name in order:
        ws = wb.create_sheet(name)
        widths = None
        if name in ("缺货", "失败订单"):
            cols, rows = COLS[name], (sheets.get(name) or [])
            total_row = None
        else:
            blk = sheets.get(name) or {}
            rows, total_row = (blk.get("rows") or []), blk.get("total")
            cols, widths = list(WH_COLS), list(WH_WIDTH)
            # ★ 2026-11-12 用户令：BHY / BEL 的帕沃尔仓 sheet 要能看出是哪个仓发的
            if blk.get("side") == "PW" and str(store or "").upper() in STORE_PRESALE:
                cols.append("仓库")
                widths.append(14)
        ws.append(cols)
        for c in range(1, len(cols) + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(c)].width = (
                (WIDTH[name] if name in WIDTH else widths)[c - 1])
        for r in rows:
            ws.append([r.get(c) for c in cols])
        if total_row is not None:
            ws.append([total_row.get(c) for c in cols])
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(cols)),
                                          max(1, len(rows) + 1))
        for row in ws.iter_rows(min_row=2, max_row=len(rows) + 1):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[1].height = 22
        if name in sheets and isinstance(sheets.get(name), dict) and sheets[name].get("side"):
            # 仓 sheet 整片浅色底，一眼分清恒信蓝 / 帕沃尔绿 / 缺货红
            f = PatternFill("solid", fgColor=WH_FILL.get(sheets[name]["side"], "FFFFFF"))
            for row in ws.iter_rows(min_row=2, max_row=max(2, ws.max_row)):
                row[0].fill = f

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    real = out
    for i in range(1, 6):
        try:
            wb.save(real)
            return real
        except PermissionError:
            stem, ext = os.path.splitext(out)
            real = "%s_v%d%s" % (stem, i + 1, ext)
            print("[WARN] %s 被占用（Excel 开着？）→ 改存 %s"
                  % (os.path.basename(out), os.path.basename(real)))
    raise RuntimeError("xlsx 全部被占用，存不进去：%s" % out)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="某店 缺货 / 失败订单 → 两 sheet Excel")
    ap.add_argument("--store", required=True, help="店（BEL / BHY）")
    ap.add_argument("--split", required=True, help="拆仓结果 split.json")
    ap.add_argument("--hwc", default="", help="帕沃尔 hwc_shop 当前状态 json（下单失败/未下单）")
    ap.add_argument("--index", default="", help="帕沃尔 deliver 索引（判该单是否已有运单号）")
    ap.add_argument("--hx-oms", default="",
                    help="恒信那份的领星 OMS 状态 json（check_po_status.py --json 产物）。"
                         "★ 不给就查不了恒信失败单，脚本会明确报「恒信未核对」")
    ap.add_argument("--hx-shipped", action="store_true",
                    help="hx_ship 已跑过（**全体**开关，老口径）。给了 --hx-shipped-file 时以它为准")
    ap.add_argument("--hx-shipped-file", default="",
                    help="本次真的送进 OMS 的单号 txt（一行一个，hx_ship 的产物）")
    ap.add_argument("--hx-count-check", default="",
                    help="领星数量核对 json（拆给恒信 N / 送进 OMS M / OMS 查到 K）。"
                         "★ 用户 2026-11-07：数量对不上直接归类到失败订单")
    ap.add_argument("--out", default="", help="输出 xlsx")
    a = ap.parse_args()

    split = _load(a.split, {})
    if not split:
        print("[FAIL] 读不到拆仓结果：%s" % a.split, file=sys.stderr)
        return 42
    hwc = _load(a.hwc, []) if a.hwc else []
    idx = _load(a.index, {}) if a.index else {}

    hx_oms = None
    if a.hx_oms:
        d = _load(a.hx_oms, {})
        # check_po_status.py 产出 {"start","end","accounts","rows":[{"po","accounts":[…]}]}
        hx_oms = {}
        for r in (d.get("rows") or []) if isinstance(d, dict) else []:
            po = str(r.get("po") or "")
            if po:
                hx_oms[po] = {"accounts": r.get("accounts") or []}

    shipped_ords = None
    if a.hx_shipped_file:
        try:
            shipped_ords = [x.strip() for x in
                            open(a.hx_shipped_file, encoding="utf-8") if x.strip()]
        except Exception:  # noqa: BLE001
            print("[WARN] 读不到 %s，退回 --hx-shipped 口径" % a.hx_shipped_file)
    hx_count = _load(a.hx_count_check, {}) if a.hx_count_check else None

    sheets = build(a.store, split, hwc, idx, hx_oms=hx_oms,
                   hx_shipped=a.hx_shipped,
                   hx_shipped_ords=shipped_ords, hx_count=hx_count)
    # ★ 2026-11-10：三个仓 sheet（<店> 恒信仓 / <店> 帕沃尔仓 / <店> 缺货）+ 失败订单
    sheets.update(group_sheets(sheets.get("失败订单") or [], split, a.store))
    out = a.out or os.path.join(_HERE, "out", time.strftime("%Y-%m-%d"),
                                "%s_缺货与失败订单_%s.xlsx"
                                % (a.store, time.strftime("%Y%m%d")))
    real = write_xlsx(a.store, sheets, out)
    if os.path.abspath(real) != os.path.abspath(out):
        print("[WARN] 目标文件被占用，实际写在：%s" % real)

    print("[%s] 恒信仓 %d / 帕沃尔仓 %d / 缺货 %d / 预售 %d / 失败订单 %d 单 → %s"
          % (a.store,
             len((sheets.get("%s 恒信仓" % a.store) or {}).get("rows") or []),
             len((sheets.get("%s 帕沃尔仓" % a.store) or {}).get("rows") or []),
             len(sheets["缺货"]),
             len(((sheets.get("%s 预售" % a.store) or {}).get("rows") or [])),
             len(sheets["失败订单"]), real))
    bad = [r for r in sheets["缺货"] if r["状态"].startswith("真缺货")]
    hint = [r for r in bad if r["库存里同款同码有的变体"]]
    print("   其中真缺货 %d（变体命名可疑 %d），已实际下单 %d"
          % (len(bad), len(hint), len(sheets["缺货"]) - len(bad)))
    # ★ 恒信必须明说核对过没有 —— 不给 --hx-oms 就等于没查，"没报失败"不代表没失败。
    if sheets.get("_hx_checked"):
        hx_fail_n = len([r for r in sheets["失败订单"] if r["拆仓归属"] == "恒信"])
        print("   恒信 %d 单已查领星 OMS：失败 %d 单"
              % (sheets.get("_hx_total") or 0, hx_fail_n))
        if sheets.get("_hx_count_ok") is False:
            print("   ⚠ 领星**发货数量对不上** —— 差额单已归到失败订单 sheet")
        elif sheets.get("_hx_count_ok") is True:
            print("   领星数量核对：拆仓 / 送进 OMS / OMS 查到 三者一致")
    else:
        print("   ⚠ **恒信那份没核对**（缺 --hx-oms）——表里看不到恒信失败单，别当它没有")
    for r in sheets["失败订单"]:
        print("   失败 [%s] %-26s %-24s %-10s %s"
              % (r["拆仓归属"] or "-", r["参考单号"], r["平台SKU"],
                 r["发货仓"] or "-", (r["失败原因"] or "")[:50]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
