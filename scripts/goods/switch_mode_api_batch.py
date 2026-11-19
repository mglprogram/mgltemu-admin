# -*- coding: utf-8 -*-
"""接口批量切「卖家自发货」：按 SPU 走一遍列表 → 逐尺码 POST shippingMode/edit → 回读核对。

不点铅笔、不开弹框，全靠 `POST /visage-agent-seller/product/sku/shippingMode/edit`：
    {"productId":<SPU>,"productSkuId":<列表数字列>,"shippingMode":1,
     "warehouseStockList":[{"warehouseId":"WH-...","warehouseStock":<原库存>}]}
需要请求头 `Anti-Content`（从真机抓包取，见 `_logs/edit_headers.json`），缺了会 403。

用法：
    python scripts/goods/switch_mode_api_batch.py --port 59670 --store BEL \
        --wh WH-05667065528452987 --apply
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

import cdp_net  # noqa: E402
import goto_spu as gs  # noqa: E402
import api_switch_mode as api  # noqa: E402

LOGS = Path(__file__).resolve().parents[2] / "_logs"
LEDGER = LOGS / "api_switch.jsonl"

BEL_ORDER = ["1778062806", "1967628638", "2321151464", "2330660256", "2946934746",
             "3715326778", "4515698446", "4832753656", "6249945071", "6484180977",
             "6542860127", "7249832474", "8093849028", "8974123783", "9458759928",
             "9982707958"]
KLS_ORDER = ["5778945108", "1295340533", "9479793069", "4199294070", "8844749868",
             "5512477931", "8279604726", "3493518381", "3399036202", "1417279573",
             "3067607844", "2551302399", "2833390999", "1688226484", "1704574887",
             "7308281892"]

JS_ROWS = r"""
var t = function (e) { return (e.innerText || '').replace(/[ \t\r\n]+/g, ' ').trim(); };
var out = [];
var trs = Array.prototype.slice.call(document.querySelectorAll('tbody tr'));
trs.forEach(function (tr) {
  var s = t(tr);
  var m = s.match(/尺码[:：]\s*([A-Za-z0-9]+)/);
  if (!m) return;
  var cells = Array.prototype.slice.call(tr.querySelectorAll('td')).map(t);
  var mode = '';
  cells.forEach(function (c) { if (c === '卖家自发货' || c === '合作对接仓发货') mode = c; });
  var nums = cells.filter(function (c) { return /^\d{6,}$/.test(c); });
  var sku = '';
  cells.forEach(function (c) { if (/^[A-Z]{2,3}-[A-Za-z0-9\-]+$/.test(c) && !sku) sku = c; });
  out.push({size: m[1], mode: mode, skuId: nums[0] || '', sku: sku});
});
return JSON.stringify(out);"""


def load_plan(store):
    name = "bel_blocked_skus2.json" if store == "BEL" else "kls_blocked_skus.json"
    items = json.loads((LOGS / name).read_text(encoding="utf-8"))
    m = {}
    for it in items:
        m[it["sku"]] = int(it["stock"])
    return m


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False), flush=True)


async def read_rows(cap):
    v = await gs.ev(cap, JS_ROWS)
    try:
        return json.loads(v)
    except Exception:
        return []


async def query_spu(cap, spu, expect_code):
    ok, detail = await gs.goto_spu(cap, spu, expect_code=expect_code)
    return ok, detail


async def main_async(a) -> int:
    plan = load_plan(a.store)
    order = BEL_ORDER if a.store == "BEL" else KLS_ORDER
    code_of = {}
    for sku in plan:
        code_of.setdefault(sku.rsplit("-", 1)[0], set()).add(sku)
    # SPU → 货号
    spu_code = {}
    src = "bel_blocked_skus2.json" if a.store == "BEL" else "kls_blocked_skus.json"
    for it in json.loads((LOGS / src).read_text(encoding="utf-8")):
        spu_code[it["spu"]] = it["sku"].rsplit("-", 1)[0]

    cap = await gs.focus_list_page(a.port)
    if cap is None:
        print("没有商品列表页")
        return 2
    tot_ok = tot_fail = tot_skip = 0
    try:
        for spu in order:
            code = spu_code.get(spu, "")
            ok, detail = await query_spu(cap, spu, code)
            if not ok:
                log({"event": "spu-fail", "spu": spu, "code": code, "why": detail})
                continue
            rows = [r for r in await read_rows(cap) if r.get("size") and r.get("skuId")]
            log({"event": "spu", "spu": spu, "code": code,
                 "rows": [(r["size"], r["mode"]) for r in rows]})
            for r in rows:
                sku = "%s-%s" % (code, r["size"])
                if sku not in plan:
                    log({"event": "skip", "sku": sku, "why": "不在清单"})
                    tot_skip += 1
                    continue
                base = plan[sku]
                # 接口不接受 0（6000074 系统异常），0 库存按 1 提交
                send = base if base > 0 else 1
                if r["mode"] == "卖家自发货":
                    log({"event": "already", "sku": sku})
                    tot_skip += 1
                    continue
                if not a.apply:
                    log({"event": "would", "sku": sku, "skuId": r["skuId"], "stock": send})
                    continue
                # 先试「加州 + kansas」两行（加州=base−1、kansas=1），失败再退回单行
                attempts = []
                if a.wh2:
                    attempts.append([(a.wh, max(1, send - 1) if send > 1 else 1), (a.wh2, 1)])
                attempts.append([(a.wh, send)])
                res = None
                for whl in attempts:
                    res = await api.call_edit(cap, spu, r["skuId"], whl)
                    body = str(res.get("body") or "")
                    if '"success":true' in body:
                        tot_ok += 1
                        log({"event": "ok", "sku": sku, "skuId": r["skuId"],
                             "wh": [w for w, _ in whl], "stock": [n for _, n in whl]})
                        break
                    log({"event": "try-fail", "sku": sku, "wh": [w for w, _ in whl],
                         "body": body[:160]})
                else:
                    body = str((res or {}).get("body") or "")
                    tot_fail += 1
                    log({"event": "fail", "sku": sku, "skuId": r["skuId"],
                         "status": (res or {}).get("status"), "body": body[:200]})
                    if "403" in str((res or {}).get("status")) or "No Permission" in body:
                        log({"event": "abort", "why": "Anti-Content 头失效，需要重新抓一次"})
                        return 4
                await asyncio.sleep(0.6)
            # 回读
            await asyncio.sleep(1.5)
            await gs.click(cap, json.loads(await gs.ev(cap, gs.JS_QUERY_XY)))
            await asyncio.sleep(3.0)
            rows2 = [r for r in await read_rows(cap) if r.get("size") and r.get("skuId")]
            left = [r["size"] for r in rows2 if r["mode"] != "卖家自发货"]
            log({"event": "verify", "spu": spu, "left": left})
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass
    log({"event": "summary", "ok": tot_ok, "fail": tot_fail, "skip": tot_skip})
    return 0 if tot_fail == 0 else 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--store", default="BEL", choices=("BEL", "KLS"))
    ap.add_argument("--wh", required=True, help="目标仓 warehouseId（加州3仓）")
    ap.add_argument("--wh2", default="", help="次仓 warehouseId（BEL 的 kansas仓），给了就先试两行")
    ap.add_argument("--apply", action="store_true")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
