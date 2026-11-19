# -*- coding: utf-8 -*-
"""汇总「发货模式」切换进度：清单 vs 台账（_logs/switch_watch.jsonl）。

输出：每店已完成/待改尺码数、按 SPU 的完成度、剩余清单，并写 `_logs/switch_progress.md`。
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

LOGS = Path(__file__).resolve().parents[2] / "_logs"
# 早期人工/半自动完成的尺码（不在 watch 台账里）
EARLY = {
    "BEL-1040-5SetA-S": "手工 51",
    "BEL-1040-5SetA-M": "手工 49+1",
    "BK-204306-4SetA-S": "手工 69",
}


def load_plan():
    plan = {}
    for name, store in (("bel_blocked_skus2.json", "BEL"), ("kls_blocked_skus.json", "KLS")):
        p = LOGS / name
        if not p.exists():
            continue
        for it in json.loads(p.read_text(encoding="utf-8")):
            plan[it["sku"]] = {"store": store, "stock": int(it["stock"]), "spu": it["spu"]}
    return plan


def load_done():
    done = {}
    p = LOGS / "switch_watch.jsonl"
    if not p.exists():
        return dict(EARLY)
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") == "done" and r.get("sku"):
            done[r["sku"]] = "%s %s/%s" % (r.get("ts", ""), r.get("filled"), r.get("base"))
    done.update(EARLY)
    return done


def main():
    plan = load_plan()
    done = load_done()
    out = ["# 发货模式切换进度", ""]
    for store in ("BEL", "KLS"):
        items = {k: v for k, v in plan.items() if v["store"] == store}
        ok = [k for k in items if k in done]
        todo = [k for k in items if k not in done]
        out.append("## %s：%d / %d 尺码完成" % (store, len(ok), len(items)))
        by_spu = OrderedDict()
        for k, v in items.items():
            by_spu.setdefault((v["spu"], k.rsplit("-", 1)[0]), []).append(k)
        for (spu, code), skus in by_spu.items():
            n_ok = sum(1 for s in skus if s in done)
            flag = "✅" if n_ok == len(skus) else "→"
            out.append("- %s %s %s %d/%d" % (flag, spu, code, n_ok, len(skus)))
        if todo:
            out.append("")
            out.append("待改：`%s`" % " ".join(todo))
        out.append("")
    md = "\n".join(out)
    (LOGS / "switch_progress.md").write_text(md, encoding="utf-8")
    print(md)
    print("→", LOGS / "switch_progress.md")


if __name__ == "__main__":
    main()
