# -*- coding: utf-8 -*-
"""只读巡查：BEL 那 16 个 SPU 现在每个尺码是「合作对接仓发货」还是「卖家自发货」。

不写任何东西，只翻列表页 + 读单元格。产出 `_logs/bel_mode_survey.json`。

    python scripts/goods/survey_bel_modes.py --port 59670
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

import goto_spu as gs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "_logs"
OUT = LOGS / "bel_mode_survey.json"

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


async def read_rows(cap):
    v = await gs.ev(cap, JS_ROWS)
    try:
        return json.loads(v)
    except Exception:
        return []


async def main_async(a) -> int:
    plan = json.loads((LOGS / "bel_blocked_skus2.json").read_text(encoding="utf-8"))
    spu_code = {}
    for it in plan:
        spu_code[it["spu"]] = it["sku"].rsplit("-", 1)[0]
    spus = list(dict.fromkeys(a.spu or spu_code.keys()))

    cap = await gs.focus_list_page(a.port)
    if cap is None:
        print("没有商品列表页")
        return 2

    out = {}
    try:
        for spu in spus:
            code = spu_code.get(spu, "")
            ok, detail = await gs.goto_spu(cap, spu, expect_code=code)
            rows = await read_rows(cap) if ok else []
            out[spu] = {"code": code, "ok": ok, "rows": rows,
                        "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            coop = [r["size"] for r in rows if r["mode"] == "合作对接仓发货"]
            self_ = [r["size"] for r in rows if r["mode"] == "卖家自发货"]
            print("%-12s %-22s ok=%s 合作仓=%s 自发货=%s"
                  % (spu, code, ok, "/".join(coop) or "-", "/".join(self_) or "-"), flush=True)
            if not ok:
                print("   detail:", str(detail)[:160], flush=True)
    finally:
        try:
            await cap.__aexit__(None, None, None)
        except Exception:
            pass

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    left = sum(1 for v in out.values() for r in v["rows"] if r["mode"] == "合作对接仓发货")
    done = sum(1 for v in out.values() for r in v["rows"] if r["mode"] == "卖家自发货")
    print("写入 %s ｜ 仍是合作仓 %d 个尺码 / 已自发货 %d 个" % (OUT, left, done))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=59670)
    ap.add_argument("--spu", action="append", help="只查这些 SPU（默认全部 16 个）")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
