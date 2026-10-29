# -*- coding: utf-8 -*-
"""store_pages.py — 「默认页面」巡检：缺哪个就补哪个，补的**留着不动**。

用户口径（2026-10-29）：
    「当你执行定时任务发现我默认的页面（商品列表、销量分析、生命周期、订单列表）
      哪个页面不存在，帮我打开，你自己打开的即用即关。」

⇒ 两类页必须分清，混在一起就会出现历史上那三次事故：

  · **用户的默认页**（本模块的四张）：缺了就补一张，补完**留着**（它从此就是默认页之一）。
    不登记进 scratch 表，所以 `sweep_leaked_scratch` / `close_new_seller_pages` 不会关它。
  · **任务自己的工作页**：用 `own_page.OwnPage` 开，`with` 退出即关（即用即关）。

判据单点复用：
    `ziniao_store.window_running`  窗口在不在（不在就不补，绝不为了补页去开窗）
    `ziniao_store.list_page_targets` / `open_tab` / `is_real_tab`

用法：
    python scripts/ensure_store_pages.py                    # 两店巡检并补齐
    python scripts/ensure_store_pages.py --stores BHY --dry-run
    python scripts/ensure_store_pages.py --json
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

PAGES = (
    ("goods", "商品列表", "https://agentseller.temu.com/goods/list", "/goods/list"),
    ("lifecycle", "生命周期", "https://agentseller.temu.com/newon/product-select",
     "/newon/product-select"),
    ("sales", "销量分析", "https://agentseller.temu.com/main/data-center/goods-data", "goods-data"),
    ("orders", "订单列表", "https://agentseller-us.temu.com/mmsos/orders.html", "/mmsos/orders"),
)


def _zs():
    import ziniao_store  # noqa: PLC0415
    return ziniao_store


def store_entry(store: str) -> dict:
    """店后缀 → 映射表条目（`{}` = 未知店）。"""
    p = os.path.join(_HERE, os.pardir, "data", "ziniao_power_map.json")
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    for it in m.get("mapping") or []:
        if store in (it.get("export_suffix"), it.get("power_name"), it.get("ziniao_account")):
            return it
    return {}


def missing(port: int, names=None) -> list:
    """返回缺的页 `[(key, name, url, frag)]`（按 `PAGES` 顺序）。"""
    zs = _zs()
    try:
        urls = [str(t.get("url") or "") for t in zs.list_page_targets(int(port or 0))]
    except Exception:  # noqa: BLE001
        return list(PAGES if not names else [p for p in PAGES if p[0] in names])
    want = set(names) if names else {p[0] for p in PAGES}
    return [p for p in PAGES if p[0] in want and not any(p[3] in u for u in urls)]


def ensure_pages(store: str, names=None, dry_run: bool = False, log=print) -> dict:
    """补齐该店缺的默认页。返回 `{"store","port","opened","missing","skipped"}`。

    **绝不**：开窗、关窗、关任何已有页。窗口不在就跳过（交给调用方决定要不要开窗）。
    """
    zs = _zs()
    e = store_entry(store)
    win = (e or {}).get("ziniao_window_name") or ""
    out = {"store": store, "window": win, "port": 0, "opened": [], "missing": [],
           "skipped": ""}
    if not win:
        out["skipped"] = "未知店（映射表里没有 %s）" % store
        return out
    port = zs.probe_port_by_window(win)
    if not port:
        # 窗口可能只是端口没就绪：进程在就等，不在就跳过（不替用户开窗、更不重开）
        out["skipped"] = ("窗口还在但端口未就绪" if zs.window_running(win)
                          else "窗口没开")
        return out
    out["port"] = int(port)
    lack = missing(port, names)
    out["missing"] = [p[1] for p in lack]
    if not lack:
        return out
    for key, name, url, frag in lack:
        if dry_run:
            out["opened"].append({"name": name, "url": url, "tid": "", "dry_run": True})
            continue
        tid = zs.open_tab(port, url)
        if tid:
            out["opened"].append({"name": name, "url": url, "tid": tid})
            log("[页] 缺「%s」→ 已补开（%s）" % (name, url))
        else:
            log("[页] 缺「%s」→ 补开失败（%s）" % (name, url))
    return out


def ensure_for_window_key(key: str, dry_run: bool = False, log=print) -> list:
    """按窗口锁的键补齐（`"BHY+BEL"` / `"BHY"` / `""`）。返回每店结果。"""
    stores = [s.strip() for s in str(key or "").split("+") if s.strip()]
    return [ensure_pages(s, dry_run=dry_run, log=log) for s in stores]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    import argparse
    ap = argparse.ArgumentParser(description="巡检并补齐 TEMU 店的默认页面")
    ap.add_argument("--stores", default="BHY,BEL", help="店后缀，逗号分隔")
    ap.add_argument("--pages", default="", help="只查这些（goods,lifecycle,sales,orders）")
    ap.add_argument("--dry-run", action="store_true", help="只报缺什么，不开")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    names = {x.strip() for x in a.pages.split(",") if x.strip()} or None
    res = [ensure_pages(s.strip(), names=names, dry_run=a.dry_run)
           for s in a.stores.split(",") if s.strip()]
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    for r in res:
        if r["skipped"]:
            print("== %s：跳过（%s）" % (r["store"], r["skipped"]))
            continue
        got = "、".join(x["name"] for x in r["opened"]) or "无"
        lack = "、".join(r["missing"]) or "无"
        print("== %s（%s）缺：%s ｜ 已补：%s" % (r["store"], r["window"], lack, got))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
