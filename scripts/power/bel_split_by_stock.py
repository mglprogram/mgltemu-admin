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

#: 跨系统对码要去掉的品牌前缀（同一款可能挂 BH/BK/BS/BS2/BLA，平台侧是 BEL）
#: ★ 2026-11-06 修（BHY 店 438 单全被判缺货）：白名单里少了**店铺自己的前缀** `BHY`/`BEL` 之外的
#:   `BHY-` 当店侧 SKU 出现时（如 `BHY-203311-4SetA-M`），库存里叫 `BS2-203311-4SetA-M`；
#:   前缀没去掉 ⇒ 键变成 `BHY-203311-4SETA-M`，两边都查不到 ⇒ 全判缺货。
#:   现把两家店的前缀都收进来（BHY/BEL 是店，BH/BK/BS/BS2/BLA 是仓侧品牌）。
_BRAND = re.compile(r"^(BH|BK|BS2|BS|BLA|BEL|BHY)-", re.I)

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


def allocate(orders: list, pw_idx: dict, lx_idx: dict, prefer: str = "HX") -> dict:
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
        if pw_idx.get(k) or lx_idx.get(k):
            return k, ""
        rk = relax_sku(k)
        if rk and rk != k and (pw_idx.get(rk) or lx_idx.get(rk)):
            return rk, rk
        return k, ""

    resolved = {k: _resolve(k) for k in want}
    left_pw, left_hx = {}, {}
    for k in want:
        rk = resolved[k][0]
        left_pw[k] = sum(int(x.get("stock") or 0) for x in (pw_idx.get(rk) or []))
        left_hx[k] = sum(float(x.get("avail") or 0) for x in (lx_idx.get(rk) or []))

    out = {"PW": [], "HX": [], "MISS": []}

    def _take_pw(o, k, q, rk, matched):
        """落帕沃尔（返回 rec）—— 调用前已确认 pw_left >= q。"""
        left_pw[k] = left_pw.get(k, 0) - q
        rec = dict(o, key=k)
        if matched:
            rec["matched_key"] = matched
        by_wh = _pw_by_wh(pw_idx.get(rk) or [])
        order_wh = [w for w in PW_SHIP_WH if by_wh.get(w, 0) >= q]
        rec["why"] = "帕沃尔可分 %d → 分配后余 %d（%s）%s" % (
            left_pw[k] + q, left_pw[k],
            "/".join("%s=%d" % (w, n) for w, n in by_wh.items()) or "-",
            "［按去数字 %s 命中］" % matched if matched else "")
        rec["wh"] = order_wh[0] if order_wh else ""
        return rec

    def _take_hx(o, k, q, matched):
        """落恒信（返回 rec）—— 调用前已确认 hx_left >= q。"""
        left_hx[k] = left_hx.get(k, 0) - q
        rec = dict(o, key=k)
        if matched:
            rec["matched_key"] = matched
        rec["why"] = "恒信可分 %.0f → 分配后余 %.0f%s" % (
            left_hx[k] + q, left_hx[k],
            "［按去数字 %s 命中］" % matched if matched else "")
        rec["wh"] = HX_WAREHOUSE
        return rec

    for o in orders:
        k = norm_sku(o["sku"])
        q = int(o.get("qty") or 1)
        rk, matched = resolved.get(k, (k, ""))
        pw_left, hx_left = left_pw.get(k, 0), left_hx.get(k, 0)
        # ★ 2026-11-06 用户改口径：「优先恒信可用库存，其次再是帕沃尔仓库存」。
        #   默认 prefer="HX" ⇒ 先恒信、后帕沃尔；prefer="PW" 保留旧顺序。
        cands = (("HX", hx_left), ("PW", pw_left)) if prefer == "HX" \
            else (("PW", pw_left), ("HX", hx_left))
        side = next((s for s, left in cands if left >= q), "")
        if side == "PW":
            out["PW"].append(_take_pw(o, k, q, rk, matched))
        elif side == "HX":
            out["HX"].append(_take_hx(o, k, q, matched))
        else:
            rec = dict(o, key=k)
            if matched:
                rec["matched_key"] = matched
            # 三种情况说清楚，别都写成「两边都无此 SKU」——那会把「被前面单占完」
            # （204203-4SetC-L 只 2 件、本批 5 单）误报成「没有这个 SKU」，排查时看不出来。
            if want.get(k) and (pw_left or hx_left) is not None:
                orig_pw = sum(int(x.get("stock") or 0) for x in (pw_idx.get(rk) or []))
                orig_hx = sum(float(x.get("avail") or 0) for x in (lx_idx.get(rk) or []))
                if orig_pw or orig_hx:
                    rec["why"] = ("可用已被前面的单分完（本批共需 %d，恒信 %.0f + 帕沃尔 %d）"
                                  % (want[k], orig_hx, orig_pw))
                else:
                    # ★ 2026-11-06：区分「变体根本不存在（疑似命名错）」与「真有这个 SKU 但没货」。
                    #   两者都是不能发，但前者要人工核对上链接命名，后者只要补货。
                    hint = variant_missing(k, set(pw_idx) | set(lx_idx))
                    rec["why"] = ("两边都无此 SKU（恒信 0 / 帕沃尔 0）"
                                  + ("——**变体不存在**：%s，疑似上链接命名有误" % hint
                                     if hint else ""))
                    if hint:
                        rec["variant_missing"] = hint
            else:
                rec["why"] = "两边都无此 SKU"
            rec["wh"] = ""
            out["MISS"].append(rec)
    return out


def read_orders(path: str) -> list:
    """读导出表 → [{"ord","sku","qty"}]（按「要求最晚发货时间」升序，先到先得）。

    支持两种来源的列名（★ 2026-11-10 用户令：SHEIN 店铺走 SHEIN 导出脚本）：
      · TEMU  ：`订单号 / SKU货号 / 应履约件数 / 订单状态=待发货 / 要求最晚发货时间`
      · SHEIN ：`订单号 / 卖家SKU / 数量 / 订单状态=待处理 / 要求发货时间`
                （`shein_export_pending.py` 的产物；一单多商品是**多行同单号**，
                  数量列按行计，别再做单号去重，否则会漏件）
    """
    import pandas as pd  # noqa: PLC0415

    df = pd.read_excel(path, sheet_name=0)
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
    groups = allocate(orders, pw_idx, lx_idx, prefer=a.prefer)
    res = {"file": a.file, "total": len(orders), "prefer": a.prefer,
           "counts": {k: len(v) for k, v in groups.items()},
           "stocks_generatedAt": {"pw": pw.get("generatedAt"), "hx": lx.get("generatedAt")},
           "rule": ("①恒信avail>0→B ②帕沃尔可用>0→A ③都不是→C缺货；按SKU汇总需求防超卖"
                    if a.prefer == "HX" else
                    "①帕沃尔可用>0→A ②恒信avail>0→B ③都不是→C缺货；按SKU汇总需求防超卖"),
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
             "MISS": "C 缺货待定（**不许发**）"}
    print("总 %d 单（优先 %s）→ A 帕沃尔 %d / B 恒信 %d / C 缺货 %d\n"
          % (len(orders), a.prefer, len(groups["PW"]), len(groups["HX"]),
             len(groups["MISS"])))
    for side in ("PW", "HX", "MISS"):
        print("== %s：%d 单 ==" % (names[side], len(groups[side])))
        for r in groups[side]:
            print("   %-26s %-24s x%s  %s" % (r["ord"], r["key"], r["qty"], r["why"]))
        print()
    if a.out:
        print("已存:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
