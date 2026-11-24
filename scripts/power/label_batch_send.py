# -*- coding: utf-8 -*-
"""**批量提交**验证：把多单拼成一个 `send_request_list` 一次 POST `batch_send`。

★ 2026-11-24 用户要求：「逐单提交可以改成批量吗」→ 用这三单验证平台行为：
    PO-211-09704408638072412
    PO-211-10228596839031721
    PO-211-09656919286392121

流程：
  ① 逐单查渠道/时段/拼 item（复用 place_one 的前半段逻辑）
  ② 把 3 个 item 放进一个 body 一次提交
  ③ 打印返回结构（看是否给多个 package_sn / 是否部分成功）

先 `--dry-run` 只打印 body，加 `--apply` 才真提交（约 $8.84）。
"""
import argparse
import time
import json
import sys

sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power")
sys.path.insert(0, r"D:\AI-workplace\TEMU\scripts\power\lib")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import temu_buylabel_api as A  # noqa: E402
import label_place as L  # noqa: E402
import order_list_api as O  # noqa: E402

POS = ["PO-211-09704408638072412", "PO-211-10228596839031721",
       "PO-211-09656919286392121", "PO-211-01747779564150047",
       "PO-211-20898008515193599", "PO-211-20983742654070014",
       "PO-211-10142844949111206", "PO-211-01791328865910299",
       "PO-211-20650519951992128", "PO-211-20956172124793954",
       "PO-211-21088721551990924", "PO-211-10221069189751380",
       "PO-211-01767940547190439", "PO-211-21184197345911445"]
DIMS = {"weight": "1.50", "length": "12.30", "width": "9.30", "height": "3.00",
        "dimension_unit": "in", "weight_unit": "lb"}


def build_item(store: str, po: str, item_meta: dict) -> dict | None:
    """调 `place_one` 同款的 `prepare_item`（含换仓/免预约兜底）。"""
    goods = [{"order_sn": g["orderSn"], "parent_order_sn": po,
              "goods_id": g["goodsId"], "sku_id": str(g["skuId"]),
              "quantity": g.get("quantity", 1)} for g in (item_meta.get("goods") or [])]
    if not goods:
        print("  %s 无 goods" % po)
        return None
    r = L.prepare_item(store, goods, DIMS, "加州三仓", log=lambda s: None)
    if not r.get("ok"):
        print("  %s ❌ %s" % (po, r.get("why")))
        return None
    it = r["item"]
    print("  %s → 仓=%s %s $%.2f"
          % (po, r.get("wh"), r.get("channel"),
             (it.get("expect_shipping_fee") or 0) / 1000))
    return it


