#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hx_ship_api.py — 恒信（HX）领星 OMS 发货 **走 OpenAPI**（替代 2026-11-11 那套页面自动化）。

用户 2026-11-12：「把昨天去领星发货的 skill 替代成 api 接口」。

为什么换 API（昨天的真机结论）
------------------------------
页面那条路（`hx_oms_ship.py`）在 11-12 凌晨连炸 4 轮：OMS 列表页改版后
输入框/每页条数/列顺序全变，无头会话里「分配仓库&物流」按钮还点不开。
而 OpenAPI 直接就能建出库单：

    POST /openapi/v1/outboundOrder/create
    data = [ {thirdOrderNo, orderNo, whCode, logisticsChannel,
              productList:[{sku, quantity}]} ]
    → 逐单返回 {thirdOrderNo, success, msg}

链路（等价 UI 的「分配仓库&物流 → 确定并审核」）
------------------------------------------------
    ① sku_map  拉库存清单，建「平台产品名 → 系统SKU」映射（只读，落 data/oms_sku_map.json）
    ② prepare  本批单号 → 解析系统SKU；已有出库单的跳过；配不到 SKU 的标「未配对」
    ③ create   分批（≤50）建出库单 → 逐单回执
    ④ verify   按 thirdOrderNo 回读 outboundOrder：status 2 仓库处理中 / 3 已出库 = 成功，
               顺带取 logisticsTrackNo
    ⑤ summary  成功/失败/未配对 三类清单落 json

用法
----
    python scripts/oms/hx_ship_api.py --split <split.json> --out <dir>            # 预览
    python scripts/oms/hx_ship_api.py --split <split.json> --out <dir> --apply    # 真建单
    python scripts/oms/hx_ship_api.py --pos-file <txt> --apply                    # 只给单号

退出码：0=全部有终态；42=有失败/未配对/未回读到；2=参数问题。

⚠ 硬前提：**产品必须已在 OMS 建好并审核通过**（`productList[].sku` 是系统SKU，
  不是平台SKU）。没配上的会回「传入产品sku有误或产品未通过审核」，本脚本把它归到
  「未配对」交人工 —— 不要反复重试，那是主数据问题。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from oms_client import OmsClient, OmsError  # noqa: E402

CFG = os.path.join(HERE, "oms_accounts.local.json")
MAP_FILE = os.path.join(HERE, "data", "oms_sku_map.json")

#: 恒信仓（skill 定稿：ZOO WAREHOUSE ⑥美西）+ 上传物流面单渠道
WH_CODE = "USW01"
CHANNEL = "Upload_Shipping_Label"

#: 出库单状态：2 仓库处理中 / 3 已出库 = 成功
STATUS_TEXT = {0: "草稿", 1: "待处理", 2: "仓库处理中", 3: "已出库",
               4: "已取消", 5: "异常"}
OK_STATUS = (2, 3)


def log(s: str) -> None:
    print(str(s), flush=True)


def client() -> OmsClient:
    acc = json.load(io.open(CFG, encoding="utf-8"))["accounts"]
    lm = next(a for a in acc if a["tag"] == "LM739")
    return OmsClient(lm["appKey"], lm["appSecret"], verbose=False)


# ---------------------------------------------------------------- ① SKU 映射

def refresh_sku_map(c: OmsClient) -> dict:
    """库存清单 → {平台产品名(大写): {sku, productName}}（纯只读，落盘）。

    库存接口 `integratedInventory/pageOpen` 每行同时带 `sku`（系统SKU）与
    `productName`（平台产品名），是当前唯一能拿到的映射来源。
    """
    rows, page = [], 1
    while page <= 60:
        r = c.post("/openapi/v1/integratedInventory/pageOpen",
                   {"pageNo": page, "pageSize": 50})
        d = r.get("data") or {}
        recs = d.get("records") or d.get("list") or []
        if not recs:
            break
        rows += recs
        if len(rows) >= (d.get("total") or 0):
            break
        page += 1
    mp = {}
    for x in rows:
        name = str(x.get("productName") or "").strip()
        sku = str(x.get("sku") or "").strip()
        if name and sku:
            mp[name.upper()] = {"sku": sku, "productName": name}
    os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
    io.open(MAP_FILE, "w", encoding="utf-8").write(
        json.dumps(mp, ensure_ascii=False, indent=1))
    log("   SKU 映射：%d 条（库存行 %d）→ %s" % (len(mp), len(rows), MAP_FILE))
    return mp


