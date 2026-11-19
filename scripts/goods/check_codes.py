# -*- coding: utf-8 -*-
"""按事件核对：用户说「改完某款/某 SPU 了」→ 只回读这几个货号，出**前后对比**。

用户 2026-11-19 选的方式 A：「我每改完一款，跟你说一声好了，你马上跑只读回读，
只对比我刚动过的货号，几十秒出核对结果」。

用法：
    # 只回读 + 对比，不写台账/面板数据（最快）
    python scripts/goods/check_codes.py --store BHY --codes BHY-203805-3BHC,BHY-203805-3BHL

    # 顺便把读到的写进面板快照（data/presale-live.json 的对应行）
    python scripts/goods/check_codes.py --store BHY --codes ... --write

输出：每个货号每个尺码一行 —— `库存 旧→新`、`开关 旧→新`、`在途预售 旧→新`、`补货日`，
最后给出「有变化 / 无变化 / 读失败」三段小结。**只读弹窗，不提交任何东西。**
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import fetch_presale15 as F  # noqa: E402
import refresh_presale_live_fields as RF  # noqa: E402

LIVE = ROOT / "data" / "presale-live.json"


def _prev_state(store: str) -> dict:
    """上一次快照：{（code, size): row}"""
    try:
        d = json.loads(LIVE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in ((d.get("stores") or {}).get(store) or {}).get("rows") or []:
        out[(str(r.get("code") or ""), str(r.get("size") or "").upper())] = r
    return out


def _spu_of(store: str, code: str) -> str:
    sp = F._goods_spu()
    s = sp.get((store, code)) or ""
    if s:
        return s
    for (st, c), v in sp.items():
        if c.upper() == code.upper():
            return v
    # 兜底：从实读快照里找
    try:
        d = json.loads(LIVE.read_text(encoding="utf-8"))
        for r in ((d.get("stores") or {}).get(store) or {}).get("rows") or []:
            if str(r.get("code") or "").upper() == code.upper():
                return str(r.get("spu") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


async def run(store: str, codes: list, write: bool) -> int:
    prev = _prev_state(store)
    tab = await RF.setup(store)
    changed, same, failed, got = [], [], [], {}
    try:
        for code in codes:
            spu = _spu_of(store, code)
            if not spu:
                print("  %-24s ✗ 查不到 SPU（不在在售明细/快照里）" % code)
                failed.append((code, "no-spu"))
                continue
            state = await RF.read_code(tab, code, spu)
            if isinstance(state, dict) and "_err" in state:
                print("  %-24s ✗ %s" % (code, state["_err"]))
                failed.append((code, state["_err"]))
                continue
            got[code] = (spu, state)
            print("  %-24s SPU %s" % (code, spu))
            for size in sorted(state):
                f = state[size] or {}
                old = prev.get((code, size)) or {}
                o_stock = old.get("temuStock")
                o_on = old.get("presaleOn")
                o_qty = old.get("presaleQty")
                n_stock = f.get("stock")
                n_on = bool(f.get("on"))
                n_qty = int(f.get("qty") or 0)
                diff = (str(o_stock) != str(n_stock)) or (bool(o_on) != n_on) or (int(o_qty or 0) != n_qty)
                mark = "★" if diff else " "
                print("    %s %-4s 库存 %-5s→%-5s 开关 %-4s→%-4s 在途预售 %-4s→%-4s 补货 %s"
                      % (mark, size, o_stock if o_stock is not None else "-", n_stock,
                         "开" if o_on else "关", "开" if n_on else "关",
                         o_qty if o_qty is not None else "-", n_qty,
                         f.get("date") or "-"))
                (changed if diff else same).append((code, size))
    finally:
        try:
            await tab.keep_tab()
        except Exception:  # noqa: BLE001
            pass
    print("\n小结：有变化 %d 条 / 无变化 %d 条 / 读取失败 %d 个货号"
          % (len(changed), len(same), len(failed)))
    if changed:
        for code, size in changed:
            print("   ★ %s-%s" % (code, size))
    if write and got:
        d = json.loads(LIVE.read_text(encoding="utf-8"))
        for code, (spu, state) in got.items():
            rows = ((d.get("stores") or {}).get(store) or {}).get("rows") or []
            for r in rows:
                if str(r.get("code") or "").upper() != code.upper():
                    continue
                f = state.get(str(r.get("size") or "").upper())
                if not f:
                    continue
                r["temuStock"] = f.get("stock") if f.get("stock") is not None else r.get("temuStock")
                r["presaleOn"] = bool(f.get("on"))
                r["presaleQty"] = int(f.get("qty") or 0)
                r["presaleDate"] = str(f.get("date") or "")
                r["readAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        LIVE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print("已写回快照：", LIVE)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="按事件回读并对比刚改过的货号（只读）")
    ap.add_argument("--store", required=True)
    ap.add_argument("--codes", required=True, help="逗号分隔的货号，如 BHY-203805-3BHC,BHY-203805-3BHL")
    ap.add_argument("--write", action="store_true", help="把读到的写回 data/presale-live.json 的快照行")
    a = ap.parse_args()
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    return asyncio.run(run(a.store, codes, a.write))


if __name__ == "__main__":
    raise SystemExit(main())
