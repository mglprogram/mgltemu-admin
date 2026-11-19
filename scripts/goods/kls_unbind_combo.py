# -*- coding: utf-8 -*-
"""KLS「绑定并切换合作仓发货」误点收敛：把这 16 个组合品的卖家自发货切回合作对接仓发货。

来源：用户 2026-11-19 明确「组合品弹窗不许点绑定并切换合作仓发货」，
但 16:01 之前的批次 `--combo bind` 已经点了 16 次。全部是 KLS，仓只剩 加州3仓（已被清零做准备）。

做法（走接口，和弹框无关）：
    POST /visage-agent-seller/product/sku/shippingMode/edit
    body={productId, productSkuId, shippingMode:2, warehouseStockList:[{加州3仓, 0}]}
    ⚠ 库存 0 会被 6000074 系统异常拒 ⇒ 先传 1，再单独把加州3仓清零。

先跑不带 --apply 看会发什么。
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
import cdp_net  # noqa: E402
import goto_spu as gs  # noqa: E402

LOGS = Path(__file__).resolve().parents[2] / "_logs"
LEDGER = LOGS / "kls_unbind.jsonl"
WH_CA = "WH-05667065528452987"   # KLS 加州3仓

JOB = ["8973767712", "5294187883", "3859449752", "4889708872", "1372607060",
       "2370406545", "2505107144", "9395714940", "9518412982", "1605073877",
       "3821300125", "9297120240", "5310214245", "8094598348", "8029937893",
       "8950499015"]

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


async def main_async(a):
    code_map = json.loads((LOGS / "kls_spu_code.json").read_text(encoding="utf-8"))
    cap = await gs.focus_list_page(a.port)
    if cap is None:
        print("没有商品列表页")
        return 2
    try:
        for spu in JOB:
            code = code_map.get(spu, "")
            ok, detail = await gs.goto_spu(cap, spu, expect_code=code)
            if not ok:
                log({"event": "spu-fail", "spu": spu, "code": code, "why": detail})
                continue
            rows = json.loads(await gs.ev(cap, JS_ROWS) or "[]")
            rows = [r for r in rows if r.get("size") and r.get("skuId")]
            log({"event": "spu", "spu": spu, "code": code,
                 "rows": [(r["size"], r["mode"]) for r in rows]})
            for r in rows:
                if r["mode"] != "卖家自发货":
                    log({"event": "skip", "spu": spu, "size": r["size"], "mode": r["mode"]})
                    continue
                if not a.apply:
                    log({"event": "would", "spu": spu, "code": code, "size": r["size"],
                         "skuId": r["skuId"], "to": "合作对接仓发货"})
                    continue
                res = await api.call_edit(cap, spu, r["skuId"], [(WH_CA, 1)])
                body = str(res.get("body") or "")
                ok2 = '"success":true' in body
                log({"event": "ok" if ok2 else "fail", "spu": spu, "size": r["size"],
                     "skuId": r["skuId"], "body": body[:180]})
                if "No Permission" in body:
                    log({"event": "abort", "why": "Anti-Content 头失效"})
                    return 4
                await asyncio.sleep(0.6)
            await asyncio.sleep(1.2)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    log({"event": "done-all"})
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
