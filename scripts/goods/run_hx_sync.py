# -*- coding: utf-8 -*-
"""06:30 / 12:30 / 18:30 那一轮的总入口：抓最新库存 → 刷新快照 → 更新恒信仓库存。

用户 2026-11-20：「抓取最新的领星总库存 - 更新恒信仓库存，定时早上 6:30、12:30、18:30」。

链路：
    ① refresh_inventory.py   领星 LM739 + 帕沃尔现货 → linxing-stock.json / pw-stock.json
    ② daily_stock_snapshot.py 算「一半 / 待清零 / 待开预售 / 已开预售」→ 快照 + 面板数据
    ③ apply_hx_half.py --apply（仅当 --apply）：把恒信加州3仓写成 max(0, 一半 − 加州三仓已有)
    ④ verify_hx_half.py      按台账核对

⚠ 默认 **只做 ①②④**（只读 + 出清单）；`--apply` 才真的写 TEMU 后台。
⚠ 清零/开预售 **永远不在本脚本里执行** —— 只出清单（pending-presale.json），等人确认。

用法：
    python scripts/goods/run_hx_sync.py            # 抓库存 + 出清单（不写后台）
    python scripts/goods/run_hx_sync.py --apply    # 抓库存 + 刷新快照 + 写恒信仓
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
G = ROOT / "scripts" / "goods"


def step(title: str, cmd: list, allow_fail: bool = False) -> int:
    print("\n=== %s ===" % title)
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    for line in out[-12:]:
        print("  " + line[:180])
    print("  → rc=%d" % p.returncode)
    if p.returncode != 0 and not allow_fail:
        return p.returncode
    return 0


def _snapshot_counts(store: str) -> dict:
    try:
        snap = ROOT / "data" / "stock-snapshot" / ("%s.json" % __import__("datetime").date.today())
        d = json.loads(snap.read_text(encoding="utf-8"))
        blk = ((d.get("stores") or {}).get(store)) or {}
        return {"counts": blk.get("counts") or {}, "sku": blk.get("sku") or 0,
                "at": d.get("generatedAt") or ""}
    except Exception:  # noqa: BLE001
        return {}


def _ledger_codes(store: str) -> list:
    p = ROOT / "data" / "hx-half-ledger.json"
    if not p.exists():
        return []
    try:
        items = json.loads(p.read_text(encoding="utf-8")).get("items") or []
    except Exception:  # noqa: BLE001
        return []
    return sorted({it["code"] for it in items if it.get("store") == store})


def push_report(store: str, step_rc: dict, note: str = "") -> None:
    """每跑一轮就把结果推到「TEMU 库存预警群」（FEISHU_WEBHOOK_STOCK）。**绝不抛异常。**"""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import notify_push as NP  # noqa: PLC0415
        s = _snapshot_counts(store)
        c = s.get("counts") or {}
        lines = ["TEMU 恒信仓库存同步 · %s" % store,
                 "时间：%s" % __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        lines += ["", "① 抓库存：%s" % ("成功" if step_rc.get("refresh") == 0 else "失败"),
                  "② 快照：%s（SKU %s）" % ("成功" if step_rc.get("snapshot") == 0 else "失败",
                                          s.get("sku") or "-")]
        if c:
            lines.append("   待开预售 %s / 待清零 %s / 已清零 %s / 已预售 %s"
                         % (c.get("待开预售", 0), c.get("待清零", 0),
                            c.get("已清零", 0), c.get("已开预售", 0)))
        lines.append("③ 写恒信仓：%s" % (step_rc.get("apply") or "跳过"))
        done = _ledger_codes(store)
        lines.append("   已写台账货号 %d 个%s" % (len(done), ("：" + "、".join(done[:8])) if done else ""))
        if note:
            lines.append("说明：" + note)
        lines.append("清零 / 开预售只出清单，等人确认后再执行。")
        ok, why = NP.push_stock_alert("TEMU 恒信仓库存同步", "\n".join(lines))
        print("[推送] %s %s" % ("OK" if ok else "失败", why))
    except Exception as e:  # noqa: BLE001
        print("[推送] 异常（已忽略）：%s" % e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="BHY")
    ap.add_argument("--apply", action="store_true", help="真的写 TEMU 后台（默认只出清单）")
    ap.add_argument("--no-push", action="store_true", help="不推飞书库存预警群")
    a = ap.parse_args()

    step_rc: dict = {}
    rc = step("① 抓最新库存（领星 + 帕沃尔）", [PY, str(G / "refresh_inventory.py")])
    step_rc["refresh"] = rc
    if rc:
        print("!! 抓库存失败，后面不再继续（避免用旧数据算一半）")
        if not a.no_push:
            push_report(a.store, step_rc, note="抓库存失败，本轮未写后台")
        return rc

    rc = step("② 刷新快照 + 清单", [PY, str(G / "daily_stock_snapshot.py"), "--stores", a.store])
    step_rc["snapshot"] = rc
    if rc:
        if not a.no_push:
            push_report(a.store, step_rc, note="快照失败，本轮未写后台")
        return rc

    if a.apply:
        rc = step("③ 更新恒信仓库存", [PY, str(G / "apply_hx_half.py"),
                                     "--store", a.store, "--apply"])
        step_rc["apply"] = "成功" if rc == 0 else "有失败项（可重跑）"
    else:
        step_rc["apply"] = "跳过（没给 --apply）"
        print("\n=== ③ 跳过写后台（没给 --apply）：清零/开预售/恒信仓都只出清单 ===")

    step("④ 按台账核对", [PY, str(G / "verify_hx_half.py"), "--store", a.store], allow_fail=True)
    if not a.no_push:
        push_report(a.store, step_rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