def load_sku_map() -> dict:
    try:
        return json.load(io.open(MAP_FILE, encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return {}


# ---------------------------------------------------------------- ② 准备

def load_orders(split: str, pos_file: str) -> dict:
    """→ {po: [{sku, qty}]}；split 走 groups.HX，pos-file 只有单号（SKU 未知）。"""
    agg: dict = {}
    if split and os.path.exists(split):
        d = json.load(io.open(split, encoding="utf-8"))
        for r in ((d.get("groups") or {}).get("HX") or []):
            po = str(r.get("ord") or "").strip()
            if not po:
                continue
            agg.setdefault(po, []).append({"sku": str(r.get("sku") or "").strip(),
                                           "qty": int(r.get("qty") or 1)})
    elif pos_file and os.path.exists(pos_file):
        for ln in io.open(pos_file, encoding="utf-8"):
            po = ln.strip()
            if po:
                agg.setdefault(po, [])
    return agg


def existing_orders(c: OmsClient, days: int = 7) -> dict:
    """近 N 天已有出库单 → {thirdOrderNo: status}（防重复建单）。"""
    start = time.strftime("%Y-%m-%d 00:00:00", time.localtime(time.time() - 86400 * days))
    end = time.strftime("%Y-%m-%d 23:59:59")
    out, page = {}, 1
    while page <= 200:
        r = c.post("/openapi/v1/outboundOrder/pageList", {
            "timeType": "orderCreateTime", "startTime": start, "endTime": end,
            "pageNo": page, "pageSize": 50})
        d = r.get("data") or {}
        recs = d.get("records") or d.get("list") or []
        if not recs:
            break
        for x in recs:
            po = str(x.get("thirdOrderNo") or "").strip()
            if po:
                out[po] = {"status": int(x.get("status") or 0),
                           "outboundOrderNo": x.get("outboundOrderNo"),
                           "track": x.get("logisticsTrackNo") or ""}
        if page * 50 >= (d.get("total") or 0):
            break
        page += 1
    return out


def prepare(agg: dict, mp: dict, exist: dict) -> tuple:
    """→ (可建单 recs, 已存在[, 未配对[, 单号无SKU]])。"""
    ready, dup, unmapped, nosku = [], [], [], []
    for po, items in agg.items():
        if po in exist and int(exist[po].get("status") or 0) != 4:
            dup.append({"po": po, "status": exist[po].get("status"),
                        "outboundOrderNo": exist[po].get("outboundOrderNo")})
            continue
        if not items:
            nosku.append({"po": po, "why": "只给了单号，没有 SKU（给 --split 才能自动建单）"})
            continue
        pl, bad = [], []
        for it in items:
            hit = mp.get(str(it["sku"]).upper())
            if not hit:
                bad.append(it["sku"])
                continue
            pl.append({"sku": hit["sku"], "quantity": int(it["qty"] or 1)})
        if bad:
            unmapped.append({"po": po, "skus": bad,
                             "why": "OMS 里没有该系统SKU/产品未审核通过"})
            continue
        ready.append({"thirdOrderNo": po, "orderNo": po, "whCode": WH_CODE,
                      "logisticsChannel": CHANNEL, "productList": pl})
    return ready, dup, unmapped, nosku


# ---------------------------------------------------------------- ③ 建单

def create(c: OmsClient, recs: list) -> list:
    """分批（≤50）建出库单，返回逐单结果。"""
    out = []
    for i in range(0, len(recs), 50):
        batch = recs[i:i + 50]
        try:
            r = c.post("/openapi/v1/outboundOrder/create", batch)
        except OmsError as ex:
            # 客户端对非 200 code 抛错，但逐单 msg 在 body 里 —— 这里退回逐单重试
            msg = str(ex)
            for rec in batch:
                try:
                    r1 = c.post("/openapi/v1/outboundOrder/create", [rec])
                    out += _rows(r1) or [{"po": rec["thirdOrderNo"], "ok": False,
                                          "msg": str(r1.get("msg"))[:120]}]
                except OmsError as ex1:
                    out.append({"po": rec["thirdOrderNo"], "ok": False,
                                "msg": str(ex1)[:160]})
            log("   ⚠ 整批建单失败，已逐单重试（%s）" % msg[:100])
            continue
        rows = _rows(r)
        if rows:
            out += rows
        else:
            out += [{"po": x["thirdOrderNo"], "ok": True, "msg": ""} for x in batch]
    return out


def _rows(r: dict) -> list:
    d = r.get("data")
    if not isinstance(d, list):
        return []
    out = []
    for x in d:
        out.append({"po": str(x.get("thirdOrderNo") or "").strip(),
                    "ok": bool(x.get("success")),
                    "msg": str(x.get("msg") or "")[:160]})
    return out


# ---------------------------------------------------------------- ④ 回读

def verify(c: OmsClient, pos: list, days: int = 3) -> dict:
    """按 thirdOrderNo 回读状态：2/3 = 成功，并取运单号。"""
    exist = existing_orders(c, days=days)
    res = {}
    for po in pos:
        e = exist.get(po)
        if not e:
            res[po] = {"status": None, "ok": False, "why": "OMS 里查不到该单"}
            continue
        st = int(e.get("status") or 0)
        res[po] = {"status": st, "statusText": STATUS_TEXT.get(st, str(st)),
                   "ok": st in OK_STATUS, "track": e.get("track") or "",
                   "outboundOrderNo": e.get("outboundOrderNo")}
    return res


# ---------------------------------------------------------------- 主流程

def run(split: str, pos_file: str, out_dir: str, apply: bool) -> dict:
    c = client()
    log("[恒信OMS-API] 开始%s" % ("（真建单）" if apply else "（预览，不写入）"))
    mp = refresh_sku_map(c)
    agg = load_orders(split, pos_file)
    if not agg:
        raise SystemExit("[FAIL] 没有单号：给 --split 或 --pos-file")
    log("   本批 %d 单" % len(agg))
    exist = existing_orders(c, days=7)
    ready, dup, unmapped, nosku = prepare(agg, mp, exist)
    log("   可建单 %d｜已存在出库单 %d｜SKU 未配对 %d｜无 SKU %d"
        % (len(ready), len(dup), len(unmapped), len(nosku)))
    for u in unmapped[:10]:
        log("     ✗ 未配对 %-22s %s" % (u["po"], ",".join(u["skus"])[:60]))

    created = []
    if apply and ready:
        created = create(c, ready)
        okn = len([x for x in created if x.get("ok")])
        log("   建单回执：成功 %d / 失败 %d" % (okn, len(created) - okn))
        for x in created[:10]:
            if not x.get("ok"):
                log("     ✗ %-22s %s" % (x["po"], x.get("msg")))

    v = verify(c, list(agg.keys())) if apply else {}
    ok = [p for p, x in v.items() if x.get("ok")]
    bad = [{"po": p, **x} for p, x in v.items() if not x.get("ok")]
    rep = {"store": "HX", "mode": "api", "total": len(agg), "applied": bool(apply),
           "ready": len(ready), "existing": dup, "unmapped": unmapped,
           "no_sku": nosku, "created": created,
           "verify_ok": ok, "verify_bad": bad}
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "hx_oms_ship.json")
    io.open(p, "w", encoding="utf-8").write(json.dumps(rep, ensure_ascii=False, indent=1))
    if apply:
        log("   [回读] 处理中/已出库 %d｜未成 %d → %s" % (len(ok), len(bad), p))
    else:
        log("   [预览] 未写入任何平台状态 → %s" % p)
    return rep


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                        # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="恒信 领星OMS 发货（OpenAPI）")
    ap.add_argument("--split", default="", help="拆仓 split.json（取 groups.HX）")
    ap.add_argument("--pos-file", default="", help="单号 txt（无 SKU，只能回读）")
    ap.add_argument("--out", default="", help="产物目录")
    ap.add_argument("--apply", action="store_true", help="真建出库单")
    a = ap.parse_args()
    out = a.out or os.path.join(HERE, "out", time.strftime("%Y-%m-%d"), "hx_api")
    rep = run(a.split, a.pos_file, out, a.apply)
    if not a.apply:
        return 0
    bad = len(rep.get("verify_bad") or []) + len(rep.get("unmapped") or []) \
        + len(rep.get("no_sku") or [])
    return 0 if bad == 0 else 42


if __name__ == "__main__":
    raise SystemExit(main())
