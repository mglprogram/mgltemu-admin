# -*- coding: utf-8 -*-
"""恒信仓库存同步总控：抓最新库存 → 快照 → **按店铺分片并行**写恒信仓 → 汇总 → 推送。

用户 2026-11-21：「执行这个定时任务直接去修改恒信仓库存，开三个子代理并行，加抓到最新库存的 1/2 库存」

⚠ 关于「三个子代理并行」的落地方式（重要）：
   紫鸟三店的窗口是**同一个浏览器进程里的独立窗口**，每个窗口只有一个 CDP 端口；
   真开 3 个 AI 子代理会互相抢同一批页面（实测会 no-link / eval 变 None）。
   所以这里用「**三个独立进程 = 三个子代理**」：每个店铺一个 `apply_hx_dialog.py` 子进程，
   各自连自己的窗口、自己的标签页，互不干扰。这就是并行，而且可复现、可重试。

链路：
    ① refresh_inventory.py            领星 + 帕沃尔（最新库存）
    ② daily_stock_snapshot.py          三店快照：一半 / 待清零 / 待开预售 / 已开预售
    ③ 并行：每店一个子进程 apply_hx_dialog.py --store X --apply
    ④ verify / 汇总 + 推 TEMU 库存预警群

用法：
    python scripts/goods/run_hx_dialog_sync.py                 # 三店，默认并行
    python scripts/goods/run_hx_dialog_sync.py --stores BHY
    python scripts/goods/run_hx_dialog_sync.py --dry-run        # 只算不写
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "scripts" / "goods"
SNAP = ROOT / "data" / "stock-snapshot"
PY = sys.executable
STORES = ("BHY", "BEL", "KinglamanSport")


def step(title: str, cmd: list) -> int:
    print("\n=== %s ===" % title)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-8:]:
        print("  " + line[:170])
    print("  → rc=%d" % p.returncode)
    return p.returncode


def codes_for(store: str, day: str) -> list:
    try:
        d = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = ((d.get("stores") or {}).get(store) or {}).get("rows") or []
    return sorted({r["code"] for r in rows if int(r.get("hxHalf") or 0) > 0})


def run_store(store: str, codes: list, apply: bool) -> tuple[str, int, str]:
    """一个店铺 = 一个子进程（相当于一个子代理）。"""
    if not codes:
        return store, 0, "没有需要同步的货号"
    cmd = [PY, str(G / "apply_hx_dialog.py"), "--store", store,
           "--codes", ",".join(codes)]
    if apply:
        cmd.append("--apply")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tail = [l for l in out.splitlines() if l.startswith("完成") or "❌" in l]
    return store, p.returncode, "\n".join(tail[-6:])


def counts(store: str, day: str) -> dict:
    try:
        d = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
        return (((d.get("stores") or {}).get(store) or {}).get("counts")) or {}
    except Exception:  # noqa: BLE001
        return {}


def push(store_results: dict, apply: bool) -> None:
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import notify_push as NP  # noqa: PLC0415
        lines = ["TEMU 恒信仓库存同步（三店并行）",
                 "时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        for st, (rc, tail) in store_results.items():
            c = counts(st, str(date.today()))
            lines.append("")
            lines.append("%s：%s" % (st, "成功" if rc == 0 else "有失败项(rc=%s)" % rc))
            if c:
                lines.append("  待开预售 %s / 待清零 %s / 已清零 %s / 已预售 %s"
                             % (c.get("待开预售", 0), c.get("待清零", 0),
                                c.get("已清零", 0), c.get("已开预售", 0)))
            for t in (tail or "").splitlines()[-2:]:
                lines.append("  " + t.strip()[:80])
        lines.append("")
        lines.append("口径：恒信加州3仓 = max(0, ⌈领星可用÷2⌉ − 加州三仓已有)；加州三仓不动。")
        ok, why = NP.push_stock_alert("TEMU 恒信仓库存同步", "\n".join(lines))
        print("[推送] %s %s" % ("OK" if ok else "失败", why))
    except Exception as e:  # noqa: BLE001
        print("[推送] 异常（忽略）：%s" % e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default=",".join(STORES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    stores = [s.strip() for s in a.stores.split(",") if s.strip()]
    day = str(date.today())

    if step("① 抓最新库存（领星 + 帕沃尔）", [PY, str(G / "refresh_inventory.py")]):
        print("!! 抓库存失败，终止（不用旧数据算一半）")
        return 1
    if step("② 三店快照", [PY, str(G / "daily_stock_snapshot.py"), "--stores", ",".join(stores)]):
        return 1

    plan = {st: codes_for(st, day) for st in stores}
    for st, cs in plan.items():
        print("  %-16s 待同步货号 %d 个" % (st, len(cs)))
    if a.dry_run:
        print("\n(dry-run) 不写后台")
        return 0

    print("\n=== ③ 并行写恒信仓（每店一个子进程）===")
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, len(stores))) as ex:
        futs = {ex.submit(run_store, st, plan[st], True): st for st in stores}
        for f in futs:
            st, rc, tail = f.result()
            results[st] = (rc, tail)
            print("--- %s rc=%d\n%s" % (st, rc, tail))

    if not a.no_push:
        push(results, True)
    bad = [st for st, (rc, _t) in results.items() if rc != 0]
    print("\n汇总：成功 %d 店 / 有失败 %d 店%s"
          % (len(results) - len(bad), len(bad), ("（%s）" % ",".join(bad)) if bad else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
