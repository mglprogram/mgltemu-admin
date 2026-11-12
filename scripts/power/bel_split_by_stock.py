# -*- coding: utf-8 -*-
"""bel_split_by_stock.py — 批次按「恒信优先、帕沃尔兜底」拆成两个平台发货 + 缺货待定。

用户口径演进（**以后者为准**）：
    2026-11-05 原话：「根据不带店铺前缀的 sku 去库存跟踪查，优先看这个尺码可用库存》
                     帕沃尔库存， 然后把订单分成两份，因为要去两个平台发货」
    2026-11-06 改口径：「导出文件根据 sku 优先恒信可用库存，其次再是帕沃尔仓库存」
    ⇒ 优先级**反转**：恒信在前、帕沃尔在后。用 `prefer` 参数控制，默认 "HX"。

判据（默认 prefer="HX"，按此顺序落档，任一不满足才往下走）：
    ① **恒信 OMS 可用 > 0** → B 份：恒信 ZOO ⑥美西发
    ② 恒信没有 → **帕沃尔可用 > 0** → A 份：帕沃尔发（仓：加州3仓 / kansas仓）
    ③ 都没有 → C 份「缺货待定」（**不许当有货发出去**）

    `prefer="PW"` 时恢复 11-05 的顺序（帕沃尔在前），留作对照/回滚。

三条硬约束（都踩过 / 都会造成超卖或错配）：
    1. **匹配用「整串去品牌前缀」**（BH/BK/BS/BS2/BLA/BEL），
       不能用库存 JSON 里的 `code`/`variant`/`size` —— 那三个字段是**有损拆解**：
       `102104A`→`102104`（丢尾字母）、`6SetA1.1`→`6SetA`（丢版本点）。
       按三字段匹配会漏 6 个 SKU，按整串只漏真没货的。
    2. **同款多单要算总需求**：`204203-4SetC-L` 5 单但帕沃尔只有 2 件
       ⇒ 只能放 2 单进帕沃尔、其余进缺货，否则必然超卖。
    3. **帕沃尔只认发货仓**（加州3仓 / kansas仓）；Temu自动分仓那类不计入可用。
    4. **整单定档**（★ 2026-11-12 用户令：「不能一个订单重复出现多个 sheet」）：
       一单一张面单、一个仓 ⇒ 整单所有行都够才落该仓，装不下就整单进缺货。
       按行分会让同一单同时出现在恒信仓/帕沃尔仓两个 sheet ⇒ 重复买面单、重复发货。

用法：
    python scripts/power/bel_split_by_stock.py --file "<导出订单.xlsx>"
    python scripts/power/bel_split_by_stock.py --file ... --out out/split.json --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_WS = os.path.dirname(os.path.dirname(_HERE))

#: 库存快照（看板产出，与 temu-goods-onsale / temu-bottom-stock-sync 同源）
PW_JSON = os.path.join(_WS, "temu-admin", "public", "data", "pw-stock.json")
LX_JSON = os.path.join(_WS, "temu-admin", "public", "data", "linxing-stock.json")

#: 可发货的帕沃尔仓（Temu自动分仓/其它虚拟仓不算可用）
PW_SHIP_WH = ("加州3仓", "kansas仓")

#: 恒信那份的发货仓（领星 OMS 里的仓名）
HX_WAREHOUSE = "ZOO WAREHOUSE ⑥美西"

#: 组名（★ 2026-11-12 新增 D=预售，用户令：「BHY 和 BEL 的 YOGA-4SetA 所有尺码开了预售，
#:   加一个放预售订单的 sheet-D」；次日口径：「从明天出现的预售款收录到预售 sheet 中」，
#:   随后又定了「203311-4SetA 删除收录」）。
#: `PRESALE` 收**整单全是预售款**的单（不再走 A/B，也不进缺货）——预售＝等补货，本批不发。
GROUPS = ("PRESALE", "PW", "HX", "MISS")

#: 预售款（去品牌前缀后的 `款号-套数`，不含尺码；大小写按 `norm_sku` 的大写口径）
#: ★ 2026-11-12 用户二次口径：「203311-4SetA 删除收录」⇒ 只剩 YOGA-4SetA。
PRESALE_KEYS = ("YOGA-4SETA",)

#: 只有这两家店开过预售（BHY / BEL 是 TEMU 半托管店，其余店不许落 D 组）
PRESALE_STORES = ("BHY", "BEL")


def is_presale(key: str, store: str = "") -> bool:
    """这个 SKU 是不是「已开预售的款」的全部尺码（纯函数）。

    判据：去掉品牌/店铺前缀后，`款号-套数` 命中 `PRESALE_KEYS`（尺码不限）。
    店不在 `PRESALE_STORES` 里一律 False —— 宁可不进 D 组，也不许把别的店单子标成预售。
    """
    if str(store or "").strip().upper() not in PRESALE_STORES:
        return False
    k = brand_tail(norm_sku(key))
    return any(k == p or k.startswith(p + "-") for p in PRESALE_KEYS)

#: 跨系统对码要去掉的品牌前缀（同一款可能挂 BH/BK/BS/BS2/BLA，平台侧是 BEL）
#: ★ 2026-11-06 修（BHY 店 438 单全被判缺货）：白名单里少了**店铺自己的前缀** `BHY`/`BEL` 之外的
#:   `BHY-` 当店侧 SKU 出现时（如 `BHY-203311-4SetA-M`），库存里叫 `BS2-203311-4SetA-M`；
#:   前缀没去掉 ⇒ 键变成 `BHY-203311-4SETA-M`，两边都查不到 ⇒ 全判缺货。
#:   现把两家店的前缀都收进来（BHY/BEL 是店，BH/BK/BS/BS2/BLA 是仓侧品牌）。
#: ★ 2026-11-11 修（BD 店 114 行全被判缺货）：SHEIN 侧导出的 SKU 带**店名前缀**
#:   `BD-202504-5SetE-S` / `BAN-204605-3BHP-M`，库存里叫 `BS-202504-5SetE-S` /
#:   `BK-...`；白名单没有 BD/BAN ⇒ 键带上店名 ⇒ 两边都查不到 ⇒ 整店判缺货（BD 実测 66/114
#:   行其实有货）。把这两家店的店名前缀也收进来。
_BRAND = re.compile(r"^(BH|BK|BS2|BS|BLA|BEL|BHY|BD|BAN)-", re.I)

#: 变体名**末尾的纯数字段（可含点）**。
#: ★ 2026-11-06 用户口径：「有些款号是 104102-1B1、1091-1B1，这个变体带数字 1、2、1.1，
#:   你去查库存的时候可以把数字去掉查库存，这是我上链接的命名问题」
#:   即平台上架时给变体加了 1 / 2 / 1.1 后缀，库存里还是 `1B` / `6SetA`。
#:   规则只削**末尾**数字，`1B`、`6SetA`、`10SetA` 里的数字不动。
_TAIL_DIGITS = re.compile(r"[\d.]+$")


def norm_sku(s: str) -> str:
    """去店铺/品牌前缀并大写 —— 跨系统唯一可信的对码键（纯函数）。"""
    return _BRAND.sub("", str(s or "").strip().upper())


def relax_sku(key: str) -> str:
    """把变体末尾的纯数字段去掉（纯函数）：`104102-1B2-L` → `104102-1B-L`。

    只对 `CODE-VARIANT-SIZE` 三段式生效；削不干净（削完变空）就原样返回，绝不猜。
    """
    parts = str(key or "").split("-")
    if len(parts) != 3:
        return str(key or "")
    code, var, size = parts
    v2 = _TAIL_DIGITS.sub("", var)
    return "%s-%s-%s" % (code, v2 or var, size)


#: 品牌前缀的形状：字母开头、总长 ≤5、可带数字（BH / BK / BS / BS2 / BLA / BEL / BHY / BD / BAN）。
#: 排除 `5SetA`（数字开头）、`H103902`（6 位，是**款号**不是品牌）。
_SEG_BRAND = re.compile(r"^[A-Z][A-Z0-9]{0,4}$")


def brand_tail(key: str) -> str:
    """**去掉品牌/店铺前缀**，只留 `款号-套数(变体)-尺码`（纯函数）。

    ★ 2026-11-11 用户口径：「品牌前缀和库存前缀都忽略，主要后面的款号-套数(变体)-尺码
      对上就可以了」。订单侧是店名前缀（`BD-202504-5SetE-S`），库存侧是品牌前缀
      （`BS-` / `BK-` / `BH-` / `BS2-` / `BLA-`），两边前缀**从来不相等** ⇒ 按整串对
      必然全判缺货（BD 店 114 行就是这么全灭的）。
    实现：先剔空段（库存里存在 `-BH-H103903-2B-XL` 这种前导横线），
    首段是品牌形状时丢掉它，其余原样（**不改款号/套数/尺码**）。
    """
    parts = [p for p in str(key or "").upper().split("-") if p]
    if len(parts) >= 4 and _SEG_BRAND.match(parts[0]):
        return "-".join(parts[-3:])
    return "-".join(parts)


def resolve(idx: dict, key: str):
    """在索引里找 `key`（纯函数）→ `(实际命中的键, 命中方式)`；找不到返回 `(key, "")`。

    命中方式：`""`=整串精确；`"prefix"`=去品牌前缀后命中；`"digits"`=再去变体末尾数字。
    查找顺序**精确优先**，退化只在精确查不到时用：
      ① 整串精确键（保留原来的行为，BH/BK/BS 各算各的）
      ② 去品牌前缀后的 `款号-套数-尺码`
      ③ ②再去变体末尾数字（`104102-1B2` → `104102-1B`）
    """
    if idx.get(key):
        return key, ""
    t = brand_tail(key)
    if t and t != key and idx.get(t):
        return t, "prefix"
    for cand in (t or key, key):
        rk = relax_sku(cand)
        if rk and rk != cand and idx.get(rk):
            return rk, "digits"
    return key, ""


def build_index(items: list, fields: tuple, wh_filter: tuple = (),
                relaxed: bool = False) -> dict:
    """按「去前缀整串」建索引（纯函数）。`wh_filter` 非空时只收这些仓。

    `relaxed=True` 时**额外**按「变体末尾去数字」再登记一份（用户 2026-11-06 口径：
    平台上架给变体加了 1/2/1.1 后缀，库存里没有）。精确键优先级更高，查的时候先查精确。
    """
    idx: dict = {}
    for it in items or []:
        if wh_filter and str(it.get("wh") or "") not in wh_filter:
            continue
        seen_keys = set()
        for f in fields:
            v = it.get(f)
            if not v:
                continue
            k = norm_sku(v)
            # ★ 2026-11-05 修（真机被用户抓到）：同一行 `sku` 与 `name` 往往是**同一个字符串**，
            #   两字段都收 ⇒ 同一行进列表两次 ⇒ 库存**算成双倍**。
            #   实测 `203304-4SetA-S` 帕沃尔只有 4 件，却报 8 件（BS 1 + BH 3，各自 ×2），
            #   于是这单明明「只在 kansas 才有货」却被判「加州3仓有货」。
            #   同一行内只登记一次。
            keys = [k]
            # ★ 2026-11-11：同时登记「去品牌前缀」的键（用户口径：前缀忽略）。
            #   精确键仍优先（`resolve()` 先查整串），这里只是让跨前缀的同一款能对上。
            bt = brand_tail(k)
            if bt and bt != k:
                keys.append(bt)
            if relaxed:
                rk = relax_sku(k)
                if rk and rk != k:
                    keys.append(rk)
            for kk in keys:
                if kk in seen_keys:
                    continue
                seen_keys.add(kk)
                idx.setdefault(kk, []).append(it)
    return idx


def _pw_by_wh(rows: list) -> dict:
    """把某 SKU 的帕沃尔行按仓汇总（纯函数）→ {"加州3仓": 4, "kansas仓": 3}。"""
    out: dict = {}
    for x in rows or []:
        w = str(x.get("wh") or "")
        out[w] = out.get(w, 0) + int(x.get("stock") or 0)
    return out


def variant_missing(key: str, all_keys: set) -> str:
    """判断「这个 SKU 的变体在库存里根本不存在」还是「存在但没货」（纯函数）。

    ★ 2026-11-06 真机：BHY 275 单缺货里有 240 单是 `BHY-202504-10SetA-*` ——
      全库只有 `202504-5SetA/5SetB/5SetE`，**没有任何 10Set 变体**。
      这不是缺货，是上链接时变体名写错了（或上了新变体但没入库），
      和「有货但被前面的单分完」处理方式完全不同，必须分开报。

    做法：把 key 拆成 `CODE-VARIANT-SIZE`，保留 CODE 与 SIZE，看库存里有没有
    同 CODE 同 SIZE 的**其它变体**。有 → 返回那些变体名（提示"是不是命名错了"）。
    没有 → 返回空串（真的是个陌生款）。
    """
    parts = str(key or "").split("-")
    if len(parts) != 3:
        return ""
    code, var, size = parts
    sibs = []
    for k in all_keys or ():
        p = str(k).split("-")
        if len(p) != 3:
            continue
        if p[0] == code and p[2] == size and p[1] != var:
            sibs.append(p[1])
    if not sibs:
        return ""
    uniq = sorted(set(sibs))
    return "%s（同款同码库存里只有 %s）" % (var, "/".join(uniq[:4]))


def allocate(orders: list, pw_idx: dict, lx_idx: dict, prefer: str = "HX",
             store: str = "") -> dict:
    """把订单分到 A(帕沃尔)/B(恒信)/C(缺货)，**按 SKU 汇总需求防超卖**（纯函数）。

    orders: [{"ord","sku","qty"}]（已按优先级排好序，先到先得）
    prefer: 先查哪个源 —— `"HX"`（默认，用户 2026-11-06 口径：恒信优先）或 `"PW"`（旧口径）。
    返回 {"PW": [...], "HX": [...], "MISS": [...]}；每项带 `why` 说明落档原因。

    ★ 2026-11-06 改（用户原话「导出文件根据 sku 优先恒信可用库存，其次再是帕沃尔仓库存」）：
      优先级从「帕沃尔优先」反转成「恒信优先」。`prefer` 控制，默认 "HX"。
      对 `204203-4SetC-L` 这类两边都只有 1~2 件的单，反转后落档结果会变
      （原来进帕沃尔，现在进恒信），**不是 bug**。

    ★ 2026-11-05 修：`rec["wh"]` 必须是**真有货的那个仓**，不能一律写「加州3仓」。
      实测 `203304-4SetA-S` 帕沃尔加州3仓 stock=0（只有 transit=69 在途）、
      kansas仓才有 4 件；原来写死加州3仓 ⇒ 真下单会因「仓里没货」失败/发错仓。
      取仓顺序：加州3仓 有货 → 加州3仓；否则 kansas仓 有货 → kansas仓；
      两边都有 → 仍按「加州3仓」优先（用户口径：帕沃尔仓不变）。
    """
    if prefer not in ("HX", "PW"):
        raise ValueError("prefer 只能是 HX 或 PW，收到 %r" % (prefer,))
    want: dict = {}
    for o in orders:
        want.setdefault(norm_sku(o["sku"]), 0)
        want[norm_sku(o["sku"])] += int(o.get("qty") or 1)

    # ★ 2026-11-06：先查**精确键**，查不到再退化成「变体去尾数字」的键。
    #   用户口径：「104102-1B1 / 1091-1B1 这种变体带 1、2、1.1，查库存时把数字去掉查」。
    #   退化命中要留痕（`matched_key`），否则后面排查会以为库存源里真有原名。
    def _resolve(k: str):
        """精确 → 去品牌前缀 → 去尾数字（用户 2026-11-11：前缀忽略，只对 款号-套数-尺码）。"""
        if pw_idx.get(k) or lx_idx.get(k):
            return k, ""
        for idx in (pw_idx, lx_idx):
            rk, how = resolve(idx, k)
            if how:
                return rk, how
        return k, ""

    resolved = {k: _resolve(k) for k in want}
    left_pw, left_hx = {}, {}
    for k in want:
        rk = resolved[k][0]
        left_pw[k] = sum(int(x.get("stock") or 0) for x in (pw_idx.get(rk) or []))
        left_hx[k] = sum(float(x.get("avail") or 0) for x in (lx_idx.get(rk) or []))

    out = {"PRESALE": [], "PW": [], "HX": [], "MISS": []}

    def _why_tag(matched):
        """命中方式 → 给人看的短语（`matched_key` 只放键，别把说明塞进去）。"""
        return {"prefix": "［去品牌前缀 %s 命中］",
                "digits": "［去数字 %s 命中］"}.get(matched, "［%s 命中］")

    def _take_pw(o, k, q, rk, matched):
        """落帕沃尔（返回 rec）—— 调用前已确认 pw_left >= q。"""
        left_pw[k] = left_pw.get(k, 0) - q
        rec = dict(o, key=k)
        if matched:
            rec["matched_key"] = rk
            rec["matched_by"] = matched
        by_wh = _pw_by_wh(pw_idx.get(rk) or [])
        order_wh = [w for w in PW_SHIP_WH if by_wh.get(w, 0) >= q]
        rec["why"] = "帕沃尔可分 %d → 分配后余 %d（%s）%s" % (
            left_pw[k] + q, left_pw[k],
            "/".join("%s=%d" % (w, n) for w, n in by_wh.items()) or "-",
            (_why_tag(matched) % rk) if matched else "")
        rec["wh"] = order_wh[0] if order_wh else ""
        return rec

    def _take_hx(o, k, q, rk, matched):
        """落恒信（返回 rec）—— 调用前已确认 hx_left >= q。"""
        left_hx[k] = left_hx.get(k, 0) - q
        rec = dict(o, key=k)
        if matched:
            rec["matched_key"] = rk
            rec["matched_by"] = matched
        rec["why"] = "恒信可分 %.0f → 分配后余 %.0f%s" % (
            left_hx[k] + q, left_hx[k],
            (_why_tag(matched) % rk) if matched else "")
        rec["wh"] = HX_WAREHOUSE
        return rec

    # ★ 2026-11-12 用户令（新口径，本文件的分配单元因此**从 SKU 行改成订单**）：
    #   「这一步加一步拆分完后，把恒信仓、帕沃尔仓、缺货三个 sheet 去重，
    #     不能一个订单重复出现多个 sheet」。
    #   为什么必须整单定档：一单只有**一张面单、一次发货**。旧逻辑按行分，
    #   一单两个 SKU 会出现「这行进恒信、那行进帕沃尔」⇒ 同一单同时出现在 hx.txt 与
    #   pw.txt、同时出现在「恒信仓」与「帕沃尔仓」两个 sheet ⇒ 两个仓各买一张面单
    #   = **重复发货**（这是真金白银的错误，不只是表难看）。
    #   现在的判据：整单**所有行**都能落在同一个源才落；装不下 ⇒ 整单进缺货交人工，
    #   不再拆成「半个单发一半」。
    all_keys = set(pw_idx) | set(lx_idx)
    buckets: list = []
    by_ord: dict = {}
    for i, o in enumerate(orders):
        oid = str(o.get("ord") or "").strip()
        key = oid or "__row_%d__" % i   # 没单号的行各自成一单，别被合并成一单
        b = by_ord.get(key)
        if b is None:
            b = []
            by_ord[key] = b
            buckets.append(b)
        b.append(o)

    for bucket in buckets:
        lines, need = [], {}
        for o in bucket:
            k = norm_sku(o["sku"])
            q = int(o.get("qty") or 1)
            rk, matched = resolved.get(k, (k, ""))
            lines.append((o, k, q, rk, matched))
            need[k] = need.get(k, 0) + q       # 同单同 SKU 多行要累加

        # ---- D 组：已开预售的款（BHY / BEL）----
        # ★ 2026-11-12 用户令：「BHY 和 BEL 的 YOGA-4SetA 所有尺码开了预售，
        #   加一个放预售订单的 sheet-D」；追问确认：「今天的预售单我已经发走了，**从明天出现的
        #   预售款收录到预售 sheet 中**」；随后「203311-4SetA 删除收录」。
        #   ⇒ 口径：**明天起**，YOGA-4SetA 的单**整单直接进 D**，不再去 A/B 发（预售＝等补货），
        #     也不进「缺货」交人工补货判断。整单判：混了非预售款的行就照常走 A/B/C，
        #     别把一张混合单整单标成预售。
        if lines and all(is_presale(o.get("sku") or "", store) for o, _k, _q, _rk, _m in lines):
            for o, k, q, rk, matched in lines:
                rec = dict(o, key=k)
                if matched:
                    rec["matched_key"] = rk
                    rec["matched_by"] = matched
                rec["why"] = "预售款（%s 全尺码已开预售）→ D 组等补货，本批不发" % (
                    " / ".join(PRESALE_KEYS))
                rec["wh"] = ""
                out["PRESALE"].append(rec)
            continue

        # ★ 2026-11-06 用户改口径：「优先恒信可用库存，其次再是帕沃尔仓库存」。
        #   默认 prefer="HX" ⇒ 先恒信、后帕沃尔；prefer="PW" 保留旧顺序。
        sides = ("HX", "PW") if prefer == "HX" else ("PW", "HX")
        picked = ""
        for s in sides:
            lefts = left_hx if s == "HX" else left_pw
            if all(lefts.get(k, 0) >= q for k, q in need.items()):
                picked = s
                break
        if picked == "PW":
            for o, k, q, rk, matched in lines:
                out["PW"].append(_take_pw(o, k, q, rk, matched))
            continue
        if picked == "HX":
            for o, k, q, rk, matched in lines:
                out["HX"].append(_take_hx(o, k, q, rk, matched))
            continue

        # ---- 整单缺货：哪个源都装不下整单 ----
        multi = len(need) > 1
        for o, k, q, rk, matched in lines:
            rec = dict(o, key=k)
            if matched:
                rec["matched_key"] = rk
                rec["matched_by"] = matched
            # 三种情况说清楚，别都写成「两边都无此 SKU」——那会把「被前面单占完」
            # （204203-4SetC-L 只 2 件、本批 5 单）误报成「没有这个 SKU」，排查时看不出来。
            orig_pw = sum(int(x.get("stock") or 0) for x in (pw_idx.get(rk) or []))
            orig_hx = sum(float(x.get("avail") or 0) for x in (lx_idx.get(rk) or []))
            fits = [s for s in sides if (left_hx if s == "HX" else left_pw).get(k, 0) >= q]
            if multi and fits:
                # 本行自己有货，缺的是「整单装不进同一个仓」——别写成「被前面的单分完」，
                # 那会让人误以为要补货。数字仍写成 report 能解析的两段「可分/分配后余」。
                by_wh = _pw_by_wh(pw_idx.get(rk) or [])
                rec["why"] = ("恒信可分 %.0f → 分配后余 %.0f；帕沃尔可分 %d → 分配后余 %d（%s）；"
                              "整单 %d 行无法全部落在同一个仓 ⇒ 整单不自动发（人工处理）"
                              % (left_hx.get(k, 0) + q, left_hx.get(k, 0),
                                 left_pw.get(k, 0) + q, left_pw.get(k, 0),
                                 "/".join("%s=%d" % (w, n2) for w, n2 in by_wh.items()) or "-",
                                 len(need)))
            elif orig_pw or orig_hx:
                rec["why"] = ("可用已被前面的单分完（本批共需 %d，恒信 %.0f + 帕沃尔 %d）"
                              % (want.get(k, 0), orig_hx, orig_pw))
            else:
                # ★ 2026-11-06：区分「变体根本不存在（疑似命名错）」与「真有这个 SKU 但没货」。
                #   两者都是不能发，但前者要人工核对上链接命名，后者只要补货。
                hint = variant_missing(k, all_keys)
                rec["why"] = ("两边都无此 SKU（恒信 0 / 帕沃尔 0）"
                              + ("——**变体不存在**：%s，疑似上链接命名有误" % hint
                                 if hint else ""))
                if hint:
                    rec["variant_missing"] = hint
            if multi and not fits:
                # 整单里只要有一行装不进同一个仓，整单就不发 —— 说清是「整单」的问题，
                # 别让人误以为这行本身没货。
                rec["why"] += ("；整单 %d 行无法全部落在同一个仓 ⇒ 整单不自动发（人工处理）"
                               % len(need))
            rec["wh"] = ""
            out["MISS"].append(rec)
    return out


#: 去重时的收敛优先级（★ 2026-11-12：最保守的排前面 —— 宁可交人工，也不重复买面单）
#: D（预售）排最前：预售单是「等补货」的明确归属，不许被别的组抢走。
GROUP_PRIORITY = ("PRESALE", "MISS", "HX", "PW")


def dedupe_groups(groups: dict) -> tuple:
    """同一订单只保留一个归属 → `(新 groups, 冲突清单)`（纯函数）。

    ★ 2026-11-12 用户令：「把恒信仓、帕沃尔仓、缺货三个 sheet 去重，
      不能一个订单重复出现多个 sheet」。
    一单只能有一张面单、只能从一个仓发 ⇒ 出现在两个组＝重复买面单/重复发货。
    `allocate()` 已改成**整单定档**（正常不会产生冲突），本函数是**兜底闸门**：
    复用旧 split.json、或以后谁再改回按行分，都在这里收敛成一组。
    收敛优先级 `GROUP_PRIORITY` = 预售 > 缺货 > 恒信 > 帕沃尔（保守优先）。
    """
    g = {k: list((groups or {}).get(k) or []) for k in GROUPS}
    owner: dict = {}
    conflicts: list = []
    for side in GROUP_PRIORITY:
        keep = []
        for r in g[side]:
            o = str(r.get("ord") or "").strip()
            if o and o in owner and owner[o] != side:
                # 该单已经落在**别的**组 → 本行丢掉（同组多行是正常的：一单多 SKU 多行）
                conflicts.append({"ord": o, "kept": owner[o], "dropped": side,
                                  "sku": str(r.get("sku") or "")})
                continue
            if o:
                owner.setdefault(o, side)
            keep.append(r)
        g[side] = keep
    return g, conflicts


def read_orders(path: str) -> list:
    """读导出表 → [{"ord","sku","qty"}]（按「要求最晚发货时间」升序，先到先得）。

    支持两种来源的列名（★ 2026-11-10 用户令：SHEIN 店铺走 SHEIN 导出脚本）：
      · TEMU  ：`订单号 / SKU货号 / 应履约件数 / 订单状态=待发货 / 要求最晚发货时间`
      · SHEIN ：`订单号 / 卖家SKU / 数量 / 订单状态=待处理 / 要求发货时间`
                （`shein_export_pending.py` 的产物；一单多商品是**多行同单号**，
                  数量列按行计，别再做单号去重，否则会漏件）
    """
    import pandas as pd  # noqa: PLC0415

    # ★ 2026-11-10：网页导出的 xlsx（shein-web-export / BEGL·BANGL）第 1 行是大标题
    #   `订单基础信息`，真正的表头在**第 2 行** —— 直接 read_excel 会把表头读成
    #   `订单基础信息 / 订单基础信息.1 / …`，整表 0 行。先嗅探表头行。
    df = pd.read_excel(path, sheet_name=0, header=None)
    hi = 0
    for i in range(min(3, len(df))):
        vals = {str(x).strip() for x in df.iloc[i].tolist() if str(x) != "nan"}
        if "订单号" in vals and ("卖家SKU" in vals or "SKU货号" in vals):
            hi = i
            break
    df.columns = [str(x).strip() for x in df.iloc[hi].tolist()]
    df = df.iloc[hi + 1:].reset_index(drop=True)
    cols = {str(c).strip() for c in df.columns}
    skein = "卖家SKU" in cols and "SKU货号" not in cols
    keep = ("待处理",) if skein else ("待发货",)
    # ★ 2026-11-09 真机（BHY 1765 行 vs 页签「待发货 165」）：导出表里**混着已发货/已签收/
    #   平台处理中** —— 它们已经有运单号或已出库，再拆仓就是重复发货、白买面单。
    #   `--status 全部` 是老口径（2026-10-30「所有状态都导」），拆仓这边必须**只认待发货**。
    #   （BEL 当天恰好全是待发货，所以这个坑只在 BHY 暴露。）
    # ★ 2026-11-10：SHEIN 侧的等价状态是「待处理」。
    if "订单状态" in df.columns:
        n0 = len(df)
        df = df[df["订单状态"].astype(str).str.strip().isin(keep)]
        dropped = n0 - len(df)
        if dropped:
            print("[拆分] 跳过非「%s」行 %d（已发货/已签收/平台处理中不重复发货）"
                  % ("/".join(keep), dropped))
    need_col = next((c for c in ("要求最晚发货时间", "要求发货时间") if c in df.columns), None)
    if need_col:
        df = df.sort_values(need_col, kind="mergesort")
    sku_col = "卖家SKU" if skein else "SKU货号"
    qty_col = "数量" if "数量" in df.columns else "应履约件数"
    out = []
    for _, r in df.iterrows():
        q = r.get(qty_col)
        try:
            q = int(float(q))
        except Exception:  # noqa: BLE001
            q = 1
        out.append({"ord": str(r.get("订单号") or "").strip(),
                    "sku": str(r.get(sku_col) or "").strip(),
                    "qty": q})
    return [o for o in out if o["ord"]]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="批次按恒信优先拆成两个平台发货")
    ap.add_argument("--file", required=True, help="TEMU 导出的订单 xlsx")
    ap.add_argument("--out", default="", help="结果 JSON 路径（空＝不落盘）")
    ap.add_argument("--prefer", default="HX", choices=("HX", "PW"),
                    help="先查哪个源：HX=恒信优先（默认，2026-11-06 口径）；PW=帕沃尔优先（旧口径）")
    ap.add_argument("--store", default="",
                    help="店（BHY/BEL/BS/BD/BAN）：决定「预售款」能不能进 D 组（只有 BHY/BEL 能）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.file):
        print("[FAIL] 文件不存在：%s" % a.file, file=sys.stderr)
        return 42
    pw = json.load(open(PW_JSON, encoding="utf-8"))
    lx = json.load(open(LX_JSON, encoding="utf-8"))
    pw_idx = build_index(pw.get("items") or [], ("sku", "name"), wh_filter=PW_SHIP_WH,
                         relaxed=True)
    lx_idx = build_index(lx.get("items") or [], ("productName", "sku"), relaxed=True)

    orders = read_orders(a.file)
    groups = allocate(orders, pw_idx, lx_idx, prefer=a.prefer, store=a.store)
    # ★ 2026-11-12：落盘前再过一道去重闸门 —— 一单只许出现在一个组（见 dedupe_groups）
    groups, conflicts = dedupe_groups(groups)
    res = {"file": a.file, "total": len(orders), "prefer": a.prefer,
           "counts": {k: len(v) for k, v in groups.items()},
           "dedupe_conflicts": conflicts,
           "stocks_generatedAt": {"pw": pw.get("generatedAt"), "hx": lx.get("generatedAt")},
           "rule": ("①恒信avail>0→B ②帕沃尔可用>0→A ③都不是→C缺货"
                    "；D=预售（仅 BHY/BEL 的 %s 全尺码，整单无货时进 D）"
                    "；**整单定档**（一单只进一个仓）" % " / ".join(PRESALE_KEYS)
                    if a.prefer == "HX" else
                    "①帕沃尔可用>0→A ②恒信avail>0→B ③都不是→C缺货"
                    "；D=预售（仅 BHY/BEL 的 %s 全尺码）"
                    "；**整单定档**（一单只进一个仓）" % " / ".join(PRESALE_KEYS)),
           "groups": groups}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0

    names = {"PW": "A 帕沃尔发货（加州3仓 / kansas仓）",
             "HX": "B 恒信发货（ZOO ⑥美西）",
             "MISS": "C 缺货待定（**不许发**）",
             "PRESALE": "D 预售待发（BHY/BEL 的 YOGA-4SetA，等补货）"}
    print("总 %d 单（优先 %s）→ A 帕沃尔 %d 行 / B 恒信 %d 行 / C 缺货 %d 行 / D 预售 %d 行\n"
          % (len(orders), a.prefer, len(groups["PW"]), len(groups["HX"]),
             len(groups["MISS"]), len(groups["PRESALE"])))
    if conflicts:
        print("⚠ 同一订单重复出现在多个组，已收敛 %d 条：" % len(conflicts))
        for c in conflicts[:10]:
            print("   %s 保留 %s，从 %s 删掉（sku=%s）"
                  % (c["ord"], c["kept"], c["dropped"], c["sku"]))
        print()
    for side in ("PW", "HX", "MISS", "PRESALE"):
        print("== %s：%d 单 ==" % (names[side], len(groups[side])))
        for r in groups[side]:
            print("   %-26s %-24s x%s  %s" % (r["ord"], r["key"], r["qty"], r["why"]))
        print()
    if a.out:
        print("已存:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
