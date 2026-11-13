# -*- coding: utf-8 -*-
"""run_daily_presale.py — 每天 08:30 按「在售款明细 + 领星总库存」自动改库存/开预售。

用户 2026-11-13：定时在 8:30 去修改库存。

流程：
    ① presale_plan.build() 算出今天该「开预售 / 只清零」的款-尺码（v3 口径）
    ② 逐款调 stock_presale_pipeline.py --apply（同款多尺码一次提交、两次确认、回读）
    ③ 成功的预售写台账；成功的清零写 data/temu-clear-ledger.json
    ④ 重建 presale-watch.json（面板「待处理 → 预售中/已清零」自动归位）

用法：
    python scripts\\goods\\run_daily_presale.py            # 真跑
    python scripts\\goods\\run_daily_presale.py --dry-run  # 只看清单
    python scripts\\goods\\run_daily_presale.py --limit 3  # 只跑前 3 个款（调试）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import presale_plan as PP  # noqa: E402

OUTDIR = ROOT / "_logs" / "daily_presale"
LEDGER = ROOT / "data" / "presale-ledger.json"
CLEAR = ROOT / "data" / "temu-clear-ledger.json"
SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL"]


def _append_presale(store, code, size, spu, qty, date_s, opened_at):
    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"items": []}
    items = [x for x in led.get("items") or []
             if not (x.get("store") == store and x.get("code") == code and x.get("size") == size)]
    items.append({"platform": "TEMU", "store": store, "code": code, "size": size, "spu": spu,
                  "qty": int(qty), "date": date_s, "openedAt": opened_at})
    LEDGER.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")


def _append_clear(store, code, size, spu, before):
    led = json.loads(CLEAR.read_text(encoding="utf-8")) if CLEAR.exists() else {"items": []}
    items = {(x.get("store"), x.get("code"), x.get("size")): x for x in led.get("items") or []}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old = items.get((store, code, size)) or {}
    items[(store, code, size)] = {
        "platform": "TEMU", "store": store, "code": code, "size": size, "spu": spu,
        "before": before, "clearedAt": now,
        "firstClearedAt": old.get("firstClearedAt") or now,
        "times": (old.get("times") or 0) + 1 if old else 1,
        "confirmed": True, "source": "daily-0830", "onlineNow": 0, "checkedAt": now,
    }
    CLEAR.parent.mkdir(parents=True, exist_ok=True)
    CLEAR.write_text(json.dumps({"updatedAt": now,
                                 "items": sorted(items.values(), key=lambda x: (x["store"], x["code"], x["size"]))},
                                ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--store", default="", help="只跑某店（BEL/BHY）")
    a = ap.parse_args()

    rows = PP.build()
    if a.store:
        rows = [r for r in rows if r["store"] == a.store.upper()]
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["store"], r["code"]), {"spu": r["spu"], "sizes": set()})
        groups[(r["store"], r["code"])]["sizes"].add(r["size"])

    print("[%s] 待办 %d 条 / %d 个款" % (datetime.now().strftime("%H:%M"), len(rows), len(groups)))
    if a.dry_run:
        for (st, code), g in sorted(groups.items()):
            print("   %-4s %-22s %s" % (st, code, ",".join(sorted(g["sizes"]))))
        return 0

    OUTDIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    ok, bad = 0, 0
    for i, ((store, code), g) in enumerate(sorted(groups.items()), 1):
        if a.limit and i > a.limit:
            break
        sizes = sorted(g["sizes"], key=lambda s: SIZE_ORDER.index(s) if s in SIZE_ORDER else 99)
        log = OUTDIR / ("%s_%s_%s.log" % (stamp, store, code))
        cmd = [sys.executable, "-X", "utf8", str(ROOT / "scripts" / "goods" / "stock_presale_pipeline.py"),
               "--store", store, "--spu", str(g["spu"]), "--code", code,
               "--sizes", ",".join(sizes), "--stock", "zero", "--apply"]
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(ROOT), timeout=900)
        out = (p.stdout or "") + (p.stderr or "")
        log.write_text(out, encoding="utf-8")
        good = "结论: ✅" in out
        print("[%02d/%d] %s %s %s → %s (%.0fs)"
              % (i, len(groups), store, code, ",".join(sizes), "✅" if good else "❌", time.time() - t0), flush=True)
        if not good:
            bad += 1
            for ln in [x for x in out.strip().splitlines() if x.strip()][-2:]:
                print("      " + ln[:160], flush=True)
            continue
        ok += 1
        opened = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for m in re.finditer(r"预售 (\S+)\s+开关=已开启\s+日期=(\d{4}-\d{2}-\d{2})\s+在途量=(\d+)", out):
            attr, date_s, qty = m.group(1), m.group(2), m.group(3)
            _append_presale(store, code, attr.split("-")[-1], g["spu"], qty, date_s, opened)
        for m in re.finditer(r"库存 (\S+)\s+服务端=0\s+期望=0\s+✅", out):
            size = m.group(1).split("-")[-1]
            before = next((r["temuStock"] for r in rows
                           if r["store"] == store and r["code"] == code and r["size"] == size), None)
            _append_clear(store, code, size, g["spu"], before)

    print("\n===== 完成：成功 %d 款 / 失败 %d 款 =====" % (ok, bad), flush=True)
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "goods" / "build_presale_watch.py")],
                   cwd=str(ROOT))
    subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "bel_goods" / "publish_admin.py"),
                    "--build", "--production", "--skip-data"], cwd=str(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
