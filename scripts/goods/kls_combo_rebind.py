# -*- coding: utf-8 -*-
"""把这 16 个 KLS 组合品从「合作对接仓发货」切回「卖家自发货」，并回填库存。

背景：2026-11-19 批次误点了「绑定并切换合作仓发货」，用户要求全部切回。
数据来源：
  · SPU/货号 ← `_logs/kls_combo_bind_list.json`
  · 原库存   ← `_logs/kls_blocked_skus.json`（这批款的原库存表；没有就用 1）

做法：列表按 SPU 查 → 读每个尺码的 shippingMode 行 → 对「合作对接仓发货」的行调接口
      `POST .../sku/shippingMode/edit`，shippingMode=1，warehouseStockList=[{加州3仓, 原库存}]
      （接口不接受 0 ⇒ 0 按 1 提交；传完再单独清零的话由用户后续决定）

用法：
    python scripts/goods/kls_combo_rebind.py --port 64936            # 只看
    python scripts/goods/kls_combo_rebind.py --port 64936 --apply    # 真改
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "power" / "lib"))

import api_switch_mode as api  # noqa: E402
import goto_spu as gs  # noqa: E402

LOGS = Path(__file__).resolve().parents[2] / "_logs"
LEDGER = LOGS / "kls_combo_rebind.jsonl"
WH_CA = "WH-05667065528452987"     # KLS 加州3仓

JS_ROWS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
Array.prototype.slice.call(document.querySelectorAll('tbody tr')).forEach(function (tr) {
  var s = t(tr);
  var m = s.match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  if (!m) return;
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var mode = '';
  cells.forEach(function (c) { if (c === '卖家自发货' || c === '合作对接仓发货') mode = c; });
  var nums = cells.filter(function (c) { return /^\d{6,}$/.test(c); });
  out.push({size: m[1], mode: mode, skuId: nums[0] || ''});
});
return JSON.stringify(out);"""


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def load_stock() -> dict:
    m = {}
    for name in ("kls_blocked_skus.json", "bel_blocked_skus2.json"):
        p = LOGS / name
        if not p.exists():
            continue
        for it in json.loads(p.read_text(encoding="utf-8")):
            m[it["sku"]] = int(it["stock"])
    return m


async def main_async(a):
    jobs = json.loads((LOGS / "kls_combo_bind_list.json").read_text(encoding="utf-8"))
    stock = load_stock()
    cap = await gs.focus_list_page(a.port)
    if cap is None:
        print("没有商品列表页")
        return 2
    ok_n = fail_n = 0
    try:
        for j in jobs:
            spu, code = j["spu"], j["code"]
            ok, detail = await gs.goto_spu(cap, spu, expect_code=code)
            if not ok:
                log({"event": "spu-fail", "spu": spu, "code": code, "why": detail})
                continue
            rows = [r for r in json.loads(await gs.ev(cap, JS_ROWS) or "[]")
                    if r.get("size") and r.get("skuId")]
            log({"event": "spu", "spu": spu, "code": code,
                 "rows": [(r["size"], r["mode"]) for r in rows]})
            for r in rows:
                if r["mode"] != "合作对接仓发货":
                    log({"event": "skip", "code": code, "size": r["size"], "mode": r["mode"]})
                    continue
                sku = "%s-%s" % (code, r["size"])
                base = stock.get(sku, 1)
                send = base if base > 0 else 1
                if not a.apply:
                    log({"event": "would", "sku": sku, "skuId": r["skuId"], "stock": send})
                    continue
                res = await api.call_edit(cap, spu, r["skuId"], [(WH_CA, send)])
                body = str(res.get("body") or "")
                if '"success":true' in body:
                    ok_n += 1
                    log({"event": "ok", "sku": sku, "skuId": r["skuId"], "stock": send})
                else:
                    fail_n += 1
                    log({"event": "fail", "sku": sku, "body": body[:160]})
                    if "No Permission" in body:
                        log({"event": "abort", "why": "Anti-Content 头失效"})
                        return 4
                await asyncio.sleep(0.5)
            if a.apply:
                await asyncio.sleep(1.5)
                await gs.click(cap, json.loads(await gs.ev(cap, gs.JS_QUERY_XY)))
                await asyncio.sleep(2.5)
                rows2 = [r for r in json.loads(await gs.ev(cap, JS_ROWS) or "[]")
                         if r.get("size") and r.get("skuId")]
                log({"event": "verify", "code": code,
                     "left": [r["size"] for r in rows2 if r["mode"] != "卖家自发货"]})
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    log({"event": "summary", "ok": ok_n, "fail": fail_n})
    return 0 if fail_n == 0 else 5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
