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


def hx_fail(rec: dict, po: str) -> dict | None:
    """恒信那份在领星 OMS 的终态判定（纯函数）：不通过就返回一行失败，通过返回 None。

    ★ 2026-11-06 用户追问「恒信的失败订单你有一起汇总到表吗」—— 之前 `build()` 里
      写了一句 `if sd == "恒信": continue`，把整个恒信组**无条件**排除在失败 sheet 外，
      恒信真失败也会被静默吞掉。这里补上：恒信只看领星 OMS 的终态，不看帕沃尔页面的
      「未下单」（那份本来就走 OMS，帕沃尔那边必然显示未下单，不是失败）。

    判据（都不许当成功）：
      · OMS 里查不到该单            → 失败「OMS 里查不到」
      · status 不在 (2 仓库处理中, 3 已出库) → 失败，按状态名说清（待处理/草稿/异常/已取消）
      · status 已受理但**没有运单号** → 失败「已受理但未出运单号」
    """
    if not rec:
        return {"失败环节": "领星 OMS", "状态": "查无此单",
                "失败原因": "领星 OMS 里搜不到该平台单号（可能没导单/店铺对不上）",
                "发货仓": "恒信加州3"}
    accs = rec.get("accounts") or []
    a = accs[0] if accs else {}
    st = a.get("status")
    sname = a.get("statusName") or OMS_STATUS.get(st, "未知")
    track = str(a.get("track") or "").strip()
    out_t = str(a.get("outboundTime") or "").strip()
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


def build(store: str, split: dict, hwc: list, idx: dict,
          hx_oms: dict | None = None) -> dict:
    """组装两个 sheet 的行（纯函数）。

    hx_oms: 恒信那份在领星 OMS 的状态 `{平台单号: {"accounts":[{status,statusName,track,outboundTime}]}}`
            （`scripts/oms/check_po_status.py --json` 的产物）。
            ★ 2026-11-06 补：`None` = **没查**（打印里会明说），`{}` = 查了但单子不在 OMS。
              前者绝不等于"恒信全成功"。
    """
    miss_rows = ((split.get("groups") or {}).get("MISS")) or []
    real_miss, already = classify_miss(miss_rows, idx)

    short = []
    for r in real_miss:
        why, hint = miss_reason(r)
        short.append({
            "参考单号": r.get("ord") or "",
            "平台SKU": r.get("sku") or "",
            "件数": int(r.get("qty") or 1),
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
            "状态": "已实际下单（有运单号）",
            "原因": "帕沃尔已出单，运单号 %s，不属于缺货" % r.get("waybill"),
            "库存里同款同码有的变体": "",
            "处理动作": "回 TEMU「待发货」勾选确认发货即可",
        })

    side = {r.get("ord"): "缺货" for r in miss_rows}
    for sd in ("PW", "HX"):
        for r in ((split.get("groups") or {}).get(sd) or []):
            side[r.get("ord")] = "帕沃尔" if sd == "PW" else "恒信"

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

    # ★ 2026-11-06 补（用户追问「恒信的失败订单你有一起汇总到表吗」）：
    #   恒信那份的失败**只能看领星 OMS**，帕沃尔页面对它们必然显示「未下单」，
    #   所以之前那句 `if sd == "恒信": continue` 会把恒信真失败静默吞掉。
    #   ⚠ `hx_oms is None` = **根本没查**（不是"查了没有"）：这时一行都不写，
    #     由调用方的 `_hx_checked` 明说未核对；传 `{}` 才是"查了、单子不在 OMS 里"。
    hx_rows = ((split.get("groups") or {}).get("HX")) or []
    if hx_oms is not None:
        for r in hx_rows:
            po = str(r.get("ord") or "")
            if not po:
                continue
            bad = hx_fail((hx_oms or {}).get(po) or {}, po)
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

    # 排序：真缺货排前面（要人工补货），「其实已下单」压到最后（只需回 TEMU 确认）。
    short.sort(key=lambda r: (0 if r["状态"].startswith("真缺货") else 1,
                              r["平台SKU"], r["参考单号"]))
    fails.sort(key=lambda r: (r["拆仓归属"] != "恒信", r["平台SKU"], r["参考单号"]))
    return {"缺货": short, "失败订单": fails,
            "_hx_checked": hx_oms is not None, "_hx_total": len(hx_rows)}


def write_xlsx(store: str, sheets: dict, out: str) -> str:
    """落 xlsx（每店两个 sheet：缺货 / 失败订单），带表头样式与列宽。"""
    from openpyxl import Workbook  # noqa: PLC0415
    from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415
    from openpyxl.utils import get_column_letter  # noqa: PLC0415

    COLS = {
        "缺货": ["参考单号", "平台SKU", "件数", "状态", "原因",
                 "库存里同款同码有的变体", "处理动作"],
        "失败订单": ["参考单号", "平台SKU", "发货仓", "拆仓归属", "状态",
                     "失败环节", "失败原因", "处理动作"],
    }
    WIDTH = {
        "缺货": [28, 26, 7, 20, 44, 34, 44],
        "失败订单": [28, 26, 12, 11, 11, 20, 44, 44],
    }
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    hdr_font = Font(color="FFFFFF", bold=True, size=11)

    wb = Workbook()
    wb.remove(wb.active)
    for name in ("缺货", "失败订单"):
        ws = wb.create_sheet(name)
        cols = COLS[name]
        rows = sheets.get(name) or []
        ws.append(cols)
        for c in range(1, len(cols) + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(c)].width = WIDTH[name][c - 1]
        for r in rows:
            ws.append([r.get(c) for c in cols])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(cols)),
                                          max(1, len(rows) + 1))
        for row in ws.iter_rows(min_row=2, max_row=len(rows) + 1):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.row_dimensions[1].height = 22

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    wb.save(out)
    return out


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

    sheets = build(a.store, split, hwc, idx, hx_oms=hx_oms)
    out = a.out or os.path.join(_HERE, "out", time.strftime("%Y-%m-%d"),
                                "%s_缺货与失败订单_%s.xlsx"
                                % (a.store, time.strftime("%Y%m%d")))
    write_xlsx(a.store, sheets, out)

    print("[%s] 缺货 %d 单 / 失败订单 %d 单 → %s"
          % (a.store, len(sheets["缺货"]), len(sheets["失败订单"]), out))
    bad = [r for r in sheets["缺货"] if r["状态"].startswith("真缺货")]
    hint = [r for r in bad if r["库存里同款同码有的变体"]]
    print("   其中真缺货 %d（变体命名可疑 %d），已实际下单 %d"
          % (len(bad), len(hint), len(sheets["缺货"]) - len(bad)))
    # ★ 恒信必须明说核对过没有 —— 不给 --hx-oms 就等于没查，"没报失败"不代表没失败。
    if sheets.get("_hx_checked"):
        hx_fail_n = len([r for r in sheets["失败订单"] if r["拆仓归属"] == "恒信"])
        print("   恒信 %d 单已查领星 OMS：失败 %d 单"
              % (sheets.get("_hx_total") or 0, hx_fail_n))
    else:
        print("   ⚠ **恒信那份没核对**（缺 --hx-oms）——表里看不到恒信失败单，别当它没有")
    for r in sheets["失败订单"]:
        print("   失败 [%s] %-26s %-24s %-10s %s"
              % (r["拆仓归属"] or "-", r["参考单号"], r["平台SKU"],
                 r["发货仓"] or "-", (r["失败原因"] or "")[:50]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
