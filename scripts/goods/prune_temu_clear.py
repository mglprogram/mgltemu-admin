# -*- coding: utf-8 -*-
"""prune_temu_clear.py — 从 TEMU 清零台账里删掉指定款（面板上「已清零」不再显示）。

场景（用户 2026-11-13）：这些款已经重新加了库存 / 用同款其他变体替发，
不再算「已清零」，要从「修改库存-预售」面板移除。

用法：
    # 只看会删哪些
    python scripts\\goods\\prune_temu_clear.py --code 202102 --code 204203-4SetC --code 204203-4SetA ^
        --code 204207-4SetA --code 2038-4SetA --code 202506-5SetC

    # 真删
    ... --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data" / "temu-clear-ledger.json"


def match(code: str, want: list[str]) -> bool:
    """want 里的写法：`202102` 匹配 BHY-202102-5SetC 这种整款；`204203-4SetC` 匹配到变体。"""
    body = code.split("-", 1)[1] if "-" in code else code
    for w in want:
        w = w.strip()
        if not w:
            continue
        if body == w or body.startswith(w + "-"):
            return True
        # 只给款号（202102）时，body 是 202102-5SetC，也算命中
        if "-" not in w and body.startswith(w):
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="从 TEMU 清零台账移除指定款")
    ap.add_argument("--code", action="append", required=True, help="款号或 款号-套系，可重复")
    ap.add_argument("--store", default="", help="只删这个店（BEL/BHY），默认全删")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"items": []}
    keep, drop = [], []
    for x in led.get("items") or []:
        if a.store and (x.get("store") or "").upper() != a.store.upper():
            keep.append(x)
            continue
        (drop if match(x.get("code") or "", a.code) else keep).append(x)

    print("命中 %d 条：" % len(drop))
    for x in sorted(drop, key=lambda y: (y.get("store") or "", y.get("code") or "", y.get("size") or "")):
        print("   %-4s %-22s %-4s 清零前 %s" % (x.get("store"), x.get("code"), x.get("size"), x.get("before")))
    if not drop:
        print("（没有可删的）")
        return 0
    if not a.apply:
        print("(dry-run，加 --apply 才写)")
        return 0
    out = {"updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "items": keep}
    LEDGER.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入 %s，剩 %d 条" % (LEDGER.name, len(keep)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
