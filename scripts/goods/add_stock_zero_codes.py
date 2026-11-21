# -*- coding: utf-8 -*-
"""给某店某款号下**所有尺码同时为 0** 的在售 SKC 批量「增加 N」（默认 50）。

用户 2026-11-19：「KLS 店铺 102503（组合套）……不仅是 10 件套，5 件套库存为零的也默认加上 50，
只要是在售中」。

设计（为什么这样写）：
  · 只处理**在售中**的货号，并且**整款全码为 0** 才加（半零的不动 —— 那种通常是卖完了，
    加 50 会把别款货占住）；
  · 「当前库存」以**弹窗实读**为准（不能信商品列表/快照，KLS 弹窗当前库存常常是「-」）；
  · 加库存 = 「增加」方向 + 填差值，多尺码一次提交、两次确认，提交后重开弹窗回读；
  · 同一 SPU 多 SKC 一律按 `--code` 定位，避免改到同属性集名的另一个货号。

用法：
    python scripts/goods/add_stock_zero_codes.py --store KinglamanSport --style 102503 --dry-run
    python scripts/goods/add_stock_zero_codes.py --store KinglamanSport --style 102503 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "goods"))
sys.path.insert(0, str(ROOT / "scripts" / "power"))
sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))

import bel_stock_set as B  # noqa: E402
import cdp_net  # noqa: E402
import zreact  # noqa: E402
from ziniao_port import probe_port  # noqa: E402

LIVE = ROOT / "data" / "presale-live.json"


def on_sale_zero(store: str, style: str) -> list:
    """返回 [(code, spu, [sizes])]：在售中、且该货号在快照里**所有尺码都是 0** 的。"""
    d = json.loads(LIVE.read_text(encoding="utf-8"))
    rows = [r for r in ((d.get("stores") or {}).get(store) or {}).get("rows") or []
            if style in str(r.get("code") or "")]
    g: dict = defaultdict(lambda: {"spu": "", "sizes": {}})
    for r in rows:
        code = str(r.get("code") or "")
        if code.startswith("D-") or code.startswith("DK-"):   # 草稿/删除态，不是在售
            continue
        e = g[code]
        e["spu"] = str(r.get("spu") or e["spu"])
        e["sizes"][str(r.get("size") or "").upper()] = int(r.get("temuStock") or 0)
    out = []
    for code, e in sorted(g.items()):
        if e["sizes"] and all(v == 0 for v in e["sizes"].values()):
            out.append((code, e["spu"], sorted(e["sizes"])))
    return out


async def read_state(tab, code: str, spu: str) -> dict:
    import refresh_presale_live_fields as RF  # noqa: PLC0415
    got = await RF.read_code(tab, code, spu)
    if isinstance(got, dict) and "_err" in got:
        print("  %-22s ✗ %s" % (code, got["_err"]))
        return {}
    return got


async def run(store: str, style: str, qty: int, apply: bool) -> int:
    targets = on_sale_zero(store, style)
    print("[%s] %s 里「在售中 + 全码 0」的货号 %d 个" % (store, style, len(targets)))
    for code, spu, sizes in targets:
        print("   %-24s SPU %-12s 尺码 %s" % (code, spu, ",".join(sizes)))
    if not targets:
        return 0

    # ⚠ 2026-11-19（KLS 实测）：`B.search()` 是按「SPU ID： xxx」那行文字判定命中的，
    #   KLS/本土店的列表不显示这行 ⇒ rows=33/hit=0，但弹窗其实能开。
    #   这里改用 `open_stock_dialog` 的返回值（成功返回 "real-mouse"/"react(...)"）当判据。
    from stock_presale_pipeline import pick_work_page, open_tab  # noqa: PLC0415

    port, tid, opened = pick_work_page(store)
    if not port:
        print("!! 探不到窗口 %s" % store)
        return 3
    tab = await open_tab(port, tid, opened, store)
    done = []
    try:
        if not await tab.open():
            print("!! 工作页打不开")
            return 3
        await tab.ensure()
        await zreact.inject(tab.session)
        if not await B.page_ready(tab, 60):
            print("!! 列表没加载出来")
            return 5
        for code, spu, _sizes in targets:
            await B.close_overlays(tab)
            if await B.dialog_open(tab):
                await B.close_dialog(tab)
            await B.pick_query_type(tab, "SPU")
            st = await B.search(tab, spu)          # 尽力而为；命中判定不依赖它
            if not await B.dialog_open(tab):
                r = await B.open_stock_dialog(tab, spu)
                if not await B.wait_dialog(tab, 14):
                    print("  %-22s ✗ 弹窗打不开（search=%s open=%s）" % (code, st, r))
                    continue
            state = await read_state(tab, code, spu)
            if not state:
                continue
            todo = {sz: f for sz, f in state.items()
                    if not int(f.get("stock") or 0) and not f.get("on")}
            print("  %-22s 尺码 %d 个，其中 0 库存未开预售 %d 个：%s"
                  % (code, len(state), len(todo), ",".join(sorted(todo))))
            if not todo:
                print("  %-22s · 没有需要加的（可能已有库存/已开预售）" % code)
                continue
            # 重新打开弹窗（read_code 读完会关）
            if not await B.dialog_open(tab):
                await B.open_stock_dialog(tab, spu)
                await B.wait_dialog(tab, 12)
            for sz in sorted(todo):
                blocks = await B.rows_of_code(tab, code, sz)
                if not blocks:
                    print("    %-4s ✗ 弹窗里找不到这个尺码" % sz)
                    continue
                r = await B.set_row(tab, blocks["attr"], "增加", str(qty), code=code)
                print("    %-4s 加 %d ← %s" % (sz, qty, r))
            if not apply:
                print("    (dry-run 不提交) 取消:", await B.close_dialog(tab))
                continue
            print("    提交:", await B.confirm(tab))
            await asyncio.sleep(3)
            after = await read_state(tab, code, spu) if not await B.dialog_open(tab) else {}
            if not after:
                if not await B.dialog_open(tab):
                    await B.open_stock_dialog(tab, spu)
                    await B.wait_dialog(tab, 12)
                after = await read_state(tab, code, spu)
            ok = all(int((after.get(sz) or {}).get("stock") or 0) >= qty for sz in todo)
            print("    %-22s 回读 %s → %s" % (
                code, {sz: (after.get(sz) or {}).get("stock") for sz in sorted(todo)},
                "✅" if ok else "❌"))
            done.append({"code": code, "spu": spu, "sizes": sorted(todo), "after": after,
                         "ok": ok})
    finally:
        try:
            await tab.keep_tab()
        except Exception:  # noqa: BLE001
            pass
    if done:
        p = ROOT / "data" / "stock-add-ledger.json"
        old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}
        items = old.get("items") or []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for d in done:
            for sz in d["sizes"]:
                items.append({"platform": "TEMU", "store": store, "code": d["code"], "size": sz,
                              "add": qty, "after": (d["after"].get(sz) or {}).get("stock"),
                              "at": now, "by": "add_stock_zero_codes"})
        p.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("台账 →", p)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--style", required=True, help="款号，如 102503")
    ap.add_argument("--qty", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return asyncio.run(run(a.store, a.style, a.qty, a.apply and not a.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
