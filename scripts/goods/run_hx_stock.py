# -*- coding: utf-8 -*-
"""恒信 / 加州3 / Kansas 三仓库存同步：一条命令跑完整 pipeline。

对应口径（2026-11-23 用户最终确认）：
    恒信仓   = max(0, ⌈领星可用 × 80%⌉)
    加州3仓  = max(0, ⌈帕沃尔加州3可用 × 80%⌉)
    Kansas   = 0
    例外：恒信与加州3 **两边都算出 0** 的（货号-尺码）不写入，只出清单等人工确认。

阶段（`--stage` 可单跑，`--from/--to` 可区间跑）：
    env       检查页面/端口/请求头/数据文件是否齐
    refresh   抓最新领星 + 帕沃尔库存（帕沃尔强制豪2 14450）
    snapshot  重建当天库存快照（只保留在售中）
    onsale    接口直读本店「在售中」货号 → data/store-db/<STORE>.onsale.txt
    db        建/刷新本店数据库（含套装映射换算）→ data/store-db/<STORE>.json
    apply     接口写库存（恒信 / 加州3 / Kansas）
    report    出结果清单 + 两边都为 0 的待确认清单

用法：
    python scripts/goods/run_hx_stock.py --store BHY                 # 全跑（dry-run，不写后台）
    python scripts/goods/run_hx_stock.py --store BHY --apply         # 真写
    python scripts/goods/run_hx_stock.py --store BEL --stage env     # 只体检
    python scripts/goods/run_hx_stock.py --store BEL --from onsale --apply
    python scripts/goods/run_hx_stock.py --all --apply               # 三店全跑
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "scripts" / "goods"
PY = sys.executable
LOGS = ROOT / "_logs"
STORES = ("BHY", "BEL", "KLS")

STAGES = ("env", "refresh", "snapshot", "onsale", "db", "apply", "report")

#: 店 → 默认 CDP 端口（每次开窗会变，env 阶段会现场探）
WINDOW = {"BHY": "TM-BHY-YJF", "BEL": "TM-BEL-MGL", "KLS": "TM-KinglamanSport"}


def run(cmd: list, allow_fail: bool = False) -> int:
    print("\n$ " + " ".join(str(x) for x in cmd[1:]), flush=True)
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    out = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    for line in out[-12:]:
        print("   " + line[:180])
    print("   → rc=%d" % p.returncode, flush=True)
    return 0 if (p.returncode == 0 or allow_fail) else p.returncode


def probe_port(store: str) -> int:
    sys.path.insert(0, str(ROOT / "scripts" / "power"))
    try:
        from lib import ziniao_store as zs  # noqa: PLC0415
        return zs.probe_port_by_window(WINDOW[store]) or 0
    except Exception as e:  # noqa: BLE001
        print("  [warn] 探端口失败：%s" % str(e)[:100])
        return 0


def store_mallid(port: int) -> str:
    sys.path.insert(0, str(G))
    sys.path.insert(0, str(ROOT / "scripts" / "power" / "lib"))
    import asyncio  # noqa: PLC0415
    import cdp_net  # noqa: PLC0415

    # 直接按 "名=" 切 cookie，避免正则转义在不同层被吃掉
    js = ("(function(){var parts=document.cookie.split(';');"
          "for(var i=0;i<parts.length;i++){var p=parts[i].trim();"
          "if(p.indexOf('mallid=')===0){return p.substring(7);}}return '';})()")

    async def _go():
        for p in [x for x in cdp_net.http_json("http://127.0.0.1:%d/json/list" % port)
                  if x.get("type") == "page" and "agentseller.temu.com" in (x.get("url") or "")]:
            try:
                r = await asyncio.wait_for(cdp_net._one_shot(
                    p["webSocketDebuggerUrl"], "Runtime.evaluate",
                    {"expression": js, "returnByValue": True}), timeout=8)
                v = str((r.get("result") or {}).get("value") or "")
                if v:
                    return v
            except Exception:
                continue
        return ""

    return asyncio.run(_go())


def stage_env(store: str, port: int) -> int:
    print("== env：%s ==" % store)
    if not port:
        print("  ✗ 探不到窗口 %s 的 CDP 端口（先开窗口）" % WINDOW[store])
        return 3
    print("  端口 %d" % port)
    mid = store_mallid(port)
    hdr = LOGS / ("edit_headers_%s.json" % mid) if mid else None
    print("  mallid %s / 请求头 %s" % (mid or "?", "有" if (hdr and hdr.exists()) else "缺"))
    if not mid:
        print("  ✗ 拿不到 mallid（页面不通或未登录）")
        return 3
    if not (hdr and hdr.exists()):
        print("  ⚠ 缺 Anti-Content：请在该店手动提交一次「修改库存」再跑")
    for f in ("temu-admin/public/data/linxing-stock.json",
              "temu-admin/public/data/pw-stock.json"):
        p = ROOT / f
        print("  %s %s" % ("✓" if p.exists() else "✗", f))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="恒信/加州3/Kansas 库存同步 pipeline")
    ap.add_argument("--store", default="BHY", choices=STORES)
    ap.add_argument("--all", action="store_true", help="三店依次跑")
    ap.add_argument("--port", type=int, default=0, help="不给就现场探")
    ap.add_argument("--stage", default="", help="只跑某一阶段：%s" % ",".join(STAGES))
    ap.add_argument("--from", dest="frm", default="")
    ap.add_argument("--to", dest="to", default="")
    ap.add_argument("--apply", action="store_true", help="真写后台（默认 dry-run）")
    ap.add_argument("--skip-refresh", action="store_true")
    a = ap.parse_args()

    stores = list(STORES) if a.all else [a.store]
    st = list(STAGES)
    if a.stage:
        st = [a.stage]
    elif a.frm or a.to:
        i = st.index(a.frm) if a.frm else 0
        j = st.index(a.to) if a.to else len(st) - 1
        st = st[i:j + 1]
    if a.skip_refresh and "refresh" in st:
        st.remove("refresh")

    rc_all = 0
    # refresh / snapshot 是全店共用的，只跑一次
    shared = [x for x in st if x in ("refresh", "snapshot")]
    if "refresh" in shared:
        rc_all = rc_all or run([PY, str(G / "refresh_inventory.py")])
    if "snapshot" in shared:
        rc_all = rc_all or run([PY, str(G / "daily_stock_snapshot.py"),
                                "--stores", ",".join(stores)])

    for store in stores:
        port = a.port or probe_port(store)
        print("\n" + "=" * 60)
        print("%s（端口 %s）" % (store, port or "?"))
        print("=" * 60)
        if "env" in st:
            rc = stage_env(store, port)
            rc_all = rc_all or rc
            if rc:
                continue
        if not port:
            print("  ✗ 没有端口，跳过 %s" % store)
            rc_all = rc_all or 3
            continue
        if "onsale" in st:
            rc_all = rc_all or run([PY, str(G / "read_onsale_api.py"),
                                    "--port", port, "--store", store])
        if "db" in st:
            rc_all = rc_all or run([PY, str(G / "store_db.py")])
        if "apply" in st:
            cmd = [PY, str(G / "api_hx_ca3.py"), "--port", port, "--store", store]
            if a.apply:
                cmd.append("--apply")
            rc_all = rc_all or run(cmd, allow_fail=True)
    if "report" in st:
        run([PY, str(G / "hx_ca3_report.py")], allow_fail=True)
        run([PY, str(G / "zero_confirm_report.py")], allow_fail=True)
        # 上传飞书云盘「temu更新库存」并推 TEMU 库存预警群（用户 2026-11-23 要求每轮都做）
        # 只有真写了后台（--apply）才推送，dry-run 不打扰
        if a.apply:
            run([PY, str(G / "push_hx_report.py"), "--all", "--apply"], allow_fail=True)
        else:
            print("\n（dry-run：跳过云盘上传与飞书推送）")
    print("\n完成（rc=%d，%s）" % (rc_all, "已写后台" if a.apply else "dry-run 未写"))
    return rc_all


if __name__ == "__main__":
    raise SystemExit(main())
