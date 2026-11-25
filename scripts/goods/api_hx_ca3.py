# -*- coding: utf-8 -*-
"""恒信 / 加州3 / Kansas 三仓目标值 + 接口执行（BEL / KLS / BHY 通用）。

用户 2026-11-23 最终口径：
    恒信仓   = max(0, ⌈领星可用 × 80%⌉)
    加州3仓  = max(0, ⌈帕沃尔加州3可用 × 80%⌉)
    Kansas   = 0                       ← 直接清零
    例外：恒信与加州3 **两边都算出 0** 的（货号-尺码）不自动执行，
          整理成清单交人工确认（一边 0、一边有值 → 正常执行）。

执行走接口（复用 api_hx_stock 的链路）：
    skc/pageQuery → queryBtgProductStockInfo → preCheckWarehouseInfo → updateMmsBtgProductSalesStock → 回读

用法：
    python scripts/goods/api_hx_ca3.py --port 63317 --store BEL           # 只看
    python scripts/goods/api_hx_ca3.py --port 63317 --store BEL --apply   # 真写
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import api_hx_stock as HX  # noqa: E402
import api_switch_mode as api  # noqa: E402
import wh_map  # noqa: E402

DATA = ROOT / "temu-admin" / "public" / "data"
SNAP = ROOT / "data" / "stock-snapshot"
LOGS = ROOT / "_logs"
LEDGER = LOGS / "api_hx_ca3.jsonl"
RATIO = 0.8
#: ★ 2026-11-25 用户：「BHY 和 KLS 在库存基础上加库存可以，但是不能清零和减到<5」
#:   ⇒ 这两店**只加不减**、目标值不低于 SAFE_MIN；BEL 才允许真正的改值/清零。
NO_CUT_STORES = ("BHY", "KINGLAMANSPORT", "KLS")
SAFE_MIN = 5


def is_no_cut(store: str) -> bool:
    return str(store or "").upper() in NO_CUT_STORES


CANON = {"KLS": "KinglamanSport"}


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False)[:420], flush=True)


def load_index(path: Path, wh_kw: str, field: str) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    out: dict = {}
    for it in d.get("items") or []:
        if wh_kw and wh_kw not in str(it.get("wh") or ""):
            continue
        code = str(it.get("code") or "")
        var = str(it.get("variant") or "")
        size = str(it.get("size") or "").upper()
        if not code or not size:
            continue
        k = (("%s-%s" % (code, var)).upper(), size)
        out[k] = out.get(k, 0.0) + float(it.get(field) or 0)
    return out


def build_plan(store: str, day: str) -> tuple:
    #: 只保留 S 码、其余尺码三个仓一律清零的货号（用户 2026-11-24，BEL/KLS 的 204605）。
    ONLY_S_KEEP = {"204605-3BHP": "S"}
    lx = load_index(DATA / "linxing-stock.json", "", "avail")
    ca3 = load_index(DATA / "pw-stock.json", "加州3仓", "stock")
    snap = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    rows = ((snap.get("stores") or {}).get(CANON.get(store.upper(), store)) or {}).get("rows") or []
    plan, zero = {}, []
    for r in rows:
        code = str(r.get("code") or "")
        size = str(r.get("size") or "").upper()
        base = code.split("-", 1)[1] if "-" in code else code
        k = (base.upper(), size)
        la = float(lx.get(k) or 0)
        pa = float(ca3.get(k) or 0)
        # ★ 规则：ONLY_S_KEEP 里的货号，只有指定尺码按 80% 算，其余尺码**三个仓全部清零**
        keep_size = ONLY_S_KEEP.get(base.upper())
        if keep_size and size != keep_size:
            plan.setdefault(code, {"spu": str(r.get("spu") or ""), "sizes": {}})
            plan[code]["sizes"][size] = {"hx": 0, "ca3": 0}
            continue
        hx = int(math.ceil(la * RATIO)) if la > 0 else 0
        ca = int(math.ceil(pa * RATIO)) if pa > 0 else 0
        # ★ 2026-11-25 用户红线：「BHY 和 KLS 在库存基础上加库存可以，但是不能清零和减到<5」
        #   这两店的目标值**下限抬到 5**：算出来 1~4 一律按 5 写，算出来 0 时不清零，
        #   改为「不写」（保持平台现值，交给面板「待加库存」）。BEL 不受此限。
        if str(store or "").upper() in ("BHY", "KINGLAMANSPORT", "KLS"):
            if hx == 0 and ca == 0:
                zero.append({"code": code, "size": size, "spu": str(r.get("spu") or ""),
                             "lx": round(la, 1), "pw": round(pa, 1),
                             "note": "该店禁清零 ⇒ 不写，进「待加库存」"})
                continue
            if 0 < hx < SAFE_MIN:
                hx = SAFE_MIN
            if 0 < ca < SAFE_MIN:
                ca = SAFE_MIN
        # ★ 保留尺码：领星为 0 时，把量放到加州3仓（用帕沃尔可用），不要写进恒信
        if keep_size and size == keep_size and hx == 0:
            ca = int(math.ceil(pa * RATIO)) if pa > 0 else 0
        if hx == 0 and ca == 0:
            zero.append({"code": code, "size": size, "spu": str(r.get("spu") or ""),
                         "lx": round(la, 1), "pw": round(pa, 1)})
            continue
        plan.setdefault(code, {"spu": str(r.get("spu") or ""), "sizes": {}})
        plan[code]["sizes"][size] = {"hx": hx, "ca3": ca}
    return plan, zero


async def do_code(cap, hdr, code, info, apply, store="", no_cut=False):
    q = await HX.call(cap, HX.URL_SKC, {"fuzzySkuExtCodes": [code], "page": 1, "pageSize": 20}, hdr)
    if q.get("status") != 200:
        log({"event": "skc-fail", "code": code, "status": q.get("status")})
        return "no-header" if q.get("status") == 403 else "fail"
    items = ((q.get("json") or {}).get("result") or {}).get("pageItems") or []
    if not items:
        log({"event": "skc-empty", "code": code})
        return "fail"
    it = items[0]
    spu = str(it.get("productId"))
    sku_size = {}
    for s in it.get("productSkuSummaries") or []:
        ext = str(s.get("extCode") or "")
        sz = ext.rsplit("-", 1)[-1].upper() if "-" in ext else ""
        if s.get("productSkuId") and sz:
            sku_size[int(s["productSkuId"])] = sz
    r = await HX.call(cap, HX.URL_QUERY, {"productId": int(spu),
                                          "productSkuIdList": sorted(sku_size)}, hdr)
    rows = []
    HX.walk_stock(r.get("json") or {}, rows)
    change, detail = [], []
    for x in rows:
        sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
        if not sz or sz not in info["sizes"]:
            continue
        wh = x.get("wh") or ""
        t = info["sizes"][sz]
        # ★ 仓归属按 wh_map（仓 id 优先），不再用仓名子串：
        #   BEL 的「恒信加州3仓」含「加州3仓」，子串匹配会把它当帕沃尔仓（踩过）。
        sid = wh_map.side_by_id(store, str(x.get("whid") or "")) or \
            wh_map.side_by_name(store, wh)
        if sid == "hx":
            target = t["hx"]
        elif sid == "ca3":
            target = t["ca3"]
        elif sid == "ks":
            target = 0
        else:
            continue
        cur = int(x["stock"])
        # ★ 2026-11-25 用户：「不能清零和减到<5」⇒ 这两店**只加不减**，
        #   且目标值不低于 5（哪怕是 kansas 的 0 也不动）。
        if no_cut:
            if target <= cur:
                continue                  # 只加不减；相等也不必写
            target = max(target, SAFE_MIN)
        diff = target - cur
        if diff:
            change.append({"productSkuId": int(x["skuId"]), "stockDiff": diff,
                           "currentStockAvailable": cur,
                           "currentShippingMode": 1, "warehouseId": x["whid"]})
            detail.append((sz, wh, cur, target, diff))
    if not change:
        return "nothing"
    print("  %s 计划 %s" % (code, detail), flush=True)
    if not apply:
        return "dry"
    pre = await HX.call(cap, HX.URL_CHECK, {"productId": int(spu), "preCheckSkuWarehouseList": [
        {"productSkuId": c["productSkuId"], "warehouseId": c["warehouseId"]} for c in change]}, hdr)
    up = await HX.call(cap, HX.URL_UPDATE, {"productId": int(spu), "skuStockChangeList": change,
                                            "skuTypeChangeList": [], "isCheckVersion": True,
                                            "source": 0}, hdr)
    good = bool((up.get("json") or {}).get("success"))
    # detail 逐条记 [尺码, 仓库, 改动前, 目标, 差额]，供事后出「修改明细」表
    log({"event": "write", "code": code, "ok": good, "n": len(change),
         "detail": [[d[0], d[1], d[2], d[3], d[4]] for d in detail],
         "precheck": bool((pre.get("json") or {}).get("success")),
         "resp": str(up.get("body"))[:100]})
    await asyncio.sleep(0.5)
    r2 = await HX.call(cap, HX.URL_QUERY, {"productId": int(spu),
                                           "productSkuIdList": sorted(sku_size)}, hdr)
    rows2 = []
    HX.walk_stock(r2.get("json") or {}, rows2)
    got = {}
    for x in rows2:
        sz = sku_size.get(int(x["skuId"])) if x.get("skuId") else None
        if sz:
            got.setdefault(sz, {})[x["wh"]] = int(x["stock"])
    log({"event": "verify", "code": code, "ok": good, "sample": str(got)[:200]})
    return "ok" if good else "fail"


async def main_async(a):
    cap0 = await HX.attach(a.port)
    if cap0 is None:
        print("没有可用页面")
        return 3
    # 头按店取：先读本店 mallid，再取 edit_headers_<mallid>.json（三店 mallid 不同）
    mallid = ""
    try:
        r = await cap0.send("Runtime.evaluate",
                            {"expression": "function c(n){var m=document.cookie.match(new RegExp('(^| )'+n+'=([^;]+)'));return m?m[2]:'';}c('mallid')",
                             "returnByValue": True}, timeout=15)
        mallid = str((r.get("result") or {}).get("value") or "")
    except Exception:
        pass
    hdr = api.load_headers(mallid)
    print("[头] %s mallid=%s anti=%d chars" % (a.store, mallid or "?", len(hdr.get("Anti-Content") or "")),
          flush=True)
    if not hdr.get("Anti-Content"):
        print("!! 该店没有 Anti-Content 头（_logs/edit_headers_%s.json）" % (mallid or "<mallid>"))
        try:
            await cap0.__aexit__(None, None, None)
        except Exception:
            pass
        return 3
    if a.refresh:
        print("[①] 刷新领星 + 帕沃尔库存…", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "goods" / "refresh_inventory.py")],
                       cwd=str(ROOT))
    snap = SNAP / ("%s.json" % a.date)
    if not snap.exists() or a.resnapshot:
        print("[②] 重建快照…", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "goods" / "daily_stock_snapshot.py"),
                        "--stores", a.store], cwd=str(ROOT))

    plan, zero = build_plan(a.store, a.date)
    # 该店的套装映射（含 hold 标记：组合套装货号库存不动）
    HXRULES = {}
    rp = LOGS / ("套装映射_%s.json" % a.store.upper())
    if rp.exists():
        try:
            for x in json.loads(rp.read_text(encoding="utf-8")).get("rules") or []:
                HXRULES[str(x.get("key")).upper()] = x
        except Exception:
            HXRULES = {}
    (LOGS / ("%s_zero_confirm.json" % a.store.lower())).write_text(
        json.dumps(zero, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[③] 货号 %d 个可写 / 待确认清零 %d 条" % (len(plan), len(zero)), flush=True)

    cap = cap0          # 复用上面已经连上的页面（避免重复 attach）
    stat = {}
    try:
        # 组合套装标记为 hold 的货号：库存不动，直接跳过（用户 2026-11-24）
        hold_keys = {k for k, v in (HXRULES or {}).items() if v.get("hold")}
        for code in sorted(plan):
            # --code 过滤：只跑指定货号（避免误跑全店，2026-11-24 踩过）
            if a.code and a.code not in code:
                continue
            base = code.split("-", 1)[1] if "-" in code else code
            if base.upper() in hold_keys:
                print("  %s 属组合套装 → 库存不动，跳过" % code, flush=True)
                stat["hold"] = stat.get("hold", 0) + 1
                continue
            r = await do_code(cap, hdr, code, plan[code], a.apply,
                              store=a.store, no_cut=is_no_cut(a.store))
            stat[r] = stat.get(r, 0) + 1
            if r == "no-header":
                print("!! Anti-Content 失效")
                break
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    print("\n结果：", stat)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", default="BEL")
    ap.add_argument("--date", default=str(date.today()))
    ap.add_argument("--refresh", action="store_true", help="先刷新领星+帕沃尔")
    ap.add_argument("--resnapshot", action="store_true", help="重建当天快照")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--code", default="", help="只处理包含该子串的货号（强烈建议指定，避免跑全店）")
    a = ap.parse_args()
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    raise SystemExit(main())
