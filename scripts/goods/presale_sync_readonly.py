# -*- coding: utf-8 -*-
"""预售面板只读同步：读三店库存/预售状态 → 重算 → 更新并发布「修改库存-预售」面板。

用户 2026-11-26：
    「你定时去读取三个店铺的库存和预售，不在前端修改库存，但是要同步到我的面板，
      我自己去修改库存和开预售和清零」

⇒ 本脚本**绝对不写 TEMU 后台**：不调用 api_hx_ca3 / api_presale / 任何 --apply。
   它只做四件事：
       ① 找三店 CDP 端口（每次开窗都变，绝不写死）
       ② fetch_live_oneshot 逐店回读真实库存 + enablePreSale + 在途预售 + 补货日
       ③ plan_v4 重算「待开预售 / 待加库存 / 待清零」建议
       ④ build_presale_watch + publish_admin 更新并发布面板

退出码：
    0  三店都读到，面板已重建
    1  面板已重建，但有店没读到（该店显示旧 fetchedAt + staleHours）
    2  三店全没读到（不动面板，避免用空数据覆盖）

用法：
    python scripts/goods/presale_sync_readonly.py --probe          # 只打印端口
    python scripts/goods/presale_sync_readonly.py --all --publish  # 全跑并发布
    python scripts/goods/presale_sync_readonly.py --all            # 全跑不发布（看结果）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "scripts" / "goods"
PY = sys.executable
LOGS = ROOT / "_logs" / "presale-sync"

#: 店 → 紫鸟窗口名（端口每次开窗都变，必须现场探）
WINDOW = {"BHY": "TM-BHY-YJF", "BEL": "TM-BEL-MGL", "KLS": "TM-KinglamanSport"}
#: 店 → 落盘用的规范名（KLS 在 panel 里叫 KinglamanSport）
CANON = {"BHY": "BHY", "BEL": "BEL", "KLS": "KinglamanSport"}


def probe(store: str) -> int:
    """探端口：先用 power 域，再用 goods 域重试版兜底（实例偶发返回 0）。"""
    win = WINDOW[store]
    try:
        sys.path.insert(0, str(ROOT / "scripts" / "power"))
        from lib import ziniao_store as zs  # noqa: PLC0415

        p = zs.probe_port_by_window(win) or 0
        if p:
            return int(p)
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.path.insert(0, str(G))
        from ziniao_port import probe_port  # noqa: PLC0415

        return int(probe_port(win, attempts=3) or 0)
    except Exception:  # noqa: BLE001
        return 0


def run(cmd: list) -> int:
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    out = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    tail = out[-8:]
    for line in tail:
        try:
            print("   " + line[:180])
        except UnicodeEncodeError:
            enc = sys.stdout.encoding or "utf-8"
            print("   " + line[:180].encode(enc, "replace").decode(enc, "replace"))
    return p.returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只打印三店端口（JSON 行）")
    ap.add_argument("--all", action="store_true", help="三店全跑")
    ap.add_argument("--store", default="", help="只跑某个店（BHY/BEL/KLS）")
    ap.add_argument("--publish", action="store_true", help="重建后发布面板")
    ap.add_argument("--skip-fetch", action="store_true", help="跳过抓取，只用现有数据重建")
    a = ap.parse_args()

    stores = list(WINDOW) if a.all else ([a.store] if a.store else list(WINDOW))

    ports = {st: probe(st) for st in stores}
    if a.probe:
        for st in stores:
            print(json.dumps({"store": st, "port": ports[st]}, ensure_ascii=False))
        return 0

    LOGS.mkdir(parents=True, exist_ok=True)
    print("== 只读同步 %s ==" % time.strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    print("端口：%s" % json.dumps(ports, ensure_ascii=False), flush=True)

    ok, skip = 0, []
    if not a.skip_fetch:
        for st in stores:
            port = ports.get(st)
            if not port:
                print("[SKIP] %s 没探到端口（窗口没开？）" % st, flush=True)
                skip.append(st)
                continue
            print("\n$ 回读 %s（端口 %s）" % (st, port), flush=True)
            rc = run([PY, str(G / "fetch_live_oneshot.py"), str(port), CANON[st]])
            if rc == 0:
                ok += 1
            else:
                skip.append(st)
                print("[WARN] %s 回读失败 rc=%d（面板该店会是旧数据）" % (st, rc), flush=True)

    if not a.skip_fetch and ok == 0:
        print("\n!! 三店全没读到 → 不动面板，避免用空数据覆盖", flush=True)
        return 2

    print("\n$ 重算计划", flush=True)
    run([PY, str(G / "plan_v4.py"), "--json", str(ROOT / "data" / "presale-v4-plan.json")])
    print("\n$ 重建面板数据", flush=True)
    run([PY, str(G / "build_presale_watch.py")])

    # 回读面板里的新鲜度，给日志一个明确结论
    try:
        d = json.loads((ROOT / "temu-admin" / "public" / "data" / "presale-watch.json")
                       .read_text(encoding="utf-8"))
        lv = d.get("live") or {}
        print("\n新鲜度 staleHours=%s staleWarn=%s"
              % (json.dumps(lv.get("staleHours"), ensure_ascii=False), lv.get("staleWarn")))
    except Exception:  # noqa: BLE001
        pass

    if a.publish:
        print("\n$ 发布面板", flush=True)
        rc = run([PY, str(ROOT / "scripts" / "bel_goods" / "publish_admin.py"),
                  "--build", "--production", "--skip-data"])
        if rc == 6:
            print("   （发布锁被占：另一轮正在发同一份数据，本次并入，非故障）")
        elif rc != 0:
            print("   [WARN] 发布失败 rc=%d（面板数据已更新，只是没上线）" % rc)

    if skip:
        print("\n完成：%d/%d 店读到；未读到：%s（面板已标 staleHours）"
              % (ok, len(stores), ",".join(skip)), flush=True)
        return 1
    print("\n完成：%d 店全部读到，面板已更新%s"
          % (ok, "并发布" if a.publish else "（未发布）"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
