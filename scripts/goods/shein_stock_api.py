# -*- coding: utf-8 -*-
"""shein_stock_api.py — SHEIN 商家库存 OpenAPI：查询 + 改库存（替代人工导表）。

用到的两个官方接口（2026-11 起）：
  查询  POST /open-api/stock/stock-query        （doc 3001695，skuCodeList ≤100）
  改库  POST /open-api/stock/change-inventory/v2（doc 3001738，10 个/批 ≤100）

用法：
    # 只查（读回核对）
    python scripts\\goods\\shein_stock_api.py query --store BAN --sku I33un8ageq8d I2mvavfg9ygghg

    # 按清零清单改库存（默认 dry-run，只打印请求体）
    python scripts\\goods\\shein_stock_api.py apply --store BAN ^
        --plan "D:\\together\\全托管\\SHEIN\\BD-库存\\清零清单.xlsx" [--apply]

    # 单条覆盖为 0
    python scripts\\goods\\shein_stock_api.py apply --store BAN --sku I33un8ageq8d ^
        --warehouse PS726492654714 [--apply]

改库语义（changeType）：
    ADD 增加 / SUB 减少（最多减到可售数量）/ OVERWRITE 覆盖总销售库存
清零用 OVERWRITE + changeQuantity=0：平台会把总销售库存压到
「预占 + 占用」的下限，即可售 = 0，且不会因为中间卖出而报负数。

⚠ 主体必须对：SKU 不属于该 openKey 的 supplier 会返回
   00-E-MSC-0006 "... is not allowed to be operated by this supplier"。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

SKILL_SCRIPTS = Path(r"C:\Users\saing\.agents\skills\shein-buylabel\scripts")
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

import shein_client as sc  # noqa: E402

CONFIG = Path(r"C:\Users\saing\.agents\skills\shein-buylabel\config.json")
QUERY_PATH = "/open-api/stock/stock-query"
CHANGE_PATH = "/open-api/stock/change-inventory/v2"
BATCH = 100


def keys(store: str | None):
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    seg = cfg.get("shein_openapi") or {}
    kid, sec, name = sc.get_keys(seg, store)
    if not kid:
        raise SystemExit("config.json 里没有店铺 %r 的 openKeyId/secretKey" % store)
    return kid, sec, name


def query(sku_codes: list[str], store: str | None, inv_type: str = "VI",
          warehouse_type: str = "2") -> dict[str, dict]:
    """返回 {skuCode: {total, usable, locked, warehouses:{code:{inv,usable,locked}}}}。"""
    kid, sec, _ = keys(store)
    out: dict[str, dict] = {}
    for i in range(0, len(sku_codes), BATCH):
        chunk = sku_codes[i:i + BATCH]
        r = sc.signed_post(sc.API_BASE, QUERY_PATH, kid, sec,
                           {"skuCodeList": chunk, "warehouseType": warehouse_type,
                            "invType": inv_type}, retry=2)
        if str(r.get("code")) != "0":
            raise SystemExit("查询失败 code=%s msg=%s" % (r.get("code"), r.get("msg")))
        info = r.get("info")
        blocks = info if isinstance(info, list) else [info]
        for b in blocks or []:
            for g in (b or {}).get("goodsInventory") or []:
                for s in g.get("skuList") or []:
                    code = s.get("skuCode")
                    out[code] = dict(
                        spu=g.get("spuName"), skc=g.get("skcName"),
                        total=s.get("totalInventoryQuantity"),
                        usable=s.get("totalUsableInventory"),
                        locked=s.get("totalLockedQuantity"),
                        warehouses={w.get("warehouseCode"): dict(
                            inv=w.get("inventoryQuantity"), usable=w.get("usableInventory"),
                            locked=w.get("lockedQuantity"))
                            for w in s.get("warehouseInventoryList") or []},
                    )
    return out


def _idem(sku: str) -> str:
    return "zero-%s-%d-%04d" % (sku, int(time.time()), random.randint(0, 9999))


def change(rows: list[dict], store: str | None, change_type: str = "OVERWRITE",
           apply: bool = False) -> list[dict]:
    """rows = [{sku, warehouse, quantity, reason}]。返回逐条结果。"""
    kid, sec, _ = keys(store)
    reqs = []
    for r in rows:
        item = {"idempotencyKey": _idem(r["sku"]), "skuCode": r["sku"], "invType": "VI",
                "changeType": change_type, "changeQuantity": int(r["quantity"])}
        if r.get("warehouse"):
            item["warehouseCode"] = r["warehouse"]
        if r.get("reason"):
            item["changeReason"] = r["reason"]
        reqs.append(item)

    results = []
    for i in range(0, len(reqs), BATCH):
        chunk = reqs[i:i + BATCH]
        if not apply:
            results.append({"dryRun": True, "requests": chunk})
            continue
        resp = sc.signed_post(sc.API_BASE, CHANGE_PATH, kid, sec,
                              {"updateSkuInventoryQuantityRequests": chunk}, retry=1)
        info = resp.get("info")
        blocks = info if isinstance(info, list) else [info]
        failed = []
        for b in blocks or []:
            if isinstance(b, dict):
                failed.extend(b.get("failedList") or [])
        for req in chunk:
            f = next((x for x in failed if x.get("skuCode") == req["skuCode"]), None)
            results.append({"sku": req["skuCode"], "ok": f is None,
                            "code": (f.get("code") if f else resp.get("code")),
                            "msg": (f.get("reason") if f else resp.get("msg")),
                            "traceId": resp.get("traceId")})
    return results


def load_plan(path: str, only_positive: bool = True) -> list[dict]:
    import openpyxl
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(x or "").strip() for x in rows[0]]
    ix = {h: i for i, h in enumerate(hdr)}
    out = []
    for r in rows[1:]:
        sku = r[ix["平台SKU"]]
        if not sku:
            continue
        avail = float(r[ix["SHEIN可用"]] or 0)
        if only_positive and avail <= 0:
            continue
        out.append(dict(sku=str(sku).strip(), seller=r[ix["商家SKU"]],
                        warehouse=str(r[ix["仓库"]] or "").strip() or None,
                        quantity=0, avail=avail, reason="可售<=3天清零(API)"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SHEIN 商家库存 OpenAPI 查询/改库")
    sub = ap.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("query", help="查询库存")
    q.add_argument("--store", default="BAN")
    q.add_argument("--sku", nargs="+", required=True)
    q.add_argument("--json", action="store_true")

    c = sub.add_parser("apply", help="改库存（默认 dry-run）")
    c.add_argument("--store", default="BAN")
    c.add_argument("--plan", default="")
    c.add_argument("--sku", nargs="+", default=None)
    c.add_argument("--warehouse", default=None)
    c.add_argument("--quantity", type=int, default=0)
    c.add_argument("--change-type", default="OVERWRITE", choices=["OVERWRITE", "ADD", "SUB"])
    c.add_argument("--apply", action="store_true", help="真的提交；不带就是 dry-run")
    a = ap.parse_args(argv)

    if a.cmd == "query":
        got = query(a.sku, a.store)
        if a.json:
            print(json.dumps(got, ensure_ascii=False, indent=1))
            return 0
        miss = [s for s in a.sku if s not in got]
        for s in a.sku:
            v = got.get(s)
            if not v:
                print("%-16s 未返回（不属于该主体？）" % s); continue
            wh = ", ".join("%s=%s/%s" % (k, w["inv"], w["usable"]) for k, w in v["warehouses"].items())
            print("%-16s 总%-6s 可用%-6s 锁定%-4s | %s" % (s, v["total"], v["usable"], v["locked"], wh))
        if miss:
            print("未返回 %d 个：%s" % (len(miss), ", ".join(miss)))
        return 0

    # apply
    if a.plan:
        rows = load_plan(a.plan)
        print("[plan] 需清零 %d 行 ← %s" % (len(rows), a.plan))
    else:
        rows = [dict(sku=s, warehouse=a.warehouse, quantity=a.quantity,
                     reason="manual %s" % a.change_type) for s in a.sku]
        print("[cli] %d 行" % len(rows))

    before = query([r["sku"] for r in rows], a.store)
    for r in rows:
        b = before.get(r["sku"]) or {}
        # 仓库码：显式给了且像编码就用它；否则自动取「有可用库存」的那个仓
        wh = r.get("warehouse")
        if not wh or not str(wh).upper().startswith("PS"):
            whs = {k: v for k, v in (b.get("warehouses") or {}).items() if (v.get("usable") or 0) > 0}
            if whs:
                wh = max(whs.items(), key=lambda kv: kv[1]["usable"])[0]
            elif b.get("warehouses"):
                wh = next(iter(b["warehouses"]))
            else:
                wh = None
        r["warehouse"] = wh
        if b:
            print("   [before] %-16s 总%-6s 可用%-6s 锁定%-4s 仓=%s"
                  % (r["sku"], b.get("total"), b.get("usable"), b.get("locked"), wh))
    for r in rows:
        print("   %-16s %-18s → %s" % (r["sku"], r.get("warehouse") or "(不带仓)", r["quantity"]))

    # 线上已经 0 的不再提交（xlsx 可能是旧快照）
    skip = [r for r in rows if (before.get(r["sku"]) or {}).get("usable") == 0]
    if skip:
        print("[skip] 线上已为 0，不重复提交：%s" % ", ".join(r["sku"] for r in skip))
        rows = [r for r in rows if r not in skip]
    if not rows:
        print("[done] 没有需要改的行")
        return 0

    res = change(rows, a.store, a.change_type, apply=a.apply)
    if not a.apply:
        print("[dry-run] 请求体：")
        print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
        print("[dry-run] 加 --apply 才会真的提交")
        return 0

    for x in res:
        print("   [%s] %-16s %s %s" % ("OK" if x["ok"] else "FAIL", x["sku"], x["code"], x["msg"]))

    after = query([r["sku"] for r in rows], a.store)
    print("---- 回读 ----")
    bad = 0
    for r in rows:
        v = after.get(r["sku"]) or {}
        before_usable = (before.get(r["sku"]) or {}).get("usable") or 0
        # 期望值按 changeType 算：OVERWRITE=覆盖值；ADD=原值+增量；SUB=原值-减量
        if a.change_type == "OVERWRITE":
            expect = int(r["quantity"])
        elif a.change_type == "ADD":
            expect = before_usable + int(r["quantity"])
        else:
            expect = max(0, before_usable - int(r["quantity"]))
        u = v.get("usable")
        ok = (u == expect)
        bad += 0 if ok else 1
        print("   %-16s 可用%-6s 期望%-6s 总%-6s 锁定%-4s %s" % (
            r["sku"], u, expect, v.get("total"), v.get("locked"), "✓" if ok else "✗ 不符"))
    print("[result] 提交 %d 行，失败 %d，回读不符 %d" % (len(rows), sum(1 for x in res if not x["ok"]), bad))
    return 0 if bad == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
