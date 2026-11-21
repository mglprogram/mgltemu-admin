# -*- coding: utf-8 -*-
"""恒信仓库存同步：三店并行 pipeline（子代理 = 每店一个独立子进程）。

用户 2026-11-21：「执行这个定时任务直接去修改恒信仓库存，开三个子代理并行，加抓到最新库存的 1/2 库存」。

⚠ 并行怎么做：紫鸟三店各自的窗口只有一个 CDP 端口，真开 3 个 AI 子代理会抢页面
   （实测 no-link / eval=None）。这里用「**每店一个独立 python 子进程**」当子代理：
   各自连自己的窗口与标签页，互不干扰，可重试、可复现。

口径（用户 2026-11-21 确认）：
    恒信加州3仓 = max(0, ⌈领星可用 ÷ 2⌉ − 加州三仓已有)
    · 只写「恒信加州3」那一行；加州三仓 / kansas **一个数字都不动**
    · 弹窗内直接「增加 / 减少」，不再打开「设置库存」页（用户 2026-11-21 改的口径）
    · 清零 / 开预售仍只出清单，等人确认

链路：
    ① refresh_inventory.py         抓最新领星(LM739) + 帕沃尔
    ② daily_stock_snapshot.py       三店快照（一半 / 待清零 / 待开预售 / 已开预售）
    ③ 并行 apply_hx_dialog.py       每店一个子进程，写恒信仓
    ④ apply_hx_dialog_loop 复跑    只补「没达标」的货号，最多 N 轮
    ⑤ run_hx_sync.push_report ✉    结果推 TEMU 库存预警群

用法：
    python scripts/goods/run_hx_pipeline.py                    # 三店并行
    python scripts/goods/run_hx_pipeline.py --stores BHY,BEL
    python scripts/goods/run_hx_pipeline.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "scripts" / "goods"
SNAP = ROOT / "data" / "stock-snapshot"
PY = sys.executable
STORES = ("BHY", "BEL", "KinglamanSport")
WINDOWS = {"BHY": "TM-BHY-YJF", "BEL": "TM-BEL-MGL", "KinglamanSport": "TM-KinglamanSport"}


def _run(title: str, cmd: list, keep: int = 8) -> tuple[int, str]:
    print("\n=== %s ===" % title)
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    for line in out.splitlines()[-keep:]:
        print("  " + line[:170])
    print("  → rc=%d" % p.returncode)
    return p.returncode, out


def codes_for(store: str, day: str) -> list:
    try:
        d = json.loads((SNAP / ("%s.json" % day)).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = ((d.get("stores") or {}).get(store) or {}).get("rows") or []
    return sorted({r["code"] for r in rows if int(r.get("hxHalf") or 0) > 0})


def window_alive(store: str) -> bool:
    sys.path.insert(0, str(G))
    sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
    try:
        from ziniao_port import probe_port  # noqa: PLC0415
        return bool(probe_port(WINDOWS[store]))
    except Exception:  # noqa: BLE001
        return False


def run_store(store: str, apply: bool) -> tuple[str, int, str]:
    if not window_alive(store):
        return store, 3, "窗口没开，跳过"
    cmd = [PY, str(G / "apply_hx_dialog.py"), "--store", store, "--all"]
    if apply:
        cmd.append("--apply")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    ok = out.count("✅")
    bad = out.count("❌")
    tail = ["✅ %d 个尺码达标 / ❌ %d 项未达标" % (ok, bad)]
    for line in out.splitlines():
        if "❌" in line or line.startswith("完成"):
            tail.append(line[:150])
    return store, p.returncode, "\n".join(tail[-6:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default=",".join(STORES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rounds", type=int, default=2, help="未达标货号最多再补几轮")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    stores = [s.strip() for s in a.stores.split(",") if s.strip()]
    day = str(_dt.date.today())

    rc, _ = _run("① 抓最新库存（领星 + 帕沃尔）", [PY, str(G / "refresh_inventory.py")])
    if rc:
        print("!! 抓库存失败，终止（不用旧数据算一半）")
        return 1
    rc, _ = _run("② 三店快照", [PY, str(G / "daily_stock_snapshot.py"),
                                "--stores", ",".join(stores)])
    if rc:
        return 1

    for st in stores:
        cs = codes_for(st, day)
        print("  %-16s 待同步货号 %-3d 窗口 %s" % (st, len(cs), "OK" if window_alive(st) else "未开"))
    if a.dry_run:
        print("\n(dry-run) 不写后台")
        return 0

    print("\n=== ③ 并行写恒信仓（每店一个子进程 = 一个子代理）===")
    results: dict = {}
    with ThreadPoolExecutor(max_workers=max(1, len(stores))) as ex:
        for st, rc, tail in ex.map(lambda s: run_store(s, True), stores):
            results[st] = (rc, tail)
            print("--- %-16s rc=%d\n%s" % (st, rc, tail))

    # ④ 只补没达标的货号
    for r in range(1, a.rounds + 1):
        bad = [st for st, (rc, tail) in results.items() if "❌" in tail]
        if not bad:
            break
        print("\n=== ④ 第 %d 轮补跑：%s ===" % (r, ",".join(bad)))
        with ThreadPoolExecutor(max_workers=max(1, len(bad))) as ex:
            for st, rc, tail in ex.map(lambda s: run_store(s, True), bad):
                results[st] = (rc, tail)
                print("--- %-16s rc=%d\n%s" % (st, rc, tail))

    if not a.no_push:
        try:
            sys.path.insert(0, str(G))
            import run_hx_sync as RH  # noqa: PLC0415
            payload = {st: (lambda rc: "成功" if rc == 0 else "rc=%d" % rc)(rc)
                       for st, (rc, _t) in results.items()}
            RH.push_report(",".join(results), payload, note="三店并行写恒信仓")
        except Exception as e:  # noqa: BLE001
            print("[推送] 跳过：%s" % e)

    bad = [st for st, (rc, tail) in results.items() if "❌" in tail or rc == 3]
    print("\n汇总：%d 店 / 未完成：%s" % (len(results), ",".join(bad) or "无"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
