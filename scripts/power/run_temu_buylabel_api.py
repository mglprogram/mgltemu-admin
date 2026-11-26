# -*- coding: utf-8 -*-
"""批量买面单入口：给一批单号（或按仓清单），全自动跑「拉单→查渠道→换仓→选时段→下单」。

用法：
    python scripts/power/run_temu_buylabel_api.py --store BHY --pos PO-1,PO-2 [--apply]
    python scripts/power/run_temu_buylabel_api.py --store BHY --limit 20 [--apply]
    python scripts/power/run_temu_buylabel_api.py --store BHY --from-split <split.json> --apply

默认 **dry-run**（只打印渠道/金额/时段），`--apply` 才真花钱。

产物（--apply 时）：
  · `_logs/temu-buylabel-api/run_<时间>.json`   —— 明细（每单结果 + 包裹号）
  · `_logs/temu-buylabel-fail/*.png`            —— 失败现场截图
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import label_fail_kind as FK  # noqa: E402
import label_place as L  # noqa: E402
import label_session as SESS  # noqa: E402
import order_list_api as O  # noqa: E402

DIMS = {"weight": "1.50", "length": "12.30", "width": "9.30", "height": "3.00",
        "dimension_unit": "in", "weight_unit": "lb"}
OUT = r"D:\AI-workplace\TEMU\_logs\temu-buylabel-api"


#: 拆仓组 → 买面单首选仓（恒信）
HX_WAREHOUSE = "恒信加州3"
#: 帕沃尔仓的两种（按行内 `wh` 分流）
PW_DEFAULT = "加州三仓"
PW_KANSAS = "kansas"


def warehouse_from_split(split_path: str) -> dict:
    """读 split.json → `{parentOrderSn: 首选仓名}`。跳过的组 / 预售 / 缺货都不出现在结果里。

    规则（用户 2026-11-24）：
      HX 组 → 恒信加州3；
      PW 组 → 行内 `wh` 含 kansas ⇒ kansas，否则 ⇒ 加州三仓；
      MISS / PRESALE / PRESALE15 → 不买。
    """
    d = json.load(open(split_path, encoding="utf-8"))
    groups = d.get("groups") or {}
    out: dict = {}
    for r in groups.get("HX") or []:
        po = str(r.get("ord") or "").strip()
        if po:
            out.setdefault(po, HX_WAREHOUSE)
    for r in groups.get("PW") or []:
        po = str(r.get("ord") or "").strip()
        if not po:
            continue
        wh = str(r.get("wh") or "")
        out.setdefault(po, PW_KANSAS if "kansas" in wh.lower() else PW_DEFAULT)
    return out


def goods_of(po: str, item: dict) -> list:
    return [{"order_sn": g["orderSn"], "parent_order_sn": po,
             "goods_id": g["goodsId"], "sku_id": str(g["skuId"]),
             "quantity": g.get("quantity", 1)} for g in (item.get("goods") or [])]


def main() -> int:
    ap = argparse.ArgumentParser(description="TEMU 批量买面单（接口版）")
    ap.add_argument("--store", required=True)
    ap.add_argument("--pos", default="", help="单号，逗号分隔")
    ap.add_argument("--limit", type=int, default=0, help="按列表最多取多少单")
    ap.add_argument("--from-split", default="", help="从 split.json 取单号（取 PW/HX 组）")
    ap.add_argument("--warehouse", default="加州三仓", help="首选仓")
    ap.add_argument("--apply", action="store_true", help="真下单（花钱！）")
    ap.add_argument("--max-orders", type=int, default=0, help="安全上限")
    a = ap.parse_args()

    pos: list = []
    if a.pos:
        pos = [x.strip() for x in a.pos.split(",") if x.strip()]
    elif a.from_split:
        # ★ 2026-11-24：按拆仓自动分仓（HX→恒信加州3；PW→kansas 或 加州三仓）
        wh_map = warehouse_from_split(a.from_split)
        for o in wh_map:
            if o not in pos:
                pos.append(o)
        print("分仓结果：" + "，".join(
            "%s %d 单" % (w, sum(1 for v in wh_map.values() if v == w))
            for w in dict.fromkeys(wh_map.values())))
        a._wh_map = wh_map
    if a.max_orders:
        pos = pos[:a.max_orders]

    if pos:
        items = {it["parentOrderSn"]: it
                 for it in (O.fetch_page(a.store, size=100, pos=pos).get("items") or [])}
    else:
        items = {it["parentOrderSn"]: it
                 for it in O.fetch_all(a.store, limit=a.limit)}
    print("拉到 %d 单（请求 %d）｜模式：%s"
          % (len(items), len(pos) or a.limit or -1, "真下单" if a.apply else "dry-run"))

    # ★ 2026-11-24：下单前先自检会话（不通自动重登），避免跑到一半才 403
    if not SESS.ensure_db_ready(a.store, log=print):
        print("\n❌ 会话未就绪（自动重登后仍不通）→ 终止，避免批量误判")
        return 2

    # ★ 2026-11-26：买之前先查「是否已买过面单」（confirmShippingList 反查包裹）。
    #   真机（KLS 111 单）：92 单早就买过了，但 dry-run 照样报「可下单」，
    #   真提交才回「发货订单重复」。预检能把这类单提前筛掉，不浪费渠道/时段查询。
    pre_bought = {}
    if os.environ.get("TEMU_LABEL_PRECHECK", "1") not in ("0", "false", "False"):
        try:
            import temu_confirm_api as _TC  # noqa: PLC0415
            _all = [str(p) for p in items if str(p).strip()]
            for _i in range(0, len(_all), 100):
                _chunk = _all[_i:_i + 100]
                if not _chunk:
                    continue
                _d = _TC.list_pending(a.store, pos=_chunk, page_size=100)
                for _it in (_d.get("items") or []):
                    for _p in _TC._po_list(_it):
                        _po = _p.get("parentOrderSn")
                        if _po:
                            pre_bought[_po] = _it.get("packageSn")
            if pre_bought:
                print("   [预检] %d 单已买过面单 → 跳过（TEMU_LABEL_PRECHECK=0 可关）"
                      % len(pre_bought))
        except Exception as _ex:  # noqa: BLE001
            print("   [预检] 跳过（%s）" % str(_ex)[:80])

    results = []
    for po, it in items.items():
        if po in pre_bought:
            print("  跳过 %s（已买过面单，包裹 %s）" % (po, pre_bought[po]))
            results.append({"po": po, "ok": True, "why": "已买过面单",
                            "package_sn": pre_bought[po], "skip": True})
            continue
        if (it.get("status") or 0) != 2:
            print("  跳过 %s（状态 %s 非待发货）" % (po, it.get("status")))
            continue
        # ★ 2026-11-24：预售单**在查渠道之前**就拦掉（否则会误报「没有可用物流商」）
        if it.get("isExtendedPreSale"):
            _end = it.get("preSaleEndTimeStr") or "未知"
            print("  跳过 %s：预售单（延长时效），补货日期 %s 之后才可发货" % (po, _end))
            results.append({"po": po, "ok": False,
                            "why": "预售单（延长时效），不能买面单"})
            continue
        goods = goods_of(po, it)
        if not goods:
            print("  跳过 %s（无 goods 明细）" % po)
            continue
        if len(goods[0]["order_sn"]) < 10:
            print("  跳过 %s（goods 异常）" % po)
            continue
        print("\n=== %s（%d 商品行）===" % (po, len(goods)))
        _wh = getattr(a, "_wh_map", {}).get(po) or a.warehouse
        if getattr(a, "_wh_map", None):
            print("   [分仓] %s → %s" % (po, _wh))
        r = L.place_one(a.store, goods, DIMS, _wh, apply=a.apply, log=print)
        results.append(dict(r, po=po))
        if r.get("ok"):
            if r.get("dry"):
                body = (r.get("body") or {}).get("send_request_list", [{}])[0]
                print("  ✅ 可下单 | %s | %s | $%.2f"
                      % (r.get("wh"), r.get("channel"),
                         (body.get("expect_shipping_fee") or 0) / 1000))
            else:
                print("  ✅ 下单成功 | 包裹 %s | 任务 %s"
                      % (r.get("package_sn"), r.get("task_id")))
        else:
            print("  ❌ %s（已试仓 %s）" % (r.get("why"), r.get("tried")))

    s = FK.batch_summary([{"po": r["po"], "ok": r.get("ok"),
                           "why": r.get("why")} for r in results])
    print("\n========== 汇总 ==========")
    print("成功 %d / 失败 %d / 共 %d" % (s["ok"], s["fail"], len(results)))
    for why, n in (s["reasons"] or {}).items():
        print("   %s × %d" % (why, n))
    if a.apply:
        os.makedirs(OUT, exist_ok=True)
        p = os.path.join(OUT, "run_%s.json" % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
        slim = [{k: r.get(k) for k in ("po", "ok", "wh", "channel", "package_sn",
                                       "task_id", "why", "tried")} for r in results]
        json.dump(slim, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("明细 → %s" % p)
    return 0 if s["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
