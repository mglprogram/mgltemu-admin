# -*- coding: utf-8 -*-
"""restore_shein_clear.py — 把「已清零」的款恢复回清零前的线上库存。

场景（用户 2026-11-12）：203318-2B / 203319-2B 是拿 MATERNITYSETS-2B / 3B
**替发**的，不该按「领星查不到就是没货」清零。规则已经补进
config/shein_stock_rules.json（BAN.equivalents），这里负责把线上量补回去。

口径：
  · 从清零台账 data/shein-clear-ledger.json 取该店记录（可再按款-变体过滤）
  · 只补「线上现值 = 0」的；用 ADD 把 `before`（清零前线上）加回去
  · 成功后把这条台账记录删掉（它不再处于清零状态，面板也就不显示了）
  · 跑完重建面板 JSON

    python scripts\\goods\\restore_shein_clear.py --store BAN --style 203318-2B,203319-2B
    python scripts\\goods\\restore_shein_clear.py --store BAN --style 203318-2B --dry-run
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))

import shein_stock_api as api        # noqa: E402
import shein_stock_sync as sync      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--style", default="", help="只恢复这些 款-变体（逗号分隔，如 203318-2B,203319-2B）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    store = a.store.upper()
    wants = {s.strip().lower() for s in a.style.split(",") if s.strip()}

    items = [x for x in sync.load_clear_ledger() if (x.get("store") or "").upper() == store]
    if wants:
        items = [x for x in items
                 if ("%s-%s" % (x.get("code"), x.get("variant"))).lower() in wants]
    todo = [x for x in items if (x.get("onlineNow") or 0) <= 0 and (x.get("before") or 0) > 0]
    if not todo:
        print("没有可恢复的记录（%s%s）" % (store, "｜%s" % a.style if a.style else ""))
        return 0
    print("待恢复 %d 条，合计 %d 件：" % (len(todo), sum(int(x["before"]) for x in todo)))
    for x in todo:
        print("   %-24s 清零前 %s 件  %s" % (x["seller"], x["before"], x["clearedAt"]))
    if a.dry_run:
        print("[dry-run] 加 --apply 才真提交")
        return 0

    rows = [dict(sku=x["sku"], warehouse="", quantity=int(x["before"]),
                 reason="恢复：替发款，不清零") for x in todo]
    # 仓库码留空 = 用接口默认仓（清零时也是这么下发的）；真需要指定就用台账里的 wh
    for r, x in zip(rows, todo):
        if x.get("wh"):
            r["warehouse"] = x["wh"]
    res = api.change(rows, store, "ADD", apply=True)
    ok = [r for r in res if r["ok"]]
    print("提交 %d，成功 %d，失败 %d" % (len(res), len(ok), len(res) - len(ok)))
    for r in res:
        if not r["ok"]:
            print("   FAIL %s %s" % (r["sku"], r["msg"]))
    if not ok:
        return 1

    # 成功的从台账里摘掉（不再算「已清零」）
    ok_sku = {r["sku"] for r in ok}
    keep = [x for x in sync.load_clear_ledger()
            if not ((x.get("store") or "").upper() == store and x.get("sku") in ok_sku)]
    sync.CLEAR_LEDGER.write_text(
        __import__("json").dumps({"updatedAt": __import__("datetime").datetime.now()
                                  .strftime("%Y-%m-%d %H:%M:%S"), "items": keep},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print("台账剩 %d 条（已移除 %d 条）" % (len(keep), len(ok)))

    subprocess.call([sys.executable, str(ROOT / "scripts" / "goods" / "build_presale_watch.py")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