def prepare_many(store: str, pos: list, items: dict, dims: dict,
                 warehouse: str = "加州三仓", log=print) -> list:
    """**批量准备**：按仓分组 → 每仓一次批量查渠道 → 逐单选渠道/查时段。

    返回 `[(po, send_request|None, why)]`，顺序同 `pos`。
    比逐单 `prepare_item` 少掉「每单一次查渠道」的往返（N 次 → 仓数 次）。
    """
    by_wh: dict = {}
    meta: dict = {}
    for po in pos:
        it = items.get(po) or {}
        goods = [{"order_sn": g["orderSn"], "parent_order_sn": po,
                  "goods_id": g["goodsId"], "sku_id": str(g["skuId"]),
                  "quantity": g.get("quantity", 1)}
                 for g in (it.get("goods") or [])]
        if not goods:
            meta[po] = {"why": "无 goods 明细"}
            continue
        meta[po] = {"goods": goods, "wh": warehouse, "tried": [warehouse]}
        by_wh.setdefault(warehouse, []).append(po)

    out = {}
    for wh, polist in by_wh.items():
        info = L._wh_info(wh)
        # ① 每仓一次批量查渠道（query_channels 目前是单 query_list，逐单调但仍复用连接）
        chans_cache = {}
        for po in polist:
            g = meta[po]["goods"]
            chans = L.query_channels(store, info["warehouse_id"], g, dims,
                                     warehouse_name=info.get("warehouse_name", ""))
            if not chans:
                pv0 = L.A.providers(store, info["warehouse_id"], g, dims)
                chans = ((pv0.get("json") or {}).get("result") or {}).get(
                    "online_channel_vo_list") or []
            allowed = [c for c in chans
                       if L.A.carrier_ok(c.get("shipping_company_name") or "")]
            ch = L.S.pick_channel(allowed)
            if not ch:
                # 换仓兜底
                nxt = L.S.next_warehouse([wh], L.DROPDOWN_WH.get(store.upper(), set()))
                meta[po]["why"] = ("无可用渠道（换 %s 兜底）" % nxt) if nxt else "无可用渠道"
                meta[po]["rebuild"] = True
                continue
            chans_cache[po] = ch

        # ② 批量问时段（reserve_info_list 支持多条）
        probes, ready = [], {}
        for po, ch in chans_cache.items():
            g = meta[po]["goods"]
            it0 = L.A.build_send_item(ch, g, dims, info, (0, 0))
            probes.append((po, ch, it0))
        if probes:
            body = {"reserve_info_list": [p[2] for p in probes]}
            ws_url, js = L._ws_and_js(store,
                                      "/mms/eagle/package/reservation/query_avail_reserve_period",
                                      body)
            import json as _json  # noqa: PLC0415
            import asyncio as _aio  # noqa: PLC0415
            raw = _aio.run(L.A._ev(ws_url, js)) or "{}"
            j = _json.loads(raw).get("body") if isinstance(raw, str) else "{}"
            res = (_json.loads(j or "{}").get("result") or {})
            lst = res.get("available_reserved_period_list") or []
            # 按 channel_id 回填（顺序可能与请求不同）
            ts_by_ch = {}
            for e in lst:
                cid = e.get("channel_id")
                ts_by_ch[cid] = L.A.pick_reserve_ts({"available_reserved_period_list": [e]},
                                                    warehouse_id=info["warehouse_id"],
                                                    channel_id=cid)
            for po, ch, it0 in probes:
                ts = ts_by_ch.get(ch.get("channel_id"))
                if ts:
                    it = L.A.build_send_item(ch, meta[po]["goods"], dims, info, ts)
                else:
                    it = L.A.build_send_item(ch, meta[po]["goods"], dims, info, (0, 0))
                    it.pop("reserve_time_period", None)
                    it["auto_reserve"] = True
                    it["agree_collection_confirm"] = True
                ready[po] = it

    result = []
    for po in pos:
        m = meta.get(po) or {}
        if po in ready:
            result.append((po, ready[po], ""))
        else:
            result.append((po, None, m.get("why") or m.get("rebuild") and "需换仓" or "未准备"))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    items = {it["parentOrderSn"]: it
             for it in (O.fetch_page("BHY", size=100, pos=POS).get("items") or [])}
    print("拉到 %d / %d 单" % (len(items), len(POS)))

    # ① 批量准备（快）
    t0 = time.time()
    prepared = prepare_many("BHY", POS, items, DIMS)
    print("批量准备耗时 %.1fs" % (time.time() - t0))

    ok_pairs, need_fallback = [], []
    for po, it, why in prepared:
        if it:
            ok_pairs.append((po, it))
            print("  ✅ %s 仓渠道已准备 $%.2f"
                  % (po, (it.get("expect_shipping_fee") or 0) / 1000))
        else:
            need_fallback.append(po)
            print("  ↩ %s 交给 place_one（%s）" % (po, why))

    total = sum((it.get("expect_shipping_fee") or 0) for _, it in ok_pairs) / 1000
    print("\n可直接提交 %d 单（$%.2f）｜需兜底 %d 单"
          % (len(ok_pairs), total, len(need_fallback)))

    if not a.apply:
        print("（dry-run，未提交）")
        return 0

    # ② 逐单提交（保证粒度）
    wins, fails = 0, 0
    for po, it in ok_pairs:
        r = A.send("BHY", [it])
        j = r.get("json") or {}
        if j.get("success"):
            res = j.get("result") or {}
            print("  ✅ %s → %s" % (po, (res.get("package_sn_list") or [""])[0]))
            wins += 1
        else:
            msg = str(j.get("error_msg") or j.get("error_code") or "")
            print("  ❌ %s → %s" % (po, msg[:60]))
            # 重量类错误交给 place_one 重试（它带回填）
            if "磅" in msg:
                need_fallback.append(po)
            else:
                fails += 1

    # ③ 兜底：交给 place_one（含换仓 / 免预约 / 重量回填 / 预售判定）
    for po in need_fallback:
        it0 = items.get(po) or {}
        goods = [{"order_sn": g["orderSn"], "parent_order_sn": po,
                  "goods_id": g["goodsId"], "sku_id": str(g["skuId"]),
                  "quantity": g.get("quantity", 1)} for g in (it0.get("goods") or [])]
        if not goods:
            print("  ❌ %s 无 goods" % po)
            fails += 1
            continue
        r = L.place_one("BHY", goods, DIMS, "加州三仓", apply=True, log=print)
        if r.get("ok"):
            print("  ✅ %s → %s（兜底成功）" % (po, r.get("package_sn")))
            wins += 1
        else:
            print("  ❌ %s → %s（兜底也失败）" % (po, r.get("why")))
            fails += 1

    print("\n========== 汇总 ==========")
    print("成功 %d / 失败 %d / 共 %d" % (wins, fails, len(POS)))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
