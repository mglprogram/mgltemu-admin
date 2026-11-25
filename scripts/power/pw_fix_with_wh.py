# -*- coding: utf-8 -*-
"""失败单修复：**换 Fnsku 后，若新 Fnsku 的库存在另一个仓，必须连仓库一起改**。

★ 用户 2026-11-25 追加口径：
    「还有一种情况，就是查到这个产品库存在另一个 sku 下，
      你必须在改完 sku 后，还要改仓库重新下单」

现状问题：
  `pw_fix_by_fnsku.fix_order` 只换产品行三件套，仓库沿用订单原来的
  （`read_form` 里 `ck_id`/仓库不动）⇒ 如果目标 Fnsku 的货在**另一个仓**，
  下单仍会报「仓库没有库存」，或者把货从错误仓发出去。

本模块：
  ① 从库存页查出每个候选 Fnsku 的 **仓库 + 代发库存**
  ② 自动挑「有货」的那个（最多库存）
  ③ 换 Fnsku **并**把订单仓库/渠道改到该 Fnsku 有货的仓
  ④ 重下单

仓库映射（`pw_hwc_flow` 常量，2026-11-25 实测）：
    CK = {"加州3仓": "22", "kansas仓": "10"}
    渠道：temu → temu + temu官方面单；shein → Shein + Shein官方面单
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\pw_orders\lib")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pw_fix_by_fnsku as FX  # noqa: E402
import pw_hwc_flow as F  # noqa: E402
import pw_session as SS  # noqa: E402

#: 库存页仓名 → 下单用的仓名（`pw_hwc_flow.CK` 的键）
WH_ALIAS = {
    "加州3仓": "加州3仓", "加州三仓": "加州3仓",
    "kansas仓": "kansas仓", "kansas": "kansas仓",
}


def _num(s) -> float:
    try:
        return float(str(s).replace(",", "").strip() or 0)
    except Exception:  # noqa: BLE001
        return -1.0


def stock_candidates(pw, seller_sku_or_fnsku: str) -> list:
    """库存页 `keywords` 查同款所有 Fnsku → `[{fnsku, seller, wh, stock}]`（按库存降序）。"""
    from urllib.parse import quote  # noqa: PLC0415
    t = pw.get("stock.php?act=list&keywords=%s" % quote(seller_sku_or_fnsku))
    t = t if isinstance(t, str) else str(t)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)

    head = None
    for tr in rows:
        cs = [FX.clean(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        if any("Fnsku" in c for c in cs):
            head = cs
            break

    out = []
    for tr in rows:
        if seller_sku_or_fnsku.lower() not in tr.lower():
            continue
        cells = [FX.clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        d = dict(zip(head or [], cells))
        fn = d.get("Fnsku") or ""
        if not fn:
            continue
        wh_raw = d.get("仓库名称", "")
        out.append({"fnsku": fn, "seller": d.get("Seller sku", ""),
                    "wh_raw": wh_raw, "wh": WH_ALIAS.get(wh_raw, wh_raw),
                    "stock": d.get("代发库存", "0"),
                    "stock_n": _num(d.get("代发库存", "0"))})
    out.sort(key=lambda x: -x["stock_n"])
    return out


def pick_and_fix(pw, po: str, seller_sku: str = "", platform: str = "temu",
                 apply: bool = False, log=print) -> dict:
    """查库存 → 挑有货 Fnsku → 换 Fnsku **并改仓** → 重下单。

    `seller_sku` 不传时，用订单当前的平台SKU（去掉店铺前缀后的款号）当关键词。
    """
    st0 = FX.state(pw, po)
    oid = st0.get("id")
    log("   单号 %s id=%s 状态=%s 仓库=%s 平台SKU=%s"
        % (po, oid, st0["status"], st0["wh"], st0["sku"]))
    if not oid:
        return {"ok": False, "why": "查不到订单"}

    kw = seller_sku or re.sub(r"^[A-Z]{2,4}-", "", st0.get("sku") or "")
    cands = [c for c in stock_candidates(pw, kw) if c["stock_n"] > 0]
    log("   库存页「%s」有货候选 %d 个：" % (kw, len(cands)))
    for c in cands[:5]:
        log("      %-14s %-20s %-8s 库存=%s"
            % (c["fnsku"], c["seller"][:20], c["wh"], c["stock"]))
    if not cands:
        return {"ok": False, "why": "该款没有有货 Fnsku（需补货）"}

    # 优先：库存最多的那个；同库存优先原仓（少改一次）
    best = max(cands, key=lambda x: (x["stock_n"], x["wh"] == st0["wh"]))
    log("   选中 %s（%s / %s，库存 %s）"
        % (best["fnsku"], best["seller"], best["wh"], best["stock"]))

    need_wh_change = best["wh"] and best["wh"] != st0["wh"]
    if need_wh_change:
        log("   ★ 仓库需改：%s → %s（新 Fnsku 货在另一个仓）" % (st0["wh"], best["wh"]))
    else:
        log("   仓库无需改（目标 Fnsku 与订单同仓）")

    if not apply:
        return {"ok": True, "dry": True, "oid": oid, "target": best,
                "wh_change": need_wh_change}

    # ① 换 Fnsku（三件套）
    r1 = FX.fix_order(pw, po, best["fnsku"], apply=True, log=log)
    if not r1.get("ok"):
        return {"ok": False, "why": "换 Fnsku 失败: %s" % str(r1)[:150]}

    # ② 若需改仓 → 用 http_set_wh 重新设仓渠道（订单状态已变，需重新查 id）
    if need_wh_change:
        st_mid = FX.state(pw, po)
        oid2 = st_mid.get("id") or oid
        log("   改仓渠道：%s（订单 id=%s）" % (best["wh"], oid2))
        rr = F.http_set_wh([int(oid2)], best["wh"], platform=platform, log=log)
        log("   → %s" % json.dumps(rr, ensure_ascii=False)[:160])
        # ③ 再下一次
        import time  # noqa: PLC0415
        time.sleep(2)
        d = FX.reship(pw, oid2)
        log("   再次下单代发 → %s" % str(d.get("msg"))[:90])
        import time as _t  # noqa: PLC0415
        _t.sleep(3)

    st2 = FX.state(pw, po)
    log("   最终 状态=%s 仓库=%s｜%s"
        % (st2["status"], st2["wh"], st2["err"][:90]))
    return {"ok": st2["status"] == "已下单", "before": st0["status"],
            "after": st2["status"], "wh_before": st0["wh"], "wh_after": st2["wh"],
            "fnsku": best["fnsku"], "track": ""}


if __name__ == "__main__":
    po = sys.argv[1] if len(sys.argv) > 1 else ""
    kw = sys.argv[2] if len(sys.argv) > 2 else ""
    do = "--apply" in sys.argv
    p = SS.PW()
    p.login()
    print(json.dumps(pick_and_fix(p, po, kw, apply=do), ensure_ascii=False)[:800])
